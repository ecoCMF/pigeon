"""Skill projection: canonical playbooks -> runtime-native subagent files."""

from __future__ import annotations

from pigeon import skills


def _playbook(repo, name="security-audit", body="You are a security reviewer.",
              extra_meta=""):
    d = repo.root / ".agentctx" / "memory" / "playbooks"
    d.mkdir(parents=True, exist_ok=True)
    page = d / f"{name}.md"
    page.write_text(
        f"---\nname: {name}\ndescription: Adversarial review.\n{extra_meta}---\n\n{body}\n",
        encoding="utf-8")
    return page


def test_projects_claude_agent_file(repo):
    _playbook(repo)
    out = skills.project_skills(repo)
    assert out["written"] == [".claude/agents/security-audit.md"]
    text = (repo.root / ".claude" / "agents" / "security-audit.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: security-audit\n")
    assert "description: Adversarial review." in text
    assert skills.GEN_MARKER in text
    assert "source: .agentctx/memory/playbooks/security-audit.md" in text
    assert "You are a security reviewer." in text


def test_tools_frontmatter_carries_over(repo):
    _playbook(repo, extra_meta="tools: Read, Grep\n")
    skills.project_skills(repo)
    text = (repo.root / ".claude" / "agents" / "security-audit.md").read_text(encoding="utf-8")
    assert "tools: Read, Grep" in text


def test_pages_without_name_are_not_projected(repo):
    d = repo.root / ".agentctx" / "memory" / "playbooks"
    d.mkdir(parents=True, exist_ok=True)
    (d / "README.md").write_text("# Playbooks\n\nJust prose, no frontmatter.\n",
                                 encoding="utf-8")
    out = skills.project_skills(repo)
    assert out["written"] == [] and out["playbooks"] == 0


def test_handwritten_agent_files_never_clobbered(repo):
    _playbook(repo)
    target = repo.root / ".claude" / "agents" / "security-audit.md"
    target.parent.mkdir(parents=True)
    target.write_text("my precious hand-written agent\n", encoding="utf-8")
    out = skills.project_skills(repo)
    assert out["written"] == []
    assert any("hand-written" in s for s in out["skipped"])
    assert target.read_text(encoding="utf-8") == "my precious hand-written agent\n"


def test_reprojection_updates_generated_files(repo):
    page = _playbook(repo, body="v1 instructions.")
    skills.project_skills(repo)
    page.write_text(page.read_text(encoding="utf-8").replace("v1", "v2"), encoding="utf-8")
    skills.project_skills(repo)
    text = (repo.root / ".claude" / "agents" / "security-audit.md").read_text(encoding="utf-8")
    assert "v2 instructions." in text


def test_frontmatter_tolerates_trailing_whitespace_and_yaml_end(repo):
    d = repo.root / ".agentctx" / "memory" / "playbooks"
    d.mkdir(parents=True, exist_ok=True)
    (d / "spaced.md").write_text(
        "--- \nname: spaced\ndescription: d.\n... \nBody here.\n", encoding="utf-8")
    pages = skills.playbooks(repo)
    assert [p["name"] for p in pages] == ["spaced"]
    assert pages[0]["body"] == "Body here."
