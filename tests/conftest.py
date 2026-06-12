"""Shared fixtures: a self-contained temporary repo for each test."""

from __future__ import annotations

from pathlib import Path

import pytest

from pigeon.config import Config, load_config

# The fixture deliberately builds a LEGACY (.agentctx) repo: every test that
# runs through it proves the pre-rename contract dir keeps working forever.
_REAL_SCHEMA = Path(__file__).resolve().parents[1] / ".pigeon" / "handoff.schema.json"

_ALPHA = '''\
"""Alpha module — does alpha things."""


def public_alpha(x):
    """Compute alpha."""
    return x + 1


def _private_alpha():
    return 0


class Widget:
    """A widget."""

    def spin(self):
        return "spin"

    def _internal(self):
        return None
'''

_AGENTS = """\
# AGENTS.md — test canonical

Goal: exercise agentctx in an isolated repo.
Architecture: alpha module with a public function and a Widget class.
"""

_PYPROJECT = """\
[project]
name = "fixture"
version = "0.0.0"

[project.scripts]
fixture = "pkg.cli:main"
"""


@pytest.fixture
def repo(tmp_path: Path) -> Config:
    root = tmp_path
    actx = root / ".agentctx"
    (actx / "handoffs").mkdir(parents=True)
    (actx / "handoff.schema.json").write_text(_REAL_SCHEMA.read_text(encoding="utf-8"), encoding="utf-8")
    # pin generated pointers so sync tests don't depend on what's on PATH;
    # auto-detection is exercised explicitly in test_context.py
    (actx / "config.yaml").write_text(
        "paths:\n  generated: [CLAUDE.md, GEMINI.md]\n", encoding="utf-8")
    (root / "AGENTS.md").write_text(_AGENTS, encoding="utf-8")
    (root / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    pkg = root / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""pkg package."""\n', encoding="utf-8")
    (pkg / "alpha.py").write_text(_ALPHA, encoding="utf-8")
    return load_config(root)
