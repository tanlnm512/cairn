"""Red-first lifecycle pins for the HTTP daemon manager (FR-010).

healthz probe helpers, the off-macOS daemonize fallback, start-race
convergence, and the macOS manager cycle. Every process boundary is
monkeypatched (C-04): no real fork/exec/signal, no launchctl call; probe
tests bind ephemeral loopback sockets only, never port 9876.
"""
from __future__ import annotations

import contextlib
import http.server
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.cli import main
from cairn.mcp_server import lifecycle as lc

DAEMON_PID = 4242
MCP_URL = "http://127.0.0.1:9876/mcp"


class _ExecHalt(Exception):
    """Stops the fake child flow at the exec boundary."""


class _HealthzAfterFirstSpawn:
    """Probe stub: unhealthy until the first fake spawn marks it healthy."""

    def __init__(self):
        self._lock = threading.Lock()
        self._spawned = False

    def mark_spawned(self) -> None:
        with self._lock:
            self._spawned = True

    def __call__(self, *args, **kwargs) -> bool:
        with self._lock:
            return self._spawned


def _spawn_fork_recorder(probe: _HealthzAfterFirstSpawn):
    """os.fork stub recording spawns and flipping the probe healthy."""
    forks: list[int] = []

    def _fork() -> int:
        forks.append(1)
        probe.mark_spawned()
        return DAEMON_PID

    return forks, _fork


