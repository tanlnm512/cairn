"""Connect CLI: start or reuse the shared HTTP daemon and print its MCP URL."""
from __future__ import annotations

import errno
import fcntl
import os
import sys
import time

import click

from .main import main
from ..mcp_server import lifecycle as lc

_LOCK_WAIT_SECONDS = 30.0
_POLL_SECONDS = 0.05


def _lockfile_path():
    # Own lockfile, not lifecycle's start lock: flock is per open-file-description,
    # so holding lifecycle's lock file across ensure_daemon_running would self-deadlock.
    return lc.daemon_home() / "mcp-connect.lock"


def _acquire_start_lock(timeout: float) -> int | None:
    """Bounded exclusive flock over the connect start sequence; held fd, or None once ``timeout`` expires."""
    path = _lockfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o644)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                os.close(fd)
                raise
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(_POLL_SECONDS)


def _report(url: str, *, started: bool, pid: int | None = None) -> None:
    """Echo the daemon URL with its started-vs-reused report."""
    if started:
        pid_part = f" (pid {pid})" if pid else ""
        click.echo(f"cairn daemon started{pid_part} at {url}")
    else:
        click.echo(f"cairn daemon already running at {url}")


@main.command()
@click.option("--port", default=lc.DEFAULT_PORT, type=int, help=f"HTTP daemon port (default {lc.DEFAULT_PORT}).")
@click.option("--host", default=lc.DEFAULT_HOST, help=f"HTTP daemon host (default {lc.DEFAULT_HOST}, loopback).")
def connect(port, host):
    """Start or reuse the shared HTTP daemon and print its MCP URL."""
    if lc.healthz_responds(port, host):
        _report(lc.mcp_url(port, host), started=False)
        return
    fd = _acquire_start_lock(_LOCK_WAIT_SECONDS)
    try:
        if lc.healthz_responds(port, host):
            _report(lc.mcp_url(port, host), started=False)
            return
        from ..paths import resolve_store

        lc.sweep_strays(str(resolve_store().db), log=True)
        try:
            pid, url, started = lc.ensure_daemon_running(port, host)
        except RuntimeError as exc:
            click.echo(f"ERROR: {exc}. Check the daemon log: {lc.daemon_log_path()}")
            sys.exit(1)
    finally:
        if fd is not None:
            os.close(fd)
    _report(url, started=started, pid=pid)
