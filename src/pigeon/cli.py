"""``pigeon`` command-line entry point.

Commands:
  refresh     rebuild manifest.json and regenerate CLAUDE.md / GEMINI.md
  handoff     build / validate / emit a handoff (pointers, not payloads)
  retrieve    hybrid ripgrep + BM25 query over the repo + manifest
  metrics     token-accounting report (delta cost vs naive baseline)
  demo        whole-MVP acceptance: 3-agent chain + retrieval, with totals
  plan        preview a tasks file: waves, badges, preflight — writes nothing
  coordinate  fan a tasks file out to agent CLIs (claude/agy/opencode) in parallel
  status      glanceable view of the latest run; --watch follows it live
  runs        list recorded coordination run manifests
  cleanup     reconcile after crashes; prune old run history
  distill     consolidate handoffs + runs into committed memory files
  pack        assemble a bounded pre-task context bundle
  graph       query the derived entity graph (multi-hop, file-based)
  mcp         serve the contract over the Model Context Protocol (stdio)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config
from . import context, coordinate, distill, init as init_mod, manifest, retrieval, tokens
from . import pack as pack_mod
from . import graph as graph_mod
from . import handoff as ho


def _cfg(args: argparse.Namespace) -> Config:
    return load_config(args.root)


def _parse_kv(pairs: list[str] | None) -> dict[str, Any]:
    """Parse ``key=value`` pairs; values are JSON-decoded when possible."""
    out: dict[str, Any] = {}
    for item in pairs or []:
        if "=" not in item:
            raise SystemExit(f"expected key=value, got {item!r}")
        key, val = item.split("=", 1)
        try:
            out[key] = json.loads(val)
        except json.JSONDecodeError:
            out[key] = val
    return out


# ------------------------------------------------------------------------- init
def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.path or args.root or os.getcwd()).resolve()
    actions = init_mod.init_repo(
        target, force=args.force, with_hook=args.with_hook, project_name=args.name
    )
    print(f"pigeon init -> {target}")
    for line in actions:
        print(f"  {line}")
    # Initial refresh now that AGENTS.md + config exist.
    cfg = load_config(target)
    m = manifest.write_manifest(cfg)
    n_modules = len(json.loads(m.read_text(encoding="utf-8")).get("modules", []))
    context.sync_context(cfg)
    print(f"  refreshed manifest ({n_modules} modules) + CLAUDE.md/GEMINI.md")
    print(
        "\nNext:\n"
        "  1. Edit AGENTS.md (fill the TODOs) and .pigeon/config.yaml (include globs).\n"
        "  2. Run `pigeon refresh` to regenerate the manifest + pointer files.\n"
        "  3. Try `pigeon retrieve \"<query>\"` and `pigeon demo`.\n"
        "  (Lexical layer needs ripgrep on PATH or AGENTCTX_RG set.)"
    )
    return 0


# --------------------------------------------------------------------- refresh
def cmd_refresh(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    note = init_mod.upgrade_schema(cfg)
    if note:
        print(f"schema:   {note}")
    path = manifest.write_manifest(cfg)
    print(f"manifest: {path.relative_to(cfg.root)}")
    for p in context.sync_context(cfg):
        print(f"synced:   {p.relative_to(cfg.root)}")
    from . import skills as skills_mod
    proj = skills_mod.project_skills(cfg)
    for rel in proj["written"]:
        print(f"skill:    {rel}")
    for note in proj["skipped"]:
        print(f"skipped:  {note}")
    return 0


# --------------------------------------------------------------------- handoff
def cmd_handoff(args: argparse.Namespace) -> int:
    cfg = _cfg(args)

    if args.validate:
        obj = json.loads(Path(args.validate).read_text(encoding="utf-8"))
        try:
            ho.validate_handoff(obj, cfg)
        except ho.HandoffValidationError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"valid: {args.validate}")
        return 0

    if args.json_in:
        raw = sys.stdin.read() if args.json_in == "-" else Path(args.json_in).read_text(encoding="utf-8")
        handoff = json.loads(raw)
    else:
        missing = [n for n in ("sid", "frm", "to", "doing") if not getattr(args, n)]
        if missing:
            print(
                "handoff: provide --json-in, or all of --sid/--from/--to/--doing",
                file=sys.stderr,
            )
            return 2
        rag = None
        if args.rag_query:
            rag = {"query": args.rag_query}
            if args.rag_top_k:
                rag["top_k"] = args.rag_top_k
        handoff = ho.build_handoff(
            sid=args.sid,
            frm=args.frm,
            to=args.to,
            done=args.done or [],
            doing=args.doing,
            artifacts=args.artifact or None,
            decisions=_parse_kv(args.decision) or None,
            rag=rag,
            constraints=_parse_kv(args.constraint) or None,
            context_ref=args.context_ref,
        )

    try:
        ho.validate_handoff(handoff, cfg)
    except ho.HandoffValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.no_write:
        sys.stdout.write(ho.serialize_handoff(handoff))
        ev = tokens.account_handoff(cfg, handoff, record_event=False)
    else:
        path = ho.write_handoff(handoff, cfg, validate=False)
        rel = path.relative_to(cfg.root)
        print(f"wrote: {rel}")
        ev = tokens.account_handoff(cfg, handoff, path=str(rel))
    print(
        f"tokens: actual={ev['actual_tokens']} baseline={ev['baseline_tokens']} "
        f"saved={ev['saved_tokens']}"
    )
    return 0


# -------------------------------------------------------------------- retrieve
def cmd_retrieve(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    try:
        results = retrieval.query(args.query, cfg, top_k=args.top_k,
                                  scope=args.scope, since=args.since)
    except ValueError as exc:
        print(f"retrieve: {exc}", file=sys.stderr)
        return 2
    ev = tokens.account_retrieval(cfg, args.query, results)
    if args.json:
        print(json.dumps([r.as_dict() for r in results], indent=2, ensure_ascii=False))
        return 0
    if not results:
        print("no results")
    for r in results:
        print(f"[{r.score:.3f}] {r.source}:{r.start_line}-{r.end_line}  (lexical_hits={r.lexical_hits})")
        preview = "\n".join(r.snippet.splitlines()[:6])
        print("    " + preview.replace("\n", "\n    "))
    print(
        f"\ntokens: returned={ev['actual_tokens']} vs whole-file "
        f"baseline={ev['baseline_tokens']} (saved {ev['saved_tokens']})"
    )
    return 0


# ---------------------------------------------------------------------- metrics
def cmd_metrics(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if args.prune is not None:
        before, after = tokens.prune_metrics(cfg, keep=args.prune)
        print(f"metrics: pruned {before - after} event(s), kept {after}")
        return 0
    summary = tokens.summarize(cfg)
    exact = tokens.using_tiktoken(cfg.tokens_cfg.get("encoding", "cl100k_base"))
    print(tokens.format_summary(summary, exact=exact))
    return 0


# ------------------------------------------------------------------------- demo
def _aggregate(events: list[dict[str, Any]]) -> dict[str, int]:
    total = {"actual_tokens": 0, "baseline_tokens": 0, "saved_tokens": 0}
    for ev in events:
        for k in total:
            total[k] += int(ev.get(k, 0))
    return total


def cmd_demo(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    print("pigeon demo — refreshing context + manifest …")
    manifest.write_manifest(cfg)
    context.sync_context(cfg)

    # A 3-agent chain over this repo's real files. Pointers only; no payloads.
    sid = "demo"
    chain: list[dict[str, Any]] = [
        dict(
            frm="Planner", to="Executor",
            done=["analyze", "design"], doing="implement",
            artifacts=["repo://AGENTS.md"],
            decisions={"layering": "3 decoupled layers"},
            rag={"query": "validate handoff against schema", "top_k": 3},
            context_ref="manifest@HEAD",
        ),
        dict(
            frm="Executor", to="Tester",
            done=["analyze", "design", "implement"], doing="test",
            artifacts=["repo://src/pigeon/handoff.py"],
            rag={"query": "deterministic manifest generator", "top_k": 3},
            context_ref="manifest@HEAD",
        ),
        dict(
            frm="Tester", to="Planner",
            done=["analyze", "design", "implement", "test"], doing="report",
            artifacts=["repo://src/pigeon/retrieval.py"],
            rag={"query": "ripgrep BM25 hybrid ranking", "top_k": 3},
            context_ref="manifest@HEAD",
        ),
    ]

    events: list[dict[str, Any]] = []
    for i, step in enumerate(chain, 1):
        rag = step.pop("rag")
        handoff = ho.build_handoff(sid=sid, **step)
        path = ho.write_handoff(handoff, cfg)            # validates on write
        ho.load_handoff(path, cfg)                       # validates on receipt
        h_ev = tokens.account_handoff(cfg, handoff, path=str(path.relative_to(cfg.root)))
        results = retrieval.query(rag["query"], cfg, top_k=rag["top_k"])
        r_ev = tokens.account_retrieval(cfg, rag["query"], results)
        events.extend([h_ev, r_ev])
        print(
            f"\nhop {i}: {step['frm']} -> {step['to']}  ({path.relative_to(cfg.root)})"
            f"\n  handoff   actual={h_ev['actual_tokens']:<5} baseline={h_ev['baseline_tokens']:<6}"
            f" saved={h_ev['saved_tokens']}"
            f"\n  retrieve  '{rag['query']}' -> {len(results)} slices,"
            f" actual={r_ev['actual_tokens']:<5} baseline={r_ev['baseline_tokens']:<6}"
            f" saved={r_ev['saved_tokens']}"
        )

    total = _aggregate(events)
    base = total["baseline_tokens"]
    pct = round(100.0 * total["saved_tokens"] / base, 1) if base else 0.0
    exact = tokens.using_tiktoken(cfg.tokens_cfg.get("encoding", "cl100k_base"))
    note = "exact (tiktoken)" if exact else "heuristic estimate"
    print(
        "\n" + "=" * 60
        + f"\nTOTAL over 3 hops  [{note}]"
        + f"\n  pigeon (deltas + pointers + slices): {total['actual_tokens']} tokens"
        + f"\n  naive baseline (prose + inlined + whole files): {base} tokens"
        + f"\n  reduction: {pct}%  ({total['saved_tokens']} tokens saved)"
        + "\n" + "=" * 60
    )
    return 0


# ----------------------------------------------------------------- coordinate
def cmd_coordinate(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    try:
        return coordinate.run_coordinate(
            Path(args.tasks_file),
            cfg,
            parallel_limit=args.parallel_limit,
            log_dir=Path(args.log_dir) if args.log_dir else None,
            skip_permissions=args.skip_permissions,
            dry_run=args.dry_run,
            telemetry=args.telemetry,
            budget_tokens=args.budget_tokens,
            budget_usd=args.budget_usd,
        )
    except ValueError as exc:
        print(f"tasks file error: {exc}", file=sys.stderr)
        return 2


def cmd_cleanup(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    report = coordinate.cleanup(cfg, keep_runs=args.keep_runs)
    for wt in report["removed_worktrees"]:
        print(f"removed orphan worktree: {wt}")
    for run_id in report["pruned_runs"]:
        print(f"pruned run: {run_id}")
    if not report["removed_worktrees"] and not report["pruned_runs"]:
        print("nothing to clean")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    import time as time_mod

    def latest() -> dict | None:
        runs = coordinate.list_runs(cfg, sid=args.sid)
        return runs[-1] if runs else None

    run = latest()
    if run is None:
        print("no coordination runs recorded"
              + (f" for sid {args.sid!r}" if args.sid else ""))
        return 0
    if args.tui:
        from . import tui  # lazy: needs the optional [tui] extra
        return tui.run(cfg, sid=args.sid, interval=args.interval)
    if not args.watch:
        print(coordinate.render_status(run, cfg))
        return 0
    try:
        tick = 0
        run_path = cfg.coordinate_runs_dir / f"{run['run_id']}.json"
        while True:
            # cheap path: re-read just this run; full rescan every 5th tick
            if tick % 5 == 0:
                fresh = latest()
                if fresh and fresh["run_id"] != run["run_id"]:
                    run = fresh
                    run_path = cfg.coordinate_runs_dir / f"{run['run_id']}.json"
            try:
                run = json.loads(run_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass  # mid-rename window; keep the last good state
            tick += 1
            if sys.stdout.isatty():
                sys.stdout.write("\x1b[2J\x1b[H")
            else:
                print("\n" + "-" * 60)  # CI logs / redirected output
            print(coordinate.render_status(run, cfg))
            rel = cfg.coordinate_runs_dir / f"{run['run_id']}.json"
            print(f"\nwatching {rel.relative_to(cfg.root)} — Ctrl-C to detach"
                  " (the run continues)")
            if run.get("status") != "running":
                return 0
            time_mod.sleep(max(0.5, args.interval))
    except KeyboardInterrupt:
        print()
        return 0


def cmd_runs(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    runs = coordinate.list_runs(cfg, sid=args.sid)
    if args.timeline or args.by_agent or args.critical_path:
        if not runs:
            print("no coordination runs recorded")
            return 0
        run = runs[-1]
        if args.timeline:
            print(coordinate.timeline_report(cfg, run))
        if args.by_agent:
            print(coordinate.by_agent_report(run))
        if args.critical_path:
            print(coordinate.critical_path_report(run))
        return 0
    if args.json:
        print(json.dumps(runs, indent=2, ensure_ascii=False))
        return 0
    if not runs:
        print("no coordination runs recorded")
        return 0
    for run in runs:
        s = run.get("summary") or {}
        counts = f"{s.get('ok', '?')}/{s.get('total', '?')} ok" if s else "-"
        print(f"{run['run_id']:<20} {run.get('status', '?'):<10} "
              f"started={run.get('started_at', '?')}  {counts}")
        for tid, t in (run.get("tasks") or {}).items():
            extras = []
            if "exit_code" in t:
                extras.append(f"exit={t['exit_code']}")
            if "duration_s" in t:
                extras.append(f"{t['duration_s']}s")
            if t.get("return_handoff"):
                extras.append(f"returned={t['return_handoff']}")
            print(f"  [{tid}] {t.get('status', '?')}"
                  + (("  " + " ".join(extras)) if extras else ""))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    try:
        spec = coordinate.load_tasks(
            Path(args.tasks_file),
            default_runner=cfg.coordinate_cfg.get("default_runner"),
            model_pools=cfg.coordinate_cfg.get("model_pools"))
    except ValueError as exc:
        print(f"tasks file error: {exc}", file=sys.stderr)
        return 2
    p = coordinate.plan(cfg, spec)
    if args.json:
        print(json.dumps(p, indent=2, ensure_ascii=False))
    else:
        print(coordinate.format_plan(p, spec["tasks"]))
    return 2 if p["preflight_errors"] else 0


def cmd_pack(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    try:
        res = pack_mod.pack(cfg, args.task, max_tokens=args.max_tokens,
                            top_k=args.top_k, since=args.since)
    except ValueError as exc:
        print(f"pack: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    counts = ", ".join(f"{k}={v}" for k, v in res["sections"].items() if v)
    print(f"bundle: {res['path']}  ({counts}; dropped={res['dropped']})")
    print(f"tokens: bundle={res['tokens']['actual_tokens']} vs whole-files "
          f"baseline={res['tokens']['baseline_tokens']} "
          f"(saved {res['tokens']['saved_tokens']})")
    return 0


def cmd_distill(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    try:
        results = (distill.distill_all(cfg) if args.sid is None
                   else [distill.distill_session(cfg, args.sid)])
    except ValueError as exc:
        print(f"distill: {exc}", file=sys.stderr)
        return 2
    if not results:
        print("nothing to distill (no handoffs, no recorded runs)")
        return 0
    for res in results:
        print(f"{res['sid']}: {res['session']}  "
              f"({res['handoffs']} handoffs, {res['runs']} runs; "
              f"memory={res['tokens']['actual_tokens']} tokens vs "
              f"raw={res['tokens']['baseline_tokens']})")
    print(f"decision ledger: {results[-1]['decisions']}")
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if args.rebuild:
        graph_mod.build_graph(cfg)
    if not args.node:
        s = graph_mod.stats(cfg)
        print(f"graph: {s['nodes']} nodes, {s['edges']} edges  ({s['path']})")
        for t, n in sorted(s["by_type"].items()):
            print(f"  {t:<10} {n}")
        return 0
    sub_g = graph_mod.neighborhood(cfg, args.node, hops=args.hops)
    if args.json:
        print(json.dumps(sub_g, indent=2, ensure_ascii=False))
        return 0
    if not sub_g["matches"]:
        print(f"no node matches {args.node!r} (try `pigeon graph` for stats)")
        return 1
    print("matched: " + ", ".join(sub_g["matches"]))
    for e in sub_g["edges"]:
        val = f" = {json.dumps(e['value'], ensure_ascii=False)}" if "value" in e else ""
        print(f"  {e['src']} -{e['rel']}-> {e['dst']}{val}  (via {e['provenance']})")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from . import mcp_server  # lazy: needs the optional [mcp] extra
    return mcp_server.serve(args.root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pigeon", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    from . import __version__
    parser.add_argument("--version", action="version", version=f"pigeon {__version__}")
    parser.add_argument("--root", default=None,
                        help="Repo root or any path inside it (default: discover from cwd).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Scaffold pigeon into a repo (idempotent).")
    p.add_argument("path", nargs="?", default=None,
                   help="Target repo dir (default: --root or cwd).")
    p.add_argument("--force", action="store_true", help="Overwrite existing pigeon files.")
    p.add_argument("--with-hook", action="store_true", help="Install a refresh pre-commit hook.")
    p.add_argument("--name", default=None, help="Project name for the AGENTS.md template.")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("refresh", help="Rebuild manifest + regenerate CLAUDE.md/GEMINI.md.")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("handoff", help="Build / validate / emit a handoff.")
    p.add_argument("--validate", metavar="FILE", help="Validate an existing handoff file and exit.")
    p.add_argument("--json-in", metavar="FILE", help="Read a complete handoff JSON ('-' for stdin).")
    p.add_argument("--sid")
    p.add_argument("--from", dest="frm")
    p.add_argument("--to")
    p.add_argument("--done", action="append", help="Completed step (repeatable).")
    p.add_argument("--doing", help="The single next step for the receiver.")
    p.add_argument("--artifact", action="append", help="Pointer (repeatable).")
    p.add_argument("--decision", action="append", help="key=value (repeatable).")
    p.add_argument("--constraint", action="append", help="key=value (repeatable).")
    p.add_argument("--rag-query")
    p.add_argument("--rag-top-k", type=int)
    p.add_argument("--context-ref")
    p.add_argument("--no-write", action="store_true", help="Print to stdout instead of appending.")
    p.set_defaults(func=cmd_handoff)

    p = sub.add_parser("retrieve", help="Hybrid ripgrep + BM25 query.")
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=None)
    p.add_argument("--json", action="store_true", help="Emit results as JSON.")
    p.add_argument("--scope", choices=retrieval.SCOPES, default="all",
                   help="Corpus: code, history (handoffs + runs), memory "
                        "(distilled), or all (default).")
    p.add_argument("--since", default=None, metavar="ISO",
                   help="Only files modified at/after this ISO date "
                        "(e.g. 2026-06-01).")
    p.set_defaults(func=cmd_retrieve)

    p = sub.add_parser("metrics", help="Token-accounting report.")
    p.add_argument("--prune", type=int, default=None, metavar="N",
                   help="Keep only the newest N events in metrics.jsonl.")
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("demo", help="Whole-MVP acceptance demo.")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser(
        "coordinate",
        help="Fan a tasks file out to agent CLIs (claude/agy/opencode) in parallel.",
    )
    p.add_argument("tasks_file", help="Tasks definition, YAML or JSON.")
    p.add_argument("--parallel-limit", type=int, default=None,
                   help="Max concurrent agents (default: coordinate.parallel_limit).")
    p.add_argument("--log-dir", default=None,
                   help="Per-task log directory (default: coordinate.log_dir).")
    p.add_argument("--skip-permissions", action="store_true",
                   help="Append each runner's unattended flag (e.g. "
                        "--dangerously-skip-permissions). Off by default; the "
                        "safety preflight must still pass.")
    p.add_argument("--dry-run", action="store_true",
                   help="Write handoffs and print commands without spawning agents.")
    p.add_argument("--telemetry", action="store_true",
                   help="Append each runner's JSON-output flags and record the "
                        "child's measured token usage into metrics.jsonl.")
    p.add_argument("--budget-tokens", type=int, default=None,
                   help="Hard ceiling on measured child tokens; once crossed, "
                        "no further tasks launch.")
    p.add_argument("--budget-usd", type=float, default=None,
                   help="Hard ceiling on measured child cost in USD.")
    p.set_defaults(func=cmd_coordinate)

    p = sub.add_parser(
        "plan",
        help="Preview a tasks file before dispatch: waves, badges, critical "
             "chain, preflight verdict. Writes nothing.",
    )
    p.add_argument("tasks_file", help="Tasks definition, YAML or JSON.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser(
        "pack",
        help="Assemble a bounded pre-task context bundle from memory, "
             "manifest, code, and history.",
    )
    p.add_argument("task", help="What the agent is about to do.")
    p.add_argument("--max-tokens", type=int, default=4000)
    p.add_argument("--top-k", type=int, default=5,
                   help="Candidates per layer before the budget cut.")
    p.add_argument("--since", default=None, metavar="ISO",
                   help="Restrict the history layer to events after this date.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser(
        "distill",
        help="Consolidate handoffs + runs into committed memory "
             "(.pigeon/memory/).",
    )
    p.add_argument("sid", nargs="?", default=None,
                   help="Session to distill (default: every known session).")
    p.set_defaults(func=cmd_distill)

    p = sub.add_parser("graph", help="Query the derived entity graph (BFS over memory + handoffs).")
    p.add_argument("node", nargs="?", default=None,
                   help="Node query (matched against ids/labels); omit for stats.")
    p.add_argument("--hops", type=int, default=1)
    p.add_argument("--rebuild", action="store_true", help="Regenerate graph.json first.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser(
        "cleanup",
        help="Reconcile after crashes: remove orphan worktrees (branches kept); "
             "--keep-runs N prunes old run manifests.",
    )
    p.add_argument("--keep-runs", type=int, default=None, metavar="N")
    p.set_defaults(func=cmd_cleanup)

    p = sub.add_parser("status", help="Glanceable view of the latest run (live or finished).")
    p.add_argument("sid", nargs="?", default=None, help="Filter by session id.")
    p.add_argument("--watch", action="store_true",
                   help="Redraw until the run finishes (reads the manifest file; "
                        "no server involved).")
    p.add_argument("--tui", action="store_true",
                   help="Full-screen dashboard (task table + log pane). Needs "
                        "`pip install pigeon[tui]`.")
    p.add_argument("--interval", type=float, default=2.0)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("runs", help="List recorded coordination run manifests.")
    p.add_argument("sid", nargs="?", default=None, help="Filter by session id.")
    p.add_argument("--json", action="store_true", help="Emit manifests as JSON.")
    p.add_argument("--timeline", action="store_true",
                   help="Chronological event stream of the latest matching run.")
    p.add_argument("--by-agent", action="store_true",
                   help="Per-runner aggregation: load, failures, measured spend.")
    p.add_argument("--critical-path", action="store_true",
                   help="Duration-weighted longest dependency chain.")
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser(
        "mcp",
        help="Serve retrieve/handoff/coordinate/metrics over MCP (stdio). "
             "Needs `pip install pigeon[mcp]`.",
    )
    p.set_defaults(func=cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        # A missing canonical file (or pointer target) is an operator error,
        # not a bug: report it without a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
