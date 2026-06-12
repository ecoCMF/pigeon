"""TUI: a Textual reader over the run manifest (optional [tui] extra)."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual", reason="optional [tui] extra not installed")

from pigeon import coordinate as co
from pigeon.tui import StatusApp


def _record_run(repo):
    rec = co.RunRecorder(
        repo, "ui1",
        [{"id": "a", "runner": "py"}, {"id": "b", "runner": "py", "needs": ["a"]}],
        tasks_file="t.yaml", parallel_limit=2, skip_permissions=False,
        dry_run=False, telemetry=False, isolated_env=None, depth=0)
    log = repo.root / ".agentctx" / "coordinate" / "logs" / "ui1-a.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("hello from a\n# exit 0\n", encoding="utf-8")
    rec.task("a", status="exited", exit_code=0, duration_s=0.5,
             log=str(log.relative_to(repo.root)))
    rec.task("b", status="queued")
    rec.finish("completed", summary={"ok": 1, "failed": 0, "skipped": 0, "total": 2})


def test_tui_renders_run_and_navigates(repo):
    _record_run(repo)

    async def main():
        app = StatusApp(repo)
        async with app.run_test() as pilot:
            from textual.widgets import DataTable, RichLog, Static
            table = app.query_one(DataTable)
            assert table.row_count == 2
            header = str(app.query_one("#header", Static).content)
            assert "ui1-1" in header and "COMPLETED" in header
            # row 0 (task a) selected: its log tail is shown
            log_lines = [str(s) for s in app.query_one(RichLog).lines]
            assert any("hello from a" in s for s in log_lines)
            # navigate to b: no log yet
            await pilot.press("j")
            log_lines = [str(s) for s in app.query_one(RichLog).lines]
            assert any("not started" in s for s in log_lines)
            await pilot.press("q")

    asyncio.run(main())


def test_tui_empty_repo_message(repo):
    async def main():
        app = StatusApp(repo)
        async with app.run_test() as pilot:
            from textual.widgets import Static
            assert "no coordination runs" in str(app.query_one("#header", Static).content)
            await pilot.press("q")

    asyncio.run(main())
