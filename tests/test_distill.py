"""Distill: episodic handoffs + runs -> durable session memory + decision ledger."""

from __future__ import annotations

import json

import pytest

from pigeon import coordinate as co
from pigeon import distill
from pigeon import handoff as ho


def _handoff(repo, sid, frm, to, *, decisions=None, artifacts=None, doing="work"):
    h = ho.build_handoff(sid=sid, frm=frm, to=to, done=["x"], doing=doing,
                         decisions=decisions, artifacts=artifacts)
    return ho.write_handoff(h, repo)


def _run_manifest(repo, sid):
    rec = co.RunRecorder(repo, sid, [{"id": "a", "runner": "py"}],
                         tasks_file="t.yaml", parallel_limit=1,
                         skip_permissions=False, dry_run=False,
                         telemetry=False, isolated_env=None, depth=0)
    rec.task("a", status="exited", exit_code=0, duration_s=0.1)
    rec.finish("completed",
               summary={"ok": 1, "failed": 0, "skipped": 0, "total": 1},
               budget={"spent_tokens": 180, "spent_usd": 0.01})


def test_distill_session_writes_record_and_ledger(repo):
    _handoff(repo, "s1", "Planner", "Executor",
             decisions={"auth": "oauth2_pkce"}, artifacts=["repo://AGENTS.md"])
    _handoff(repo, "s1", "Executor", co.COORDINATOR, doing="review my work")
    _run_manifest(repo, "s1")

    res = distill.distill_session(repo, "s1")
    assert res["handoffs"] == 2 and res["runs"] == 1

    record = (repo.root / res["session"]).read_text(encoding="utf-8")
    assert "# Session s1" in record
    assert "1 ok / 0 failed" in record
    assert "measured agent spend: 180 tokens" in record
    assert "`auth` = \"oauth2_pkce\"" in record
    assert "from **Executor**: next → review my work" in record
    assert "repo://AGENTS.md" in record
    assert "**a** — exited, exit 0" in record

    ledger = (repo.root / res["decisions"]).read_text(encoding="utf-8")
    assert "## auth" in ledger and "oauth2_pkce" in ledger and "session s1" in ledger

    events = [json.loads(l) for l in repo.metrics.read_text(encoding="utf-8").splitlines()]
    ev = [e for e in events if e.get("kind") == "distill"]
    assert len(ev) == 1 and ev[0]["sid"] == "s1"
    assert ev[0]["baseline_tokens"] > ev[0]["actual_tokens"] > 0


def test_distill_is_deterministic_and_idempotent(repo):
    _handoff(repo, "s1", "A", "B", decisions={"k": 1})
    first = distill.distill_session(repo, "s1")
    text1 = (repo.root / first["session"]).read_text(encoding="utf-8")
    second = distill.distill_session(repo, "s1")
    text2 = (repo.root / second["session"]).read_text(encoding="utf-8")
    assert text1 == text2


def test_decision_ledger_tracks_evolution_across_sessions(repo):
    _handoff(repo, "s1", "A", "B", decisions={"db": "sqlite"})
    _handoff(repo, "s2", "A", "B", decisions={"db": "postgres"})
    distill.distill_all(repo)
    ledger = (repo.root / ".agentctx" / "memory" / "decisions.md").read_text(encoding="utf-8")
    assert ledger.index("current: \"postgres\"") < ledger.index("earlier: \"sqlite\"")
    assert (repo.root / ".agentctx" / "memory" / "sessions" / "s1.md").is_file()
    assert (repo.root / ".agentctx" / "memory" / "sessions" / "s2.md").is_file()


def test_distill_unknown_session_raises(repo):
    with pytest.raises(ValueError, match="nothing to distill"):
        distill.distill_session(repo, "ghost")


def test_known_sids_unions_handoffs_and_runs(repo):
    _handoff(repo, "from-handoff", "A", "B")
    _run_manifest(repo, "from-run")
    assert distill.known_sids(repo) == ["from-handoff", "from-run"]


def test_init_scaffolds_playbooks(tmp_path):
    from pigeon import init as init_mod
    actions = init_mod.init_repo(tmp_path)
    readme = tmp_path / ".pigeon" / "memory" / "playbooks" / "README.md"
    assert readme.is_file()
    assert "procedural memory" in readme.read_text(encoding="utf-8")
    # idempotent
    actions2 = init_mod.init_repo(tmp_path)
    assert any("ok     memory/playbooks/" in a for a in actions2)


def test_auto_distill_runs_after_coordinate(repo):
    import sys
    import yaml
    (repo.root / ".git").mkdir(exist_ok=True)
    (repo.root / ".agentctx" / "config.yaml").write_text(yaml.safe_dump({
        "coordinate": {
            "auto_distill": True,
            "runners": {"py": [sys.executable, "-c", "print('hi')"]},
        }}), encoding="utf-8")
    from pigeon.config import load_config
    cfg = load_config(repo.root)
    (repo.root / "tasks.yaml").write_text(yaml.safe_dump({
        "sid": "auto", "tasks": [{"id": "a", "runner": "py", "doing": "x"}]}),
        encoding="utf-8")
    assert co.run_coordinate(repo.root / "tasks.yaml", cfg) == 0
    assert (repo.root / ".agentctx" / "memory" / "sessions" / "auto.md").is_file()
