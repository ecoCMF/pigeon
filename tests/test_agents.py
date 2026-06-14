"""Agent-CLI discovery: detect which runners are available on this machine."""

from __future__ import annotations

import shutil

from pigeon import agents


def test_detect_marks_found_versions_and_configured(repo, monkeypatch):
    # Simulate claude + opencode installed; everything else absent.
    installed = {"claude": "/usr/bin/claude", "opencode": "/usr/bin/opencode"}
    monkeypatch.setattr(shutil, "which", lambda b: installed.get(b))
    monkeypatch.setattr(agents, "_probe_version", lambda b, a: f"{b} 9.9")

    by = {r["name"]: r for r in agents.detect_agents(repo)}
    assert by["claude"]["found"] and by["claude"]["version"] == "claude 9.9"
    assert by["codex"]["found"] is False and by["codex"]["version"] is None
    # claude + opencode are in the repo's default coordinate.runners
    assert by["claude"]["configured"] is True
    assert by["opencode"]["configured"] is True
    # an absent CLI is never marked configured
    assert by["aider"]["configured"] is False
    # runner-ready CLIs carry a template; unknown-invocation ones do not
    assert by["claude"]["runner_template"] and by["codex"]["runner_template"] is None


def test_configured_detects_binary_even_when_wrapped(repo, monkeypatch):
    # opencode wrapped in `timeout … env … opencode run …` still counts.
    repo.coordinate_cfg["runners"]["oc"] = [
        "timeout", "600", "env", "X=1", "opencode", "run", "-m", "{model}", "{prompt}"]
    monkeypatch.setattr(shutil, "which", lambda b: "/usr/bin/opencode"
                        if b == "opencode" else None)
    monkeypatch.setattr(agents, "_probe_version", lambda b, a: None)
    by = {r["name"]: r for r in agents.detect_agents(repo)}
    assert by["opencode"]["configured"] is True


def test_format_lists_found_and_missing(repo, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda b: "/x" if b == "opencode" else None)
    monkeypatch.setattr(agents, "_probe_version", lambda b, a: None)
    out = agents.format_agents(agents.detect_agents(repo))
    assert "1/" in out and "installed" in out
    assert "opencode" in out and "not installed" in out
    assert "free-model army" in out or "army tip" in out  # opencode highlighted
