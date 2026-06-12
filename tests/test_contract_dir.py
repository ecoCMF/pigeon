"""Contract-dir duality: .pigeon native, .agentctx honored forever."""

from __future__ import annotations

from pigeon import init as init_mod
from pigeon.config import contract_dirname, load_config


def test_fresh_repo_is_pigeon_native(tmp_path):
    init_mod.init_repo(tmp_path)
    assert contract_dirname(tmp_path) == ".pigeon"
    cfg = load_config(tmp_path)
    assert cfg.contract_dir == tmp_path / ".pigeon"
    assert str(cfg.handoffs_dir).endswith(".pigeon/handoffs")
    assert str(cfg.coordinate_runs_dir).endswith(".pigeon/coordinate/runs")
    # the scaffolded config's own paths point at the same dir
    text = (tmp_path / ".pigeon" / "config.yaml").read_text(encoding="utf-8")
    assert ".pigeon/manifest.json" in text and ".agentctx" not in text


def test_legacy_repo_stays_legacy(repo):
    # the shared fixture builds .agentctx — init must respect, not migrate
    actions = init_mod.init_repo(repo.root)
    assert contract_dirname(repo.root) == ".agentctx"
    assert any("legacy, honored" in a for a in actions)
    assert not (repo.root / ".pigeon").exists()
    cfg = load_config(repo.root)
    assert str(cfg.handoffs_dir).endswith(".agentctx/handoffs")


def test_pigeon_dir_wins_when_both_exist(tmp_path):
    (tmp_path / ".pigeon").mkdir()
    (tmp_path / ".agentctx").mkdir()
    assert contract_dirname(tmp_path) == ".pigeon"


def test_gitignore_entries_follow_the_dir(tmp_path):
    init_mod.init_repo(tmp_path)
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".pigeon/handoffs/*.json" in gi
    assert ".agentctx" not in gi
