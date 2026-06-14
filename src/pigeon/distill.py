"""Consolidate episodic logs into durable, retrievable memory.

``pigeon distill`` turns a session's raw event trail — handoffs in
``.pigeon/handoffs/`` and run manifests in ``coordinate/runs/`` — into two
kinds of plain-Markdown memory under ``.pigeon/memory/``:

* ``sessions/<sid>.md`` — a compact session record: outcomes, task results,
  hand-backs, artifacts touched, and measured spend;
* ``decisions.md`` — a ledger of every ``decisions`` entry carried in any
  handoff, with provenance, regenerated from the source events each time.

Both are **derived data, deterministically generated** (no LLM in the loop):
re-running distill always produces the same files from the same events. The
memory directory is meant to be *committed* — handoffs and runs are
gitignored runtime artifacts, so the distilled record is the part of the
episodic log that survives a clone. Markdown keeps it inside the retrieval
index, so past sessions are queryable like any other context.

Distillation is token-accounted (kind ``distill``): actual = the memory
files, baseline = the raw events they replace in an agent's context.
"""

from __future__ import annotations

import json
from typing import Any

from .config import Config
from . import coordinate, tokens
from .sessions import known_sids, session_handoffs  # re-exported: public API


def _render_session(sid: str, runs: list[dict[str, Any]],
                    handoffs: list[tuple[str, dict[str, Any]]]) -> str:
    lines = [f"# Session {sid} — distilled record", ""]

    if runs:
        latest = runs[-1]
        totals = {"ok": 0, "failed": 0, "skipped": 0, "total": 0}
        spent_tokens, spent_usd = 0, 0.0
        for run in runs:
            for key in totals:
                totals[key] += int((run.get("summary") or {}).get(key, 0))
            budget = run.get("budget") or {}
            spent_tokens += int(budget.get("spent_tokens", 0))
            spent_usd += float(budget.get("spent_usd", 0.0))
        lines += [
            "## Outcome",
            f"- runs: {len(runs)} (latest {latest['run_id']}: "
            f"{latest.get('status', '?')}, finished {latest.get('finished_at')})",
            f"- tasks: {totals['ok']} ok / {totals['failed']} failed / "
            f"{totals['skipped']} skipped (of {totals['total']})",
        ]
        if spent_tokens or spent_usd:
            lines.append(f"- measured agent spend: {spent_tokens} tokens, "
                         f"${round(spent_usd, 4)}")
        lines += ["", f"## Tasks (latest run {latest['run_id']})"]
        for tid, t in sorted((latest.get("tasks") or {}).items()):
            bits = [t.get("status", "?")]
            if t.get("model"):
                bits.append(f"model {t['model']}")
            if "exit_code" in t:
                bits.append(f"exit {t['exit_code']}")
            if "duration_s" in t:
                bits.append(f"{t['duration_s']}s")
            iso = t.get("isolation") or {}
            if iso.get("branch"):
                bits.append(f"branch {iso['branch']}")
            if iso.get("diff"):
                bits.append(f"diff {iso['diff']}")
            if t.get("skipped_because"):
                bits.append(f"because: {', '.join(t['skipped_because'])}")
            # Append the task's intent so its keywords land in the retrieval
            # index — `pigeon retrieve "<what a task did>"` then hits this row.
            doing = (t.get("doing") or "").strip()
            tail = f" — {doing}" if doing else ""
            lines.append(f"- **{tid}** — {', '.join(bits)}{tail}")
        lines.append("")

        # Per-model rollup: the empirical "what worked" record for this run —
        # which model ran, how many it landed, what it cost. Committed + indexed,
        # so the next run can recall a model's track record (Reasoning Bank).
        model_stats: dict[str, dict[str, Any]] = {}
        for t in (latest.get("tasks") or {}).values():
            m = t.get("model")
            if not m:
                continue
            s = model_stats.setdefault(m, {"tasks": 0, "ok": 0, "tokens": 0})
            s["tasks"] += 1
            if t.get("status") in ("completed", "exited"):
                s["ok"] += 1
            s["tokens"] += int((t.get("telemetry") or {}).get("total_tokens") or 0)
        if model_stats:
            lines += ["## Models (latest run)"]
            for m in sorted(model_stats):
                s = model_stats[m]
                row = f"- **{m}** — {s['ok']}/{s['tasks']} ok"
                if s["tokens"]:
                    row += f", {s['tokens']} tokens"
                lines.append(row)
            lines.append("")

    decisions = [(key, val, rel) for rel, h in handoffs
                 for key, val in ((h.get("state") or {}).get("decisions") or {}).items()]
    if decisions:
        lines += ["## Decisions"]
        for key, val, rel in decisions:
            lines.append(f"- `{key}` = {json.dumps(val, ensure_ascii=False)}  ({rel})")
        lines.append("")

    handbacks = [(h["from"], (h.get("state") or {}).get("doing", ""), rel)
                 for rel, h in handoffs if h.get("to") == coordinate.COORDINATOR]
    if handbacks:
        lines += ["## Hand-backs to the Coordinator"]
        for frm, doing, rel in handbacks:
            lines.append(f"- from **{frm}**: next → {doing}  ({rel})")
        lines.append("")

    artifacts = sorted({a for _, h in handoffs
                        for a in (h.get("state") or {}).get("artifacts", [])})
    if artifacts:
        lines += ["## Artifacts referenced"] + [f"- {a}" for a in artifacts] + [""]

    lines += [f"_Sources: {len(handoffs)} handoffs, {len(runs)} runs. "
              "Generated by `pigeon distill`; edits will be overwritten._", ""]
    return "\n".join(lines)


