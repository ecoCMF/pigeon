"""Coordinate: tasks loading, safety preflight, handoff fan-out, streaming."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from pigeon import coordinate as co
from pigeon import handoff as ho
from pigeon.config import Config, load_config

# A runner that needs no external CLI: python -c, echoing its task id.
_PY_OK = [sys.executable, "-c", "print('hello from {task_id}')"]
_PY_FAIL = [sys.executable, "-c", "import sys; print('boom {task_id}'); sys.exit(3)"]


def _write_tasks(root: Path, spec: dict, fmt: str = "yaml") -> Path:
    path = root / f"tasks.{fmt}"
    text = json.dumps(spec) if fmt == "json" else yaml.safe_dump(spec)
    path.write_text(text, encoding="utf-8")
    return path


def _spec(**over) -> dict:
    base = {
        "sid": "co1",
        "tasks": [
            {"id": "t1", "runner": "py", "doing": "say hello"},
            {"id": "t2", "runner": "py", "doing": "say hello too"},
        ],
    }
    base.update(over)
    return base


def _setup(repo: Config, runners: dict | None = None) -> Config:
    """Mark the fixture repo as 'set up' (.git) and override runner templates."""
    (repo.root / ".git").mkdir(exist_ok=True)
    cfg_path = repo.root / ".agentctx" / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "coordinate": {
                "runners": runners if runners is not None else {"py": _PY_OK},
                "skip_permissions_flags": {"py": ["--yolo"]},
            }
        }),
        encoding="utf-8",
    )
    return load_config(repo.root)


# ----------------------------------------------------------------- load_tasks
def test_load_tasks_yaml_and_json(repo):
    for fmt in ("yaml", "json"):
        path = _write_tasks(repo.root, _spec(), fmt)
        spec = co.load_tasks(path)
        assert spec["sid"] == "co1"
        assert [t["id"] for t in spec["tasks"]] == ["t1", "t2"]
        # no runner + no default = refused (the 10-minute-Pro-burn lesson)
        path2 = _write_tasks(repo.root, {"sid": "s", "tasks": [{"id": "a", "doing": "x"}]}, fmt)
        with pytest.raises(ValueError, match="default_runner"):
            co.load_tasks(path2)
        assert co.load_tasks(path2, default_runner="agy")["tasks"][0]["runner"] == "agy"


@pytest.mark.parametrize("spec,needle", [
    ({"tasks": [{"id": "a", "doing": "x"}]}, "sid"),
    ({"sid": "s"}, "tasks"),
    ({"sid": "s", "tasks": []}, "non-empty"),
    ({"sid": "s", "tasks": [{"doing": "x"}]}, "id"),
    ({"sid": "s", "tasks": [{"id": "a"}]}, "doing"),
    ({"sid": "s", "tasks": [{"id": "a", "doing": "x"}, {"id": "a", "doing": "y"}]}, "duplicate"),
])
def test_load_tasks_rejects_bad_shapes(repo, spec, needle):
    path = _write_tasks(repo.root, spec)
    with pytest.raises(ValueError, match=needle):
        co.load_tasks(path)


# ------------------------------------------------------------------ preflight
def test_refuses_when_repo_not_setup(repo, capsys):
    # fixture has .agentctx but no .git: agents may not modify the folder
    tasks = _write_tasks(repo.root, _spec())
    rc = co.run_coordinate(tasks, repo, dry_run=True)
    assert rc == 2
    assert "repository is not set up" in capsys.readouterr().err


def test_refuses_unknown_runner(repo, capsys):
    cfg = _setup(repo)
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "runner": "nope", "doing": "x"}]))
    rc = co.run_coordinate(tasks, cfg, dry_run=True)
    assert rc == 2
    assert "unknown runner" in capsys.readouterr().err


def test_refuses_package_mutation_outside_isolated_env(repo, capsys, monkeypatch):
    cfg = _setup(repo)
    monkeypatch.setattr(co, "isolated_env", lambda: None)
    tasks = _write_tasks(repo.root, _spec(
        tasks=[{"id": "deps", "runner": "py", "doing": "pip install x", "mutates_packages": True}]
    ))
    rc = co.run_coordinate(tasks, cfg, dry_run=True)
    assert rc == 2
    assert "mutates_packages" in capsys.readouterr().err


def test_allows_package_mutation_inside_isolated_env(repo, monkeypatch):
    cfg = _setup(repo)
    monkeypatch.setattr(co, "isolated_env", lambda: "conda env: test")
    tasks = _write_tasks(repo.root, _spec(
        tasks=[{"id": "deps", "runner": "py", "doing": "pip install x", "mutates_packages": True}]
    ))
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 0


def test_refuses_non_linux(repo, monkeypatch, capsys):
    cfg = _setup(repo)
    monkeypatch.setattr(co.sys, "platform", "darwin")
    tasks = _write_tasks(repo.root, _spec())
    rc = co.run_coordinate(tasks, cfg, dry_run=True)
    assert rc == 2
    assert "Linux only" in capsys.readouterr().err


# ----------------------------------------------------- handoffs + dry-run cmds
def test_dry_run_writes_validated_handoffs_with_safety_constraints(repo, capsys):
    cfg = _setup(repo)
    tasks = _write_tasks(repo.root, _spec())
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 0

    paths = sorted(cfg.handoffs_dir.glob("co1-*.json"))
    assert len(paths) == 2
    by_to = {}
    for path in paths:
        obj = ho.load_handoff(path, cfg)  # validates on receipt
        by_to[obj["to"]] = obj
    assert set(by_to) == {"t1", "t2"}
    h = by_to["t1"]
    assert h["from"] == co.COORDINATOR
    assert h["state"]["doing"] == "say hello"
    # safety policy embedded in the contract, not just enforced locally
    assert "conda env, virtualenv, or container" in h["constraints"]["package_policy"]
    assert "repository" in h["constraints"]["fs_scope"]

    out = capsys.readouterr().out
    assert "would run:" in out and "no agents spawned" in out


def test_task_constraints_override_safety_defaults(repo):
    cfg = _setup(repo)
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "a", "runner": "py", "doing": "x", "constraints": {"fs_scope": "src/ only"}}
    ]))
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 0
    obj = ho.load_handoff(next(cfg.handoffs_dir.glob("co1-*.json")), cfg)
    assert obj["constraints"]["fs_scope"] == "src/ only"
    assert "package_policy" in obj["constraints"]  # untouched defaults remain


def test_skip_permissions_flag_appended_only_on_request(repo, capsys):
    cfg = _setup(repo)
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "runner": "py", "doing": "x"}]))
    co.run_coordinate(tasks, cfg, dry_run=True)
    assert "--yolo" not in capsys.readouterr().out
    co.run_coordinate(tasks, cfg, dry_run=True, skip_permissions=True)
    assert "--yolo" in capsys.readouterr().out


def test_default_runners_include_claude_agy_opencode(repo):
    runners = repo.coordinate_cfg["runners"]
    assert set(runners) >= {"claude", "agy", "opencode"}
    flags = repo.coordinate_cfg["skip_permissions_flags"]
    assert "--dangerously-skip-permissions" in flags["claude"]


# ------------------------------------------------------------------ execution
def test_parallel_execution_streams_prefixed_output_and_logs(repo, capsys):
    cfg = _setup(repo)
    tasks = _write_tasks(repo.root, _spec())
    rc = co.run_coordinate(tasks, cfg, parallel_limit=2)
    assert rc == 0

    out = capsys.readouterr().out
    assert "[t1] hello from t1" in out
    assert "[t2] hello from t2" in out
    assert "2/2 tasks ok" in out

    for tid in ("t1", "t2"):
        log = cfg.coordinate_log_dir / f"co1-{tid}.log"
        assert log.is_file()
        text = log.read_text(encoding="utf-8")
        assert f"hello from {tid}" in text
        assert "# exit 0" in text


def test_failing_task_yields_exit_1(repo, capsys):
    cfg = _setup(repo, runners={"py": _PY_OK, "bad": _PY_FAIL})
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "good", "runner": "py", "doing": "x"},
        {"id": "bad", "runner": "bad", "doing": "y"},
    ]))
    rc = co.run_coordinate(tasks, cfg)
    assert rc == 1
    out = capsys.readouterr().out
    assert "[bad] boom bad" in out
    assert "FAILED (exit 3)" in out
    assert "1/2 tasks ok" in out


def test_custom_log_dir(repo, tmp_path):
    cfg = _setup(repo)
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "runner": "py", "doing": "x"}]))
    log_dir = tmp_path / "elsewhere"
    assert co.run_coordinate(tasks, cfg, log_dir=log_dir) == 0
    assert (log_dir / "co1-a.log").is_file()


# -------------------------------------------------------------- depth/budget
def test_depth_guard_refuses_nested_coordination(repo, monkeypatch, capsys):
    cfg = _setup(repo)
    monkeypatch.setenv(co.DEPTH_ENV, "1")
    tasks = _write_tasks(repo.root, _spec())
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 2
    assert "depth 1 reached the limit" in capsys.readouterr().err


def test_children_inherit_incremented_depth(repo, monkeypatch, capsys):
    cfg = _setup(repo, runners={
        "py": [sys.executable, "-c",
               f"import os; print('depth=' + os.environ['{co.DEPTH_ENV}'])"],
    })
    monkeypatch.delenv(co.DEPTH_ENV, raising=False)
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "runner": "py", "doing": "x"}]))
    assert co.run_coordinate(tasks, cfg) == 0
    assert "[a] depth=1" in capsys.readouterr().out


def test_budget_stops_new_launches(repo, capsys):
    # each billed task reports 180 measured tokens; ceiling is 100, so the
    # first completion exhausts the budget and the rest never launch
    script = repo.root / "billed.py"
    script.write_text(f"print('''{_CLAUDE_JSON}''')\n", encoding="utf-8")
    cfg = _setup(repo, runners={"billed": [sys.executable, str(script)]})
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "a", "runner": "billed", "doing": "x"},
        {"id": "b", "runner": "billed", "doing": "y", "needs": ["a"]},
        {"id": "c", "runner": "billed", "doing": "z", "needs": ["b"]},
    ]))
    rc = co.run_coordinate(tasks, cfg, parallel_limit=1, budget_tokens=100)
    assert rc == 1
    out = capsys.readouterr().out
    assert "token budget exhausted (180/100)" in out

    run = co.list_runs(cfg, sid="co1")[0]
    assert run["tasks"]["a"]["status"] == "exited"
    assert run["tasks"]["b"]["status"] == "skipped"
    assert "token budget exhausted" in run["tasks"]["b"]["skipped_because"][0]
    assert run["tasks"]["c"]["status"] == "skipped"
    assert run["budget"]["spent_tokens"] == 180
    assert run["budget"]["max_tokens"] == 100
    assert run["summary"] == {"ok": 1, "failed": 0, "skipped": 2, "total": 3}


def test_no_budget_means_no_ceiling(repo):
    tracker = co.BudgetTracker()
    tracker.add(10**9, 10**6)
    assert tracker.exhausted() is None
    capped = co.BudgetTracker(max_usd=0.05)
    capped.add(0, 0.049)
    assert capped.exhausted() is None
    capped.add(0, 0.002)
    assert "cost budget exhausted" in capped.exhausted()


# ----------------------------------------------------------------- worktrees
import subprocess as sp


def _real_git(root: Path) -> None:
    """Turn the fixture into a real git repo with one commit (worktree base)."""
    for args in (
        ["git", "-c", "init.defaultBranch=main", "init", "-q"],
        ["git", "config", "user.email", "t@test"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "init"],
    ):
        sp.run(args, cwd=root, check=True, capture_output=True)


def test_worktree_isolation_keeps_main_checkout_clean(repo):
    cfg = _setup(repo, runners={
        "writer": [sys.executable, "-c", "open('out_{task_id}.txt', 'w').write('made')"],
    })
    _real_git(repo.root)
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "iso", "runner": "writer", "doing": "x", "isolation": "worktree"},
        {"id": "shared", "runner": "writer", "doing": "y"},
    ]))
    assert co.run_coordinate(tasks, cfg, parallel_limit=2) == 0

    # shared task wrote into the main checkout; isolated task did not
    assert (repo.root / "out_shared.txt").is_file()
    assert not (repo.root / "out_iso.txt").exists()

    run = co.list_runs(cfg, sid="co1")[0]
    iso = run["tasks"]["iso"]["isolation"]
    assert iso["branch"] == "pigeon/co1-1/iso"
    assert iso["changed"] is True and iso["commit"]
    assert "out_iso.txt" in iso["diffstat"]
    # the work survives on the task branch; the worktree itself is gone
    shown = sp.run(["git", "show", f"{iso['branch']}:out_iso.txt"],
                   cwd=repo.root, capture_output=True, text=True, check=True)
    assert shown.stdout == "made"
    assert not (cfg.coordinate_worktrees_dir / "co1-1" / "iso").exists()
    assert "isolation" not in run["tasks"]["shared"]


def test_worktree_handoffs_are_harvested_for_completion_contract(repo):
    code = (
        "import json, os; os.makedirs('.agentctx/handoffs', exist_ok=True); "
        "json.dump({\"schema_version\": \"1.0\", \"sid\": \"{sid}\", "
        "\"from\": \"{task_id}\", \"to\": \"Coordinator\", "
        "\"state\": {\"done\": [\"x\"], \"doing\": \"review\"}}, "
        "open('.agentctx/handoffs/{sid}-77.json', 'w'))"
    )
    cfg = _setup(repo, runners={"yields": [sys.executable, "-c", code]})
    _real_git(repo.root)
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "remote", "runner": "yields", "doing": "x", "isolation": "worktree"},
    ]))
    assert co.run_coordinate(tasks, cfg) == 0
    run = co.list_runs(cfg, sid="co1")[0]
    t = run["tasks"]["remote"]
    assert t["harvested_handoffs"] == [".agentctx/handoffs/co1-77.json"]
    assert t["status"] == "completed"
    assert t["return_handoff"] == ".agentctx/handoffs/co1-77.json"
    assert (repo.root / ".agentctx" / "handoffs" / "co1-77.json").is_file()


def test_worktree_without_changes_leaves_no_branch(repo):
    cfg = _setup(repo)
    _real_git(repo.root)
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "quiet", "runner": "py", "doing": "x", "isolation": "worktree"},
    ]))
    assert co.run_coordinate(tasks, cfg) == 0
    run = co.list_runs(cfg, sid="co1")[0]
    assert run["tasks"]["quiet"]["isolation"] == {"branch": None, "changed": False}
    branches = sp.run(["git", "branch", "--list", "pigeon/*"],
                      cwd=repo.root, capture_output=True, text=True).stdout
    assert branches.strip() == ""


def test_worktree_requires_a_commit(repo, capsys):
    cfg = _setup(repo)  # fake empty .git dir: rev-parse HEAD fails
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "iso", "runner": "py", "doing": "x", "isolation": "worktree"},
    ]))
    assert co.run_coordinate(tasks, cfg) == 2
    assert "at least one commit" in capsys.readouterr().err


def test_load_tasks_rejects_bad_isolation(repo):
    path = _write_tasks(repo.root, {"sid": "s", "tasks": [
        {"id": "a", "doing": "x", "isolation": "vm"}]})
    with pytest.raises(ValueError, match="isolation"):
        co.load_tasks(path)


# --------------------------------------------------------------- run manifest
def test_run_manifest_records_successful_run(repo):
    cfg = _setup(repo)
    tasks = _write_tasks(repo.root, _spec())
    assert co.run_coordinate(tasks, cfg, parallel_limit=2) == 0

    runs = co.list_runs(cfg, sid="co1")
    assert len(runs) == 1
    run = runs[0]
    assert run["run_id"] == "co1-1"
    assert run["status"] == "completed"
    assert run["summary"] == {"ok": 2, "failed": 0, "skipped": 0, "total": 2}
    assert run["started_at"] and run["finished_at"]
    for tid in ("t1", "t2"):
        t = run["tasks"][tid]
        # dummy runner exits 0 but never hands back to the Coordinator
        assert t["status"] == "exited"
        assert t["exit_code"] == 0
        assert t["duration_s"] >= 0
        assert t["output_lines"] == 1
        assert t["handoff"].startswith(".agentctx/handoffs/co1-")
        assert t["log"].endswith(f"co1-{tid}.log")
        assert "return_handoff" not in t


def test_run_manifest_completion_contract(repo):
    """A task that hands back to the Coordinator is upgraded to 'completed'."""
    code = (
        "import json, pathlib; "
        "pathlib.Path('.agentctx/handoffs/{sid}-9.json').write_text(json.dumps("
        "{\"schema_version\": \"1.0\", \"sid\": \"{sid}\", \"from\": \"{task_id}\","
        " \"to\": \"Coordinator\","
        " \"state\": {\"done\": [\"x\"], \"doing\": \"review my work\"}}))"
    )
    cfg = _setup(repo, runners={"py": _PY_OK, "yields": [sys.executable, "-c", code]})
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "polite", "runner": "yields", "doing": "x"},
        {"id": "silent", "runner": "py", "doing": "y"},
    ]))
    assert co.run_coordinate(tasks, cfg) == 0
    run = co.list_runs(cfg, sid="co1")[0]
    assert run["tasks"]["polite"]["status"] == "completed"
    assert run["tasks"]["polite"]["return_handoff"] == ".agentctx/handoffs/co1-9.json"
    assert run["tasks"]["silent"]["status"] == "exited"


def test_run_manifest_records_failure_and_refusal(repo, capsys):
    cfg = _setup(repo, runners={"py": _PY_OK, "bad": _PY_FAIL})
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "bad", "runner": "bad", "doing": "x"}]))
    assert co.run_coordinate(tasks, cfg) == 1
    run = co.list_runs(cfg, sid="co1")[-1]
    assert run["status"] == "failed"
    assert run["tasks"]["bad"]["status"] == "failed"
    assert run["tasks"]["bad"]["exit_code"] == 3

    # a refused run is still recorded, with its preflight errors
    tasks2 = _write_tasks(repo.root, _spec(
        sid="co2", tasks=[{"id": "a", "runner": "nope", "doing": "x"}]))
    assert co.run_coordinate(tasks2, cfg) == 2
    refused = co.list_runs(cfg, sid="co2")[0]
    assert refused["status"] == "refused"
    assert any("unknown runner" in e for e in refused["preflight_errors"])
    capsys.readouterr()


def test_run_manifests_are_append_only(repo):
    cfg = _setup(repo)
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "runner": "py", "doing": "x"}]))
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 0
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 0
    runs = co.list_runs(cfg, sid="co1")
    assert [r["run_id"] for r in runs] == ["co1-1", "co1-2"]
    assert all(r["status"] == "dry-run" for r in runs)
    assert runs[0]["tasks"]["a"]["status"] == "dry-run"


# --------------------------------------------------------------- dependencies
@pytest.mark.parametrize("tasks,needle", [
    ([{"id": "a", "doing": "x", "needs": "b"}], "list"),
    ([{"id": "a", "doing": "x", "needs": ["a"]}], "itself"),
    ([{"id": "a", "doing": "x", "needs": ["ghost"]}], "unknown dependency"),
    ([{"id": "a", "doing": "x", "needs": ["b"]},
      {"id": "b", "doing": "y", "needs": ["a"]}], "cycle"),
    ([{"id": "a", "doing": "x", "needs": ["c"]},
      {"id": "b", "doing": "y", "needs": ["a"]},
      {"id": "c", "doing": "z", "needs": ["b"]}], "cycle"),
])
def test_load_tasks_rejects_bad_dependencies(repo, tasks, needle):
    path = _write_tasks(repo.root, {"sid": "s", "tasks": tasks})
    with pytest.raises(ValueError, match=needle):
        co.load_tasks(path)


def _order_runner(root):
    """A runner that appends its task id to order.txt — observable scheduling."""
    return [sys.executable, "-c",
            "open('order.txt', 'a').write('{task_id};')"]


def test_needs_orders_execution_diamond(repo):
    cfg = _setup(repo, runners={"mark": _order_runner(repo.root)})
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "d", "runner": "mark", "doing": "join", "needs": ["b", "c"]},
        {"id": "b", "runner": "mark", "doing": "left", "needs": ["a"]},
        {"id": "c", "runner": "mark", "doing": "right", "needs": ["a"]},
        {"id": "a", "runner": "mark", "doing": "root"},
    ]))
    assert co.run_coordinate(tasks, cfg, parallel_limit=4) == 0
    order = (repo.root / "order.txt").read_text(encoding="utf-8").strip(";").split(";")
    assert order[0] == "a"
    assert order[-1] == "d"
    assert set(order[1:3]) == {"b", "c"}
    run = co.list_runs(cfg, sid="co1")[0]
    assert run["tasks"]["d"]["needs"] == ["b", "c"]
    assert run["summary"]["ok"] == 4


def test_failed_dependency_skips_downstream_cascade(repo, capsys):
    cfg = _setup(repo, runners={"py": _PY_OK, "bad": _PY_FAIL})
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "root", "runner": "bad", "doing": "fail"},
        {"id": "mid", "runner": "py", "doing": "x", "needs": ["root"]},
        {"id": "leaf", "runner": "py", "doing": "y", "needs": ["mid"]},
        {"id": "free", "runner": "py", "doing": "z"},
    ]))
    assert co.run_coordinate(tasks, cfg) == 1
    out = capsys.readouterr().out
    assert "[mid] skipped (dependency failed: root)" in out
    assert "[leaf] skipped (dependency failed: mid)" in out
    assert "hello from free" in out

    run = co.list_runs(cfg, sid="co1")[0]
    assert run["status"] == "failed"
    assert run["tasks"]["root"]["status"] == "failed"
    assert run["tasks"]["mid"]["status"] == "skipped"
    assert run["tasks"]["mid"]["skipped_because"] == ["root"]
    assert run["tasks"]["leaf"]["status"] == "skipped"
    assert run["tasks"]["free"]["status"] == "exited"
    assert run["summary"] == {"ok": 1, "failed": 1, "skipped": 2, "total": 4}


def test_needs_chain_respects_parallel_limit_one(repo):
    cfg = _setup(repo, runners={"mark": _order_runner(repo.root)})
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "c", "runner": "mark", "doing": "third", "needs": ["b"]},
        {"id": "b", "runner": "mark", "doing": "second", "needs": ["a"]},
        {"id": "a", "runner": "mark", "doing": "first"},
    ]))
    assert co.run_coordinate(tasks, cfg, parallel_limit=1) == 0
    order = (repo.root / "order.txt").read_text(encoding="utf-8").strip(";").split(";")
    assert order == ["a", "b", "c"]


# ------------------------------------------------------------------ telemetry
_CLAUDE_JSON = (
    '{"type":"result","result":"done","num_turns":4,"total_cost_usd":0.0123,'
    '"usage":{"input_tokens":100,"output_tokens":50,'
    '"cache_creation_input_tokens":5,"cache_read_input_tokens":25}}'
)


def test_extract_telemetry_shapes():
    # single JSON document (claude -p --output-format json)
    t = co._extract_telemetry(_CLAUDE_JSON)
    assert t["total_tokens"] == 180
    assert t["total_cost_usd"] == 0.0123
    # NDJSON / stream-json: usage report on the last line
    t = co._extract_telemetry('{"type":"system"}\nplain text\n' + _CLAUDE_JSON)
    assert t["total_tokens"] == 180
    # plain text output is not an error
    assert co._extract_telemetry("hello\nworld") is None
    assert co._extract_telemetry('{"no_usage": true}') is None


def test_telemetry_recorded_in_manifest_and_metrics(repo):
    # a script file dodges shell-quoting noise around the JSON payload
    script = repo.root / "billed.py"
    script.write_text(f"print('working')\nprint('''{_CLAUDE_JSON}''')\n", encoding="utf-8")
    cfg = _setup(repo, runners={"py": _PY_OK, "billed": [sys.executable, str(script)]})

    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "cheap", "runner": "py", "doing": "x"},
        {"id": "spender", "runner": "billed", "doing": "y"},
    ]))
    assert co.run_coordinate(tasks, cfg) == 0

    run = co.list_runs(cfg, sid="co1")[0]
    assert "telemetry" not in run["tasks"]["cheap"]
    telem = run["tasks"]["spender"]["telemetry"]
    assert telem["total_tokens"] == 180
    assert telem["usage"]["input_tokens"] == 100

    events = [json.loads(l) for l in cfg.metrics.read_text(encoding="utf-8").splitlines()]
    agent_runs = [e for e in events if e.get("kind") == "agent_run"]
    assert len(agent_runs) == 1
    assert agent_runs[0]["task"] == "spender"
    assert agent_runs[0]["actual_tokens"] == 180
    assert agent_runs[0]["cost_usd"] == 0.0123


def test_telemetry_flags_appended_only_when_requested(repo, capsys):
    cfg_path = repo.root / ".agentctx" / "config.yaml"
    (repo.root / ".git").mkdir(exist_ok=True)
    cfg_path.write_text(yaml.safe_dump({
        "coordinate": {
            "runners": {"py": _PY_OK},
            "telemetry_flags": {"py": ["--output-format", "json"]},
        }
    }), encoding="utf-8")
    from pigeon.config import load_config
    cfg = load_config(repo.root)
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "runner": "py", "doing": "x"}]))

    co.run_coordinate(tasks, cfg, dry_run=True)
    assert "--output-format" not in capsys.readouterr().out
    co.run_coordinate(tasks, cfg, dry_run=True, telemetry=True)
    assert "--output-format json" in capsys.readouterr().out
    # per-task override beats the run-level default
    tasks2 = _write_tasks(repo.root, _spec(
        tasks=[{"id": "a", "runner": "py", "doing": "x", "telemetry": False}]))
    co.run_coordinate(tasks2, cfg, dry_run=True, telemetry=True)
    assert "--output-format" not in capsys.readouterr().out


def test_handoffs_are_token_accounted(repo):
    cfg = _setup(repo)
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "runner": "py", "doing": "x"}]))
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 0
    events = [json.loads(l) for l in cfg.metrics.read_text(encoding="utf-8").splitlines()]
    assert any(ev.get("kind") == "handoff" for ev in events)


# ----------------------------------------------------------------------- plan
def test_compute_waves_and_longest_chain():
    tasks = [
        {"id": "d", "needs": ["b", "c"]},
        {"id": "b", "needs": ["a"]},
        {"id": "c", "needs": ["a"]},
        {"id": "a"},
        {"id": "free"},
    ]
    assert co.compute_waves(tasks) == [["a", "free"], ["b", "c"], ["d"]]
    chain = co.longest_chain(tasks)
    assert chain[0] == "a" and chain[-1] == "d" and len(chain) == 3


def test_plan_is_read_only_and_structured(repo):
    cfg = _setup(repo)
    spec = co.load_tasks(_write_tasks(repo.root, _spec(tasks=[
        {"id": "a", "runner": "py", "doing": "x"},
        {"id": "b", "runner": "py", "doing": "y", "needs": ["a"],
         "isolation": "worktree", "pack": True},
    ])))
    p = co.plan(cfg, spec)
    assert p["waves"] == [["a"], ["b"]]
    assert p["longest_chain"] == ["a", "b"]
    assert p["tasks"]["b"]["isolation"] == "worktree"
    assert p["tasks"]["b"]["pack"] is True
    # worktree task + fake empty .git -> preflight catches it in the preview
    assert any("at least one commit" in e for e in p["preflight_errors"])
    # read-only: no handoffs, no run manifests, no events of any kind
    assert not list(cfg.handoffs_dir.glob("*.json"))
    assert co.list_runs(cfg) == []
    assert not cfg.metrics.exists()


def test_format_plan_renders_waves_and_badges(repo):
    cfg = _setup(repo)
    spec = co.load_tasks(_write_tasks(repo.root, _spec(tasks=[
        {"id": "a", "runner": "py", "doing": "x"},
        {"id": "b", "runner": "py", "doing": "y", "needs": ["a"], "telemetry": True},
    ])))
    text = co.format_plan(co.plan(cfg, spec), spec["tasks"])
    assert "wave 1  a  [py]" in text
    assert "wave 2  b  [py · telemetry · ← a]" in text
    assert "longest chain: a → b" in text
    assert "preflight: ok" in text


def test_run_header_prints_wave_plan(repo, capsys):
    cfg = _setup(repo)
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "a", "runner": "py", "doing": "x"},
        {"id": "b", "runner": "py", "doing": "y", "needs": ["a"]},
    ]))
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 0
    assert "plan: [a]  →  [b]" in capsys.readouterr().out


# ----------------------------------------------------------------------- crew
_CREW = {
    "skills": ["advanced-python-backend"],
    "subagents": [
        {"role": "implementer", "skill": "advanced-python-backend",
         "doing": "write the endpoints"},
        {"role": "adversarial-reviewer", "skill": "security-audit",
         "verdict": "must approve before hand-back"},
    ],
}


@pytest.mark.parametrize("crew,needle", [
    ("not-a-dict", "mapping"),
    ({"skills": "oops"}, "list of names"),
    ({"skills": [1]}, "list of names"),
    ({"subagents": [{"skill": "x"}]}, "role"),
    ({}, "empty"),
])
def test_load_tasks_rejects_bad_crew(repo, crew, needle):
    path = _write_tasks(repo.root, {"sid": "s", "tasks": [
        {"id": "a", "doing": "x", "crew": crew}]})
    with pytest.raises(ValueError, match=needle):
        co.load_tasks(path)


def test_crew_lands_in_handoff_prompt_and_manifest(repo, capsys):
    # a runner template that carries {prompt} in its argv, like real CLIs do
    cfg = _setup(repo, runners={"py": [sys.executable, "-c", "print('ok')", "{prompt}"]})
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "api", "runner": "py", "doing": "build it", "crew": _CREW}]))
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 0

    # handoff: schema-validated 1.1 with the crew object intact
    h = ho.load_handoff(next(cfg.handoffs_dir.glob("co1-*.json")), cfg)
    assert h["schema_version"] == "1.1"
    assert h["crew"] == _CREW

    # prompt: marching orders rendered into the spawn command
    out = capsys.readouterr().out
    assert "Load these skills before starting: advanced-python-backend." in out
    assert "Dispatch a subagent for the role" in out
    assert "adversarial-reviewer" in out
    assert "Gate: must approve before hand-back." in out

    # run manifest records the roster
    run = co.list_runs(cfg, sid="co1")[0]
    assert run["tasks"]["api"]["crew"] == _CREW


def test_schema_rejects_malformed_crew(repo):
    h = ho.build_handoff(sid="s", frm="A", to="B", done=[], doing="x",
                         crew={"subagents": [{"skill": "no-role"}]})
    with pytest.raises(ho.HandoffValidationError, match="role"):
        ho.validate_handoff(h, repo)
    h2 = ho.build_handoff(sid="s", frm="A", to="B", done=[], doing="x",
                          crew={"surprise": True})
    with pytest.raises(ho.HandoffValidationError):
        ho.validate_handoff(h2, repo)


def test_plan_shows_crew_badge(repo):
    cfg = _setup(repo)
    spec = co.load_tasks(_write_tasks(repo.root, _spec(tasks=[
        {"id": "api", "runner": "py", "doing": "x", "crew": _CREW}])))
    p = co.plan(cfg, spec)
    assert p["tasks"]["api"]["crew"] == _CREW
    assert "crew×3" in co.format_plan(p, spec["tasks"])


def test_crew_appended_to_custom_prompt(repo, capsys):
    cfg = _setup(repo, runners={"py": [sys.executable, "-c", "print('ok')", "{prompt}"]})
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "a", "runner": "py", "doing": "x", "prompt": "Custom orders.",
         "crew": {"skills": ["geospatial-engineering-postgis"]}}]))
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "Custom orders. Load these skills" in out


# --------------------------------------------------------------------- status
def test_render_status_screen(repo):
    run = {
        "run_id": "s-1", "status": "running", "depth": 0,
        "started_at": "2026-06-11T03:00:00+00:00",
        "isolated_env": "conda env: base", "skip_permissions": False,
        "budget": {"spent_tokens": 1423, "max_tokens": 10000, "spent_usd": 0.21},
        "tasks": {
            "ddl": {"runner": "claude", "status": "exited", "exit_code": 0,
                    "duration_s": 12.1},
            "schema": {"runner": "claude", "status": "completed",
                       "duration_s": 41.0,
                       "return_handoff": ".agentctx/handoffs/s-7.json"},
            "api": {"runner": "agy", "status": "running",
                    "started_at": "2026-06-11T03:01:00+00:00",
                    "log": ".agentctx/coordinate/logs/s-api.log",
                    "isolation": {"branch": "pigeon/s-1/api"}},
            "tests": {"runner": "claude", "status": "queued", "needs": ["api"]},
            "doomed": {"runner": "py", "status": "skipped",
                       "skipped_because": ["budget exhausted"]},
        },
    }
    text = co.render_status(run)
    assert "s-1  RUNNING" in text
    assert "tasks: 2 ok · 1 running · 1 queued · 0 failed · 1 skipped" in text
    assert "budget: 1423/10000 tok" in text
    assert "✔ schema" in text and "↩ .agentctx/handoffs/s-7.json" in text
    assert "▶ api" in text and "⎇ pigeon/s-1/api" in text
    assert "log: .agentctx/coordinate/logs/s-api.log" in text
    assert "· tests" in text and "└─ needs: api" in text
    assert "⊘ doomed" in text and "because: budget exhausted" in text
    assert "%" not in text  # no fictional progress percentages, ever


# ------------------------------------------------------------- events/reports
def test_event_stream_records_the_run_chronologically(repo):
    cfg = _setup(repo, runners={"py": _PY_OK, "bad": _PY_FAIL})
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "a", "runner": "py", "doing": "x"},
        {"id": "b", "runner": "bad", "doing": "y", "needs": ["a"]},
        {"id": "c", "runner": "py", "doing": "z", "needs": ["b"]},
    ]))
    assert co.run_coordinate(tasks, cfg, parallel_limit=1) == 1

    events = co.run_events(cfg, "co1-1")
    names = [e["event"] for e in events]
    assert names[0] == "run.started"
    assert names[-1] == "run.failed"
    assert names.count("handoff.dispatched") == 3
    assert "task.running" in names and "task.failed" in names
    assert "task.skipped" in names
    # chronological and self-describing
    assert events == sorted(events, key=lambda e: e["ts"])
    failed = next(e for e in events if e["event"] == "task.failed")
    assert failed["task"] == "b" and failed["exit_code"] == 3


def test_timeline_by_agent_critical_path_reports(repo):
    cfg = _setup(repo, runners={"py": _PY_OK, "slow": [
        sys.executable, "-c", "import time; time.sleep(0.2); print('done')"]})
    tasks = _write_tasks(repo.root, _spec(tasks=[
        {"id": "fast", "runner": "py", "doing": "x"},
        {"id": "slow1", "runner": "slow", "doing": "y"},
        {"id": "slow2", "runner": "slow", "doing": "z", "needs": ["slow1"]},
    ]))
    assert co.run_coordinate(tasks, cfg, parallel_limit=3) == 0
    run = co.list_runs(cfg, sid="co1")[0]

    timeline = co.timeline_report(cfg, run)
    assert "run.started" in timeline and "task.exited" in timeline
    assert "handoff.dispatched" in timeline

    agents = co.by_agent_report(run)
    assert "py" in agents and "tasks=1  ok=1" in agents
    assert "slow" in agents and "tasks=2  ok=2" in agents

    crit = co.critical_path_report(run)
    # the weighted chain is slow1 -> slow2, not the fast independent task
    assert "slow1" in crit and "slow2" in crit
    assert crit.index("slow1") < crit.index("slow2")
    assert "fast" not in crit.split("critical path")[1].split("total")[0]


def test_timeline_falls_back_gracefully_without_event_file(repo):
    run = {"run_id": "ghost-1", "tasks": {}}
    assert "no event stream" in co.timeline_report(repo, run)


# -------------------------------------------------------------- audit round 2
@pytest.mark.parametrize("bad", ["../evil", "a b", "-flag", "x/../y"])
def test_unsafe_task_ids_and_sids_refused(repo, bad):
    with pytest.raises(ValueError, match="unsafe|required|non-empty"):
        co.load_tasks(_write_tasks(repo.root, {"sid": bad,
                                               "tasks": [{"id": "a", "doing": "x"}]}))
    with pytest.raises(ValueError, match="unsafe"):
        co.load_tasks(_write_tasks(repo.root, {
            "sid": "ok", "tasks": [{"id": bad, "doing": "x"}]}))


def test_env_allowlist_blocks_secrets(repo, monkeypatch):
    monkeypatch.setenv("FAKE_CLOUD_SECRET", "hunter2")
    monkeypatch.setenv("PIGEON_KEEP_ME", "yes")
    code = ("import os; print('secret=' + str('FAKE_CLOUD_SECRET' in os.environ)); "
            "print('kept=' + os.environ.get('PIGEON_KEEP_ME', 'missing')); "
            "print('path=' + str('PATH' in os.environ))")
    import yaml as _yaml
    (repo.root / ".git").mkdir(exist_ok=True)
    (repo.root / ".agentctx" / "config.yaml").write_text(_yaml.safe_dump({
        "coordinate": {
            "env_allowlist": ["PIGEON_KEEP_ME"],
            "runners": {"py": [sys.executable, "-c", code]},
        }}), encoding="utf-8")
    cfg = load_config(repo.root)
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "runner": "py", "doing": "x"}]))
    assert co.run_coordinate(tasks, cfg) == 0
    log = (cfg.coordinate_log_dir / "co1-a.log").read_text(encoding="utf-8")
    assert "secret=False" in log     # the operator's secret never reached the child
    assert "kept=yes" in log         # allowlisted var did
    assert "path=True" in log        # functional baseline survives


def test_env_inherits_everything_by_default(repo, monkeypatch):
    monkeypatch.setenv("FAKE_CLOUD_SECRET", "hunter2")
    cfg = _setup(repo, runners={"py": [
        sys.executable, "-c", "import os; print('secret=' + str('FAKE_CLOUD_SECRET' in os.environ))"]})
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "runner": "py", "doing": "x"}]))
    assert co.run_coordinate(tasks, cfg) == 0
    assert "secret=True" in (cfg.coordinate_log_dir / "co1-a.log").read_text(encoding="utf-8")


def test_cleanup_removes_orphans_and_prunes_history(repo):
    cfg = _setup(repo)
    _real_git(repo.root)
    # simulate a crashed coordinator: worktree set up, never finished
    wt_dir, branch = co._worktree_setup(cfg, "crash-1", "stuck")
    assert wt_dir.is_dir()
    # plus two finished runs worth of history
    for _ in range(2):
        tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "runner": "py", "doing": "x"}]))
        assert co.run_coordinate(tasks, cfg, dry_run=True) == 0

    report = co.cleanup(cfg, keep_runs=1)
    assert report["removed_worktrees"] == ["crash-1/stuck"]
    assert not wt_dir.exists()
    # committed work is never garbage: the branch survives
    out = sp.run(["git", "branch", "--list", branch], cwd=repo.root,
                 capture_output=True, text=True).stdout
    assert branch in out
    assert report["pruned_runs"] == ["co1-1"]
    assert len(co.list_runs(cfg)) == 1


def test_render_status_shows_failed_task_log_tail(repo):
    log = repo.root / ".agentctx" / "coordinate" / "logs" / "x-bad.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("$ cmd\nTraceback (most recent call last):\nValueError: boom\n# exit 1\n",
                   encoding="utf-8")
    run = {"run_id": "x-1", "status": "failed",
           "tasks": {"bad": {"runner": "py", "status": "failed", "exit_code": 1,
                             "log": str(log.relative_to(repo.root))}}}
    text = co.render_status(run, repo)
    assert "⌙ ValueError: boom" in text
    assert "# exit 1" not in text  # bookkeeping lines filtered
    # without a config, no tail (TUI header path stays cheap)
    assert "⌙" not in co.render_status(run)


# -------------------------------------------------------------- runner routing
def test_default_runner_round_robin_spreads_load(repo):
    path = _write_tasks(repo.root, {"sid": "s", "tasks": [
        {"id": f"t{i}", "doing": "x"} for i in range(5)]})
    spec = co.load_tasks(path, default_runner=["agy", "opencode"])
    runners = [t["runner"] for t in spec["tasks"]]
    assert runners == ["agy", "opencode", "agy", "opencode", "agy"]
    # explicit choices are never overridden
    path2 = _write_tasks(repo.root, {"sid": "s", "tasks": [
        {"id": "a", "doing": "x", "runner": "claude"},
        {"id": "b", "doing": "y"}]})
    spec2 = co.load_tasks(path2, default_runner=["agy"])
    assert [t["runner"] for t in spec2["tasks"]] == ["claude", "agy"]


def test_config_default_runner_reaches_run_and_plan(repo, capsys):
    import yaml as _yaml
    (repo.root / ".git").mkdir(exist_ok=True)
    (repo.root / ".agentctx" / "config.yaml").write_text(_yaml.safe_dump({
        "coordinate": {
            "default_runner": "py",
            "runners": {"py": _PY_OK},
        }}), encoding="utf-8")
    cfg = load_config(repo.root)
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "doing": "x"}]))
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 0
    run = co.list_runs(cfg, sid="co1")[0]
    assert run["tasks"]["a"]["runner"] == "py"


def test_restrain_subagents_constraint_default_on(repo):
    cfg = _setup(repo)
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "runner": "py", "doing": "x"}]))
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 0
    h = ho.load_handoff(next(cfg.handoffs_dir.glob("co1-*.json")), cfg)
    assert "do not fan out additional subagents" in h["constraints"]["subagents"]


def test_restrain_subagents_can_be_disabled(repo):
    import yaml as _yaml
    (repo.root / ".git").mkdir(exist_ok=True)
    (repo.root / ".agentctx" / "config.yaml").write_text(_yaml.safe_dump({
        "coordinate": {"runners": {"py": _PY_OK},
                       "safety": {"restrain_subagents": False}}}), encoding="utf-8")
    cfg = load_config(repo.root)
    tasks = _write_tasks(repo.root, _spec(tasks=[{"id": "a", "runner": "py", "doing": "x"}]))
    assert co.run_coordinate(tasks, cfg, dry_run=True) == 0
    h = ho.load_handoff(next(cfg.handoffs_dir.glob("co1-*.json")), cfg)
    assert "subagents" not in h["constraints"]
