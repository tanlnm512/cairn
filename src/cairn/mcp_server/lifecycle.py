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

from cairn.paths import cairn_home_env

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
        # FR-003: propagate a non-default CAIRN_HOME so the launchd daemon
        # resolves config.json and shared libs under the same store the
        # invoking shell used ({} when the home is default).
        **cairn_home_env(),
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


# `cairn serve <sub>` lifecycle subcommands: transient CLI invocations, never
# servers -- the stray sweeper must not target them.
_SERVE_LIFECYCLE_SUBCOMMANDS = {"start", "stop", "status", "restart"}


def _pid_cmdline(pid: int) -> str | None:
    """Full command line of ``pid``, or None when it can't be read.

    Tries /proc/<pid>/cmdline (Linux) first, then `ps -p <pid> -o command=`
    (macOS). None means the pid exited, is unreadable, or the platform has
    neither source -- callers must treat that as "can't identify, don't kill".
    Never raises.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
        if raw:
            return " ".join(raw.decode("utf-8", "replace").split("\0")).strip()
    except OSError:
        pass
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def _is_cairn_serve_cmdline(cmdline: str) -> bool:
    """True when ``cmdline`` is a real `cairn serve` SERVER invocation.

    Anchored token match on the argv shapes that actually occur (audit F1):
    editors spawn plain ``cairn serve`` (stdio -- docs/quickstart.md) and
    launchd runs ``cairn serve run --port N``. Both have argv[0] ending in
    ``cairn`` and argv[1] exactly ``serve``. The lifecycle subcommands
    (start/stop/status/restart) are excluded, as are loose cmdline substrings
    (``grep cairn serve``, ``vi cairn server.py``, ``cairn build``) that a
    pgrep pattern scan alone would false-positive on.
    """
    tokens = cmdline.split()
    if len(tokens) < 2:
        return False
    if Path(tokens[0]).name != "cairn" or tokens[1] != "serve":
        return False
    return not (len(tokens) > 2 and tokens[2] in _SERVE_LIFECYCLE_SUBCOMMANDS)


def _db_holder_pids(db_path: str | Path) -> set[int] | None:
    """Pids that hold ``db_path`` open (the same lsof technique serve_status
    uses; ``-F p`` machine-readable output emits ``p<pid>`` lines).

    Returns None when verification was IMPOSSIBLE (lsof missing, timed out,
    or errored) -- callers must treat None as "can't verify", never as "no
    holders". A legitimate "nobody holds this file" is an empty set: lsof
    exits 1 with NO stderr for that, but 1 WITH stderr for real failures.
    """
    try:
        r = subprocess.run(
            ["lsof", "-F", "p", str(db_path)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if r.returncode not in (0, 1) or (r.returncode == 1 and r.stderr.strip()):
        return None
    holders: set[int] = set()
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("p") and line[1:].isdigit():
            holders.add(int(line[1:]))
    return holders


def _pgrep_candidates() -> list[int]:
    """Pids whose full command line mentions `cairn serve` (a superset).

    Deliberately a broad substring regex: the anchored per-pid token check in
    find_strays is the real filter, so this pattern only needs to over-match,
    never under-match (every ``argv[0]=*cairn argv[1]=serve`` shape contains
    the literal ``cairn serve``). pgrep never lists itself.
    """
    try:
        r = subprocess.run(
            ["pgrep", "-f", "cairn serve"],
            capture_output=True, text=True,
        )
    except (OSError, ValueError):
        return []
    pids: list[int] = []
    if r.returncode == 0:
        for pid_s in r.stdout.split():
            try:
                pids.append(int(pid_s))
            except ValueError:
                continue
    return pids


def find_strays(db_path: str | Path) -> list[int]:
    """Find `cairn serve` PIDs NOT managed by launchd that hold THIS db.

    These are orphaned stdio servers left over from editor sessions -- the
    root cause of WAL lock contention. A pid qualifies as a stray only when
    ALL of these hold (audit F1/F2):

    1. Its full command line is a real `cairn serve` server invocation
       (anchored token match via ps). Editors launch plain ``cairn serve``
       with the db passed via CAIRN_DB env -- never in argv -- so the old
       argv-pattern scans (`cairn serve.*<db>`, `cairn serve run`) matched
       nothing real and orphaned stdio servers were invisible.
    2. It is not in the protected set: self, the launchd daemon, and the
       daemon's children.
    3. It actually holds ``db_path`` open, verified via lsof. A foreground
       server on a DIFFERENT db is never ours to kill; if lsof verification
       is impossible, NOTHING is killed -- an unverifiable kill is worse
       than a missed sweep pass.
    """
    daemon_pid = running_pid()
    protected = {daemon_pid, os.getpid()}
    if daemon_pid is not None:
        protected |= _children_of(daemon_pid)

    server_pids: list[int] = []
    for pid in _pgrep_candidates():
        if pid in protected:
            continue
        cmdline = _pid_cmdline(pid)
        if cmdline is not None and _is_cairn_serve_cmdline(cmdline):
            server_pids.append(pid)
    if not server_pids:
        return []

    holders = _db_holder_pids(db_path)
    if holders is None:
        print(
            "cairn: stray sweep skipped -- could not verify which processes "
            "hold the DB via lsof; not killing unverified `cairn serve` "
            "candidates",
            file=sys.stderr, flush=True,
        )
        return []
    return [pid for pid in server_pids if pid in holders]


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


def terminate_pid(pid: int, timeout: float = 5.0, cmd_check=None) -> None:
    """SIGTERM a pid, wait, SIGKILL if still alive. Best-effort, never raises.

    ``cmd_check`` (audit F4): optional predicate over the pid's command line.
    When given, the command line is re-verified immediately before SIGTERM
    and again before SIGKILL: if the targeted process died in between (the
    TERM->KILL window is up to ``timeout`` seconds) and the kernel REUSED the
    pid for an unrelated process, the kill is aborted rather than fired at
    the innocent newcomer. Unreadable cmdlines also abort -- can't confirm,
    can't kill.
    """
    import signal
    import time

    def _still_ours() -> bool:
        if cmd_check is None:
            return True
        cmdline = _pid_cmdline(pid)
        return cmdline is not None and cmd_check(cmdline)

    if not _still_ours():
        return
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
    if not _still_ours():
        return  # pid died + was reused during the TERM->KILL wait: abort
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def sweep_strays(db_path: str | Path, log: bool = False) -> int:
    """Find and kill stray `cairn serve` processes. Returns count killed.

    Idempotent; best-effort (a pid that died between find and kill is a no-op).
    Passes the anchored cmdline check to terminate_pid so a pid that was
    reused for an unrelated process between find and kill is never SIGKILLed.
    """
    strays = find_strays(db_path)
    for pid in strays:
        terminate_pid(pid, cmd_check=_is_cairn_serve_cmdline)
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
