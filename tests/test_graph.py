"""Graph: derived entity graph over handoffs + memory pages, BFS queries."""

from __future__ import annotations

import pytest

from pigeon import distill, graph
from pigeon import handoff as ho


def _seed(repo):
    h1 = ho.build_handoff(sid="s1", frm="Planner", to="Executor", done=[],
                          doing="survey", decisions={"vessel": "MV Aurora"},
                          artifacts=["repo://AGENTS.md"])
    ho.write_handoff(h1, repo)
    entities = repo.root / ".agentctx" / "memory" / "entities"
    entities.mkdir(parents=True)
    (entities / "mv-aurora.md").write_text(
        "# MV Aurora\n\nOperates under [[Ballast Rule 12]] in [[North Sector]].\n",
        encoding="utf-8")
    (entities / "north-sector.md").write_text(
        "# North Sector\n\nPatrolled by [[MV Aurora]].\n", encoding="utf-8")


def test_build_graph_nodes_edges_and_stubs(repo):
    _seed(repo)
    g = graph.build_graph(repo)
    ids = {n["id"] for n in g["nodes"]}
    assert "session:s1" in ids
    assert "decision:vessel" in ids
    assert "artifact:repo://AGENTS.md" in ids
    assert "agent:Planner" in ids and "agent:Executor" in ids
    assert "page:entities/mv-aurora.md" in ids
    # unresolved [[Ballast Rule 12]] becomes a stub — memory worth writing
    assert "stub:ballast-rule-12" in ids

    edges = {(e["src"], e["rel"], e["dst"]) for e in g["edges"]}
    assert ("session:s1", "decided", "decision:vessel") in edges
    assert ("page:entities/mv-aurora.md", "links", "page:entities/north-sector.md") in edges
    assert ("page:entities/north-sector.md", "links", "page:entities/mv-aurora.md") in edges
    decided = [e for e in g["edges"] if e["rel"] == "decided"]
    assert decided[0]["value"] == "MV Aurora"
    assert decided[0]["provenance"].startswith(".agentctx/handoffs/")
    assert graph.graph_path(repo).is_file()


def test_neighborhood_bfs_hops(repo):
    _seed(repo)
    graph.build_graph(repo)
    one = graph.neighborhood(repo, "north sector", hops=1)
    assert "page:entities/north-sector.md" in one["matches"]
    ids1 = {n["id"] for n in one["nodes"]}
    assert "page:entities/mv-aurora.md" in ids1
    assert "stub:ballast-rule-12" not in ids1  # two hops away

    two = graph.neighborhood(repo, "north sector", hops=2)
    ids2 = {n["id"] for n in two["nodes"]}
    assert "stub:ballast-rule-12" in ids2
    assert ids1 < ids2


def test_neighborhood_no_match(repo):
    _seed(repo)
    out = graph.neighborhood(repo, "kraken")
    assert out == {"matches": [], "nodes": [], "edges": []}


def test_distill_rebuilds_graph(repo):
    _seed(repo)
    assert not graph.graph_path(repo).is_file()
    distill.distill_session(repo, "s1")
    assert graph.graph_path(repo).is_file()
    # the distilled session page itself joins the vault graph
    g = graph.load_graph(repo)
    assert any(n["id"] == "page:sessions/s1.md" for n in g["nodes"])


def test_stats(repo):
    _seed(repo)
    s = graph.stats(repo)
    assert s["nodes"] > 0 and s["edges"] > 0
    assert s["by_type"]["session"] == 1
    assert s["by_type"]["stub"] == 1