def _render_decisions(config: Config) -> str:
    """The cross-session decision ledger, regenerated from every handoff."""
    lines = [
        "# Decision ledger",
        "",
        "Every `decisions` entry carried in a handoff, with provenance.",
        "Generated by `pigeon distill`; the handoffs are the source of truth.",
        "",
    ]
    by_key: dict[str, list[tuple[str, Any, str]]] = {}
    for sid in known_sids(config):
        for rel, h in session_handoffs(config, sid):
            for key, val in ((h.get("state") or {}).get("decisions") or {}).items():
                by_key.setdefault(key, []).append((sid, val, rel))
    for key in sorted(by_key):
        entries = by_key[key]
        current = entries[-1]
        lines.append(f"## {key}")
        lines.append(f"- current: {json.dumps(current[1], ensure_ascii=False)} "
                     f"(session {current[0]}, {current[2]})")
        for sid, val, rel in entries[:-1]:
            lines.append(f"- earlier: {json.dumps(val, ensure_ascii=False)} "
                         f"(session {sid}, {rel})")
        lines.append("")
    if not by_key:
        lines.append("_No decisions recorded yet._\n")
    return "\n".join(lines)


def _distill_one(config: Config, sid: str) -> dict[str, Any]:
    """One session's record + token event. Global ledgers are NOT touched here
    — ``_write_globals`` runs exactly once per distill call, keeping
    ``distill_all`` O(sessions) instead of O(sessions^2)."""
    runs = coordinate.list_runs(config, sid=sid)
    handoffs = session_handoffs(config, sid)
    if not runs and not handoffs:
        raise ValueError(f"nothing to distill for session {sid!r}: "
                         "no handoffs and no recorded runs")

    sessions_dir = config.memory_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    session_path = sessions_dir / f"{sid}.md"
    session_md = _render_session(sid, runs, handoffs)
    session_path.write_text(session_md, encoding="utf-8")

    encoding = config.tokens_cfg.get("encoding", "cl100k_base")
    baseline_text = "".join(
        json.dumps(h, ensure_ascii=False) for _, h in handoffs
    ) + "".join(json.dumps(r, ensure_ascii=False) for r in runs)
    actual = tokens.count_tokens(session_md, encoding)
    baseline = tokens.count_tokens(baseline_text, encoding)
    event = tokens.record(config, {
        "kind": "distill", "sid": sid,
        "actual_tokens": actual,
        "baseline_tokens": baseline,
        "saved_tokens": max(0, baseline - actual),
        "path": str(session_path.relative_to(config.root)),
    })
    return {
        "sid": sid,
        "session": str(session_path.relative_to(config.root)),
        "handoffs": len(handoffs),
        "runs": len(runs),
        "tokens": {k: event[k] for k in
                   ("actual_tokens", "baseline_tokens", "saved_tokens")},
    }


def _write_globals(config: Config) -> str:
    """Regenerate the cross-session ledgers (decisions.md + graph.json) once."""
    decisions_path = config.memory_dir / "decisions.md"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    decisions_path.write_text(_render_decisions(config), encoding="utf-8")
    from . import graph as graph_mod  # lazy: keeps coordinate->distill cheap
    graph_mod.build_graph(config)
    return str(decisions_path.relative_to(config.root))


def distill_session(config: Config, sid: str) -> dict[str, Any]:
    """Distill one session into memory files. Raises if there is nothing."""
    result = _distill_one(config, sid)
    result["decisions"] = _write_globals(config)
    return result


def distill_all(config: Config) -> list[dict[str, Any]]:
    """Distill every known session; global ledgers are rebuilt exactly once."""
    results = [_distill_one(config, sid) for sid in known_sids(config)]
    if results:
        decisions_rel = _write_globals(config)
        for result in results:
            result["decisions"] = decisions_rel
    return results
