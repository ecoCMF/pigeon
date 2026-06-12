"""MCP server: expose the repo's pigeon contract to any MCP client.

``pigeon mcp`` serves the repo's context layer over the Model Context
Protocol (stdio transport), so CLI agents — Claude Code, Codex, Gemini CLI,
opencode — call ``retrieve`` / ``handoff_write`` / ``coordinate_run`` as
native tools instead of shelling out. Register with e.g.::

    claude mcp add pigeon -- pigeon mcp --root /path/to/repo

Design rules:

* every tool is a thin wrapper over an importable ``*_impl`` function that
  takes a :class:`~pigeon.config.Config` — the implementations are plain,
  testable Python with no MCP dependency;
* the ``mcp`` SDK is an optional extra (``pip install pigeon[mcp]``) and is
  imported lazily so the rest of pigeon never needs it;
* stdio transport owns ``stdout``: anything the wrapped commands print
  (coordinate's live streaming in particular) is redirected to ``stderr`` so
  it cannot corrupt the JSON-RPC stream;
* config is re-loaded per call, so edits to ``.pigeon/config.yaml`` take
  effect without restarting the server.

Tool results are plain JSON-serializable dicts; handoffs and retrieval go
through the exact same validation and token-accounting paths as the CLI, so
``pigeon metrics`` stays truthful regardless of which door the work came in.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config
from . import context, coordinate, manifest, retrieval, tokens
from . import distill as distill_mod
from . import pack as pack_mod
from . import graph as graph_mod
from . import handoff as ho


# ------------------------------------------------------------ implementations
def retrieve_impl(config: Config, query: str, top_k: int | None = None,
                  scope: str = "all", since: str | None = None) -> dict[str, Any]:
    """Hybrid ripgrep + BM25 retrieval; returns ranked, bounded slices."""
    results = retrieval.query(query, config, top_k=top_k, scope=scope, since=since)
    ev = tokens.account_retrieval(config, query, results)
    return {
        "results": [r.as_dict() for r in results],
        "tokens": {k: ev[k] for k in ("actual_tokens", "baseline_tokens", "saved_tokens")},
    }


def handoff_write_impl(
    config: Config,
    *,
    sid: str,
    from_agent: str,
    to_agent: str,
    doing: str,
    done: list[str] | None = None,
    artifacts: list[str] | None = None,
    decisions: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
    rag_query: str | None = None,
    rag_top_k: int | None = None,
    crew: dict[str, Any] | None = None,
    context_ref: str | None = None,
) -> dict[str, Any]:
    """Build, validate, append, and token-account a handoff."""
    rag: dict[str, Any] | None = None
    if rag_query:
        rag = {"query": rag_query}
        if rag_top_k:
            rag["top_k"] = rag_top_k
    handoff = ho.build_handoff(
        sid=sid, frm=from_agent, to=to_agent,
        done=list(done or []), doing=doing,
        artifacts=artifacts or None, decisions=decisions or None,
        rag=rag, constraints=constraints or None, crew=crew or None,
        context_ref=context_ref,
    )
    path = ho.write_handoff(handoff, config)  # validates on write
    rel = str(path.relative_to(config.root))
    ev = tokens.account_handoff(config, handoff, path=rel)
    return {
        "path": rel,
        "handoff": handoff,
        "tokens": {k: ev[k] for k in ("actual_tokens", "baseline_tokens", "saved_tokens")},
    }


def _repo_path(config: Config, path: str) -> "Path":
    """Resolve a repo-relative path, refusing escapes from the repo root."""
    target = (config.root / path).resolve()
    if not target.is_relative_to(config.root):
        raise ValueError(f"path escapes the repository root: {path!r}")
    return target


def handoff_read_impl(config: Config, path: str) -> dict[str, Any]:
    """Load a handoff, validating on receipt."""
    return {"path": path, "handoff": ho.load_handoff(_repo_path(config, path), config)}


def handoff_validate_impl(config: Config, path: str | None = None,
                          handoff_json: str | None = None) -> dict[str, Any]:
    """Validate a handoff file or an inline JSON string."""
    if (path is None) == (handoff_json is None):
        raise ValueError("provide exactly one of 'path' or 'handoff_json'")
    if path is not None:
        obj = json.loads(_repo_path(config, path).read_text(encoding="utf-8"))
    else:
        obj = json.loads(handoff_json)  # type: ignore[arg-type]
    try:
        ho.validate_handoff(obj, config)
    except ho.HandoffValidationError as exc:
        return {"valid": False, "errors": str(exc)}
    return {"valid": True}


def coordinate_run_impl(
    config: Config,
    tasks_file: str,
    *,
    parallel_limit: int | None = None,
    log_dir: str | None = None,
    skip_permissions: bool = False,
    dry_run: bool = False,
    telemetry: bool = False,
    budget_tokens: int | None = None,
    budget_usd: float | None = None,
) -> dict[str, Any]:
    """Run ``pigeon coordinate`` and return its run manifest.

    Live output is redirected to stderr (stdio MCP owns stdout); the caller
    gets the structured manifest, and can tail the per-task logs it points to.
    """
    before = {r["run_id"] for r in coordinate.list_runs(config)}
    with contextlib.redirect_stdout(sys.stderr):
        exit_code = coordinate.run_coordinate(
            (config.root / tasks_file).resolve(),
            config,
            parallel_limit=parallel_limit,
            log_dir=(config.root / log_dir).resolve() if log_dir else None,
            skip_permissions=skip_permissions,
            dry_run=dry_run,
            telemetry=telemetry,
            budget_tokens=budget_tokens,
            budget_usd=budget_usd,
        )
    new = [r for r in coordinate.list_runs(config) if r["run_id"] not in before]
    return {"exit_code": exit_code, "run": new[-1] if new else None}


def coordinate_plan_impl(config: Config, tasks_file: str) -> dict[str, Any]:
    """Read-only preview of a tasks file: waves, badges, preflight verdict."""
    spec = coordinate.load_tasks((config.root / tasks_file).resolve())
    return coordinate.plan(config, spec)


def coordinate_status_impl(config: Config, sid: str | None = None,
                           latest: bool = True) -> dict[str, Any]:
    """Run manifests — the latest one (default) or the full history."""
    runs = coordinate.list_runs(config, sid=sid)
    if latest:
        return {"run": runs[-1] if runs else None}
    return {"runs": runs}


def metrics_summary_impl(config: Config) -> dict[str, Any]:
    """Token-accounting totals: pigeon cost vs the naive baseline."""
    summary = tokens.summarize(config)
    summary["exact"] = tokens.using_tiktoken(config.tokens_cfg.get("encoding", "cl100k_base"))
    return summary


def repo_manifest_impl(config: Config) -> dict[str, Any]:
    """The generated repo manifest (modules, interfaces, entry points)."""
    if not config.manifest.is_file():
        raise FileNotFoundError(f"manifest not found: run `pigeon refresh` ({config.manifest})")
    return json.loads(config.manifest.read_text(encoding="utf-8"))


def pack_impl(config: Config, task: str, max_tokens: int = 4000,
              top_k: int = 5, since: str | None = None) -> dict[str, Any]:
    """Assemble a bounded pre-task context bundle."""
    return pack_mod.pack(config, task, max_tokens=max_tokens,
                         top_k=top_k, since=since)


def distill_impl(config: Config, sid: str | None = None) -> dict[str, Any]:
    """Consolidate handoffs + run manifests into committed memory files."""
    if sid is not None:
        return {"results": [distill_mod.distill_session(config, sid)]}
    return {"results": distill_mod.distill_all(config)}


def graph_query_impl(config: Config, query: str | None = None,
                     hops: int = 1) -> dict[str, Any]:
    """Stats (no query) or a BFS neighborhood of the derived entity graph."""
    if not query:
        return graph_mod.stats(config)
    return graph_mod.neighborhood(config, query, hops=hops)


def refresh_impl(config: Config) -> dict[str, Any]:
    """Rebuild manifest, pointer files, and projected runtime skill files."""
    from . import skills as skills_mod
    path = manifest.write_manifest(config)
    synced = [str(p.relative_to(config.root)) for p in context.sync_context(config)]
    projected = skills_mod.project_skills(config)
    return {"manifest": str(path.relative_to(config.root)), "synced": synced,
            "skills": projected}


# --------------------------------------------------------------- MCP wiring
def _require_mcp():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without extra
        raise SystemExit(
            "the MCP server needs the optional 'mcp' dependency: "
            "pip install 'pigeon[mcp]'"
        ) from exc
    return FastMCP


def build_server(root: Path | str | None = None):
    """Create the FastMCP server bound to a repo root (config loaded per call)."""
    FastMCP = _require_mcp()
    repo_root = load_config(root).root  # discover once; fail fast if unusable
    server = FastMCP(
        "pigeon",
        instructions=(
            "Context layer for this repository. Use retrieve instead of reading "
            "whole files, handoff_write to pass work between agents (pointers, "
            "not payloads), coordinate_run to fan tasks out to agent CLIs, and "
            "coordinate_status / metrics_summary to observe runs and token cost."
        ),
    )

    def cfg() -> Config:
        return load_config(repo_root)

    @server.tool()
    def retrieve(query: str, top_k: int | None = None,
                 scope: str = "all", since: str | None = None) -> dict:
        """Hybrid lexical+BM25 search. scope: 'code' (the repo), 'history'
        (handoffs + run manifests), 'memory' (distilled sessions/decisions),
        or 'all'. since: ISO date — only files modified at/after it. Returns
        ranked slices plus token savings vs whole files."""
        return retrieve_impl(cfg(), query, top_k=top_k, scope=scope, since=since)

    @server.tool()
    def handoff_write(
        sid: str, from_agent: str, to_agent: str, doing: str,
        done: list[str] | None = None,
        artifacts: list[str] | None = None,
        decisions: dict | None = None,
        constraints: dict | None = None,
        rag_query: str | None = None,
        rag_top_k: int | None = None,
        crew: dict | None = None,
        context_ref: str | None = None,
    ) -> dict:
        """Append a schema-validated handoff (sparse deltas + pointers, never
        payloads) to .pigeon/handoffs/. crew = deterministic staffing the
        receiver must dispatch ({skills: [...], subagents: [{role, skill,
        doing, verdict}]}). Returns its path and token cost."""
        return handoff_write_impl(
            cfg(), sid=sid, from_agent=from_agent, to_agent=to_agent, doing=doing,
            done=done, artifacts=artifacts, decisions=decisions,
            constraints=constraints, rag_query=rag_query, rag_top_k=rag_top_k,
            crew=crew, context_ref=context_ref,
        )

    @server.tool()
    def handoff_read(path: str) -> dict:
        """Read a handoff file (repo-relative path), validating on receipt."""
        return handoff_read_impl(cfg(), path)

    @server.tool()
    def handoff_validate(path: str | None = None, handoff_json: str | None = None) -> dict:
        """Validate a handoff: pass a repo-relative path OR an inline JSON string."""
        return handoff_validate_impl(cfg(), path=path, handoff_json=handoff_json)

    @server.tool()
    def coordinate_run(
        tasks_file: str,
        parallel_limit: int | None = None,
        log_dir: str | None = None,
        skip_permissions: bool = False,
        dry_run: bool = False,
        telemetry: bool = False,
        budget_tokens: int | None = None,
        budget_usd: float | None = None,
    ) -> dict:
        """Fan a tasks file (YAML/JSON, repo-relative) out to agent CLIs in
        parallel. Safety preflight may refuse (exit_code 2). Blocks until all
        tasks finish and returns the structured run manifest. telemetry=true
        records each child's measured token usage; budget_tokens/budget_usd
        set hard measured-spend ceilings (no new tasks once crossed)."""
        return coordinate_run_impl(
            cfg(), tasks_file, parallel_limit=parallel_limit, log_dir=log_dir,
            skip_permissions=skip_permissions, dry_run=dry_run,
            telemetry=telemetry, budget_tokens=budget_tokens,
            budget_usd=budget_usd,
        )

    @server.tool()
    def coordinate_plan(tasks_file: str) -> dict:
        """Preview a tasks file BEFORE dispatching: execution waves (the
        run's shape), per-task badges (runner/isolation/pack/needs), the
        longest dependency chain, and the safety preflight verdict. Writes
        nothing — call this first, then coordinate_run."""
        return coordinate_plan_impl(cfg(), tasks_file)

    @server.tool()
    def coordinate_status(sid: str | None = None, latest: bool = True) -> dict:
        """Run manifest(s): per-task status (queued/running/completed/exited/
        failed), exit codes, durations, log + handoff pointers."""
        return coordinate_status_impl(cfg(), sid=sid, latest=latest)

    @server.tool()
    def metrics_summary() -> dict:
        """Token-accounting totals for this repo's handoffs and retrievals."""
        return metrics_summary_impl(cfg())

    @server.tool()
    def repo_manifest() -> dict:
        """The generated repo manifest: modules, public interfaces, entry
        points, decisions, owners. Use this instead of dumping the file tree."""
        return repo_manifest_impl(cfg())

    @server.tool()
    def pack(task: str, max_tokens: int = 4000, top_k: int = 5,
             since: str | None = None) -> dict:
        """Assemble ONE bounded context bundle for a task before starting
        work: distilled memory + repo map + code slices + recent history,
        deduplicated and cut to max_tokens. Returns the bundle path — read
        it first instead of issuing many searches."""
        return pack_impl(cfg(), task, max_tokens=max_tokens,
                         top_k=top_k, since=since)

    @server.tool()
    def distill(sid: str | None = None) -> dict:
        """Consolidate a session's handoffs + runs (default: all sessions)
        into durable memory under .pigeon/memory/ — session records and a
        cross-session decision ledger, deterministic and committed."""
        return distill_impl(cfg(), sid=sid)

    @server.tool()
    def graph_query(query: str | None = None, hops: int = 1) -> dict:
        """Multi-hop query over the derived entity graph (sessions, decisions,
        artifacts, agents, memory pages, [[wiki-links]]). No query => stats.
        Edges carry provenance pointers back to the source handoffs."""
        return graph_query_impl(cfg(), query=query, hops=hops)

    @server.tool()
    def refresh() -> dict:
        """Rebuild manifest.json and regenerate the CLAUDE.md/GEMINI.md pointers."""
        return refresh_impl(cfg())

    return server


def serve(root: Path | str | None = None) -> int:
    """Run the stdio MCP server until the client disconnects."""
    build_server(root).run()
    return 0
