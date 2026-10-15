"""Boundary pins for MCP tool fixes: structured cache bypass, find_definition
cap signal, visualize_graph freshness, and read-only recall ref gating."""
from __future__ import annotations

from cairn.mcp_server import tools_graph
from cairn.mcp_server import tools_memory
from cairn.mcp_server.structured import ImpactAnalysisResult


class _NoBanner:
    """Freshness stub whose banner() is always empty."""

    def banner(self):
        return ""


class _Stale:
    """Freshness stub whose banner() always fires."""

    def banner(self):
        return "STALE-BANNER"


def test_impact_analysis_cached_structured_honors_structured(fresh_db, monkeypatch):
    """cached=True + structured=True must return the typed result from the live
    traversal, never the cache's prose (the cache lacks depth/cycle/test data)."""
    from cairn.graph import dataflow, queries

    monkeypatch.setattr(tools_graph, "_conn", lambda: fresh_db)
    monkeypatch.setattr(tools_graph, "_fresh_graph", lambda conn: _NoBanner())
    monkeypatch.setattr(tools_graph, "_repo_of", lambda conn, name: "r")
    monkeypatch.setattr(
        dataflow,
        "get_dataflow",
        lambda conn, name: {
            "symbol": name, "repo": "r",
            "within_repo": ["a", "b"], "cross_repo": ["other"], "updated": "",
        },
    )
    live_calls: list[str] = []
    monkeypatch.setattr(
        queries,
        "impact_analysis",
        lambda conn, name, **k: live_calls.append(name)
        or {"impacted": [], "total": 0, "truncated": False, "cycles": []},
    )
    monkeypatch.setattr(queries, "cross_repo_deps", lambda conn, repo: {"dependents": []})

    result = tools_graph.impact_analysis("sym", cached=True, structured=True)

    assert isinstance(result, ImpactAnalysisResult), (
        "structured=True must never fall back to prose on a cache hit"
    )
    assert live_calls == ["sym"], "structured output requires the live traversal"


def test_impact_analysis_cached_prose_still_uses_cache(fresh_db, monkeypatch):
    """The prose cached path stays O(1): no live traversal, cache prose out."""
    from cairn.graph import dataflow, queries

    monkeypatch.setattr(tools_graph, "_conn", lambda: fresh_db)
    monkeypatch.setattr(tools_graph, "_fresh_graph", lambda conn: _NoBanner())
    monkeypatch.setattr(
        dataflow,
        "get_dataflow",
        lambda conn, name: {
            "symbol": name, "repo": "r",
            "within_repo": ["a"], "cross_repo": [], "updated": "",
        },
    )
    live_calls: list[str] = []
    monkeypatch.setattr(
        queries,
        "impact_analysis",
        lambda conn, name, **k: live_calls.append(name) or {"impacted": []},
    )

    out = tools_graph.impact_analysis("sym", cached=True)

    assert "cached" in out and "Within-repo impact" in out
    assert live_calls == [], "prose + cached must read the precomputed table only"


def test_find_definition_signals_limit_cap(fresh_db, monkeypatch):
    """When the match count reaches the limit the output says so, and the
    limit travels to the primitive."""
    from cairn.graph import queries

    monkeypatch.setattr(tools_graph, "_conn", lambda: fresh_db)
    monkeypatch.setattr(tools_graph, "_fresh_graph", lambda conn: _NoBanner())
    seen: dict[str, int] = {}
    rows = [
        {
            "file_path": f"a{i}.kt", "line_start": 1, "kind": "function",
            "qualified_name": f"m.f{i}", "name": f"f{i}", "repo": "r",
        }
        for i in range(50)
    ]

    def fake_find_definition(conn, name, limit=50):
        seen["limit"] = limit
        return rows[:limit]

    monkeypatch.setattr(queries, "find_definition", fake_find_definition)

    out = tools_graph.find_definition("f")

    assert seen["limit"] == 50
    assert "hit the limit cap" in out, "a capped result must not look complete"
    assert "pass a higher limit" in out

    out_more = tools_graph.find_definition("f", limit=200)
    assert seen["limit"] == 200
    assert "hit the limit cap" not in out_more


def test_visualize_graph_carries_freshness_banner(fresh_db, monkeypatch):
    """visualize_graph probes freshness like its sibling graph tools and prepends
    the banner on the agent-readable mermaid output."""
    from cairn.viz import query as vq

    monkeypatch.setattr(tools_graph, "_conn", lambda: fresh_db)
    monkeypatch.setattr(tools_graph, "_fresh_graph", lambda conn: _Stale())
    monkeypatch.setattr(
        vq,
        "get_symbol_graph",
        lambda conn, symbol: {"nodes": [], "edges": [], "metadata": {"scope": "symbol"}},
    )

    out = tools_graph.visualize_graph(scope="symbol", symbol="x")

    assert out.startswith("STALE-BANNER"), "a stale render must warn"
    assert "graph LR" in out


def test_visualize_graph_json_stays_machine_readable(fresh_db, monkeypatch):
    """format=json must stay pure JSON: the freshness banner would break parsers."""
    import json

    from cairn.viz import query as vq

    monkeypatch.setattr(tools_graph, "_conn", lambda: fresh_db)
    monkeypatch.setattr(tools_graph, "_fresh_graph", lambda conn: _Stale())
    monkeypatch.setattr(
        vq,
        "get_symbol_graph",
        lambda conn, symbol: {"nodes": [], "edges": [], "metadata": {"scope": "symbol"}},
    )

    data = json.loads(tools_graph.visualize_graph(scope="symbol", symbol="x", format="json"))

    assert data["metadata"]["scope"] == "symbol"


def test_recall_memory_skips_ref_session_under_read_only(fresh_db, monkeypatch):
    """Under CAIRN_READ_ONLY the recall session id is withheld, so search_memory
    never attempts its read-through memory_refs write."""
    from cairn.memory import promotion

    monkeypatch.setattr(tools_memory, "_conn", lambda: fresh_db)
    monkeypatch.setattr(tools_memory, "_bundle", lambda: object())
    seen: list = []

    def fake_search(conn, bundle, query, **kwargs):
        seen.append(kwargs.get("session_id"))
        return []

    monkeypatch.setattr(promotion, "search_memory", fake_search)
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    tools_memory.recall_memory("backoff")
    monkeypatch.setenv("CAIRN_READ_ONLY", "1")
    tools_memory.recall_memory("backoff")

    assert seen[0] and seen[0].startswith("mcp-"), "read-write recall keeps its session id"
    assert seen[1] is None, "read-only recall must not drive the memory_refs write"