@contextlib.contextmanager
def _healthz_server(status: int):
    """Ephemeral loopback server answering /healthz with ``status``, 404 elsewhere."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status if self.path == "/healthz" else 404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestHealthzProbes:
    """mcp_url/healthz_responds: probes target /mcp and GET /healthz (200 = pass)."""

    def test_mcp_url_points_at_the_mcp_path(self):
        assert lc.mcp_url() == "http://127.0.0.1:9876/mcp"
        assert lc.mcp_url(port=9999, host="localhost") == "http://localhost:9999/mcp"

    def test_healthz_responds_true_on_200(self):
        with _healthz_server(200) as port:
            assert lc.healthz_responds(port=port, host="127.0.0.1")

    def test_healthz_responds_false_on_non_200(self):
        with _healthz_server(500) as port:
            assert not lc.healthz_responds(port=port, host="127.0.0.1")

    def test_healthz_responds_false_when_nothing_listens(self):
        assert not lc.healthz_responds(port=_free_port(), host="127.0.0.1")


class TestDaemonizeFallback:
    """TC-018 unit shadow: off-macOS the manager daemonizes instead of exiting 1."""

    def test_serve_start_off_macos_starts_daemon_not_exit_1(self, monkeypatch):
        monkeypatch.setattr(lc, "is_macos", lambda: False)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        monkeypatch.setattr(
            lc,
            "ensure_daemon_running",
            lambda port, host, *args, **kwargs: (DAEMON_PID, f"http://{host}:{port}/mcp", True),
            raising=False,
        )
        result = CliRunner().invoke(main, ["serve", "start"])
        assert result.exit_code == 0, result.output
        assert "macOS-only" not in result.output
        assert "/mcp" in result.output

    def test_ensure_daemon_running_daemonizes_and_persists_pid(self, monkeypatch):
        monkeypatch.setattr(lc, "is_macos", lambda: False)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        probe = _HealthzAfterFirstSpawn()
        monkeypatch.setattr(lc, "healthz_responds", probe, raising=False)
        _, fake_fork = _spawn_fork_recorder(probe)
        monkeypatch.setattr(os, "fork", fake_fork)
        monkeypatch.setattr(
            os, "_exit", lambda code: pytest.fail("the starting process must return, not _exit")
        )

        pid, url, started = lc.ensure_daemon_running(port=9876, host="127.0.0.1")

        assert (pid, url, started) == (DAEMON_PID, MCP_URL, True)
        home = Path(os.environ["CAIRN_HOME"])
        home.mkdir(parents=True, exist_ok=True)
        pidfiles = [
            p for p in home.rglob("*")
            if p.is_file() and p.read_text().strip() == str(DAEMON_PID)
        ]
        assert pidfiles, "daemon pid must be persisted to a pidfile under CAIRN_HOME"

    def test_daemonize_child_execs_serve_run_detached(self, monkeypatch):
        monkeypatch.setattr(lc, "is_macos", lambda: False)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        monkeypatch.setattr(lc, "healthz_responds", lambda *a, **k: False, raising=False)
        monkeypatch.setattr(os, "fork", lambda: 0)
        sessions: list[int] = []
        monkeypatch.setattr(os, "setsid", lambda: sessions.append(1))
        redirected: list[int] = []
        monkeypatch.setattr(os, "dup2", lambda src, dst: redirected.append(dst))
        recorded: dict[str, tuple] = {}

        def _exec(path, args, *rest, **kwargs):
            recorded["exec"] = (path, list(args))
            raise _ExecHalt

        def _halt(code):
            raise _ExecHalt

        for name in ("execv", "execve", "execvp", "execvpe"):
            monkeypatch.setattr(os, name, _exec)
        monkeypatch.setattr(os, "_exit", _halt)

        with pytest.raises(_ExecHalt):
            lc.ensure_daemon_running(port=9876, host="127.0.0.1")

        path, args = recorded["exec"]
        assert Path(path).name == "cairn"
        assert args[1:] == ["serve", "run", "--port", "9876", "--host", "127.0.0.1"]
        assert sessions, "the daemon child must detach via setsid"
        assert {1, 2} <= set(redirected), "daemon stdio must be redirected off the terminal"


class TestStartRaceConvergence:
    """NFR-004: concurrent starts converge to one daemon, warm daemon reused."""

    def test_concurrent_starts_spawn_once_and_converge(self, monkeypatch):
        monkeypatch.setattr(lc, "is_macos", lambda: False)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        probe = _HealthzAfterFirstSpawn()
        monkeypatch.setattr(lc, "healthz_responds", probe, raising=False)
        forks, fake_fork = _spawn_fork_recorder(probe)
        monkeypatch.setattr(os, "fork", fake_fork)

        results: list[tuple] = []
        errors: list[BaseException] = []

        def _start():
            try:
                results.append(lc.ensure_daemon_running(port=9876, host="127.0.0.1"))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_start) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert errors == []
        assert not any(t.is_alive() for t in threads), "the start sequence must not deadlock"
        assert len(results) == 4
        assert len(forks) == 1, "exactly one start sequence may spawn the daemon"
        assert sum(1 for _, _, started in results if started) == 1
        assert {pid for pid, _, _ in results} == {DAEMON_PID}
        assert {url for _, url, _ in results} == {MCP_URL}

    def test_healthy_daemon_reused_not_respawned(self, monkeypatch):
        monkeypatch.setattr(lc, "is_macos", lambda: False)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        monkeypatch.setattr(lc, "healthz_responds", lambda *a, **k: True, raising=False)
        forks, fake_fork = _spawn_fork_recorder(_HealthzAfterFirstSpawn())
        monkeypatch.setattr(os, "fork", fake_fork)

        _, url, started = lc.ensure_daemon_running(port=9876, host="127.0.0.1")

        assert started is False
        assert url == MCP_URL
        assert forks == [], "a healthy daemon must be reused, never respawned"


class TestMacOSManagerCycle:
    """TC-017 unit shadow: start/status/restart/stop drive the HTTP daemon via launchd."""

    def test_manager_cycle_reports_mcp_daemon(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lc, "is_macos", lambda: True)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        monkeypatch.setenv("CAIRN_WORKSPACE", str(tmp_path / "ws"))
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "ws" / ".kg" / "graph.db"))
        monkeypatch.setattr(lc, "is_loaded", lambda: False)
        monkeypatch.setattr(lc, "load", lambda: True)
        unloaded: list[int] = []
        loaded = {"state": True}

        def _is_loaded() -> bool:
            return loaded["state"]

        def _unload() -> bool:
            unloaded.append(1)
            loaded["state"] = False
            return True

        monkeypatch.setattr(lc, "unload", _unload)
        monkeypatch.setattr(lc, "running_pid", lambda: 4321)
        monkeypatch.setattr(lc, "healthz_responds", lambda *a, **k: True, raising=False)

        runner = CliRunner()

        start = runner.invoke(main, ["serve", "start", "--port", "9876"])
        assert start.exit_code == 0, start.output
        assert "/mcp" in start.output, "the managed daemon serves the /mcp endpoint"

        monkeypatch.setattr(lc, "is_loaded", _is_loaded)
        status = runner.invoke(main, ["serve", "status", "--port", "9876"])
        assert status.exit_code == 0, status.output
        assert "4321" in status.output and "HEALTHY" in status.output

        restart = runner.invoke(main, ["serve", "restart", "--port", "9876"])
        assert restart.exit_code == 0, restart.output
        assert unloaded, "restart keeps its stop+start composition"

        stop = runner.invoke(main, ["serve", "stop"])
        assert stop.exit_code == 0, stop.output
        assert len(unloaded) == 1, "the post-restart stop finds nothing left to unload"
        assert "stopped." in stop.output


class TestCgBinResolution:
    """The plist/daemon child launches the cairn that's RUNNING, never a
    stale PATH install (a pipx 0.20.2 binary kept serving the retired /sse)."""

    def test_running_executable_wins_over_which(self, monkeypatch, tmp_path):
        running = tmp_path / "cairn"
        running.write_text("#!/bin/sh\n")
        on_path = tmp_path / "stale-cairn"
        on_path.write_text("#!/bin/sh\n")
        monkeypatch.setattr(sys, "argv", [str(running), "connect"])
        monkeypatch.setattr(lc, "shutil_which", lambda name: str(on_path))
        assert lc.cg_bin() == str(running.resolve())

    def test_cairn_bin_env_wins_first(self, monkeypatch, tmp_path):
        pinned = tmp_path / "pinned-cairn"
        pinned.write_text("#!/bin/sh\n")
        running = tmp_path / "cairn"
        running.write_text("#!/bin/sh\n")
        monkeypatch.setattr(sys, "argv", [str(running), "connect"])
        monkeypatch.setenv("CAIRN_BIN", str(pinned))
        assert lc.cg_bin() == str(pinned.resolve())


class TestLaunchdStartRobustness:
    """A loaded-but-mid-respawn agent is awaited, never double-loaded; every
    launchctl call is bounded so the start lock cannot wedge (NFR-004)."""

    def test_loaded_unhealthy_waits_for_respawn_not_double_load(self, monkeypatch):
        monkeypatch.setattr(lc, "is_macos", lambda: True)
        monkeypatch.setattr(lc, "is_loaded", lambda: True)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        calls: list[str] = []

        def _healthz(*args, **kwargs):
            calls.append("healthz")
            return len(calls) >= 3

        monkeypatch.setattr(lc, "healthz_responds", _healthz)
        monkeypatch.setattr(lc, "write_plist", lambda plist: calls.append("write_plist"))
        monkeypatch.setattr(lc, "load", lambda: calls.append("load") or True)
        monkeypatch.setattr(lc, "unload", lambda: calls.append("unload") or True)
        monkeypatch.setattr(lc, "running_pid", lambda: 4321)
        monkeypatch.setattr("time.sleep", lambda s: None)

        pid, url, started = lc.ensure_daemon_running(port=9876, host="127.0.0.1")

        assert (pid, url, started) == (4321, MCP_URL, False)
        assert "load" not in calls, "an already-loaded agent must not be double-loaded"
        assert "write_plist" not in calls
        assert "unload" not in calls

    def test_launchctl_calls_carry_timeouts(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lc, "is_macos", lambda: True)
        plist = tmp_path / "agent.plist"
        plist.write_text("<plist/>")
        monkeypatch.setattr(lc, "plist_path", lambda: plist)
        seen: list[tuple[list[str], object]] = []

        def fake_run(args, **kwargs):
            seen.append((list(args), kwargs.get("timeout")))
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)

        lc.is_loaded()
        lc.running_pid()
        lc.load()
        lc.unload()

        launchctl = [entry for entry in seen if entry[0][0] == "launchctl"]
        assert len(launchctl) >= 4, "expected the list/list/load/unload launchctl calls"
        for args, timeout in launchctl:
            assert timeout, f"launchctl {' '.join(args[:2])} must be bounded by a timeout"


class TestBoundedStartLock:
    """The start lock must fail bounded, never block a caller forever."""

    def test_start_lock_raises_runtime_error_when_held(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lc, "_lockfile_path", lambda: tmp_path / "mcp-daemon.lock")
        with lc._start_lock():
            with pytest.raises(RuntimeError, match="start lock"):
                with lc._start_lock(timeout=0.05):
                    pass  # pragma: no cover - never reached while the lock is held

    def test_start_lock_acquires_when_free(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lc, "_lockfile_path", lambda: tmp_path / "mcp-daemon.lock")
        with lc._start_lock(timeout=1.0):
            pass


class TestDaemonizeWedgeRecovery:
    """A verified pidfile daemon that holds the db but never serves healthz is
    cycled before a replacement fork (the launchd wedge-unload counterpart)."""

    def _patch_wedge(self, monkeypatch, tmp_path, *, holds_db):
        monkeypatch.setattr(lc, "pidfile_pid", lambda: 424242)
        monkeypatch.setattr(
            lc, "_pid_cmdline", lambda pid: "/usr/local/bin/cairn serve run --port 9876"
        )
        holders = {424242} if holds_db else set()
        monkeypatch.setattr(lc, "_db_holder_pids", lambda db_path: holders)
        killed: list[int] = []
        monkeypatch.setattr(
            lc, "terminate_pid", lambda pid, **kwargs: killed.append(pid)
        )
        monkeypatch.setattr(lc, "healthz_responds", lambda *a, **k: False)
        monkeypatch.setattr(lc, "pidfile_path", lambda: tmp_path / "mcp-daemon.pid")
        return killed

    def test_wedged_pidfile_daemon_is_cycled(self, monkeypatch, tmp_path):
        killed = self._patch_wedge(monkeypatch, tmp_path, holds_db=True)
        (tmp_path / "mcp-daemon.pid").write_text("424242\n")

        lc._cycle_wedged_daemon(9876, "127.0.0.1", str(tmp_path / "graph.db"))

        assert killed == [424242], "the wedged verified daemon must be terminated"
        assert not (tmp_path / "mcp-daemon.pid").exists(), "the stale pidfile must go"

    def test_unverified_or_nonholder_pid_is_left_alone(self, monkeypatch, tmp_path):
        killed = self._patch_wedge(monkeypatch, tmp_path, holds_db=False)
        (tmp_path / "mcp-daemon.pid").write_text("424242\n")

        lc._cycle_wedged_daemon(9876, "127.0.0.1", str(tmp_path / "graph.db"))

        assert killed == [], "never kill a daemon that does not hold this db"
        assert (tmp_path / "mcp-daemon.pid").exists()


class TestServeStopUnloadTimeout:
    """A launchctl unload timeout must not abort stop with a traceback or skip
    the pidfile/stray cleanup, and must fail the command."""

    def test_stop_runs_cleanup_and_exits_1_on_unload_timeout(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lc, "is_macos", lambda: True)
        monkeypatch.setattr(lc, "is_loaded", lambda: True)

        def _boom():
            raise RuntimeError("launchctl unload timed out after 15s")

        monkeypatch.setattr(lc, "unload", _boom)
        monkeypatch.setattr(lc, "pidfile_pid", lambda: None)
        calls: list[str] = []
        monkeypatch.setattr(lc, "find_strays", lambda db_path: calls.append("strays") or [])
        monkeypatch.setenv("CAIRN_HOME", str(tmp_path))

        result = CliRunner().invoke(main, ["serve", "stop"])

        assert result.exit_code == 1, result.output
        assert "launchctl unload timed out" in result.output
        assert calls == ["strays"], "stray sweep must run despite the unload failure"
        assert "stopped." not in result.output


class TestServeStatusResolvesDaemonBind:
    """serve_status probes the port/host the daemon was started with, not just
    the command's own defaults."""

    def test_status_probes_the_plist_port(self, monkeypatch, tmp_path):
        import plistlib

        plist = tmp_path / "dev.cairn.mcp.plist"
        plist.write_bytes(
            plistlib.dumps(
                {
                    "Label": "dev.cairn.mcp",
                    "ProgramArguments": [
                        "/usr/local/bin/cairn", "serve", "run",
                        "--port", "9999", "--host", "127.0.0.1",
                    ],
                }
            )
        )
        monkeypatch.setattr(lc, "is_macos", lambda: True)
        monkeypatch.setattr(lc, "plist_path", lambda: plist)
        monkeypatch.setattr(lc, "is_loaded", lambda: False)
        monkeypatch.setattr(lc, "running_pid", lambda: None)
        monkeypatch.setattr(lc, "pidfile_pid", lambda: None)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        probed: list[tuple[int, str]] = []

        def _healthz(port, host, *args, **kwargs):
            probed.append((port, host))
            return False

        monkeypatch.setattr(lc, "healthz_responds", _healthz)
        monkeypatch.setenv("CAIRN_HOME", str(tmp_path))

        result = CliRunner().invoke(main, ["serve", "status"])

        assert result.exit_code == 0, result.output
        assert probed == [(9999, "127.0.0.1")], (
            "status must probe the daemon's configured port, not the CLI default"
        )
        assert "9999" in result.output

    def test_explicit_port_flag_wins(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lc, "is_macos", lambda: False)
        monkeypatch.setattr(lc, "pidfile_pid", lambda: None)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        probed: list[tuple[int, str]] = []

        def _healthz(port, host, *args, **kwargs):
            probed.append((port, host))
            return False

        monkeypatch.setattr(lc, "healthz_responds", _healthz)
        monkeypatch.setenv("CAIRN_HOME", str(tmp_path))

        result = CliRunner().invoke(main, ["serve", "status", "--port", "9443"])

        assert result.exit_code == 0, result.output
        assert probed == [(9443, lc.DEFAULT_HOST)]


