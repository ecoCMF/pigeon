"""Terminal dashboard: ``pigeon status --tui``.

A full-screen, keyboard-driven view of the latest coordination run — the
Phase-1 surface of the visuals plan. Same architecture as ``status``: a
pure *reader* of the atomically-updated run manifest and the per-task logs.
No server, no socket; quitting never touches the run, and a finished run
renders exactly like a live one (replay for free).

Panes: header (run state + budget), task table (arrow/j/k to navigate),
and the selected task's log tail. Needs the optional ``[tui]`` extra
(Textual); the plain ``pigeon status --watch`` covers dumb terminals.
"""

from __future__ import annotations

from pathlib import Path

try:
    from textual.app import App, ComposeResult
    from textual.widgets import DataTable, Footer, RichLog, Static
except ImportError as exc:  # pragma: no cover - exercised only without extra
    raise SystemExit(
        "the TUI needs the optional 'textual' dependency: "
        "pip install 'pigeon[tui]' (plain `pigeon status --watch` works without it)"
    ) from exc

from .config import Config
from . import coordinate

_LOG_TAIL_LINES = 300


class StatusApp(App):
    """Reader of coordinate/runs/<id>.json + logs; refreshes on an interval."""

    TITLE = "pigeon status"
    CSS = """
    #header { height: 3; padding: 0 1; color: $text; background: $surface; }
    #tasks  { height: 1fr; }
    #log    { height: 14; border-top: tall $accent; }
    """
    BINDINGS = [
        ("q", "quit", "quit"),
        ("j", "row_down", "down"),
        ("k", "row_up", "up"),
        ("r", "refresh_now", "refresh"),
    ]

    def __init__(self, config: Config, sid: str | None = None,
                 interval: float = 2.0) -> None:
        super().__init__()
        self.config = config
        self.sid = sid
        self.interval = max(0.5, interval)
        self.run_data: dict | None = None
        self._row_tids: list[str] = []

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        yield DataTable(id="tasks")
        yield RichLog(id="log", wrap=False, highlight=False, markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns(" ", "task", "status", "time", "runner", "info")
        self.refresh_run()
        self.set_interval(self.interval, self.refresh_run)

    # ------------------------------------------------------------- refresh
    def refresh_run(self) -> None:
        runs = coordinate.list_runs(self.config, sid=self.sid)
        header = self.query_one("#header", Static)
        if not runs:
            header.update("no coordination runs recorded")
            return
        self.run_data = run = runs[-1]
        header.update("\n".join(coordinate.render_status(run).splitlines()[:2]))

        table = self.query_one(DataTable)
        cursor = table.cursor_row
        table.clear()
        self._row_tids = []
        for tid, t in (run.get("tasks") or {}).items():
            status = t.get("status", "?")
            info = []
            if t.get("needs") and status == "queued":
                info.append("needs: " + ",".join(t["needs"]))
            if (t.get("isolation") or {}).get("branch"):
                info.append("⎇ " + t["isolation"]["branch"])
            if t.get("return_handoff"):
                info.append("↩ handed back")
            if t.get("skipped_because"):
                info.append("; ".join(t["skipped_because"]))
            table.add_row(
                coordinate._STATUS_GLYPHS.get(status, "?"),
                tid, status,
                f"{t['duration_s']}s" if "duration_s" in t else "",
                t.get("runner", "?"),
                "  ".join(info),
            )
            self._row_tids.append(tid)
        if self._row_tids:
            table.move_cursor(row=min(cursor or 0, len(self._row_tids) - 1))
        self.show_log()

    def show_log(self) -> None:
        log = self.query_one(RichLog)
        log.clear()
        if not (self.run_data and self._row_tids):
            return
        table = self.query_one(DataTable)
        tid = self._row_tids[min(table.cursor_row or 0, len(self._row_tids) - 1)]
        rel = (self.run_data["tasks"].get(tid) or {}).get("log")
        if not rel:
            log.write(f"[{tid}] not started yet")
            return
        path = Path(rel) if Path(rel).is_absolute() else self.config.root / rel
        if not path.is_file():
            log.write(f"[{tid}] no log yet at {rel}")
            return
        for line in path.read_text(encoding="utf-8",
                                   errors="replace").splitlines()[-_LOG_TAIL_LINES:]:
            log.write(line)

    # ------------------------------------------------------------- actions
    def on_data_table_row_highlighted(self, _event) -> None:
        self.show_log()

    def action_row_down(self) -> None:
        self.query_one(DataTable).action_cursor_down()

    def action_row_up(self) -> None:
        self.query_one(DataTable).action_cursor_up()

    def action_refresh_now(self) -> None:
        self.refresh_run()


def run(config: Config, sid: str | None = None, interval: float = 2.0) -> int:
    StatusApp(config, sid=sid, interval=interval).run()
    return 0
