"""Configuration loading and repo-root discovery.

Config lives at ``<contract-dir>/config.yaml``, where the contract directory
is ``.pigeon/`` in pigeon-native repositories and ``.agentctx/`` in
repositories scaffolded before the rename — the legacy name is honored
forever (deployed consumers never break). It is YAML because it is
human-edited (the one place YAML is appropriate). The cross-model *contract*
— handoffs — is strictly JSON; see the decision record in AGENTS.md.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONTRACT_DIR = ".pigeon"            # native name for new repositories
LEGACY_CONTRACT_DIR = ".agentctx"   # pre-rename repos: honored forever


def contract_dirname(root: Path) -> str:
    """The contract directory this repository actually uses."""
    if (root / CONTRACT_DIR).is_dir():
        return CONTRACT_DIR
    if (root / LEGACY_CONTRACT_DIR).is_dir():
        return LEGACY_CONTRACT_DIR
    return CONTRACT_DIR  # fresh repos are pigeon-native


CONFIG_RELPATH = f"{LEGACY_CONTRACT_DIR}/config.yaml"  # back-compat constant

# Defaults are deep-merged under the on-disk config, so a partial or absent
# config.yaml still yields a fully-populated, working configuration.
def default_config(contract_dir: str = LEGACY_CONTRACT_DIR) -> dict[str, Any]:
    d = contract_dir
    return {
        "schema_version": "1.0",
        "paths": {
            "canonical": "AGENTS.md",
            "generated": "auto",  # detect installed CLIs; or an explicit list
            "manifest": f"{d}/manifest.json",
            "handoffs_dir": f"{d}/handoffs",
            "metrics": f"{d}/metrics.jsonl",
            "handoff_schema": f"{d}/handoff.schema.json",
            "memory_dir": f"{d}/memory",
            "context_dir": f"{d}/context",
        },
        "manifest": {
            "include": ["src/**/*.py", "*.py"],
            "exclude": ["**/__pycache__/**"],
            "decisions": {},
            "owners": {},
        },
        "retrieval": {
            "include": ["**/*.py", "**/*.md", "**/*.json", "**/*.toml", "**/*.sh"],
            "exclude": [
                "**/__pycache__/**",
                ".git/**",
                ".venv/**",
                "venv/**",
                "**/*.egg-info/**",
                f"{d}/metrics.jsonl",
            ],
            "max_file_bytes": 200_000,
            "chunk_lines": 40,
            "chunk_overlap": 10,
            "default_top_k": 5,
            "ripgrep_path": None,
            "vector": {
                "enabled": False,
                "model": "all-MiniLM-L6-v2",
                "store_dir": f"{d}/vector",
            },
        },
        "resolve": {
            "allow_s3": False,
        },
        "tokens": {
            "encoding": "cl100k_base",
        },
        # Skill projection: playbook pages -> each runtime's native subagent files.
        "skills": {
            "targets": {
                "claude": ".claude/agents",
            },
        },
        "coordinate": {
            "log_dir": f"{d}/coordinate/logs",
            "runs_dir": f"{d}/coordinate/runs",
            "events_dir": f"{d}/coordinate/events",
            "worktrees_dir": f"{d}/coordinate/worktrees",
            "parallel_limit": 4,
            # Runner for tasks that don't name one. A string assigns it to
            # every unassigned task; a LIST round-robins across them (spread
            # load off your metered CLI); null (default) REFUSES unassigned
            # tasks — after one too many surprise bills, implicit routing to
            # an expensive runner is not a default this tool will ever have.
            "default_runner": None,
            # Run `pigeon distill <sid>` automatically when a coordinate run ends.
            "auto_distill": False,
            # argv templates; placeholders: {prompt} {handoff} {root} {task_id} {sid}
            "runners": {
                "claude": ["claude", "-p", "{prompt}"],
                "agy": ["agy", "-p", "{prompt}"],
                "opencode": ["opencode", "run", "{prompt}"],
            },
            # Appended only when the operator passes --skip-permissions.
            "skip_permissions_flags": {
                "claude": ["--dangerously-skip-permissions"],
                "agy": ["--dangerously-skip-permissions"],
                "opencode": [],
            },
            # Appended with --telemetry (or per-task `telemetry: true`): makes the
            # child CLI emit a machine-readable usage report we mine for *measured*
            # token consumption. Output with a `usage` object is parsed regardless.
            # Appended with --telemetry / per-task telemetry: true. Only a
            # runner whose CLI actually emits a usage report should get a
            # flag here — a wrong flag makes the runner print help and exit.
            # claude -p --output-format json is verified; add your runner's
            # real usage flag (agy/opencode have none by default).
            "telemetry_flags": {
                "claude": ["--output-format", "json"],
                "agy": [],
                "opencode": [],
            },
            # Strict mode: when set, only these env vars (plus a functional
            # baseline: PATH/HOME/...) reach spawned agents. None = inherit all.
            "env_allowlist": None,
            "safety": {
                # Agents may modify the folder only in a .git checkout with
                # pigeon initialized (revertible + contract-validated).
                "require_repo_setup": True,
                # The subprocess fan-out is only supported on Linux.
                "require_linux": True,
                # pip install/remove & library changes only inside a conda env,
                # virtualenv, or container — never the system interpreter.
                "require_isolated_env_for_packages": True,
                # Children inherit AGENTCTX_DEPTH; a child running `coordinate`
                # again past this depth is refused (no agent fork-bombs).
                "max_depth": 1,
            },
            # Hard spend ceilings for a run, measured via child telemetry. Once
            # exceeded, no further tasks launch (running ones finish). None = off.
            "budget": {
                "tokens": None,
                "usd": None,
            },
        },
    }


DEFAULT_CONFIG: dict[str, Any] = default_config(LEGACY_CONTRACT_DIR)


def _deep_merge(base: dict[str, Any], override: dict[str, Any],
                _depth: int = 0) -> dict[str, Any]:
    """Recursively merge ``override`` onto a copy of ``base``.

    Depth-guarded: a YAML anchor cycle (`a: &x {b: *x}`) must fail loudly
    instead of recursing forever.
    """
    if _depth > 64:
        raise ValueError("config nesting deeper than 64 levels — cyclic YAML anchors?")
    out = copy.deepcopy(base)
    for key, val in (override or {}).items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge(out[key], val, _depth + 1)
        else:
            out[key] = copy.deepcopy(val)
    return out


def find_repo_root(start: Path | str | None = None) -> Path:
    """Walk upward from ``start`` (default cwd) looking for a repo root.

    A repo root is the nearest ancestor containing ``.pigeon/``,
    ``.agentctx/`` (legacy), or ``.git``. Falls back to ``start`` itself.
    """
    cur = Path(start).resolve() if start else Path.cwd().resolve()
    if cur.is_file():
        cur = cur.parent
    for candidate in (cur, *cur.parents):
        if ((candidate / CONTRACT_DIR).is_dir()
                or (candidate / LEGACY_CONTRACT_DIR).is_dir()
                or (candidate / ".git").exists()):
            return candidate
    return cur


@dataclass(frozen=True)
class Config:
    """Resolved configuration bound to a repository root.

    Path accessors return absolute :class:`pathlib.Path` objects rooted at
    :attr:`root`, so callers never join paths by hand.
    """

    root: Path
    data: dict[str, Any]

    @property
    def contract_dir(self) -> Path:
        """The repo's contract directory (.pigeon, or legacy .agentctx)."""
        return self.root / contract_dirname(self.root)

    # -- path helpers ---------------------------------------------------
    def _p(self, relpath: str) -> Path:
        return (self.root / relpath).resolve()

    @property
    def canonical(self) -> Path:
        return self._p(self.data["paths"]["canonical"])

    @property
    def generated(self) -> list[Path]:
        from . import context
        return context.resolve_generated(self)

    @property
    def manifest(self) -> Path:
        return self._p(self.data["paths"]["manifest"])

    @property
    def handoffs_dir(self) -> Path:
        return self._p(self.data["paths"]["handoffs_dir"])

    @property
    def metrics(self) -> Path:
        return self._p(self.data["paths"]["metrics"])

    @property
    def handoff_schema(self) -> Path:
        return self._p(self.data["paths"]["handoff_schema"])

    @property
    def memory_dir(self) -> Path:
        return self._p(self.data["paths"]["memory_dir"])

    @property
    def context_dir(self) -> Path:
        return self._p(self.data["paths"]["context_dir"])

    # -- section accessors ---------------------------------------------
    @property
    def manifest_cfg(self) -> dict[str, Any]:
        return self.data["manifest"]

    @property
    def retrieval_cfg(self) -> dict[str, Any]:
        return self.data["retrieval"]

    @property
    def resolve_cfg(self) -> dict[str, Any]:
        return self.data["resolve"]

    @property
    def tokens_cfg(self) -> dict[str, Any]:
        return self.data["tokens"]

    @property
    def coordinate_cfg(self) -> dict[str, Any]:
        return self.data["coordinate"]

    @property
    def skills_cfg(self) -> dict[str, Any]:
        return self.data["skills"]

    @property
    def coordinate_log_dir(self) -> Path:
        return self._p(self.data["coordinate"]["log_dir"])

    @property
    def coordinate_runs_dir(self) -> Path:
        return self._p(self.data["coordinate"]["runs_dir"])

    @property
    def coordinate_worktrees_dir(self) -> Path:
        return self._p(self.data["coordinate"]["worktrees_dir"])

    @property
    def coordinate_events_dir(self) -> Path:
        return self._p(self.data["coordinate"]["events_dir"])


def load_config(root: Path | str | None = None) -> Config:
    """Load and validate-merge the configuration for a repository.

    ``root`` may point anywhere inside the repo; the root is discovered.
    """
    repo_root = find_repo_root(root)
    dirname = contract_dirname(repo_root)
    cfg_path = repo_root / dirname / "config.yaml"
    on_disk: dict[str, Any] = {}
    if cfg_path.is_file():
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{cfg_path} must contain a YAML mapping at the top level")
        on_disk = loaded
    merged = _deep_merge(default_config(dirname), on_disk)
    return Config(root=repo_root, data=merged)
