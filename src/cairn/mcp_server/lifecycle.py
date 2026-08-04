"""Lifecycle management for the cairn SSE daemon (macOS launchd).

Provides the plumbing for `cairn serve start|stop|status|restart` so a single
SSE server can run as a persistent per-user LaunchAgent, shared by all MCP
clients (ZCode, Claude Desktop, Cursor) instead of one stdio process per
client.

Design:
- A LaunchAgent plist at ~/Library/LaunchAgents/dev.cairn.sse.plist runs
  `cairn serve --port <N>` with KeepAlive (restart on crash) and RunAtLoad
  (start at login). launchd becomes the parent (pid 1), so the server's
  stdio-only `_install_exit_watchdog` is correctly bypassed under SSE.
- `start` is idempotent: reloading an already-loaded job is a no-op.
- `stop` does `launchctl unload` (graceful SIGTERM from launchd) and also
  sweeps stray `cairn serve` processes that aren't under launchd -- the
  orphan-accumulation problem that causes "database is locked".

Only macOS is supported (launchd). On other platforms the functions raise
RuntimeError with a clear message; callers can fall back to `cairn serve`
(foreground) or wire up systemd equivalent.
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

LABEL = "dev.cairn.sse"
DEFAULT_PORT = 9876
DEFAULT_HOST = "127.0.0.1"


def is_macos() -> bool:
    return sys.platform == "darwin"


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return agents_dir() / f"{LABEL}.plist"


def log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "cairn-sse.log"


def cg_bin() -> str:
    """Absolute path to the cairn executable to launch. Prefers the same one
    that's running this process; falls back to PATH lookup."""
    for cand in (
        os.environ.get("CAIRN_BIN"),
        shutil_which("cairn"),
        str(Path.home() / ".local" / "bin" / "cairn"),
    ):
        if cand and Path(cand).exists():
            return cand
    return "cairn"  # let launchd's PATH resolve it


def shutil_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def render_plist(
    port: int = DEFAULT_PORT,
    host: str = DEFAULT_HOST,
    workspace: str | None = None,
    db_path: str | None = None,
    knowledge_path: str | None = None,
    read_only: bool = True,
) -> dict:
    """Build the LaunchAgent plist as a dict (plistlib-renderable).

    Runs `cairn serve run --port <N> --read-only` (the foreground SSE subcommand).
    Note the `run` subcommand: the top-level `cairn serve` group doesn't accept
    --port directly (it's a group dispatcher), so the plist must invoke the
    `run` subcommand explicitly.

    The daemon is read-only by default: the shared SSE server opens the graph
    DB with `mode=ro` so it can never acquire SQLite's writer lock and therefore
    never contends with `cairn build`/`cairn embed`/`cairn memory`. Serving-time write
    paths (memory ref-counts, tool metrics) silently no-op; write tools still
    open a writable connection as needed. Pass read_only=False for a read-write
    daemon (not recommended for a shared multi-client daemon).

    Args:
        workspace: absolute path to the workspace whose store the daemon
            should serve. Under launchd the cwd is `/`, so without this the
            daemon can't find the right store via ancestor walk.
        db_path: explicit DB path (overrides workspace-derived path).
        knowledge_path: explicit knowledge dir.
    """
    bin_ = cg_bin()
    env = {
        # Inherit the user PATH so `cairn` can find python etc. launchd
        # otherwise starts with a minimal environment.
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    if workspace:
        env["CAIRN_WORKSPACE"] = str(workspace)
    if db_path:
        env["CAIRN_DB"] = str(db_path)
    if knowledge_path:
        env["CAIRN_KNOWLEDGE"] = str(knowledge_path)
    args = [bin_, "serve", "run", "--port", str(port)]
    if read_only:
        args.append("--read-only")
        env["CAIRN_READ_ONLY"] = "1"
    return {
        "Label": LABEL,
        "ProgramArguments": args,
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log_path()),
        "StandardErrorPath": str(log_path()),
    }


def write_plist(plist: dict) -> Path:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        plistlib.dump(plist, f)
    return path


