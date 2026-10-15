"""Lifecycle management for the cairn HTTP daemon (macOS launchd)."""
from __future__ import annotations

import contextlib
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

from cairn.paths import cairn_home_env, resolve_store

LABEL = "dev.cairn.mcp"
LEGACY_LABEL = "dev.cairn.sse"
DEFAULT_PORT = 9876
DEFAULT_HOST = "127.0.0.1"
_LAUNCHCTL_TIMEOUT = 15.0
# Bounds the blocking start lock: a wedged holder must fail callers with a
# clear error instead of blocking them forever.
_START_LOCK_TIMEOUT = 120.0


def is_macos() -> bool:
    return sys.platform == "darwin"


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path() -> Path:
    return agents_dir() / f"{LABEL}.plist"


def log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "cairn-mcp.log"


def daemon_home() -> Path:
    """CAIRN_HOME for daemon state, resolved at call time (env override wins)."""
    env = os.environ.get("CAIRN_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cairn"


def daemon_log_path() -> Path:
    """Log file the managed daemon writes to on this platform."""
    if is_macos():
        return log_path()
    return daemon_home() / "cairn-mcp.log"


def pidfile_path() -> Path:
    return daemon_home() / "mcp-daemon.pid"


def _lockfile_path() -> Path:
    return daemon_home() / "mcp-daemon.lock"


def pidfile_pid() -> int | None:
    """Daemon pid recorded in the pidfile; None when absent or unparsable."""
    try:
        text = pidfile_path().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def _write_pidfile(pid: int) -> None:
    path = pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def _running_bin() -> str | None:
    """Absolute path of the cairn executable running this process, when nameable."""
    argv0 = sys.argv[0] if sys.argv else None
    if not argv0 or Path(argv0).name != "cairn":
        return None
    resolved = os.path.realpath(os.path.abspath(argv0))
    return resolved if Path(resolved).exists() else None


def cg_bin() -> str:
    """Absolute path to the cairn executable to launch: CAIRN_BIN, then the
    one running this process, then PATH lookup, then the ~/.local install."""
    for cand in (
        os.environ.get("CAIRN_BIN"),
        _running_bin(),
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
    read_only: bool = False,
) -> dict:
    """Build the LaunchAgent plist dict for a read-write-by-default `cairn serve run` daemon."""
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
    args = [bin_, "serve", "run", "--port", str(port), "--host", str(host)]
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


def _run_launchctl(args: list[str]) -> subprocess.CompletedProcess | None:
    """Bounded launchctl call; None on timeout so no invocation can wedge the start lock."""
    try:
        return subprocess.run(
            ["launchctl", *args],
            capture_output=True, text=True, timeout=_LAUNCHCTL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None


def is_loaded() -> bool:
    """True if the LaunchAgent is currently loaded."""
    if not is_macos():
        return False
    r = _run_launchctl(["list", LABEL])
    return r is not None and r.returncode == 0


def running_pid() -> int | None:
    """PID of the running daemon, or None; parses both launchctl output formats."""
    if not is_macos():
        return None
    r = _run_launchctl(["list", LABEL])
    if r is None or r.returncode != 0:
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
    if len(tokens) >= 3 and Path(tokens[0]).name.startswith("python"):
        # Shebang exec: console scripts run as `python <path>/cairn serve ...`.
        tokens = tokens[1:]
    if len(tokens) < 2:
        return False
    if Path(tokens[0]).name != "cairn" or tokens[1] != "serve":
        return False
    return not (len(tokens) > 2 and tokens[2] in _SERVE_LIFECYCLE_SUBCOMMANDS)


is_cairn_serve_cmdline = _is_cairn_serve_cmdline  # public name for CLI consumers


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
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
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
    pf_pid = pidfile_pid()
    if pf_pid is not None and pf_pid not in protected:
        cmdline = _pid_cmdline(pf_pid)
        if cmdline is not None and _is_cairn_serve_cmdline(cmdline):
            protected.add(pf_pid)

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
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
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


_legacy_label_unloaded = False


def _unload_legacy_label_once() -> None:
    """Unload the retired sse-label agent once, before the first load of the current label."""
    global _legacy_label_unloaded
    if _legacy_label_unloaded:
        return
    _legacy_label_unloaded = True
    legacy_plist = agents_dir() / f"{LEGACY_LABEL}.plist"
    if not legacy_plist.exists():
        return
    _run_launchctl(["unload", str(legacy_plist)])
    try:
        # Without this, launchd re-launches the retired agent at every login.
        legacy_plist.unlink()
    except OSError:
        pass


def load() -> bool:
    """Load (and start) the LaunchAgent. Returns True on success."""
    if not is_macos():
        raise RuntimeError("LaunchAgent daemon management is macOS-only.")
    path = plist_path()
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `cairn serve start` first to create it."
        )
    _unload_legacy_label_once()
    r = _run_launchctl(["load", str(path)])
    if r is None:
        raise RuntimeError(
            f"launchctl load timed out after {_LAUNCHCTL_TIMEOUT}s"
        )
    return r.returncode == 0


def unload() -> bool:
    """Unload (and stop) the LaunchAgent. Returns True on success/already-unloaded."""
    if not is_macos():
        raise RuntimeError("LaunchAgent daemon management is macOS-only.")
    path = plist_path()
    if not path.exists():
        return True  # nothing to unload
    r = _run_launchctl(["unload", str(path)])
    if r is None:
        raise RuntimeError(
            f"launchctl unload timed out after {_LAUNCHCTL_TIMEOUT}s"
        )
    # unload returns nonzero if already unloaded; treat as success.
    return r.returncode == 0


def mcp_url(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> str:
    return f"http://{host}:{port}/mcp"


def healthz_responds(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST, timeout: float = 2.0) -> bool:
    """True when the daemon answers GET /healthz with 200 within ``timeout``."""
    import http.client

    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/healthz")
        return conn.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def healthz_json(port: int = DEFAULT_PORT, host: str = DEFAULT_HOST, timeout: float = 2.0) -> dict | None:
    """Parsed /healthz payload, or None when the daemon does not answer 200."""
    import http.client
    import json

    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/healthz")
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        return json.loads(resp.read())
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        conn.close()


def _args_bind(args: list[str]) -> tuple[int | None, str | None]:
    """Extract the (--port, --host) pair from a `serve run` argv; Nones when absent."""
    port: int | None = None
    host: str | None = None
    for i, tok in enumerate(args):
        if tok == "--port" and i + 1 < len(args) and args[i + 1].isdigit():
            port = int(args[i + 1])
        elif tok == "--host" and i + 1 < len(args):
            host = args[i + 1]
    return port, host


def daemon_bind() -> tuple[int | None, str | None]:
    """(port, host) the managed daemon was started with, from its plist or argv; Nones when undiscoverable."""
    args: list[str] = []
    if is_macos():
        try:
            with open(plist_path(), "rb") as f:
                args = list(plistlib.load(f).get("ProgramArguments") or [])
        except (OSError, plistlib.InvalidFileException):
            return None, None
    else:
        pid = pidfile_pid()
        cmdline = _pid_cmdline(pid) if pid is not None else None
        if cmdline:
            args = cmdline.split()
    return _args_bind(args)


@contextlib.contextmanager
def _start_lock(timeout: float = _START_LOCK_TIMEOUT):
    """Bounded exclusive flock over the daemon start sequence; RuntimeError once ``timeout`` expires."""
    import errno
    import fcntl
    import time

    path = _lockfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"timed out after {timeout:.0f}s waiting for the "
                        f"daemon start lock ({path})"
                    ) from exc
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def _wait_for_healthz(port: int, host: str, attempts: int = 60, delay: float = 0.5) -> bool:
    import time

    for _ in range(attempts):
        if healthz_responds(port, host):
            return True
        time.sleep(delay)
    return False


def _ensure_running_launchd(port: int, host: str) -> tuple[int | None, str, bool]:
    """macOS core: reuse the loaded healthy agent, else sweep, write plist, load, wait."""
    if is_loaded():
        if healthz_responds(port, host):
            return running_pid(), mcp_url(port, host), False
        # KeepAlive respawn window: the job is loaded but not yet serving --
        # wait it out before any write/load attempt.
        if _wait_for_healthz(port, host):
            return running_pid(), mcp_url(port, host), False
        unload()  # wedged job: cycle it before a fresh load
    store = resolve_store()
    sweep_strays(str(store.db), log=True)
    write_plist(render_plist(
        port=port, host=host,
        workspace=str(store.workspace),
        db_path=str(store.db),
        knowledge_path=str(store.knowledge),
    ))
    if not load():
        raise RuntimeError("launchctl load failed")
    if not _wait_for_healthz(port, host):
        raise RuntimeError("daemon loaded but healthz not responding")
    return running_pid(), mcp_url(port, host), True


def _daemonize_exec(port: int, host: str, bin_: str, env: dict[str, str]) -> None:
    """Child side of the daemonize fallback: detach, redirect stdio, exec serve run."""
    os.setsid()
    log_fd = os.open(
        str(daemon_log_path()),
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o644,
    )
    devnull_fd = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
    os.dup2(devnull_fd, 0)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(log_fd)
    os.close(devnull_fd)
    try:
        os.execvpe(
            bin_, [bin_, "serve", "run", "--port", str(port), "--host", str(host)], env
        )
    except OSError:
        os._exit(1)


def _cycle_wedged_daemon(port: int, host: str, db_path: str) -> None:
    """Kill a verified pidfile daemon that holds this db but is not serving healthz."""
    pf_pid = pidfile_pid()
    if pf_pid is None or healthz_responds(port, host):
        return
    cmdline = _pid_cmdline(pf_pid)
    if cmdline is None or not _is_cairn_serve_cmdline(cmdline):
        return
    holders = _db_holder_pids(db_path)
    if holders is None or pf_pid not in holders:
        return
    terminate_pid(pf_pid, cmd_check=_is_cairn_serve_cmdline)
    pidfile_path().unlink(missing_ok=True)


def _ensure_running_daemonize(port: int, host: str) -> tuple[int | None, str, bool]:
    """Non-macOS core: reuse a healthy daemon, else fork a detached serve run."""
    if healthz_responds(port, host):
        return pidfile_pid(), mcp_url(port, host), False
    store = resolve_store()
    _cycle_wedged_daemon(port, host, str(store.db))
    sweep_strays(str(store.db), log=True)
    env = {
        **os.environ,
        "CAIRN_WORKSPACE": str(store.workspace),
        "CAIRN_DB": str(store.db),
        "CAIRN_KNOWLEDGE": str(store.knowledge),
    }
    bin_ = cg_bin()
    pid = os.fork()
    if pid == 0:
        _daemonize_exec(port, host, bin_, env)
        os._exit(1)
    _write_pidfile(pid)
    if not _wait_for_healthz(port, host):
        raise RuntimeError("daemon started but healthz not responding")
    return pid, mcp_url(port, host), True


def ensure_daemon_running(
    port: int = DEFAULT_PORT, host: str = DEFAULT_HOST,
) -> tuple[int | None, str, bool]:
    """Bring up the shared HTTP daemon; returns (pid, mcp_url, started)."""
    with _start_lock():
        if is_macos():
            return _ensure_running_launchd(port, host)
        return _ensure_running_daemonize(port, host)
