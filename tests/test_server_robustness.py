"""Test MCP server robustness improvements for M9, L1, L3.

Tests for:
- M9: Store existence check at boot
- L1: Tool count assertion
- L3: @instrument error sanitization
- Buffered-telemetry drain (audit F3): the drain helper must flush every
  sink, isolated per sink.
- Model cache race (audit F5): _MODEL_CACHE lazy load must be thread-safe.
- HTTP boots and the warm daemon: a bare boot takes the HTTP serving path;
  the daemon serves read-write by default with --read-only as the opt-out;
  a second client's first call rides the same warm process.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cairn.mcp_server import server


def _bare_http_boot(monkeypatch, tmp_path) -> tuple[dict, list[str]]:
    """Run a bare server.run(port=0) boot against a scratch store.

    Serving is mocked at the app-build boundary so the boot returns instead
    of listening. Returns (http_kwargs, boot_work): the kwargs the HTTP app
    build received (empty unless the boot served via the HTTP path) and the
    write-side boot work that ran ("catch-up"/"decay").
    """
    from cairn.graph.schema import _apply_schema

    test_db = tmp_path / "bare-http-boot.db"
    conn = sqlite3.connect(str(test_db))
    _apply_schema(conn)
    conn.close()
    monkeypatch.setenv("CAIRN_DB", str(test_db))

    http_kwargs: dict = {}
    boot_work: list[str] = []

    def _fake_build(**kwargs):
        http_kwargs.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(server, "mcp", MagicMock())
    monkeypatch.setattr(server, "verify_tool_count", lambda: None)
    monkeypatch.setattr(server, "_build_http_app", _fake_build)
    monkeypatch.setattr("uvicorn.Server", MagicMock())
    monkeypatch.setattr("uvicorn.Config", MagicMock())
    monkeypatch.setattr(
        "cairn.graph.watcher.ensure_fresh_force",
        lambda _conn, _ws: boot_work.append("catch-up") or 0,
    )
    monkeypatch.setattr(
        "cairn.memory.promotion.decay",
        lambda *a, **k: boot_work.append("decay")
        or {"expired_raw": 0, "archived_tribal": 0},
    )
    try:
        server.run(port=0)
    except SystemExit as exc:
        if exc.code == 1:
            pytest.fail("bare boot must not exit 1 on a valid store")
    return http_kwargs, boot_work


class TestStoreExistenceCheck:
    """Test M9: Server boot guard for missing store."""

    def test_valid_store_proceeds(self, monkeypatch, tmp_path):
        """Valid DB with symbols table boots the HTTP serving path."""
        http_kwargs, _ = _bare_http_boot(monkeypatch, tmp_path)
        assert http_kwargs.get("port") == 0, "bare boot must serve via the HTTP app"

    # ------------------------------------------------------------------
    # The boot-guard error must name the resolved db
    # path, the env resolution chain in effect, and the remediation --
    # not the bare OperationalError text alone. RED until the boot-guard
    # message is enriched.
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
        """boot against a missing store directory exits 1 with an
        error naming the resolved db path, the env chain in effect, and the
        set-CAIRN-HOME / cairn init && cairn build remediation."""
        home, workspace, db, knowledge = self._missing_store_env(
            monkeypatch, tmp_path
        )

        with patch("cairn.mcp_server.server.mcp"), \
             patch("cairn.mcp_server.server.verify_tool_count"):
            with pytest.raises(SystemExit) as exc_info:
                server.run(port=0)

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
        """Edge case: chain entries with no env value in effect are still
        named, rendered as 'unset' (value or 'unset' per entry)."""
        monkeypatch.setenv("CAIRN_DB",
                           str((tmp_path / "no-such-store" / "missing.db").resolve()))
        monkeypatch.delenv("CAIRN_WORKSPACE", raising=False)
        monkeypatch.delenv("CAIRN_KNOWLEDGE", raising=False)

        with patch("cairn.mcp_server.server.mcp"), \
             patch("cairn.mcp_server.server.verify_tool_count"):
            with pytest.raises(SystemExit) as exc_info:
                server.run(port=0)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "CAIRN_WORKSPACE" in err
        assert "CAIRN_KNOWLEDGE" in err
        assert "unset" in err

    def test_cli_get_db_missing_store_error_is_actionable(
        self, monkeypatch, tmp_path
    ):
        """The CLI db-open path raises the SAME OperationalError type
        when the store's parent directory is missing, but the text carries
        the resolved path, the env chain, and the remediation -- the red
        that drives the schema.get_db pre-check."""
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
    """Audit F3: _drain_buffered_telemetry must invoke every subsystem's
    flush entry point exactly once, isolated per sink."""

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


class TestSessionIdentity:
    """run() stamps a per-process CAIRN_SESSION at boot.

    The env var has three readers (metric_buffering, telemetry/events,
    graph/builder) that default the session column to "unknown" — boot is the
    writer. An externally provided value must keep precedence (setdefault),
    and the generated id must be a 12-char hex string.
    """

    @staticmethod
    def _boot_server(monkeypatch, tmp_path):
        """Bare server.run(port=0) boot against a valid sandboxed store, with
        serving mocked; asserts the HTTP serving path was taken."""
        http_kwargs, _ = _bare_http_boot(monkeypatch, tmp_path)
        assert http_kwargs, "boot must take the HTTP serving path"

    def test_preset_session_env_wins(self, monkeypatch, tmp_path):
        """A pre-set CAIRN_SESSION survives boot untouched."""
        monkeypatch.setenv("CAIRN_SESSION", "preset-42")
        self._boot_server(monkeypatch, tmp_path)
        assert os.environ["CAIRN_SESSION"] == "preset-42"

    def test_unset_session_env_gets_generated(self, monkeypatch, tmp_path):
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


# --- Live HTTP daemon boots (subprocess children) -----------------------------

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "robustness-test", "version": "0"},
    },
}
_BOOT_RUN = (
    "import sys; from cairn.mcp_server.server import run; "
    "run(port=int(sys.argv[1]))"
)
_BOOT_CLI = "from cairn.cli import main; main()"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _live_store(tmp_path) -> str:
    """Scratch store with the full schema (satisfies the boot guard)."""
    from cairn.graph.schema import _apply_schema

    db_path = tmp_path / "live-store.db"
    conn = sqlite3.connect(str(db_path))
    _apply_schema(conn)
    conn.close()
    return str(db_path)


def _child_env(db_path: str) -> dict:
    env = dict(os.environ)
    env["CAIRN_DB"] = db_path
    env["CAIRN_EMBED_BACKEND"] = "hash"
    env.pop("CAIRN_READ_ONLY", None)
    env.pop("CAIRN_MCP_API_KEY", None)
    return env


def _log_tail(log_path, limit: int = 1500) -> str:
    try:
        return log_path.read_text()[-limit:]
    except OSError:
        return "<unreadable>"


def _wait_http_ready(url: str, proc: subprocess.Popen, log_path, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"daemon exited rc={proc.returncode} before serving; log tail: {_log_tail(log_path)}"
            )
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return  # any HTTP answer means the server is up
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.25)
    raise AssertionError(
        f"daemon never answered on {url}; log tail: {_log_tail(log_path)}"
    )


@contextmanager
def _daemon_child(build_argv, db_path: str, tmp_path):
    """Boot one daemon child; yields (base_url, proc, log_path); always tears down."""
    port = _free_port()
    log_path = tmp_path / f"daemon-{port}.log"
    with open(log_path, "w") as log:
        proc = subprocess.Popen(
            build_argv(port),
            env=_child_env(db_path),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
    try:
        _wait_http_ready(f"http://127.0.0.1:{port}/healthz", proc, log_path)
        yield f"http://127.0.0.1:{port}", proc, log_path
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@contextmanager
def _cli_daemon(db_path: str, tmp_path, *serve_flags: str):
    """Foreground `cairn serve` daemon on a free port."""

    def _argv(port: int) -> list[str]:
        return [
            sys.executable, "-c", _BOOT_CLI, "serve",
            "--db", db_path, *serve_flags, "--port", str(port),
        ]

    with _daemon_child(_argv, db_path, tmp_path) as yielded:
        yield yielded


@contextmanager
def _bare_run_daemon(db_path: str, tmp_path):
    """Foreground bare server.run(port=...) daemon on a free port."""

    def _argv(port: int) -> list[str]:
        return [sys.executable, "-c", _BOOT_RUN, str(port)]

    with _daemon_child(_argv, db_path, tmp_path) as yielded:
        yield yielded


def _post_rpc(base_url: str, payload: dict, session_id: str | None = None):
    """POST one JSON-RPC message; return (headers, body)."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    request = urllib.request.Request(
        base_url + "/mcp",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.headers, response.read().decode()
    except urllib.error.HTTPError as err:
        return err.headers, err.read().decode()


