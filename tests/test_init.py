"""`pigeon init` scaffolding."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from pigeon import init as init_mod
from pigeon import manifest
from pigeon.config import load_config


def test_init_creates_scaffold(tmp_path):
    actions = init_mod.init_repo(tmp_path, project_name="Demo")
    assert (tmp_path / ".pigeon" / "handoff.schema.json").is_file()
    assert (tmp_path / ".pigeon" / "config.yaml").is_file()
    assert (tmp_path / ".pigeon" / "handoffs" / ".gitkeep").is_file()
    assert (tmp_path / "AGENTS.md").is_file()
    assert "Demo" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert any(a.startswith("write ") for a in actions)


def test_init_idempotent(tmp_path):
    init_mod.init_repo(tmp_path)
    actions = init_mod.init_repo(tmp_path)
    assert all("skip" in a or "ok" in a for a in actions if "handoff.schema" in a or "config.yaml" in a or "AGENTS.md" in a)


def test_init_force_overwrites(tmp_path):
    init_mod.init_repo(tmp_path)
    (tmp_path / "AGENTS.md").write_text("hand-edited\n", encoding="utf-8")
    init_mod.init_repo(tmp_path, force=True)
    assert "hand-edited" not in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


def test_init_extends_existing_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    init_mod.init_repo(tmp_path)
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in gi  # preserved
    assert ".pigeon/manifest.json" in gi  # extended


def test_init_then_refresh_and_validate(tmp_path):
    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "m.py").write_text('"""m."""\n\ndef go():\n    return 1\n', encoding="utf-8")
    init_mod.init_repo(tmp_path)
    cfg = load_config(tmp_path)
    m = manifest.write_manifest(cfg)
    mods = json.loads(m.read_text(encoding="utf-8"))["modules"]
    assert any(mod["module"] == "app.m" for mod in mods)


def test_packaged_schema_matches_committed():
    """The template schema must stay byte-identical to the repo's own schema."""
    packaged = files("pigeon").joinpath("templates", "handoff.schema.json").read_text(encoding="utf-8")
    committed = (Path(__file__).resolve().parents[1] / ".pigeon" / "handoff.schema.json").read_text(encoding="utf-8")
    assert packaged == committed


# --------------------------------------------------- schema upgrade (live fix)
def test_upgrade_schema_bumps_a_stale_handoff_schema(repo):
    from pigeon import init as init_mod
    # simulate a repo scaffolded under agentctx 1.0: rewrite the $id to 1.0
    schema = repo.handoff_schema
    text = schema.read_text(encoding="utf-8").replace("handoff-1.1.json", "handoff-1.0.json")
    schema.write_text(text, encoding="utf-8")
    note = init_mod.upgrade_schema(repo)
    assert note and "1.0 -> 1.1" in note
    assert "handoff-1.1.json" in schema.read_text(encoding="utf-8")
    # idempotent: a current schema is left alone
    assert init_mod.upgrade_schema(repo) is None


def test_upgrade_schema_writes_a_missing_one(repo):
    repo.handoff_schema.unlink()
    note = init_mod.upgrade_schema(repo)
    assert note and "wrote missing" in note
    assert repo.handoff_schema.is_file()
