"""``pigeon init`` — scaffold pigeon into any repository.

Idempotent: existing files are left untouched unless ``force`` is set. Templates
(schema, config, AGENTS.md) ship with the package, so init works from any
install — not just from a checkout of pigeon itself.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from .config import CONTRACT_DIR, contract_dirname

_GITIGNORE_HEADER = "# pigeon (generated artifacts)"
def _gitignore_entries(dirname: str) -> list[str]:
    return [
        f"{dirname}/manifest.json",
        f"{dirname}/metrics.jsonl",
        f"{dirname}/handoffs/*.json",
        f"{dirname}/coordinate/",
        f"{dirname}/context/",
        f"{dirname}/vector/",
    ]

_HOOK = """\
#!/usr/bin/env bash
# Installed by `pigeon init --with-hook`: keep generated context in sync.
set -euo pipefail
if command -v pigeon >/dev/null 2>&1; then
    pigeon refresh >/dev/null
else
    python -m pigeon.cli refresh >/dev/null
fi
git add AGENTS.md CLAUDE.md GEMINI.md >/dev/null 2>&1 || true
"""


def _template(name: str) -> str:
    return files("pigeon").joinpath("templates", name).read_text(encoding="utf-8")


def _write(path: Path, content: str, *, force: bool, actions: list[str], label: str) -> None:
    existed = path.exists()
    if existed and not force:
        actions.append(f"skip   {label} (exists)")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    actions.append(f"{'force ' if existed else 'write '}{label}")


_PLAYBOOKS_README = """\
# Playbooks — procedural memory

One Markdown file per routine: how releases are cut, how spatial data is
validated, how a survey is processed. Written by humans or promoted from
distilled sessions; agents find them via `pigeon retrieve --scope memory`
and `pigeon pack`. Keep each playbook short, imperative, and current —
this directory is committed and shared between humans and agents.

Pages that declare YAML frontmatter become *projectable skills*: `pigeon
refresh` generates each runtime's native subagent file from them (Claude
Code: `.claude/agents/<name>.md`), so a `crew:` entry's `skill:` name
resolves to the same canonical page on every CLI:

    ---
    name: security-audit
    description: Adversarial security review of changed code.
    ---
    You are a security reviewer for this repository. ...
"""


def _ensure_playbooks(root: Path, actions: list[str], dirname: str) -> None:
    readme = root / dirname / "memory" / "playbooks" / "README.md"
    if readme.exists():
        actions.append("ok     memory/playbooks/")
        return
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text(_PLAYBOOKS_README, encoding="utf-8")
    actions.append("create memory/playbooks/README.md")


def _ensure_gitignore(root: Path, actions: list[str], dirname: str) -> None:
    gi = root / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.is_file() else ""
    missing = [e for e in _gitignore_entries(dirname) if e not in existing.splitlines()]
    if not missing:
        actions.append("ok     .gitignore (already covers pigeon artifacts)")
        return
    block = ("" if existing.endswith("\n") or not existing else "\n") + "\n" + _GITIGNORE_HEADER + "\n" + "\n".join(missing) + "\n"
    with gi.open("a", encoding="utf-8") as fh:
        fh.write(block)
    actions.append(f"update .gitignore (+{len(missing)} entries)")


def _install_hook(root: Path, actions: list[str], *, force: bool) -> None:
    hooks_dir = root / ".git" / "hooks"
    if not (root / ".git").is_dir():
        actions.append("skip   pre-commit hook (.git not found)")
        return
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-commit"
    if hook.exists() and not force:
        actions.append("skip   pre-commit hook (exists; use --force to replace)")
        return
    hook.write_text(_HOOK, encoding="utf-8")
    hook.chmod(0o755)
    actions.append("write  .git/hooks/pre-commit")


def init_repo(
    root: Path | str,
    *,
    force: bool = False,
    with_hook: bool = False,
    project_name: str | None = None,
) -> list[str]:
    """Scaffold pigeon into ``root``. Returns a human-readable action log."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    actions: list[str] = []

    # New repos are pigeon-native (.pigeon/); a repo already carrying the
    # legacy .agentctx/ keeps it — the dirname is the contract, never churned.
    dirname = contract_dirname(root)
    actions.append(f"dir    {dirname}/" + ("" if dirname == CONTRACT_DIR
                                           else " (legacy, honored)"))

    (root / dirname / "handoffs").mkdir(parents=True, exist_ok=True)
    gitkeep = root / dirname / "handoffs" / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.write_text("", encoding="utf-8")

    _write(root / dirname / "handoff.schema.json", _template("handoff.schema.json"),
           force=force, actions=actions, label=f"{dirname}/handoff.schema.json")
    config_text = _template("config.yaml").replace(".agentctx/", f"{dirname}/")
    _write(root / dirname / "config.yaml", config_text,
           force=force, actions=actions, label=f"{dirname}/config.yaml")

    agents_md = (_template("AGENTS.md")
                 .replace("{PROJECT}", project_name or root.name)
                 .replace(".agentctx/", f"{dirname}/"))
    _write(root / "AGENTS.md", agents_md, force=force, actions=actions, label="AGENTS.md")

    _ensure_gitignore(root, actions, dirname)
    _ensure_playbooks(root, actions, dirname)
    if with_hook:
        _install_hook(root, actions, force=force)
    return actions
