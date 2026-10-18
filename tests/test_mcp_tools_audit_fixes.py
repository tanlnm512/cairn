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
    monkeypatch.setattr(tools_graph, "_definer_repos", lambda conn, name: ["r"])
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


def test_record_memory_commits_validity_rows(tmp_path, monkeypatch):
    """record_memory must land capture_memory's validity DML: the pooled
    close() is a no-op, so only an explicit commit persists the rows."""
    import sqlite3

    from cairn.mcp_server import _server_core

    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(tmp_path / "knowledge"))
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    try:
        out = tools_memory.record_memory(
            "decision", "Commit discipline", "body without refs", confidence=0.8,
        )
        assert out.startswith("Recorded ")

        fresh = sqlite3.connect(str(tmp_path / "graph.db"))
        try:
            rows = fresh.execute("SELECT concept_id FROM memory_validity").fetchall()
        finally:
            fresh.close()
        assert rows, "memory_validity rows must survive the tool's close()"

        pooled = _server_core._conn()
        pooled.execute("BEGIN")  # an open leftover transaction fails here
        pooled.execute("ROLLBACK")
    finally:
        _server_core._reset_conn_pool()


def test_knowledge_write_tools_refuse_under_read_only(tmp_path, monkeypatch):
    """CAIRN_READ_ONLY must gate every knowledge write tool like generate_flow."""
    from cairn.mcp_server import tools_knowledge
    from cairn.okf.bundle import OKFBundle

    monkeypatch.setenv("CAIRN_READ_ONLY", "1")
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(tmp_path / "knowledge"))
    monkeypatch.setattr(
        tools_knowledge, "_bundle", lambda: OKFBundle(tmp_path / "knowledge")
    )

    def _no_rw():
        raise AssertionError("_rw_conn opened under a read-only server")

    monkeypatch.setattr(tools_knowledge, "_rw_conn", _no_rw)

    assert "read-only server" in tools_knowledge.knowledge_add("T", "b", "decision")
    assert "read-only server" in tools_knowledge.knowledge_delete("knowledge/decision/t")
    assert "read-only server" in tools_knowledge.knowledge_status(
        "knowledge/decision/t", "archived"
    )


def test_impact_analysis_cross_repo_section_covers_all_definer_repos(fresh_db, monkeypatch):
    """A symbol defined in several repos must scope the cross-repo section over
    every definer repo, not an arbitrary first row."""
    from cairn.graph import queries

    conn = fresh_db
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('ra', 'ra', '/tmp/ra')")
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('rb', 'rb', '/tmp/rb')")
    for fid, repo in ((1, "ra"), (2, "rb")):
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, 'a.py', 'python')",
            (fid, repo),
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
            "VALUES (?, ?, 'shared', 'function', 'shared', 1, 2)",
            (fid, fid),
        )
    conn.commit()

    monkeypatch.setattr(tools_graph, "_conn", lambda: conn)
    monkeypatch.setattr(tools_graph, "_fresh_graph", lambda c: _NoBanner())
    monkeypatch.setattr(
        queries,
        "impact_analysis",
        lambda c, name, **k: {"impacted": [], "total": 0, "truncated": False, "cycles": []},
    )
    seen: list[str] = []

    def fake_xref(c, repo):
        seen.append(repo)
        return {"dependents": [{"repo": f"consumer-{repo}", "count": 2}]}

    monkeypatch.setattr(queries, "cross_repo_deps", fake_xref)

    out = tools_graph.impact_analysis("shared")

    assert sorted(seen) == ["ra", "rb"], "both definer repos must be scoped"
    assert "consumer-ra" in out and "consumer-rb" in out


def test_ask_compass_names_the_ignored_file_path(fresh_db, hash_backend, monkeypatch, tmp_path):
    """Routing mode given a file_path must say the path was not applied, not drop it silently."""
    from cairn.mcp_server import tools_compass
    from cairn.okf.bundle import OKFBundle

    class _KeepOpen:
        """Delegates to the fixture conn; close() is a no-op across calls."""

        def __init__(self, c):
            self._c = c

        def __getattr__(self, name):
            return getattr(self._c, name)

        def close(self):
            pass

    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setattr(tools_compass, "_conn", lambda: _KeepOpen(fresh_db))
    monkeypatch.setattr(tools_compass, "_bundle", lambda: OKFBundle(knowledge))

    out = tools_compass.ask_compass(
        "how does the retry flow work", file_path="src/mod/Foo.kt"
    )

    assert "src/mod/Foo.kt" in out
    assert "ignored in routing mode" in out

    plain = tools_compass.ask_compass("how does the retry flow work")
    assert "ignored in routing mode" not in plain
