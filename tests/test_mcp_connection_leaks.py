"""Test that MCP tools close their SQLite connections on exception.

Regression guard: tools previously leaked connections when exceptions occurred
because they didn't use try/finally. This test verifies that all tools properly
close connections even when queries fail.

The 12 leaky tools are:
- tools_graph.py: find_definition, get_callers, get_callees, impact_analysis,
  explore, search_symbols, cross_repo_deps, visualize_graph
- tools_memory.py: recall_memory, record_memory
- tools_compass.py: ask_compass

The 5 correct tools already use try/finally:
- tools_graph.py: semantic_search
- tools_memory.py: memory_promote, memory_delete
- tools_memory.py: memory_demote (no DB, so no leak)
- tools_compass.py: get_compass, search_knowledge (no DB, so no leak)

Note (pruned 2026-07-31): this file previously had 22 tests -- one exception
AND one normal-exit test per tool. The exception path strictly subsumes the
normal path (both exercise the same ``finally: conn.close()``), so the 11
normal-exit duplicates were removed. The exception path is the harder, more
load-bearing case: it proves close() runs even when the query raises. A
representative normal-exit sanity check lives in tests/test_core_smoke.py.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_conn():
    """Create a mock connection that tracks close() calls and can raise on queries."""
    conn = MagicMock(spec=sqlite3.Connection)
    close_called = False

    def close_side_effect():
        nonlocal close_called
        close_called = True

    conn.close.side_effect = close_side_effect
    conn.is_close_called = lambda: close_called
    return conn


class TestMCPConnectionLeaks:
    """Test that all MCP tools close connections on exception paths.

    Each tool's exception test is kept; the paired normal-exit test was
    removed as a strict duplicate (see module docstring).
    """

    def test_find_definition_closes_conn_on_exception(self, mock_conn):
        """find_definition must close conn even when query raises."""
        from cairn.mcp_server.tools_graph import find_definition

        def mock_query_raises(*args, **kwargs):
            raise RuntimeError("Query failed")

        with patch("cairn.graph.queries.find_definition", mock_query_raises):
            with patch("cairn.mcp_server.tools_graph._conn", return_value=mock_conn):
                try:
                    find_definition("test_symbol")
                except RuntimeError:
                    pass
        assert mock_conn.is_close_called(), "find_definition must close conn on exception"

    def test_get_callers_closes_conn_on_exception(self, mock_conn):
        """get_callers must close conn even when query raises."""
        from cairn.mcp_server.tools_graph import get_callers

        def mock_query_raises(*args, **kwargs):
            raise RuntimeError("Query failed")

        with patch("cairn.graph.queries.get_callers", mock_query_raises):
            with patch("cairn.mcp_server.tools_graph._conn", return_value=mock_conn):
                try:
                    get_callers("test_symbol")
                except RuntimeError:
                    pass
        assert mock_conn.is_close_called(), "get_callers must close conn on exception"

    def test_get_callees_closes_conn_on_exception(self, mock_conn):
        """get_callees must close conn even when query raises."""
        from cairn.mcp_server.tools_graph import get_callees

        def mock_query_raises(*args, **kwargs):
            raise RuntimeError("Query failed")

        with patch("cairn.graph.queries.get_callees", mock_query_raises):
            with patch("cairn.mcp_server.tools_graph._conn", return_value=mock_conn):
                try:
                    get_callees("test_symbol")
                except RuntimeError:
                    pass
        assert mock_conn.is_close_called(), "get_callees must close conn on exception"

    def test_impact_analysis_closes_conn_on_exception(self, mock_conn):
        """impact_analysis must close conn even when query raises."""
        from cairn.mcp_server.tools_graph import impact_analysis

        def mock_query_raises(*args, **kwargs):
            raise RuntimeError("Query failed")

        with patch("cairn.graph.queries.impact_analysis", mock_query_raises):
            with patch("cairn.mcp_server.tools_graph._conn", return_value=mock_conn):
                try:
                    impact_analysis("test_symbol")
                except RuntimeError:
                    pass
        assert mock_conn.is_close_called(), "impact_analysis must close conn on exception"

    def test_explore_closes_conn_on_exception(self, mock_conn):
        """explore must close conn even when query raises."""
        from cairn.mcp_server.tools_graph import explore

        def mock_query_raises(*args, **kwargs):
            raise RuntimeError("Query failed")

        with patch("cairn.graph.queries.explore", mock_query_raises):
            with patch("cairn.mcp_server.tools_graph._conn", return_value=mock_conn):
                try:
                    explore("test_symbol")
                except RuntimeError:
                    pass
        assert mock_conn.is_close_called(), "explore must close conn on exception"

    def test_search_symbols_closes_conn_on_exception(self, mock_conn):
        """search_symbols must close conn even when query raises."""
        from cairn.mcp_server.tools_graph import search_symbols

        def mock_query_raises(*args, **kwargs):
            raise RuntimeError("Query failed")

        with patch("cairn.graph.queries.search_symbols", mock_query_raises):
            with patch("cairn.mcp_server.tools_graph._conn", return_value=mock_conn):
                try:
                    search_symbols("test_symbol")
                except RuntimeError:
                    pass
        assert mock_conn.is_close_called(), "search_symbols must close conn on exception"

    def test_cross_repo_deps_closes_conn_on_exception(self, mock_conn):
        """cross_repo_deps must close conn even when query raises."""
        from cairn.mcp_server.tools_graph import cross_repo_deps

        def mock_query_raises(*args, **kwargs):
            raise RuntimeError("Query failed")

        with patch("cairn.graph.queries.cross_repo_deps", mock_query_raises):
            with patch("cairn.mcp_server.tools_graph._conn", return_value=mock_conn):
                try:
                    cross_repo_deps("test_repo")
                except RuntimeError:
                    pass
        assert mock_conn.is_close_called(), "cross_repo_deps must close conn on exception"

    def test_visualize_graph_closes_conn_on_exception(self, mock_conn):
        """visualize_graph must close conn even when query raises."""
        from cairn.mcp_server.tools_graph import visualize_graph

        def mock_query_raises(*args, **kwargs):
            raise RuntimeError("Query failed")

        with patch("cairn.viz.query.get_symbol_graph", mock_query_raises):
            with patch("cairn.mcp_server.tools_graph._conn", return_value=mock_conn):
                try:
                    visualize_graph(scope="symbol", symbol="test")
                except RuntimeError:
                    pass
        assert mock_conn.is_close_called(), "visualize_graph must close conn on exception"

    def test_recall_memory_closes_conn_on_exception(self, mock_conn):
        """recall_memory must close conn even when query raises."""
        from cairn.mcp_server.tools_memory import recall_memory

        def mock_search_raises(*args, **kwargs):
            raise RuntimeError("Query failed")

        with patch("cairn.mcp_server.tools_memory._bundle", return_value=MagicMock()):
            with patch("cairn.mcp_server.tools_memory._conn", return_value=mock_conn):
                with patch("cairn.memory.promotion.search_memory", mock_search_raises):
                    try:
                        recall_memory("test_query")
                    except RuntimeError:
                        pass
        assert mock_conn.is_close_called(), "recall_memory must close conn on exception"

    def test_record_memory_closes_conn_on_exception(self, mock_conn):
        """record_memory must close conn even when query raises.

        record_memory uses _rw_conn() (a writable connection) rather than the
        read-only _conn(), since its purpose is to write -- patch _rw_conn here.
        """
        from cairn.mcp_server.tools_memory import record_memory

        def mock_capture_raises(*args, **kwargs):
            raise RuntimeError("Query failed")

        with patch("cairn.mcp_server.tools_memory._bundle", return_value=MagicMock()):
            with patch("cairn.mcp_server.tools_memory._rw_conn", return_value=mock_conn):
                with patch("cairn.memory.promotion.capture_memory", mock_capture_raises):
                    try:
                        record_memory("decision", "test_title", "test_body")
                    except RuntimeError:
                        pass
        assert mock_conn.is_close_called(), "record_memory must close conn on exception"

    def test_ask_compass_closes_conn_on_exception(self, mock_conn):
        """ask_compass must close conn even when query raises."""
        from cairn.mcp_server.tools_compass import ask_compass

        def mock_route_raises(*args, **kwargs):
            raise RuntimeError("Query failed")

        with patch("cairn.mcp_server.tools_compass._bundle", return_value=MagicMock()):
            with patch("cairn.mcp_server.tools_compass._conn", return_value=mock_conn):
                with patch("cairn.compass.router.route_query", mock_route_raises):
                    try:
                        ask_compass("test_query")
                    except RuntimeError:
                        pass
        assert mock_conn.is_close_called(), "ask_compass must close conn on exception"
