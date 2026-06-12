"""Token counting and accounting."""

from __future__ import annotations

from pigeon import handoff as ho
from pigeon import retrieval as rt
from pigeon import tokens as tk


def test_count_empty_is_zero():
    assert tk.count_tokens("") == 0


def test_heuristic_deterministic():
    s = "validate handoff against the schema, twice"
    assert tk.count_tokens(s) == tk.count_tokens(s)
    assert tk.count_tokens(s) > 0


def test_handoff_pointers_beat_inlining(repo):
    h = ho.build_handoff(
        sid="s1", frm="Planner", to="Executor",
        done=["analyze"], doing="implement",
        artifacts=["repo://AGENTS.md", "repo://src/pkg/alpha.py"],
    )
    ev = tk.account_handoff(repo, h, record_event=False)
    assert ev["baseline_tokens"] > ev["actual_tokens"]
    assert ev["saved_tokens"] == ev["baseline_tokens"] - ev["actual_tokens"]


def test_retrieval_slices_not_more_than_whole_files(repo):
    results = rt.query("public_alpha widget", repo, top_k=3)
    ev = tk.account_retrieval(repo, "public_alpha widget", results, record_event=False)
    assert ev["baseline_tokens"] >= ev["actual_tokens"]


def test_record_and_summarize(repo):
    h = ho.build_handoff(sid="s1", frm="A", to="B", done=[], doing="x",
                         artifacts=["repo://AGENTS.md"])
    tk.account_handoff(repo, h)
    results = rt.query("alpha", repo, top_k=2)
    tk.account_retrieval(repo, "alpha", results)
    summary = tk.summarize(repo)
    assert summary["overall"]["events"] == 2
    assert "handoff" in summary["by_kind"]
    assert "retrieval" in summary["by_kind"]
    assert summary["overall"]["baseline_tokens"] >= summary["overall"]["actual_tokens"]


def test_prune_metrics_keeps_newest(repo):
    from pigeon import tokens as tk
    for i in range(10):
        tk.record(repo, {"kind": "t", "n": i, "actual_tokens": 1,
                         "baseline_tokens": 1, "saved_tokens": 0})
    before, after = tk.prune_metrics(repo, keep=3)
    assert (before, after) == (10, 3)
    import json
    kept = [json.loads(l)["n"] for l in repo.metrics.read_text().splitlines()]
    assert kept == [7, 8, 9]


def test_punctuation_runs_not_overcounted():
    from pigeon.tokens import _heuristic_tokens
    assert _heuristic_tokens("...") == 1          # was 3 before the fix
    assert _heuristic_tokens("):") == 1
