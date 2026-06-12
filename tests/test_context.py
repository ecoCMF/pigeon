"""Canonical context generation and no-drift guarantee."""

from __future__ import annotations

from pigeon import context


def test_sync_creates_pointer_files(repo):
    written = context.sync_context(repo)
    names = {p.name for p in written}
    assert names == {"CLAUDE.md", "GEMINI.md"}
    for p in written:
        assert p.is_file()


def test_generated_files_duplicate_no_prose(repo):
    context.sync_context(repo)
    body = (repo.root / "CLAUDE.md").read_text(encoding="utf-8")
    # The unique sentence from AGENTS.md must NOT appear in the pointer file.
    assert "exercise agentctx in an isolated repo" not in body
    assert "AGENTS.md" in body  # it points, instead


def test_status_detects_staleness(repo):
    context.sync_context(repo)
    assert all(state == "ok" for _, state in context.context_status(repo))
    # editing AGENTS.md changes the fingerprint -> generated files go stale
    (repo.root / "AGENTS.md").write_text("# AGENTS.md — changed\n", encoding="utf-8")
    assert all(state == "stale" for _, state in context.context_status(repo))
    # refresh restores
    context.sync_context(repo)
    assert all(state == "ok" for _, state in context.context_status(repo))


def test_fingerprint_propagates_to_all(repo):
    context.sync_context(repo)
    import re
    fps = []
    for name in ("CLAUDE.md", "GEMINI.md"):
        text = (repo.root / name).read_text(encoding="utf-8")
        fps.append(re.search(r"sha256:([0-9a-f]+)", text).group(1))
    assert len(set(fps)) == 1  # identical source fingerprint everywhere


def test_deep_merge_depth_guard():
    from pigeon.config import _deep_merge
    base, override = {}, {}
    base["self"] = base          # both sides cyclic: recursion would never end
    override["self"] = override
    import pytest
    with pytest.raises(ValueError, match="cyclic"):
        _deep_merge(base, override)


# ------------------------------------------------- CLI auto-detection (PATH)
def test_detect_pointer_files_maps_installed_clis(monkeypatch):
    installed = {"claude", "gemini", "codex", "opencode"}
    monkeypatch.setattr(context.shutil, "which",
                        lambda b: f"/usr/bin/{b}" if b in installed else None)
    # codex/opencode read AGENTS.md natively -> no pointer file
    assert context.detect_pointer_files() == ["CLAUDE.md", "GEMINI.md"]


def test_detect_nothing_when_only_native_clis(monkeypatch):
    monkeypatch.setattr(context.shutil, "which",
                        lambda b: "/usr/bin/codex" if b == "codex" else None)
    assert context.detect_pointer_files() == []


def test_auto_generated_follows_path(repo, monkeypatch):
    # repo fixture pins an explicit list; override to "auto" and detect
    repo.data["paths"]["generated"] = "auto"
    monkeypatch.setattr(context.shutil, "which",
                        lambda b: "/usr/bin/gemini" if b == "gemini" else None)
    written = {p.name for p in context.sync_context(repo)}
    assert written == {"GEMINI.md"}


def test_explicit_list_overrides_detection(repo, monkeypatch):
    # nothing installed, but the explicit list still generates both
    monkeypatch.setattr(context.shutil, "which", lambda b: None)
    written = {p.name for p in context.sync_context(repo)}
    assert written == {"CLAUDE.md", "GEMINI.md"}


def test_pointer_filenames_built_from_registry():
    assert context.POINTER_FILENAMES == ("CLAUDE.md", "GEMINI.md")