class TestServeRestartResolvesDaemonBind:
    """serve_restart stops/starts on the daemon's configured bind, never on
    the fixed CLI defaults (a custom-port daemon must survive a restart)."""

    def test_restart_reuses_the_configured_bind(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lc, "is_macos", lambda: True)
        monkeypatch.setattr(lc, "is_loaded", lambda: False)
        monkeypatch.setattr(lc, "pidfile_pid", lambda: None)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        monkeypatch.setattr(lc, "daemon_bind", lambda: (9999, "127.0.0.1"))
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
        starts: list[tuple[int, str]] = []

        def _ensure(port, host, *args, **kwargs):
            starts.append((port, host))
            return 4321, f"http://{host}:{port}/mcp", True

        monkeypatch.setattr(lc, "ensure_daemon_running", _ensure)

        result = CliRunner().invoke(main, ["serve", "restart"])

        assert result.exit_code == 0, result.output
        assert "stopped." in result.output, "restart keeps its stop+start composition"
        assert starts == [(9999, "127.0.0.1")], (
            "restart must restart the daemon on its configured bind"
        )


class TestServeStopVerifiesShutdown:
    """serve stop must not report success while the managed daemon survives."""

    def test_stop_fails_when_the_daemon_is_still_there(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lc, "is_macos", lambda: True)
        monkeypatch.setattr(lc, "is_loaded", lambda: True)
        monkeypatch.setattr(lc, "running_pid", lambda: 4321)
        monkeypatch.setattr(lc, "pidfile_pid", lambda: None)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setenv("CAIRN_HOME", str(tmp_path))

        result = CliRunner().invoke(main, ["serve", "stop"])

        assert result.exit_code == 1, result.output
        assert "still running" in result.output
        assert "stopped." not in result.output


