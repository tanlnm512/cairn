"""Serve CLI: the serve group and daemon lifecycle."""
from __future__ import annotations

import click
import os
import subprocess
import sys

from .main import main
from ..mcp_server import lifecycle as lc

@main.group(invoke_without_command=True)
@click.option("--db", default=None, help="SQLite DB path (default: central store).")
@click.option("--port", default=None, type=int, help=f"HTTP daemon port (default {lc.DEFAULT_PORT}).")
@click.option(
    "--read-only/--read-write",
    "read_only",
    default=None,
    help="Open the graph DB read-only (default: read-write).",
)
@click.option(
    "--transport",
    default=None,
    hidden=True,
    help="Removed: cairn serves streamable HTTP only.",
)
@click.option(
    "--host",
    default=lc.DEFAULT_HOST,
    help=f"Bind host for the HTTP daemon (default {lc.DEFAULT_HOST}, loopback).",
)
@click.option(
    "--api-key",
    default=None,
    help=(
        "Bearer API key required by the HTTP daemon on non-loopback binds. "
        "Takes precedence over CAIRN_MCP_API_KEY; prefer the env var (this flag is visible in process listings)."
    ),
)
@click.option(
    "--stateless",
    is_flag=True,
    default=False,
    help="Run the HTTP daemon without session affinity (sequential session-less requests succeed; for load balancers).",
)
@click.pass_context
def serve(ctx, db, port, read_only, transport, host, api_key, stateless):
    """Start the HTTP MCP server daemon in the foreground, or manage the shared daemon."""
    if transport is not None:
        _refuse_removed_transport(port, host)
    if ctx.invoked_subcommand is None:
        _serve_foreground(
            db=db, port=port, read_only=read_only,
            host=host, api_key=api_key, stateless=stateless,
        )


@serve.command("run")
@click.option("--db", default=None, help="SQLite DB path (default: central store).")
@click.option("--port", default=None, type=int, help=f"HTTP daemon port (default {lc.DEFAULT_PORT}).")
@click.option(
    "--read-only/--read-write",
    "read_only",
    default=None,
    help=(
        "Open the graph DB read-only (default: read-write). A read-only "
        "connection cannot acquire SQLite's writer lock, so it never "
        "contends with `cairn build`/`cairn embed`/`cairn memory`."
    ),
)
@click.option(
    "--transport",
    default=None,
    hidden=True,
    help="Removed: cairn serves streamable HTTP only.",
)
@click.option(
    "--host",
    default=lc.DEFAULT_HOST,
    help=f"Bind host for the HTTP daemon (default {lc.DEFAULT_HOST}, loopback).",
)
@click.option(
    "--api-key",
    default=None,
    help=(
        "Bearer API key required by the HTTP daemon on non-loopback binds. "
        "Takes precedence over CAIRN_MCP_API_KEY; prefer the env var (this flag is visible in process listings)."
    ),
)
@click.option(
    "--stateless",
    is_flag=True,
    default=False,
    help="Run the HTTP daemon without session affinity (sequential session-less requests succeed; for load balancers).",
)
def serve_run(db, port, read_only, transport, host, api_key, stateless):
    """Run the HTTP MCP server daemon in the foreground."""
    if transport is not None:
        _refuse_removed_transport(port, host)
    _serve_foreground(
        db=db, port=port, read_only=read_only,
        host=host, api_key=api_key, stateless=stateless,
    )


def _refuse_removed_transport(port, host):
    """Refuse a removed --transport value, naming the HTTP daemon URL and connect."""
    url = lc.mcp_url(port or lc.DEFAULT_PORT, host or lc.DEFAULT_HOST)
    click.echo(f"cairn: error: --transport is removed; cairn serves streamable HTTP at {url}.")
    click.echo("Run `cairn connect` to start or reuse the daemon and print its URL.")
    sys.exit(1)


def _serve_foreground(db, port=None, read_only=None, host=None, api_key=None, stateless=False):
    """Foreground serve: the HTTP daemon, read-write unless ``read_only``."""
    from ..mcp_server.auth import resolve_api_key
    from ..paths import resolve_store

    store = resolve_store()
    os.environ["CAIRN_DB"] = db or str(store.db)
    os.environ["CAIRN_KNOWLEDGE"] = str(store.knowledge)
    os.environ["CAIRN_WORKSPACE"] = str(store.workspace)
    os.environ["CAIRN_READ_ONLY"] = "1" if read_only else "0"
    port = port if port is not None else lc.DEFAULT_PORT
    from ..mcp_server.server import run

    # Key precedence is decided here and travels in-process; run() never re-reads the env.
    run(
        port=port, host=host,
        api_key=resolve_api_key(api_key), stateless=stateless,
    )


