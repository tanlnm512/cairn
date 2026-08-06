"""Tests for Phase 3.2 (status Resource), 3.3 (structuredContent), 3.4 (lifespan).

These three are the MCP-surface-polish items. They don't boot the full server
(source-scraping / in-process checks only) so they stay fast and don't need a
materialized store.
"""
from __future__ import annotations

import inspect
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_CORE = REPO_ROOT / "src" / "cairn" / "mcp_server" / "_server_core.py"
TOOLS_GRAPH = REPO_ROOT / "src" / "cairn" / "mcp_server" / "tools_graph.py"


class TestStatusResource:
    """Phase 3.2: cairn://status is a subscribable resource, not a tool."""

    def test_resource_is_registered(self):
        src = SERVER_CORE.read_text(encoding="utf-8")
        assert '@mcp.resource("cairn://status")' in src, (
            "cairn://status resource must be registered in _server_core.py"
        )
        assert "def status_resource" in src

    def test_resource_is_not_a_tool(self):
        """The status surface must be browsable data, not a tool action."""
        src = SERVER_CORE.read_text(encoding="utf-8")
        # The status_resource function should not carry @mcp.tool.
        # Find the function and check the decorator immediately above it.
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if "def status_resource" in line:
                # Walk up past blanks/comments to the nearest decorator.
                j = i - 1
                while j >= 0 and (lines[j].strip() == "" or lines[j].lstrip().startswith("#")):
                    j -= 1
                assert "@mcp.tool" not in lines[j], (
                    "status should be a @mcp.resource, not a @mcp.tool"
                )
                assert "@mcp.resource" in lines[j]
                return
        raise AssertionError("status_resource not found")


class TestStructuredContent:
    """Phase 3.3: get_callers offers a structured= opt-in return."""

    def test_get_callers_has_structured_kwarg(self):
        from cairn.mcp_server.tools_graph import get_callers

        sig = inspect.signature(get_callers)
        assert "structured" in sig.parameters, (
            "get_callers must accept a structured= kwarg (Phase 3.3 pilot)"
        )
        assert sig.parameters["structured"].default is False, (
            "structured must default to False (preserve prose return)"
        )

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

    def test_structured_models_validate_data_helper_output(self):
        """Each Pydantic model accepts the dict its *_data helper produces."""
        from cairn.mcp_server.structured import (
            GetCallersResult,
            GetCalleesResult,
            SearchSymbolsResult,
            ImpactAnalysisResult,
        )

        # Minimal valid dicts in each helper's shape.
        assert GetCallersResult.model_validate({
            "symbol": "x", "count": 0, "used_fallback": False,
            "hit_limit": False, "stale_banner": "", "callers": [],
        })
        assert GetCalleesResult.model_validate({
            "symbol": "x", "count": 0, "used_fallback": False,
            "hit_limit": False, "callees": [],
        })
        assert SearchSymbolsResult.model_validate({
            "pattern": "x", "count": 0, "total_count": 0, "truncated": False, "symbols": [],
        })
        assert ImpactAnalysisResult.model_validate({
            "symbol": "x", "total": 0, "truncated": False, "fuzzy": False,
            "by_depth": {}, "cycles": [], "affected_tests": [],
            "cross_repo_dependents": [],
        })

    def test_tools_declare_structured_output_in_decorator(self):
        """The @mcp.tool decorators carry structured_output=True so FastMCP
        derives outputSchema (the gate for native structuredContent)."""
        import ast

        src = TOOLS_GRAPH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        structured_tools = {
            "get_callers", "get_callees", "search_symbols",
            "semantic_search", "impact_analysis",
        }
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in structured_tools:
                for dec in node.decorator_list:
                    # Match @mcp.tool(...) calls containing structured_output=True.
                    if isinstance(dec, ast.Call) and hasattr(dec.func, "attr") and dec.func.attr == "tool":
                        for kw in dec.keywords:
                            if kw.arg == "structured_output":
                                if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                    found.add(node.name)
        assert found == structured_tools, (
            f"these tools must declare structured_output=True: "
            f"{structured_tools - found}"
        )


    def test_all_parse_heavy_tools_have_structured_kwarg(self):
        """get_callers, get_callees, search_symbols, semantic_search,
        impact_analysis all expose the structured= opt-in."""
        import inspect

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

    def test_each_tool_has_extracted_data_and_render_helpers(self):
        """The structured core + prose renderer were extracted per tool."""
        from cairn.mcp_server import tools_graph

        # (data helper, render helper) pairs -- one per refactored tool.
        pairs = [
            ("get_callers_data", "_render_callers"),
            ("get_callees_data", "_render_callees"),
            ("search_symbols_data", "_render_search_symbols"),
            ("impact_analysis_data", "_render_impact_analysis"),
        ]
        for data_fn, render_fn in pairs:
            assert hasattr(tools_graph, data_fn), f"missing {data_fn}"
            assert hasattr(tools_graph, render_fn), f"missing {render_fn}"
            assert callable(getattr(tools_graph, data_fn))
            assert callable(getattr(tools_graph, render_fn))

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
    """Phase 3.4: FastMCP is constructed with the lifespan pattern."""

    def test_app_context_dataclass_exists(self):
        from cairn.mcp_server._server_core import AppContext

        fields = {f.name for f in __import__("dataclasses").fields(AppContext)}
        assert {"db_path", "knowledge_path", "read_only"}.issubset(fields)

    def test_app_lifespan_is_async_context_manager(self):
        from cairn.mcp_server._server_core import app_lifespan

        # @asynccontextmanager wraps the generator; the result is an
        # async context manager callable.
        assert callable(app_lifespan)

    def test_fastmcp_constructed_with_lifespan(self):
        """The server passes app_lifespan to FastMCP()."""
        src = SERVER_CORE.read_text(encoding="utf-8")
        assert 'FastMCP("cairn", lifespan=app_lifespan' in src, (
            "FastMCP must be constructed with lifespan=app_lifespan (Phase 3.4)"
        )

    def test_fastmcp_pins_log_level(self):
        """FastMCP's default log_level="INFO" calls logging.basicConfig() at
        import time, clobbering the root logger for every `cairn` CLI
        invocation (not just `cairn serve`), since this module is imported
        eagerly to register the serve command. Pin it so constructing this
        singleton doesn't leak third-party INFO logs into unrelated commands.
        """
        from cairn.mcp_server._server_core import mcp

        assert mcp.settings.log_level == "WARNING"