class TestServeRestartSurvivesFailedStop:
    """A failed stop must not abort the restart with the daemon down: the
    start attempt reuses a healthy daemon or cycles a wedged one."""

    def test_restart_starts_after_a_failed_stop(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lc, "is_macos", lambda: True)
        monkeypatch.setattr(lc, "is_loaded", lambda: True)

        def _boom():
            raise RuntimeError("launchctl unload timed out after 15s")

        monkeypatch.setattr(lc, "unload", _boom)
        monkeypatch.setattr(lc, "running_pid", lambda: None)
        monkeypatch.setattr(lc, "pidfile_pid", lambda: None)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
        starts: list[tuple[int, str]] = []

        def _ensure(port, host, *args, **kwargs):
            starts.append((port, host))
            return 4321, f"http://{host}:{port}/mcp", True

        monkeypatch.setattr(lc, "ensure_daemon_running", _ensure)

        result = CliRunner().invoke(main, ["serve", "restart"])

        assert starts == [(lc.DEFAULT_PORT, lc.DEFAULT_HOST)], (
            "restart must attempt the start even though stop failed"
        )
        assert result.exit_code == 0, result.output


class TestPlistCarriesApiKey:
    """A keyed non-loopback daemon must reach launchd with its key: the plist
    env carries CAIRN_MCP_API_KEY, else the bind guard exits and KeepAlive
    crash-loops."""

    def test_render_plist_embeds_the_api_key(self):
        plist = lc.render_plist(port=9876, host="0.0.0.0", api_key="secret-1")
        assert plist["EnvironmentVariables"]["CAIRN_MCP_API_KEY"] == "secret-1"

    def test_render_plist_omits_the_key_when_none(self):
        plist = lc.render_plist(port=9876, host="127.0.0.1")
        assert "CAIRN_MCP_API_KEY" not in plist["EnvironmentVariables"]

    def test_ensure_daemon_running_threads_the_key_into_the_plist(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(lc, "is_macos", lambda: True)
        monkeypatch.setattr(lc, "is_loaded", lambda: False)
        monkeypatch.setattr(lc, "find_strays", lambda db_path: [])
        written: list[dict] = []
        monkeypatch.setattr(lc, "write_plist", lambda plist: written.append(plist))
        monkeypatch.setattr(lc, "load", lambda: True)
        monkeypatch.setattr(lc, "healthz_responds", lambda *a, **k: True, raising=False)
        monkeypatch.setattr(lc, "running_pid", lambda: 4321)
        monkeypatch.setenv("CAIRN_HOME", str(tmp_path))
        monkeypatch.setenv("CAIRN_WORKSPACE", str(tmp_path / "ws"))
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "ws" / ".kg" / "graph.db"))

        pid, url, started = lc.ensure_daemon_running(
            9876, "0.0.0.0", api_key="secret-1",
        )

        assert started is True
        assert written, "the launchd path must (re)write the plist"
        assert written[0]["EnvironmentVariables"]["CAIRN_MCP_API_KEY"] == "secret-1"

    def test_serve_start_resolves_the_key_explicit_then_env(self, monkeypatch):
        monkeypatch.setattr(lc, "is_macos", lambda: False)
        seen: list[dict] = []

        def _ensure(port, host, *args, **kwargs):
            seen.append(kwargs)
            return 4321, f"http://{host}:{port}/mcp", True

        monkeypatch.setattr(lc, "ensure_daemon_running", _ensure)
        monkeypatch.setenv("CAIRN_MCP_API_KEY", "env-key")

        env_only = CliRunner().invoke(main, ["serve", "start", "--port", "9876"])
        assert env_only.exit_code == 0, env_only.output
        explicit = CliRunner().invoke(
            main, ["serve", "start", "--port", "9876", "--api-key", "flag-key"],
        )
        assert explicit.exit_code == 0, explicit.output

        assert seen[0].get("api_key") == "env-key"
        assert seen[1].get("api_key") == "flag-key"


class TestDaemonizeExecFailureLogging:
    """A daemon exec failure must land in the daemon log (stderr is redirected
    there) instead of dying silently behind a misleading healthz timeout."""

    def test_exec_failure_writes_the_log_and_exits_1(self, monkeypatch, capsys):
        monkeypatch.setattr(os, "setsid", lambda: None)
        monkeypatch.setattr(os, "open", lambda *a, **k: 3)
        monkeypatch.setattr(os, "dup2", lambda *a, **k: None)
        monkeypatch.setattr(os, "close", lambda fd: None)

        def _boom(*a, **k):
            raise OSError("no such file")

        monkeypatch.setattr(os, "execvpe", _boom)
        codes: list[int] = []

        def _exit(code):
            codes.append(code)
            raise _ExecHalt

        monkeypatch.setattr(os, "_exit", _exit)

        with pytest.raises(_ExecHalt):
            lc._daemonize_exec(9876, "127.0.0.1", "/nope/cairn", {})

        assert codes == [1]
        assert "failed to exec" in capsys.readouterr().err