def _rpc_message(body: str) -> dict:
    """Parse one JSON-RPC message out of a plain-JSON or SSE-framed body."""
    body = body.strip()
    if body.startswith("{"):
        return json.loads(body)
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise AssertionError(f"no JSON-RPC message in response: {body[:500]}")


def _open_session(base_url: str) -> str:
    """Initialize one MCP session; return its mcp-session-id."""
    headers, _ = _post_rpc(base_url, _INITIALIZE)
    session_id = headers.get("mcp-session-id")
    assert session_id, "initialize response carried no mcp-session-id"
    _post_rpc(
        base_url,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session_id,
    )
    return session_id


def _call_tool(base_url: str, session_id: str, request_id: int, name: str, arguments: dict) -> dict:
    """Invoke one tool call; return the JSON-RPC response message."""
    _, body = _post_rpc(
        base_url,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        session_id,
    )
    return _rpc_message(body)


def _health(base_url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(base_url + "/healthz", timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as err:
        return err.code, {}


class TestReadWriteDaemonDefault:
    """The daemon serves read-write by default; --read-only is the opt-out."""

    def test_default_boot_serves_read_write_across_clients(self, tmp_path):
        """Default daemon: health reports read-write, a write tool from one
        session is recallable from a second session."""
        db_path = _live_store(tmp_path)
        with _cli_daemon(db_path, tmp_path) as (base_url, proc, log_path):
            status, payload = _health(base_url)
            assert status == 200, (
                f"healthz must answer on the HTTP daemon; log tail: {_log_tail(log_path)}"
            )
            assert payload["read_only"] is False, "default daemon must be read-write"

            sid_a = _open_session(base_url)
            written = _call_tool(base_url, sid_a, 2, "record_memory", {
                "type": "decision",
                "title": "shared daemon decision probe",
                "body": "Two agents share one writable daemon.",
            })
            write_result = written.get("result")
            assert write_result and not write_result.get("isError"), written
            assert not written.get("error"), written

            sid_b = _open_session(base_url)
            recalled = _call_tool(base_url, sid_b, 3, "recall_memory", {
                "query": "shared daemon decision probe",
            })
            recall_result = recalled.get("result")
            assert recall_result and not recall_result.get("isError"), recalled
            text = recall_result["content"][0]["text"]
            assert "shared daemon decision probe" in text

    def test_read_only_opt_out_blocks_write_tools(self, tmp_path):
        """--read-only daemon: health reports read-only and the write tool
        answers with a tool error."""
        db_path = _live_store(tmp_path)
        with _cli_daemon(db_path, tmp_path, "--read-only") as (base_url, proc, log_path):
            status, payload = _health(base_url)
            assert status == 200, (
                f"healthz must answer on the HTTP daemon; log tail: {_log_tail(log_path)}"
            )
            assert payload["read_only"] is True

            sid = _open_session(base_url)
            written = _call_tool(base_url, sid, 2, "record_memory", {
                "type": "decision",
                "title": "must not persist",
                "body": "read-only probe",
            })
            assert written.get("error") or \
                written.get("result", {}).get("isError") is True, written


class TestWarmDaemonAmortization:
    """A warm daemon answers a second client's first call without rebooting."""

    def test_second_client_first_call_rides_warm_process(self, tmp_path):
        """Second session's first tool call succeeds on the same process, with
        one boot (one readiness line) and the first session still live."""
        db_path = _live_store(tmp_path)
        with _bare_run_daemon(db_path, tmp_path) as (base_url, proc, log_path):
            sid_a = _open_session(base_url)

            sid_b = _open_session(base_url)
            first = _call_tool(base_url, sid_b, 2, "recall_memory", {
                "query": "warm-pin-no-such-memory",
            })
            first_result = first.get("result")
            assert first_result and not first_result.get("isError"), first

            still = _call_tool(base_url, sid_a, 3, "recall_memory", {
                "query": "warm-pin-no-such-memory",
            })
            still_result = still.get("result")
            assert still_result and not still_result.get("isError"), still

            assert proc.poll() is None, "daemon must not restart between clients"
        assert log_path.read_text().count("listening on") == 1, \
            "boot work must run once per process"