# --- daemon lifecycle subcommands -----------------------------------------
@serve.command("start")
@click.option("--port", default=lc.DEFAULT_PORT, type=int, help=f"HTTP daemon port (default {lc.DEFAULT_PORT}).")
@click.option("--host", default=lc.DEFAULT_HOST, help=f"Bind host (default {lc.DEFAULT_HOST}, loopback).")
@click.option(
    "--api-key",
    default=None,
    help=(
        "Bearer API key required by the HTTP daemon on non-loopback binds. "
        "Takes precedence over CAIRN_MCP_API_KEY; prefer the env var (this flag is visible in process listings)."
    ),
)
def serve_start(port, host, api_key):
    """Start the persistent shared HTTP daemon (launchd on macOS, daemonized otherwise); idempotent."""
    from ..mcp_server.auth import resolve_api_key

    try:
        pid, url, started = lc.ensure_daemon_running(
            port, host, api_key=resolve_api_key(api_key),
        )
    except RuntimeError as exc:
        click.echo(f"ERROR: {exc}. Check the daemon log: {lc.daemon_log_path()}")
        sys.exit(1)
    if started:
        click.echo(f"cairn daemon started (pid {pid}) at {url}")
    else:
        click.echo(f"cairn daemon already running (pid {pid}) at {url}")
    click.echo(f"  logs: {lc.daemon_log_path()}")
    if lc.is_macos():
        click.echo("  auto-restarts on crash; starts at login. Stop with `cairn serve stop`.")
    else:
        click.echo("  Stop with `cairn serve stop`.")


@serve.command("stop")
def serve_stop():
    """Stop the shared HTTP daemon and kill stray `cairn serve` processes."""
    import time

    from ..paths import resolve_store

    store = resolve_store()
    unload_error: str | None = None

    if lc.is_macos():
        if lc.is_loaded():
            try:
                lc.unload()
                click.echo("unloaded LaunchAgent (launchd sent SIGTERM)")
            except RuntimeError as exc:
                # A launchctl timeout must not skip the pidfile/stray cleanup below.
                unload_error = str(exc)
                click.echo(f"ERROR: {exc}")
        else:
            click.echo("LaunchAgent not loaded")

    pid = lc.pidfile_pid()
    if pid is not None:
        lc.terminate_pid(pid, cmd_check=lc.is_cairn_serve_cmdline)
        lc.pidfile_path().unlink(missing_ok=True)
        click.echo(f"stopped daemonized daemon (pid {pid})")

    strays = lc.find_strays(str(store.db))
    if strays:
        click.echo(f"killing {len(strays)} stray cairn serve process(es): {strays}")
        lc.sweep_strays(str(store.db))

    time.sleep(0.5)
    if unload_error is not None:
        sys.exit(1)
    if lc.is_loaded() and lc.running_pid() is not None:
        click.echo("ERROR: the daemon is still running after stop; check `cairn serve status`.")
        sys.exit(1)
    click.echo("stopped.")


@serve.command("status")
@click.option(
    "--port", default=None, type=int,
    help="HTTP daemon port (default: the managed daemon's configured port).",
)
@click.option("--host", default=None, help="HTTP daemon host (default: the managed daemon's configured host).")
def serve_status(port, host):
    """Show daemon health: managed state, pid, healthz, strays, DB lock holders."""
    from ..paths import resolve_store

    store = resolve_store()

    # An unpassed option defers to the port/host the daemon was started with,
    # so a custom-port daemon is not probed (and reported UNHEALTHY) on the
    # default port.
    d_port, d_host = lc.daemon_bind()
    if port is None:
        port = d_port if d_port is not None else lc.DEFAULT_PORT
    if host is None:
        host = d_host if d_host is not None else lc.DEFAULT_HOST

    loaded = lc.is_loaded()
    pid = lc.running_pid() or lc.pidfile_pid()
    responds = lc.healthz_responds(port, host)
    strays = lc.find_strays(str(store.db))

    # DB lock holders via lsof.
    db_holders = []
    try:
        r = subprocess.run(
            ["lsof", str(store.db)],
            capture_output=True, text=True, timeout=5,
        )
        db_holders = sorted({
            f"{line.split()[0]}({line.split()[1]})"
            for line in r.stdout.splitlines()[1:]
            if len(line.split()) >= 2
        })
    except Exception:
        pass

    if responds and not strays:
        health = "HEALTHY"
    elif pid is None and not loaded and not responds:
        health = "not running"
    else:
        health = "UNHEALTHY"
    click.echo(f"cairn daemon: {health}")
    click.echo(f"  LaunchAgent loaded : {loaded}")
    click.echo(f"  daemon pid         : {pid}")
    click.echo(f"  healthz responds   : {responds}  ({lc.mcp_url(port, host)})")
    click.echo(f"  stray cairn serve pids: {strays if strays else 'none'}")
    click.echo(f"  DB lock holders    : {db_holders if db_holders else 'none'}")
    if lc.is_macos():
        click.echo(f"  plist              : {lc.plist_path()}")
    click.echo(f"  log                : {lc.daemon_log_path()}")
    if strays:
        click.echo("  -> run `cairn serve stop` to kill strays")


@serve.command("restart")
@click.option("--port", default=None, type=int,
              help="HTTP daemon port (default: the managed daemon's configured port).")
@click.option("--host", default=None,
              help="HTTP daemon host (default: the managed daemon's configured host).")
@click.pass_context
def serve_restart(ctx, port, host):
    """Stop then start the daemon."""
    # Defer to the daemon's configured bind so a restart never rebinds a
    # custom-port daemon to the defaults.
    d_port, d_host = lc.daemon_bind()
    if port is None:
        port = d_port if d_port is not None else lc.DEFAULT_PORT
    if host is None:
        host = d_host if d_host is not None else lc.DEFAULT_HOST
    # A failed stop must not leave the daemon down: start reuses a healthy
    # daemon or cycles a wedged one.
    import contextlib

    with contextlib.suppress(SystemExit):
        ctx.invoke(serve_stop)
    ctx.invoke(serve_start, port=port, host=host)
