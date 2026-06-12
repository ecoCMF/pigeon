"""Retrieval relevance, bounding, and the optional lexical layer.

These tests assert the BM25 path, which works with or without ripgrep, so they
pass in CI where ``rg`` may be absent. A separate test exercises the lexical
layer only when ripgrep is available.
"""

from __future__ import annotations

import pytest

from pigeon import retrieval as rt


def test_relevant_top_result(repo):
    results = rt.query("public_alpha widget spin", repo, top_k=3)
    assert results, "expected at least one hit"
    assert any("alpha.py" in r.source for r in results)
    assert results[0].source.endswith("alpha.py")


def test_bounded_output(repo):
    results = rt.query("alpha", repo, top_k=2)
    assert len(results) <= 2


def test_empty_on_genuine_miss(repo):
    assert rt.query("qzzxnonexistenttoken9999", repo, top_k=5) == []


def test_empty_query(repo):
    assert rt.query("   ", repo) == []


def test_tokenize_splits_camel_case():
    assert rt.tokenize("publicAlpha Widget") == ["public", "alpha", "widget"]


def test_lexical_layer_when_ripgrep_present(repo):
    if rt.find_ripgrep(repo) is None:
        pytest.skip("ripgrep not available")
    results = rt.query("public_alpha", repo, top_k=3)
    assert any(r.lexical_hits > 0 for r in results)


def test_vector_flag_errors_without_extra(repo):
    repo.retrieval_cfg["vector"]["enabled"] = True
    with pytest.raises((RuntimeError, NotImplementedError)):
        rt.query("alpha", repo, top_k=1)


# ----------------------------------------------------------- scopes / since
def test_scope_history_finds_handoffs_and_runs(repo):
    from pigeon import handoff as ho
    from pigeon import retrieval

    h = ho.build_handoff(sid="hist", frm="Planner", to="Executor",
                         done=["x"], doing="implement the zorbulator")
    ho.write_handoff(h, repo)

    hits = retrieval.query("zorbulator", repo, scope="history")
    assert hits and all(r.source.startswith(".agentctx/handoffs/") for r in hits)
    # the same event is invisible to the code scope...
    assert not any("handoffs" in r.source
                   for r in retrieval.query("zorbulator", repo, scope="code"))
    # ...but visible in the default union scope
    assert any("handoffs" in r.source
               for r in retrieval.query("zorbulator", repo, scope="all"))


def test_scope_memory_finds_distilled_sessions(repo):
    from pigeon import distill, retrieval
    from pigeon import handoff as ho

    h = ho.build_handoff(sid="mem", frm="A", to="B", done=[], doing="work",
                         decisions={"flux_capacitor": "enabled"})
    ho.write_handoff(h, repo)
    distill.distill_session(repo, "mem")

    hits = retrieval.query("flux capacitor", repo, scope="memory")
    assert hits and all(r.source.startswith(".agentctx/memory/") for r in hits)


def test_since_filters_old_files(repo):
    import os
    import time
    from pigeon import retrieval

    from datetime import datetime, timedelta, timezone

    target = repo.root / "src" / "pkg" / "alpha.py"
    old = time.time() - 90 * 86400
    os.utime(target, (old, old))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    fresh = retrieval.query("alpha widget", repo, scope="code", since=cutoff)
    assert not any(r.source.endswith("alpha.py") for r in fresh)
    anytime = retrieval.query("alpha widget", repo, scope="code")
    assert any(r.source.endswith("alpha.py") for r in anytime)


def test_bad_scope_and_since_raise(repo):
    import pytest
    from pigeon import retrieval

    with pytest.raises(ValueError, match="unknown scope"):
        retrieval.query("x", repo, scope="universe")
    with pytest.raises(ValueError, match="invalid --since"):
        retrieval.query("alpha", repo, since="not-a-date")
