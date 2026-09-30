"""Tests for Phase 3.2 (status Resource), 3.3 (structuredContent), 3.4 (lifespan).

These three are the MCP-surface-polish items. They don't boot the full server
(source-scraping / in-process checks only) so they stay fast and don't need a
materialized store.
"""
from __future__ import annotations

import inspect



class TestStatusResource:
    """Phase 3.2: cairn://status is a subscribable resource, not a tool."""

    def test_resource_is_registered(self):
        from cairn.mcp_server._server_core import mcp

        uris = {str(r.uri) for r in mcp._resource_manager.list_resources()}
        assert "cairn://status" in uris

class TestStructuredContent:
    """Phase 3.3: get_callers offers a structured= opt-in return."""

    def test_get_callers_data_returns_dict_shape(self):
        """The extracted structured core returns the documented fields."""
        from cairn.mcp_server.tools_graph import get_callers_data

        # Monkeypatch _conn to return a stub that yields no rows -- exercises
        # the empty path without needing a real graph DB.
        import cairn.mcp_server.tools_graph as tg

        class _StubConn:
            def close(self):
                pass

        class _StubQuery:
            @staticmethod
            def get_callers(conn, name, fuzzy=False, limit=200):
                return []

        original_conn = tg._conn
        try:
            tg._conn = lambda: _StubConn()
            # Patch the lazy import inside get_callers_data.
            import types

            fake_queries = types.SimpleNamespace(get_callers=_StubQuery.get_callers)
            # The function does `from cairn.graph import queries` lazily --
            # patch the resolved attribute.
            import cairn.graph
            original_graph_queries = getattr(cairn.graph, "queries", None)
            cairn.graph.queries = fake_queries
            try:
                data = get_callers_data("missingSymbol")
            finally:
                if original_graph_queries is not None:
                    cairn.graph.queries = original_graph_queries
                else:
                    del cairn.graph.queries
        finally:
            tg._conn = original_conn

        assert isinstance(data, dict)
        for field in ("symbol", "count", "used_fallback", "hit_limit", "stale_banner", "callers"):
            assert field in data, f"missing structured field: {field}"
        assert data["symbol"] == "missingSymbol"
        assert data["count"] == 0
        assert data["callers"] == []

    def test_render_callers_empty_message_preserved(self):
        """The legacy prose path keeps the empty-result next-step hint."""
        from cairn.mcp_server.tools_graph import _render_callers

        msg = _render_callers({"symbol": "x", "count": 0, "used_fallback": False,
                               "hit_limit": False, "stale_banner": "", "callers": []})
        assert "No callers found" in msg

    # --- Phase 3.3 rollout: native structuredContent via Pydantic models ---
    # The structured=True path returns a typed Pydantic model so FastMCP derives
    # outputSchema and populates the native structuredContent field (not a
    # stringified dict). This test guards the wiring without booting the server:
    # the model validates the dict shape the *_data helper produces.

    def test_tools_declare_structured_output(self):
        """The five parse-heavy tools derive an outputSchema (the gate for
        native structuredContent)."""
        import cairn.mcp_server.tools_graph  # noqa: F401 -- registers the tools
        from cairn.mcp_server._server_core import mcp

        tools = {t.name: t for t in mcp._tool_manager.list_tools()}
        structured = {
            "get_callers", "get_callees", "search_symbols",
            "semantic_search", "impact_analysis",
        }
        for name in structured:
            assert tools[name].output_schema, f"{name} must derive an outputSchema"

    def test_get_callees_result_model_keeps_stale_banner(self):
        """get_callees_data returns stale_banner; the structured model must
        carry it through validation (parity with GetCallersResult)."""
        from cairn.mcp_server.structured import GetCalleesResult

        model = GetCalleesResult.model_validate({
            "symbol": "x", "count": 0, "used_fallback": False,
            "hit_limit": False, "stale_banner": "pending sync", "callees": [],
        })
        assert model.stale_banner == "pending sync"


    def test_all_parse_heavy_tools_have_structured_kwarg(self):
        """get_callers, get_callees, search_symbols, semantic_search,
        impact_analysis all expose the structured= opt-in."""

        from cairn.mcp_server import tools_graph

        tools = [
            tools_graph.get_callers,
            tools_graph.get_callees,
            tools_graph.search_symbols,
            tools_graph.semantic_search,
            tools_graph.impact_analysis,
        ]
        for fn in tools:
            sig = inspect.signature(fn)
            assert "structured" in sig.parameters, (
                f"{fn.__name__} must accept structured= (Phase 3.3 rollout)"
            )
            assert sig.parameters["structured"].default is False, (
                f"{fn.__name__}.structured must default to False"
            )

    def test_render_callees_empty_message_preserved(self):
        from cairn.mcp_server.tools_graph import _render_callees

        msg = _render_callees({"symbol": "x", "count": 0, "used_fallback": False,
                               "hit_limit": False, "callees": []})
        assert "No callees found" in msg

    def test_render_search_symbols_empty_message_preserved(self):
        from cairn.mcp_server.tools_graph import _render_search_symbols

        msg = _render_search_symbols({"pattern": "x", "count": 0, "truncated": False,
                                      "symbols": []})
        assert "No symbols matching" in msg

    def test_render_impact_analysis_round_trips(self):
        """The impact renderer reconstructs the prose from structured data."""
        from cairn.mcp_server.tools_graph import _render_impact_analysis

        data = {
            "symbol": "Foo.bar",
            "total": 2,
            "truncated": False,
            "fuzzy": False,
            "by_depth": {"1": 2},
            "cycles": [],
            "affected_tests": [],
            "cross_repo_dependents": [{"repo": "svc-a", "count": 3}],
        }
        msg = _render_impact_analysis(data, limit=500)
        assert "Impact of 'Foo.bar': 2 total" in msg
        assert "Depth 1: 2 callers" in msg
        assert "svc-a" in msg


class TestLifespan:
    """Phase 3.4: FastMCP is constructed with the lifespan pattern.

    The lifespan is a minimal no-op (FastMCP requires one for hooks);
    config flows through _conn()/_store().
    """

    def test_fastmcp_pins_log_level(self):
        """FastMCP's default log_level="INFO" calls logging.basicConfig() at
        import time, clobbering the root logger for every `cairn` CLI
        invocation (not just `cairn serve`), since this module is imported
        eagerly to register the serve command. Pin it so constructing this
        singleton doesn't leak third-party INFO logs into unrelated commands.
        """
        from cairn.mcp_server._server_core import mcp

        assert mcp.settings.log_level == "WARNING"
