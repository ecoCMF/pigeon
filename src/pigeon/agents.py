"""Discover which agent CLIs are installed and usable as coordinate runners.

``pigeon agents`` scans ``$PATH`` for known coding-agent CLIs, probes each one's
version, and cross-checks it against the repo's ``coordinate.runners`` — so you
can see, at a glance, what army you can field on *this* machine, which CLI to
point heavy free-model generation at, and which already have a ready runner.

The registry is the whole maintenance surface; ``$PATH`` keeps it honest. A
``runner_template`` is provided only for invocations pigeon actually knows how to
drive non-interactively — the rest are detected and described, not guessed at.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from .config import Config


@dataclass(frozen=True)
class AgentCLI:
    name: str
    binary: str
    # A ready-to-use coordinate runner argv, or None when the non-interactive
    # invocation is tool-specific and best configured by hand.
    runner_template: list[str] | None
    cost: str            # metered | free-tier | free-models | byo-key | unknown
    reads_agents_md: bool
    note: str
    version_args: tuple[str, ...] = ("--version",)


# Known coding-agent CLIs. Add a row to teach pigeon about a new one.
KNOWN_AGENTS: list[AgentCLI] = [
    AgentCLI("claude", "claude", ["claude", "-p", "{prompt}"],
             "metered", False, "Anthropic Claude Code; loads CLAUDE.md."),
    AgentCLI("opencode", "opencode", ["opencode", "run", "-m", "{model}", "{prompt}"],
             "free-models", True,
             "Many providers via -m provider/model — the free-model army. "
             "List them with `opencode models`."),
    AgentCLI("agy", "agy", ["agy", "-p", "{prompt}"],
             "unknown", True, "Generic -p agent runner."),
    AgentCLI("gemini", "gemini", ["gemini", "-p", "{prompt}"],
             "free-tier", False, "Google Gemini CLI; loads GEMINI.md."),
    AgentCLI("codex", "codex", None,
             "metered", True,
             "OpenAI Codex CLI; reads AGENTS.md. Configure a runner for its "
             "non-interactive mode."),
    AgentCLI("crush", "crush", None,
             "free-models", True, "Charm Crush; multi-provider, reads AGENTS.md."),
    AgentCLI("copilot", "copilot", None,
             "metered", True, "GitHub Copilot CLI; reads AGENTS.md."),
    AgentCLI("cursor-agent", "cursor-agent", None,
             "metered", True, "Cursor CLI agent."),
    AgentCLI("qwen", "qwen", ["qwen", "-p", "{prompt}"],
             "free-tier", True, "Qwen Code CLI (gemini-cli fork); reads AGENTS.md."),
    AgentCLI("aider", "aider", None,
             "byo-key", False, "Aider; bring-your-own-key pair programmer."),
]


def _probe_version(binary: str, version_args: tuple[str, ...]) -> str | None:
    """Best-effort first line of ``<binary> --version`` (short timeout)."""
    try:
        proc = subprocess.run([binary, *version_args], capture_output=True,
                              text=True, timeout=4)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (proc.stdout or proc.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else None


def detect_agents(config: Config | None = None) -> list[dict[str, Any]]:
    """One record per known CLI: installed?, path, version, cost, whether a
    configured ``coordinate.runner`` already drives it."""
    runners = (config.coordinate_cfg.get("runners") if config else {}) or {}
    # A binary counts as "configured" if it appears anywhere in a runner template
    # (it may be wrapped, e.g. `timeout … opencode run …`).
    def _configured(binary: str) -> bool:
        return any(binary in (tmpl or []) for tmpl in runners.values())

    records: list[dict[str, Any]] = []
    for a in KNOWN_AGENTS:
        path = shutil.which(a.binary)
        records.append({
            "name": a.name,
            "binary": a.binary,
            "found": path is not None,
            "path": path,
            "version": _probe_version(a.binary, a.version_args) if path else None,
            "cost": a.cost,
            "reads_agents_md": a.reads_agents_md,
            "runner_template": a.runner_template,
            "configured": _configured(a.binary),
            "note": a.note,
        })
    return records


def format_agents(records: list[dict[str, Any]]) -> str:
    found = [r for r in records if r["found"]]
    missing = [r for r in records if not r["found"]]
    lines = [f"agent CLIs on this machine: {len(found)}/{len(records)} installed"]
    for r in found:
        tags = [r["cost"]]
        if r["runner_template"]:
            tags.append("runner-ready")
        if r["configured"]:
            tags.append("configured")
        ver = f"  ({r['version']})" if r["version"] else ""
        lines.append(f"  ✔ {r['name']:<13}{ver}  [{', '.join(tags)}]")
        lines.append(f"      {r['note']}")
        if r["runner_template"] and not r["configured"]:
            tmpl = ", ".join(r["runner_template"])
            lines.append(f"      add a runner →  {r['name']}: [{tmpl}]")
    if missing:
        lines.append("")
        lines.append("not installed (add to your toolbox to recruit them):")
        lines.append("  " + ", ".join(r["name"] for r in missing))
    army = next((r for r in found if r["name"] == "opencode"), None)
    if army:
        lines.append("")
        lines.append("army tip: opencode is here — point heavy generation at free "
                     "models via a model_pool, then gate with claude/opus.")
    return "\n".join(lines)
