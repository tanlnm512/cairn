"""Test MCP server robustness improvements for M9, L1, L3.

Tests for:
- M9: Store existence check at boot
- L1: Tool count assertion
- L3: @instrument error sanitization
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch
from pathlib import Path
import pytest

from cairn.mcp_server import server


class TestStoreExistenceCheck:
    """Test M9: Server boot guard for missing store."""

    def test_missing_store_exits_cleanly(self, monkeypatch, tmp_path):
        """Empty DB at boot raises SystemExit with helpful message."""
        # Create an empty database (no schema)
        empty_db = tmp_path / "empty.db"
        empty_db.touch()
        
        monkeypatch.setenv("CAIRN_DB", str(empty_db))
        
        # Mock the actual mcp.run() so we don't try to start the server
        with patch("cairn.mcp_server.server.mcp"), \
             patch("cairn.mcp_server.server.verify_tool_count"):
            with pytest.raises(SystemExit) as exc_info:
                server.run(transport="stdio")
            
            # Should exit with error code 1
            assert exc_info.value.code == 1

    def test_valid_store_proceeds(self, monkeypatch, tmp_path, fresh_db):
        """Valid DB with symbols table boots successfully."""
        # Create a temporary database with schema using fresh_db
        test_db = tmp_path / "test.db"
        
        # Copy the schema from fresh_db to a file
        conn = sqlite3.connect(str(test_db))
        
        # Apply the schema to the test database
        from cairn.graph.schema import _apply_schema
        _apply_schema(conn)
        conn.close()
        
        monkeypatch.setenv("CAIRN_DB", str(test_db))
        
        # Mock the actual mcp.run() so we don't try to start the server
        with patch("cairn.mcp_server.server.mcp") as mock_mcp, \
             patch("cairn.mcp_server.server.verify_tool_count"):
            with patch("cairn.graph.watcher.ensure_fresh_force", return_value=0):
                with patch("cairn.memory.promotion.decay", return_value={"expired_raw": 0, "archived_tribal": 0}):
                    try:
                        server.run(transport="stdio")
                    except SystemExit as e:
                        # If it's not exit code 1, it might be from other parts (e.g., mcp.run())
                        # Exit code 1 is our missing store check
                        if e.code == 1:
                            pytest.fail("server.run() should not exit with code 1 for valid store")
                    
                    # Verify mcp.run() was called (meaning we didn't exit early)
                    mock_mcp.run.assert_called_once()


class TestToolCountAssertion:
    """Test L1: Tool count frozen with assertion."""

    def test_tool_count_assertion(self):
        """Registered tool count should match expected constant."""

        # L1: verify_tool_count() raises AssertionError if the registered tool
        # count drifts from _EXPECTED_TOOL_COUNT. The guard is deferred to
        # run() / this test rather than running at import time, so unrelated
        # importers don't trip a regression as an AssertionError.
        import cairn.mcp_server.server as server_mod

        # If the count drifted, this raises AssertionError.
        server_mod.verify_tool_count()
        assert server_mod._count_fastmcp_tools() == server_mod._EXPECTED_TOOL_COUNT


class TestInstrumentErrorSanitization:
    """Test L3: @instrument error normalization."""

    def test_instrument_sanitizes_error(self, caplog):
        """Exception is re-raised after being logged server-side (C3 fix).

        Previously @instrument swallowed every exception into a
        ``[ERROR: ...]`` prose string, which broke the MCP error contract --
        clients checking ``isError`` never saw failures. Now it logs the full
        traceback and re-raises so FastMCP shapes the proper MCP error
        response.
        """
        from cairn.mcp_server.metric_buffering import instrument
        import logging

        caplog.set_level(logging.DEBUG)

        @instrument
        def failing_function():
            leaky = str(Path.home() / "Projects" / "cairn" / ".knowledge" / "compass" / "some-module.md")
            raise FileNotFoundError(f"No such file or directory: '{leaky}'")

        # The function must re-raise (not return a sanitized string), so
        # FastMCP's Tool.run converts it into a proper MCP isError response.
        with pytest.raises(FileNotFoundError):
            failing_function()

        # The full exception should still be logged server-side for debugging.
        assert any("FileNotFoundError" in record.message for record in caplog.records), \
            "Full exception should be logged server-side"

    def test_instrument_records_error_metric(self, monkeypatch):
        """Error is recorded as 'error' status in metrics before re-raising."""
        from cairn.mcp_server.metric_buffering import instrument, _METRIC_BUFFER, _METRIC_LOCK

        @instrument
        def failing_function():
            raise ValueError("Test error")

        # Clear the buffer
        with _METRIC_LOCK:
            _METRIC_BUFFER.clear()

        # The function re-raises (C3 fix) but must still record the metric
        # before propagating the exception.
        with pytest.raises(ValueError):
            failing_function()

        # Check that an error metric was logged
        with _METRIC_LOCK:
            assert len(_METRIC_BUFFER) > 0
            metric = _METRIC_BUFFER[0]
            # metric format: (tool_name, session_id, invoked_at, duration_ms, status, error_message)
            assert metric[4] == "error", f"Status should be 'error', got {metric[4]}"
            assert metric[5] is not None, "Error message should be recorded"
