"""Lifecycle management for the cairn SSE daemon (macOS launchd).

Plumbing for `cairn serve start|stop|status|restart` so a single SSE server
runs as a persistent per-user LaunchAgent shared by all MCP clients.

Design:
- A LaunchAgent plist runs `cairn serve --port <N>` with KeepAlive and RunAtLoad.
- `start` is idempotent; `stop` does `launchctl unload` and sweeps stray
  `cairn serve` processes (the orphan-accumulation cause of "database is locked").

macOS-only; on other platforms functions raise RuntimeError.
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

    Runs `cairn serve run --port <N> --read-only`. The daemon is read-only by
    default: the shared SSE server opens the graph DB with `mode=ro` so it
    never contends with `cairn build`/`cairn embed`/`cairn memory`. Pass
    read_only=False for a read-write daemon (not recommended for shared use).

    Args:
        workspace: absolute path to the workspace whose store the daemon
            should serve (launchd's cwd is `/`).
        db_path: explicit DB path (overrides workspace-derived path).
        knowledge_path: explicit knowledge dir.
    """
    bin_ = cg_bin()
    env = {
        # Inherit the user PATH so `cairn` can find python etc.
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

    Handles the two launchctl output formats across macOS versions (plist-style
    `"PID" = N;` and tab-style `PID<Tab>Status<Tab>Label`).
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
    # Tab-separated output: PID<Tab>Status<Tab>Label.
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0].strip().isdigit():
            return int(parts[0].strip())
    return None


def find_strays(db_path: str | Path) -> list[int]:
    """Find `cairn serve` PIDs NOT managed by launchd that hold the DB.

    These are orphaned stdio servers left over from editor sessions -- the root
    cause of WAL lock contention. Matched by command shape (real `cairn serve`
    invocations) and optionally scoped by db_path. Daemon and its children are
    excluded.
    """
    pids: list[int] = []
    daemon_pid = running_pid()
    # Build the set of pids to PRESERVE: the launchd-managed daemon pid plus
    # any processes it spawned, so we don't kill the live SSE server.
    protected = {daemon_pid, os.getpid()}
    if daemon_pid is not None:
        protected |= _children_of(daemon_pid)
    # pgrep -f matches against the full command line on macOS. Scope to the
    # literal db_path when it looks non-default; otherwise fall back to the
    # `run` subcommand, which only a real foreground/SSE server invocation
    # contains. Under launchd db_path is typically passed via CAIRN_DB env (not
    # argv), so the scoped pattern rarely matches; we still try it first for
    # the `--db` argv case.
    db_str = str(db_path) if db_path else ""
    candidates: list[int] = []
    patterns = [rf"cairn serve.*{re.escape(db_str)}", r"cairn serve run"] if db_str else [r"cairn serve run"]
    seen: set[int] = set()
    for pattern in patterns:
        r = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            continue
        for pid_s in r.stdout.split():
            try:
                pid = int(pid_s)
            except ValueError:
                continue
            if pid not in seen:
                seen.add(pid)
                candidates.append(pid)
        # If the scoped pattern matched anything, don't bother with the broad
        # fallback -- it would only add false positives.
        if candidates:
            break
    for pid in candidates:
        if pid in protected:
            continue
        pids.append(pid)
    return pids


def _children_of(ppid: int) -> set[int]:
    """Return direct child pids of ``ppid`` via a single ``pgrep -P`` call.

    Best-effort: on failure or a non-macOS host it returns an empty set.
    """
    try:
        r = subprocess.run(
            ["pgrep", "-P", str(ppid)],
            capture_output=True, text=True,
        )
    except (OSError, ValueError):
        return set()
    children: set[int] = set()
    if r.returncode == 0:
        for pid_s in r.stdout.split():
            try:
                children.add(int(pid_s))
            except ValueError:
                continue
    return children


def terminate_pid(pid: int, timeout: float = 5.0) -> None:
    """SIGTERM a pid, wait, SIGKILL if still alive. Best-effort, never raises."""
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

    Idempotent; best-effort (a pid that died between find and kill is a no-op).
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

    Probes the root path ``/`` (not ``/sse``) and reads the start of the
    response status line. A bare TCP accept is a false-positive (the listen
    backlog accepts even when uvicorn is wedged). Hitting ``/`` avoids the SSE
    handler's request validation; a 404 still proves uvicorn parsed the request.

    Returns True only if the server both accepted AND emitted a response byte
    within `timeout`.
    """
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            # Probe the root path -- any HTTP status line (including 404) proves
            # uvicorn is servicing requests.
            sock.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            first = sock.recv(1, socket.MSG_PEEK)
            return bool(first)
    except OSError:
        return False
