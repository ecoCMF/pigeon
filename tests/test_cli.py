"""CLI entry point: version, help, exit codes (audit gap L3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pigeon import __version__
from pigeon.cli import build_parser, main


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert f"pigeon {__version__}" in capsys.readouterr().out
    # the real anti-drift invariant: code version == packaging version
    import tomllib
    pyproject = tomllib.loads(Path(__file__).resolve().parents[1]
                              .joinpath("pyproject.toml").read_text())
    assert __version__ == pyproject["project"]["version"]


def test_help_lists_all_subcommands(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    for cmd in ("init", "refresh", "handoff", "retrieve", "metrics", "demo",
                "plan", "coordinate", "status", "runs", "distill", "pack",
                "graph", "mcp"):
        assert cmd in out


def test_runs_on_empty_repo(repo, capsys):
    assert main(["--root", str(repo.root), "runs"]) == 0
    assert "no coordination runs" in capsys.readouterr().out


def test_tasks_file_error_is_friendly(repo, tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("sid: s\ntasks: []\n", encoding="utf-8")
    assert main(["--root", str(repo.root), "coordinate", str(bad)]) == 2
    assert "tasks file error" in capsys.readouterr().err
