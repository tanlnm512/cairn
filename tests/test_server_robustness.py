"""Test MCP server robustness improvements for M9, L1, L3.

Tests for:
- M9: Store existence check at boot
- L1: Tool count assertion
- L3: @instrument error sanitization
- Watchdog buffer drain (audit F3): the parent-death watchdog's os._exit(0)
  bypasses atexit, so buffered telemetry must be drained explicitly.
- Model cache race (audit F5): _MODEL_CACHE lazy load must be thread-safe.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
import time
import types
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

    # ------------------------------------------------------------------
    # FR-004 / TC-007: the boot-guard error must name the resolved db
    # path, the env resolution chain in effect, and the remediation --
    # not the bare OperationalError text alone. RED until the boot-guard
    # message is enriched (tech-spec D-008).
    # ------------------------------------------------------------------

    @staticmethod
    def _missing_store_env(monkeypatch, tmp_path):
        """Point every env var of the resolution chain at sandbox paths
        whose store directory does not exist; return the values in effect."""
        db = (tmp_path / "no-such-store" / "missing.db").resolve()
        home = str((tmp_path / "home").resolve())
        workspace = str((tmp_path / "ws").resolve())
        knowledge = str((tmp_path / "knowledge").resolve())
        monkeypatch.setenv("CAIRN_HOME", home)
        monkeypatch.setenv("CAIRN_WORKSPACE", workspace)
        monkeypatch.setenv("CAIRN_DB", str(db))
        monkeypatch.setenv("CAIRN_KNOWLEDGE", knowledge)
        return home, workspace, str(db), knowledge

    def test_missing_store_error_names_path_env_and_remediation(
        self, monkeypatch, tmp_path, capsys
    ):
        """TC-007: boot against a missing store directory exits 1 with an
        error naming the resolved db path, the env chain in effect, and the
        set-CAIRN-HOME / cairn init && cairn build remediation."""
        home, workspace, db, knowledge = self._missing_store_env(
            monkeypatch, tmp_path
        )

        with patch("cairn.mcp_server.server.mcp"), \
             patch("cairn.mcp_server.server.verify_tool_count"):
            with pytest.raises(SystemExit) as exc_info:
                server.run(transport="stdio")

        # Exit-code-1 contract (survives from test_missing_store_exits_cleanly).
        assert exc_info.value.code == 1

        err = capsys.readouterr().err
        # (a) the resolved db path
        assert db in err, "error must name the resolved db path"
        # (b) the env resolution chain in effect: each var named with its value
        assert "CAIRN_HOME" in err and home in err
        assert "CAIRN_WORKSPACE" in err and workspace in err
        assert "CAIRN_DB" in err
        assert "CAIRN_KNOWLEDGE" in err and knowledge in err
        # (c) the remediation: point CAIRN_HOME at the built store, or build it
        assert "CAIRN_HOME" in err, "remediation must mention CAIRN_HOME"
        assert "cairn init" in err
        assert "cairn build" in err

    def test_missing_store_error_reports_unset_env_vars(
        self, monkeypatch, tmp_path, capsys
    ):
        """TC-007 edge: chain entries with no env value in effect are still
        named, rendered as 'unset' (D-008: value or 'unset' per entry)."""
        monkeypatch.setenv("CAIRN_DB",
                           str((tmp_path / "no-such-store" / "missing.db").resolve()))
        monkeypatch.delenv("CAIRN_WORKSPACE", raising=False)
        monkeypatch.delenv("CAIRN_KNOWLEDGE", raising=False)

        with patch("cairn.mcp_server.server.mcp"), \
             patch("cairn.mcp_server.server.verify_tool_count"):
            with pytest.raises(SystemExit) as exc_info:
                server.run(transport="stdio")

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "CAIRN_WORKSPACE" in err
        assert "CAIRN_KNOWLEDGE" in err
        assert "unset" in err

    def test_cli_get_db_missing_store_error_is_actionable(
        self, monkeypatch, tmp_path
    ):
        """TC-008: the CLI db-open path raises the SAME OperationalError type
        when the store's parent directory is missing, but the text carries
        the resolved path, the env chain, and the remediation -- the red
        that drives the schema.get_db pre-check (D-008)."""
        from cairn.graph.schema import get_db

        home, workspace, db, knowledge = self._missing_store_env(
            monkeypatch, tmp_path
        )

        with pytest.raises(sqlite3.OperationalError) as exc_info:
            get_db(db, read_only=True)

        msg = str(exc_info.value)
        assert db in msg, "error must name the resolved db path"
        assert "CAIRN_HOME" in msg and home in msg
        assert "CAIRN_WORKSPACE" in msg and workspace in msg
        assert "CAIRN_DB" in msg
        assert "CAIRN_KNOWLEDGE" in msg and knowledge in msg
        assert "CAIRN_HOME" in msg, "remediation must mention CAIRN_HOME"
        assert "cairn init" in msg
        assert "cairn build" in msg


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


class TestWatchdogBufferDrain:
    """Audit F3: os._exit(0) from the watchdog thread bypasses atexit, so the
    sinks' atexit drains (telemetry.sink._flush_all, embed_buffering._flush)
    never fire on a NORMAL client disconnect -- up to 30s of events/metrics
    and 15s of queued embeddings were silently lost. The watchdog must call
    the public flush entry points directly before exiting."""

    def test_drain_calls_all_three_flush_entry_points(self, monkeypatch):
        """Each subsystem's flush entry point is invoked exactly once:
        cairn.telemetry.flush (events), metric_buffering._flush_metrics
        (tool_metrics), embed_buffering._flush (memory embeddings)."""
        calls = []
        monkeypatch.setattr("cairn.telemetry.flush", lambda: calls.append("events"))
        monkeypatch.setattr(
            "cairn.mcp_server.metric_buffering._flush_metrics",
            lambda: calls.append("metrics"),
        )
        monkeypatch.setattr(
            "cairn.mcp_server.embed_buffering._flush", lambda: calls.append("embeds")
        )
        server._drain_buffered_telemetry()
        assert calls == ["events", "metrics", "embeds"]

    def test_drain_isolates_flush_failures(self, monkeypatch):
        """A raising flush entry point must not abort the other drains (and
        must not propagate -- the exit that follows must still happen)."""
        calls = []

        def boom():
            raise RuntimeError("sink down")

        monkeypatch.setattr("cairn.telemetry.flush", boom)
        monkeypatch.setattr(
            "cairn.mcp_server.metric_buffering._flush_metrics",
            lambda: calls.append("metrics"),
        )
        monkeypatch.setattr(
            "cairn.mcp_server.embed_buffering._flush", lambda: calls.append("embeds")
        )
        server._drain_buffered_telemetry()
        assert calls == ["metrics", "embeds"]

    def test_watchdog_drains_before_os_exit(self, monkeypatch):
        """The parent-change exit path drains the buffers BEFORE os._exit(0).
        Simulates the watchdog inline: the first poll-interval sleep 'kills'
        the parent, os._exit is captured, everything mocked."""
        events = []
        monkeypatch.setattr("cairn.telemetry.flush", lambda: events.append("flush"))
        monkeypatch.setattr(
            "cairn.mcp_server.metric_buffering._flush_metrics", lambda: None
        )
        monkeypatch.setattr("cairn.mcp_server.embed_buffering._flush", lambda: None)

        class _Stop(Exception):
            """Sentinel to break the watchdog loop under test."""

        def fake_exit(code):
            events.append(("exit", code))
            raise _Stop

        monkeypatch.setattr(server.os, "_exit", fake_exit)
        ppid = {"v": 100}
        monkeypatch.setattr(server.os, "getppid", lambda: ppid["v"])

        def fake_sleep(_s):
            ppid["v"] = 999  # parent dies while the watchdog sleeps

        monkeypatch.setattr(server.time, "sleep", fake_sleep)

        with pytest.raises(_Stop):
            server._watch_parent_loop()
        assert events == ["flush", ("exit", 0)], "drain must precede os._exit"


class TestSessionIdentity:
    """D-004 (FR-007): run() stamps a per-process CAIRN_SESSION at boot.

    The env var has three readers (metric_buffering, telemetry/events,
    graph/builder) that default the session column to "unknown" — boot is the
    writer. An externally provided value must keep precedence (setdefault),
    and the generated id must be a 12-char hex string.
    """

    @staticmethod
    def _boot_server(monkeypatch, tmp_path):
        """Run server.run(transport="stdio") against a valid sandboxed store,
        with mcp.run() mocked so the call returns instead of serving."""
        test_db = tmp_path / "session-test.db"
        conn = sqlite3.connect(str(test_db))
        from cairn.graph.schema import _apply_schema
        _apply_schema(conn)
        conn.close()
        monkeypatch.setenv("CAIRN_DB", str(test_db))

        with patch("cairn.mcp_server.server.mcp") as mock_mcp, \
             patch("cairn.mcp_server.server.verify_tool_count"), \
             patch("cairn.graph.watcher.ensure_fresh_force", return_value=0), \
             patch("cairn.memory.promotion.decay",
                   return_value={"expired_raw": 0, "archived_tribal": 0}):
            try:
                server.run(transport="stdio")
            except SystemExit as e:
                if e.code == 1:
                    pytest.fail("server.run() should not exit with code 1 for valid store")
            mock_mcp.run.assert_called_once()

    def test_preset_session_env_wins(self, monkeypatch, tmp_path, fresh_db):
        """A pre-set CAIRN_SESSION survives boot untouched."""
        monkeypatch.setenv("CAIRN_SESSION", "preset-42")
        self._boot_server(monkeypatch, tmp_path)
        assert os.environ["CAIRN_SESSION"] == "preset-42"

    def test_unset_session_env_gets_generated(self, monkeypatch, tmp_path, fresh_db):
        """Without CAIRN_SESSION, boot generates a 12-hex-char id."""
        monkeypatch.delenv("CAIRN_SESSION", raising=False)
        self._boot_server(monkeypatch, tmp_path)
        value = os.environ.get("CAIRN_SESSION", "")
        assert re.fullmatch(r"[0-9a-f]{12}", value), \
            f"expected a generated 12-hex id, got {value!r}"


class TestModelCacheRace:
    """Audit F5: _MODEL_CACHE's lazy SentenceTransformer load is reachable
    from both the embed flusher thread and tool threads. Unsynchronized it
    double-loads the weights and -- with two model keys racing the
    single-entry eviction -- KeyErrors the loser. Double-checked locking must
    give exactly one load per key and no exceptions."""

    @staticmethod
    def _fake_st_module(monkeypatch, delay: float = 0.02) -> list:
        """Install a fake sentence_transformers module recording loads.

        The artificial delay widens the race window so the test would reliably
        fail against an unsynchronized implementation.
        """
        loads: list = []

        class FakeSentenceTransformer:
            def __init__(self, name, **kw):
                time.sleep(delay)
                loads.append(name)
                self.max_seq_length = None

        mod = types.ModuleType("sentence_transformers")
        mod.SentenceTransformer = FakeSentenceTransformer
        monkeypatch.setitem(sys.modules, "sentence_transformers", mod)
        return loads

    def test_concurrent_same_key_loads_once(self, monkeypatch):
        from cairn.graph import embeddings as emb

        loads = self._fake_st_module(monkeypatch)
        monkeypatch.setattr(emb, "_MODEL_CACHE", {})

        n_threads = 8
        barrier = threading.Barrier(n_threads)
        results: list = []

        def worker():
            barrier.wait()
            results.append(emb._get_local_model("fake-m"))

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(loads) == 1, f"model must load exactly once, loaded {len(loads)}x"
        assert all(m is results[0] for m in results), "all threads share one model"

    def test_concurrent_different_keys_no_keyerror(self, monkeypatch):
        """Two model keys racing the single-entry eviction: the loser must
        still get a usable model, never a KeyError from the final lookup."""
        from cairn.graph import embeddings as emb

        loads = self._fake_st_module(monkeypatch, delay=0.05)
        monkeypatch.setattr(emb, "_MODEL_CACHE", {})

        errors: list = []

        def worker(name):
            try:
                model = emb._get_local_model(name)
                assert model is not None
            except Exception as exc:  # noqa: BLE001 -- recording for assertion
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(name,))
            for name in ("fake-a", "fake-b", "fake-a", "fake-b")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"racing loads raised: {errors}"
        assert set(loads) <= {"fake-a", "fake-b"}
