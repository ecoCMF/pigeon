"""Phase D: the review playbooks project, and the edit-review-verify example
is a well-formed topology (edit -> [review, verify] -> concord)."""

from __future__ import annotations

from pathlib import Path

from pigeon import coordinate as co
from pigeon import skills

_ROOT = Path(__file__).resolve().parents[1]
_PLAYBOOKS = _ROOT / ".pigeon" / "memory" / "playbooks"
_EXAMPLE = _ROOT / "docs" / "design" / "examples" / "edit-review-verify.tasks.yaml"


def test_review_playbooks_are_projectable():
    cr = skills.parse_playbook(_PLAYBOOKS / "code-reviewer.md")
    rc = skills.parse_playbook(_PLAYBOOKS / "review-concordance.md")
    assert cr is not None and cr["name"] == "code-reviewer"
    assert rc is not None and rc["name"] == "review-concordance"
    assert cr["meta"].get("description") and rc["meta"].get("description")
    # the conventions live in the body (no code parses them)
    assert "state.artifacts" in cr["body"] and "findings" in cr["body"]
    assert "accepted" in rc["body"] and "verdict" in rc["body"]
    # they render to the Claude subagent format without writing anything
    cr["source"] = ".pigeon/memory/playbooks/code-reviewer.md"  # set by playbooks()
    rendered = skills._render_claude(cr)
    assert rendered.startswith("---\nname: code-reviewer\n")
    assert skills.GEN_MARKER in rendered


def test_edit_review_verify_example_is_valid_topology():
    spec = co.load_tasks(_EXAMPLE)
    assert co.compute_waves(spec["tasks"]) == [["edit"], ["review", "verify"], ["concord"]]
    by_id = {t["id"]: t for t in spec["tasks"]}
    # edit is isolated -> its diff is materialized to the shared tree (Phase B)
    assert by_id["edit"]["isolation"] == "worktree"
    # review/verify receive that diff by glob (Phase C); concord receives artifacts
    assert any("diffs/" in p for p in by_id["review"]["receives"])
    assert any("diffs/" in p for p in by_id["verify"]["receives"])
    assert len(by_id["concord"]["receives"]) == 2
    # the playbooks are wired via crew skills (resolved at runtime, not by code)
    assert by_id["review"]["crew"]["skills"] == ["code-reviewer"]
    assert by_id["concord"]["crew"]["skills"] == ["review-concordance"]
