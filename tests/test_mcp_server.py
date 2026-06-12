"""MCP server: tool implementations (no SDK needed) + FastMCP registration."""

from __future__ import annotations

import asyncio
import json

import pytest
import yaml

from pigeon import mcp_server as srv
from pigeon import handoff as ho


# ------------------------------------------------------------ implementations
def test_handoff_write_read_validate_roundtrip(repo):
    out = srv.handoff_write_impl(
        repo, sid="m1", from_agent="Planner", to_agent="Executor",
        doing="implement", done=["design"], artifacts=["repo://AGENTS.md"],
        decisions={"auth": "oauth2_pkce"}, rag_query="alpha", rag_top_k=2,
    )
    assert out["path"].endswith("m1-1.json")
    assert out["tokens"]["actual_tokens"] > 0
    assert out["handoff"]["rag"] == {"query": "alpha", "top_k": 2}

    read = srv.handoff_read_impl(repo, out["path"])
    assert read["handoff"]["to"] == "Executor"

    ok = srv.handoff_validate_impl(repo, path=out["path"])
    assert ok == {"valid": True}

    bad = dict(read["handoff"])
    bad.pop("state")
    res = srv.handoff_validate_impl(repo, handoff_json=json.dumps(bad))
    assert res["valid"] is False and "state" in res["errors"]


def test_handoff_validate_requires_exactly_one_input(repo):
    with pytest.raises(ValueError, match="exactly one"):
        srv.handoff_validate_impl(repo, path=None, handoff_json=None)


def test_retrieve_impl_returns_slices_and_tokens(repo):
    out = srv.retrieve_impl(repo, "alpha widget", top_k=3)
    assert "results" in out and "tokens" in out
    for r in out["results"]:
        assert {"source", "start_line", "end_line", "snippet"} <= set(r)


def test_metrics_summary_impl(repo):
    srv.handoff_write_impl(repo, sid="m2", from_agent="A", to_agent="B", doing="x")
    summary = srv.metrics_summary_impl(repo)
    assert summary["overall"]["events"] >= 1
    assert "handoff" in summary["by_kind"]
    assert isinstance(summary["exact"], bool)


def test_refresh_and_repo_manifest_impl(repo):
    with pytest.raises(FileNotFoundError):
        srv.repo_manifest_impl(repo)
    out = srv.refresh_impl(repo)
    assert out["manifest"].endswith("manifest.json")
    m = srv.repo_manifest_impl(repo)
    assert any("alpha" in mod["path"] for mod in m["modules"])


def test_coordinate_run_and_status_impl(repo, capsys):
    (repo.root / ".git").mkdir(exist_ok=True)
    import sys
    (repo.root / ".agentctx" / "config.yaml").write_text(
        yaml.safe_dump({"coordinate": {"runners": {
            "py": [sys.executable, "-c", "print('via mcp {task_id}')"]
        }}}),
        encoding="utf-8",
    )
    from pigeon.config import load_config
    cfg = load_config(repo.root)
    (repo.root / "tasks.yaml").write_text(
        yaml.safe_dump({"sid": "mcp1", "tasks": [{"id": "a", "runner": "py", "doing": "x"}]}),
        encoding="utf-8",
    )

    out = srv.coordinate_run_impl(cfg, "tasks.yaml")
    assert out["exit_code"] == 0
    assert out["run"]["status"] == "completed"
    assert out["run"]["tasks"]["a"]["status"] == "exited"
    # coordinate's streaming must land on stderr, never stdout (stdio MCP)
    captured = capsys.readouterr()
    assert "via mcp a" not in captured.out
    assert "via mcp a" in captured.err

    status = srv.coordinate_status_impl(cfg, sid="mcp1")
    assert status["run"]["run_id"] == "mcp1-1"
    history = srv.coordinate_status_impl(cfg, latest=False)
    assert len(history["runs"]) == 1


# ----------------------------------------------------------- FastMCP wiring
mcp_sdk = pytest.importorskip("mcp", reason="optional [mcp] extra not installed")

EXPECTED_TOOLS = {
    "retrieve", "handoff_write", "handoff_read", "handoff_validate",
    "coordinate_run", "coordinate_status", "metrics_summary",
    "repo_manifest", "refresh", "distill", "pack", "graph_query",
    "coordinate_plan",
}


def test_build_server_registers_all_tools(repo):
    server = srv.build_server(repo.root)
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS
    # descriptions are the agent-facing docs; none may be empty
    assert all(t.description for t in tools)


def test_server_tool_call_roundtrip(repo):
    server = srv.build_server(repo.root)
    result = asyncio.run(server.call_tool(
        "handoff_write",
        {"sid": "rt", "from_agent": "A", "to_agent": "B", "doing": "x"},
    ))
    # FastMCP returns (content, structured) — accept either shape across 1.x
    structured = result[1] if isinstance(result, tuple) else None
    if structured:
        payload = structured.get("result", structured)
    else:
        payload = json.loads(result[0].text)
    assert payload["path"].endswith("rt-1.json")
    assert ho.load_handoff(repo.root / payload["path"], repo)["to"] == "B"


def test_repo_path_guard_refuses_escapes(repo):
    with pytest.raises(ValueError, match="escapes the repository root"):
        srv.handoff_read_impl(repo, "../../etc/passwd")
    with pytest.raises(ValueError, match="escapes the repository root"):
        srv.handoff_validate_impl(repo, path="/etc/passwd")
