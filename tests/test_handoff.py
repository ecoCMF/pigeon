"""Handoff build / validate / serialize / append."""

from __future__ import annotations

import pytest

from pigeon import handoff as ho


def _valid(**over):
    base = dict(
        sid="s1", frm="Planner", to="Executor",
        done=["analyze", "design"], doing="implement",
        artifacts=["repo://AGENTS.md"], decisions={"auth": "oauth2_pkce"},
    )
    base.update(over)
    return ho.build_handoff(**base)


def test_build_and_validate_roundtrip(repo):
    h = _valid()
    ho.validate_handoff(h, repo)  # should not raise
    text = ho.serialize_handoff(h)
    assert text.endswith("\n")
    # canonical: stable, sorted keys
    assert ho.serialize_handoff(h) == text


def test_missing_required_field_rejected(repo):
    bad = {"schema_version": "1.0", "sid": "x", "from": "A", "to": "B", "state": {"done": []}}
    with pytest.raises(ho.HandoffValidationError) as exc:
        ho.validate_handoff(bad, repo)
    assert "doing" in str(exc.value)


def test_additional_property_rejected(repo):
    h = _valid()
    h["surprise"] = 1
    with pytest.raises(ho.HandoffValidationError):
        ho.validate_handoff(h, repo)


def test_bad_schema_version_pattern_rejected(repo):
    h = _valid(schema_version="one-point-oh")
    with pytest.raises(ho.HandoffValidationError):
        ho.validate_handoff(h, repo)


def test_append_only_numbering(repo):
    p1 = ho.write_handoff(_valid(), repo)
    p2 = ho.write_handoff(_valid(), repo)
    p3 = ho.write_handoff(_valid(sid="other"), repo)
    assert p1.name == "s1-1.json"
    assert p2.name == "s1-2.json"
    assert p3.name == "other-1.json"
    # round-trips and re-validates on receipt
    loaded = ho.load_handoff(p2, repo)
    assert loaded["sid"] == "s1"


def test_plan_example_handoff_validates(repo):
    example = {
        "schema_version": "1.0", "sid": "sess_42", "from": "Planner", "to": "Executor",
        "state": {"done": ["analyze", "design"], "doing": "implement",
                  "artifacts": ["repo://.agentctx/plans/design_v3.json"],
                  "decisions": {"auth_flow": "oauth2_pkce"}},
        "rag": {"query": "code patterns for design_v3", "top_k": 2},
        "constraints": {"max_tokens": 8000, "fail_fast": True},
        "context_ref": "manifest@HEAD",
    }
    ho.validate_handoff(example, repo)


def test_schema_rejects_empty_crew_arrays(repo):
    h = _valid()
    h["crew"] = {"skills": []}
    with pytest.raises(ho.HandoffValidationError):
        ho.validate_handoff(h, repo)
    h["crew"] = {"subagents": []}
    with pytest.raises(ho.HandoffValidationError):
        ho.validate_handoff(h, repo)


def test_claim_path_is_collision_safe(repo, tmp_path):
    # claim the same slot pattern twice: second claim must move to the next n
    a = ho.claim_path(tmp_path, lambda n: f"s-{n}.json")
    b = ho.claim_path(tmp_path, lambda n: f"s-{n}.json")
    assert a.name == "s-1.json" and b.name == "s-2.json"
