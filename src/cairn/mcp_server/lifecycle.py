"""Lifecycle management for the cairn SSE daemon (macOS launchd)."""
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
    """Build the LaunchAgent plist dict for a read-only-by-default `cairn serve run` daemon."""
    bin_ = cg_bin()
    env = {
        # Inherit the user PATH so `cairn` can find python etc.
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        # Propagate a non-default CAIRN_HOME so the launchd daemon
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
    """PID of the running daemon, or None; parses both launchctl output formats."""
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
    """Full command line of ``pid``; None when unreadable (never identify-and-kill blindly)."""
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
    """True when ``cmdline`` is a real `cairn serve` server invocation (anchored, subcommands excluded)."""
    tokens = cmdline.split()
    if len(tokens) < 2:
        return False
    if Path(tokens[0]).name != "cairn" or tokens[1] != "serve":
        return False
    return not (len(tokens) > 2 and tokens[2] in _SERVE_LIFECYCLE_SUBCOMMANDS)


def _db_holder_pids(db_path: str | Path) -> set[int] | None:
    """Pids holding ``db_path`` open; None means verification failed (never conflated with empty)."""
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
    """Pids whose cmdline mentions `cairn serve` — a deliberately over-matching superset."""
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
    """Find `cairn serve` pids not managed by launchd that hold this db; never kills unverified pids."""
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
    """Return direct child pids of ``ppid`` (best-effort, empty on failure)."""
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
    """SIGTERM, wait, then SIGKILL; cmd_check re-verification aborts on pid reuse."""
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
    """Find and kill stray `cairn serve` processes; returns the count killed."""
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
    """True when the SSE server accepts and answers an HTTP request within ``timeout``."""
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