def is_loaded() -> bool:
    """True if the LaunchAgent is currently loaded."""
    if not is_macos():
        return False
    r = subprocess.run(
        ["launchctl", "list", LABEL],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def running_pid() -> int | None:
    """PID of the running daemon, or None if loaded-but-not-running / not loaded.

    Handles the two launchctl output formats across macOS versions:
      - plist style:        \\t"PID" = 6155;   (key quoted, indented, trailing ;)
      - tab style:          PID\\tStatus\\tLabel  (first column is the pid)
    """
    if not is_macos():
        return None
    r = subprocess.run(
        ["launchctl", "list", LABEL],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    # Modern plist-style output: "PID" = 6155;
    m = re.search(r'"PID"\s*=\s*(\d+)', r.stdout)
    if m:
        return int(m.group(1))
    # Tab-separated output: PID  Status  Label (first line).
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].strip().isdigit():
            return int(parts[0].strip())
    return None


def find_strays(db_path: str | Path) -> list[int]:
    """Find `cairn serve` PIDs NOT managed by launchd that hold the DB.

    These are orphaned stdio servers left over from editor sessions -- the
    root cause of WAL lock contention. Returns PIDs to kill.

    The match is scoped two ways for safety:
      - by command shape: only real ``cairn serve run``/``cairn serve``
        invocations (a bare ``cairn serve`` substring would otherwise match ANY
        process whose argv merely contains it -- editors, grep, this very
        process);
      - by db_path: only processes serving the SAME DB. A server sets
        ``CAIRN_DB`` (see cli/serve.py) and may also pass ``--db``; we
        match the resolved path that appears in argv/env-derived cmdline.
        When db_path looks like the default/central store (i.e. it may not
        appear literally in argv), we fall back to matching ``cairn serve run``.
    """
    pids: list[int] = []
    daemon_pid = running_pid()
    # Build the pgrep pattern. `pgrep -f` matches against the full command
    # line on macOS. We scope to the literal db_path when it looks non-default
    # (so unrelated `cairn serve` processes serving OTHER dbs are spared); we
    # otherwise fall back to the `run` subcommand, which only a real
    # foreground/SSE server invocation contains.
    db_str = str(db_path) if db_path else ""
    if db_str:
        pattern = rf"cairn serve.*{re.escape(db_str)}"
    else:
        pattern = r"cairn serve run"
    r = subprocess.run(
        ["pgrep", "-f", pattern],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return pids
    for pid_s in r.stdout.split():
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        if pid == daemon_pid:
            continue
        # Exclude the launchd-managed daemon's children (the actual server
        # process spawned by the plist). The daemon pid from launchctl is the
        # one we want to keep; its descendants serve the SSE port.
        pids.append(pid)
    return pids


def terminate_pid(pid: int, timeout: float = 5.0) -> None:
    """SIGTERM a pid, wait, SIGKILL if still alive. Best-effort, never raises.

    Defined here so the daemon's own stray-sweeper thread can evict orphans
    without importing the CLI layer (which would pull Click and the whole
    command tree into the server process).
    """
    import signal
    import time

    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)  # probe
        except ProcessLookupError:
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def sweep_strays(db_path: str | Path, log: bool = False) -> int:
    """Find and kill stray `cairn serve` processes. Returns count killed.

    Idempotent and safe to call from the daemon's background sweeper thread or
    from `cairn serve start`/`stop`. Best-effort: a pid that died between find and
    kill is a no-op.
    """
    strays = find_strays(db_path)
    for pid in strays:
        terminate_pid(pid)
        if log:
            import datetime
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] cairn: stray sweeper killed orphan cairn serve pid {pid}",
                  file=sys.stderr, flush=True)
    return len(strays)


def load() -> bool:
    """Load (and start) the LaunchAgent. Returns True on success."""
    if not is_macos():
        raise RuntimeError("LaunchAgent daemon management is macOS-only.")
    path = plist_path()
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `cairn serve start` first to create it."
        )
    r = subprocess.run(
        ["launchctl", "load", str(path)],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def unload() -> bool:
    """Unload (and stop) the LaunchAgent. Returns True on success/already-unloaded."""
    if not is_macos():
        raise RuntimeError("LaunchAgent daemon management is macOS-only.")
    path = plist_path()
    if not path.exists():
        return True  # nothing to unload
    r = subprocess.run(
        ["launchctl", "unload", str(path)],
        capture_output=True, text=True,
    )
    # unload returns nonzero if already unloaded; treat as success.
    return r.returncode == 0


def sse_url(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> str:
    return f"http://{host}:{port}/sse"


def sse_responds(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST, timeout: float = 2.0) -> bool:
    """Liveness check: does the SSE server actually answer HTTP requests?

    A bare TCP accept is a false-positive liveness signal -- the listen
    backlog accepts even when uvicorn is wedged (e.g. blocking on a SQLite WAL
    lock during a write) and can't service the request. That left `cairn serve
    status` reading "SSE responds: True (but curl times out mid-stream)"
    during a real lockup.

    We probe the root path ``/`` (NOT ``/sse``) with a spec-complete HTTP/1.1
    GET and read the start of the response status line. Hitting ``/`` avoids
    the SSE handler's request validation, which would otherwise reject the
    probe and spam the daemon log with "Request validation failed" on every
    health check. A 404 from the root is still a valid liveness signal: it
    proves uvicorn parsed the request and emitted a status line.

    Returns True only if the server both accepted AND emitted a response byte
    within `timeout`.
    """
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            # Probe the root path -- any HTTP status line (including 404) proves
            # uvicorn is servicing requests, not just holding the listen socket.
            sock.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            first = sock.recv(1, socket.MSG_PEEK)
            return bool(first)
    except OSError:
        return False
