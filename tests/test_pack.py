"""Pack: bounded pre-task context bundles from every memory layer."""

from __future__ import annotations

import json

import pytest

from pigeon import coordinate as co
from pigeon import distill, manifest, pack
from pigeon import handoff as ho


def _seed(repo):
    """One decision in memory, one history event, a manifest, real code."""
    h = ho.build_handoff(sid="s1", frm="Planner", to="Executor", done=["x"],
                         doing="refactor the alpha widget spinner",
                         decisions={"alpha_policy": "spin twice"})
    ho.write_handoff(h, repo)
    distill.distill_session(repo, "s1")
    manifest.write_manifest(repo)


def test_pack_bundles_all_layers(repo):
    _seed(repo)
    res = pack.pack(repo, "alpha widget spin policy", max_tokens=4000)

    bundle = (repo.root / res["path"]).read_text(encoding="utf-8")
    assert res["path"].startswith(".agentctx/context/alpha-widget-spin-policy-1")
    assert "# Context bundle — alpha widget spin policy" in bundle
    assert "## Memory (distilled)" in bundle and "alpha_policy" in bundle
    assert "## Code" in bundle and "alpha.py" in bundle
    assert "## Repo map" in bundle
    assert "## Recent history" in bundle
    assert res["sections"]["memory"] >= 1
    assert res["sections"]["code"] >= 1

    events = [json.loads(l) for l in repo.metrics.read_text(encoding="utf-8").splitlines()]
    ev = [e for e in events if e.get("kind") == "pack"]
    assert len(ev) == 1
    assert ev[0]["actual_tokens"] > 0
    # fixture files are tiny, so no whole-file savings here; just bookkeeping
    assert ev[0]["baseline_tokens"] > 0
    assert ev[0]["saved_tokens"] == max(
        0, ev[0]["baseline_tokens"] - ev[0]["actual_tokens"])


def test_pack_respects_token_budget(repo):
    _seed(repo)
    small = pack.pack(repo, "alpha widget spin policy", max_tokens=120)
    big = pack.pack(repo, "alpha widget spin policy", max_tokens=8000)
    assert small["tokens"]["actual_tokens"] < big["tokens"]["actual_tokens"]
    assert small["dropped"] > 0


def test_pack_bundle_paths_are_append_only(repo):
    _seed(repo)
    a = pack.pack(repo, "alpha widget", max_tokens=500)
    b = pack.pack(repo, "alpha widget", max_tokens=500)
    assert a["path"].endswith("alpha-widget-1.md")
    assert b["path"].endswith("alpha-widget-2.md")


def test_pack_rejects_empty_task(repo):
    with pytest.raises(ValueError, match="must not be empty"):
        pack.pack(repo, "   ")


def test_coordinate_pack_true_attaches_bundle(repo):
    import sys
    import yaml
    _seed(repo)
    (repo.root / ".git").mkdir(exist_ok=True)
    (repo.root / ".agentctx" / "config.yaml").write_text(
        yaml.safe_dump({"coordinate": {"runners": {
            "py": [sys.executable, "-c", "print('ok')"]}}}), encoding="utf-8")
    from pigeon.config import load_config
    cfg = load_config(repo.root)
    (repo.root / "tasks.yaml").write_text(yaml.safe_dump({
        "sid": "pk", "tasks": [
            {"id": "a", "runner": "py", "doing": "refactor the alpha widget", "pack": True},
        ]}), encoding="utf-8")
    assert co.run_coordinate(repo.root / "tasks.yaml", cfg, dry_run=True) == 0
    h = ho.load_handoff(next(cfg.handoffs_dir.glob("pk-*.json")), cfg)
    bundles = [a for a in h["state"]["artifacts"] if "/context/" in a]
    assert len(bundles) == 1 and bundles[0].startswith("repo://.agentctx/context/")
    assert (repo.root / bundles[0][len("repo://"):]).is_file()
