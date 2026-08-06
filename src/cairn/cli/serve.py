"""Serve CLI: the serve group and daemon lifecycle."""
from __future__ import annotations

import click
import os
import subprocess
import sys

from .main import main
from ._helpers import _human_bytes, _mods, _shorten  # noqa: F401
from ..mcp_server import lifecycle as lc

@main.group(invoke_without_command=True)
@click.option("--db", default=None, help="SQLite DB path (default: central store).")
@click.option("--port", default=None, type=int, help="Port (for SSE transport).")
@click.option(
    "--read-only/--read-write",
    "read_only",
    default=None,
    help="Open the graph DB read-only (default: read-only under SSE, read-write under stdio).",
)
@click.pass_context
def serve(ctx, db, port, read_only):
    """Start the cairn MCP server, or manage the persistent SSE daemon.

    \b
    Run in the foreground (classic stdio / one-shot SSE):
      cairn serve                 # stdio (MCP clients spawn this)
      cairn serve --port N        # SSE on port N, foreground

    \b
    Manage a persistent SSE daemon shared by all clients (macOS launchd):
      cairn serve start           # install + start LaunchAgent (runs forever)
      cairn serve stop            # unload LaunchAgent + kill stray servers
      cairn serve status          # health check
      cairn serve restart         # stop + start
    """
    if ctx.invoked_subcommand is None:
        # `cairn serve` with no subcommand: foreground mode.
        _serve_foreground(db=db, port=port, read_only=read_only)


@serve.command("run")
@click.option("--db", default=None, help="SQLite DB path (default: central store).")
@click.option("--port", default=None, type=int, help="Port (for SSE transport).")
@click.option(
    "--read-only/--read-write",
    "read_only",
    default=None,
    help=(
        "Open the graph DB read-only (default for the shared SSE daemon). "
        "A read-only connection cannot acquire SQLite's writer lock, so it "
        "never contends with `cairn build`/`cairn embed`/`cairn memory` -- the "
        "serving-time write paths (memory ref-counts, tool metrics) silently "
        "no-op, and the write tools (record_memory, knowledge_add, ...) "
        "still open a writable connection as needed. Use --read-write to "
        "force read-write behaviour."
    ),
)
def serve_run(db, port, read_only):
    """Run the MCP server in the foreground (stdio by default, SSE with --port)."""
    _serve_foreground(db=db, port=port, read_only=read_only)


def _serve_foreground(db, port, read_only=None):
    """Foreground serve: stdio unless --port is given (then SSE).

    read_only tri-state: None => auto (read-only under SSE/launchd,
    read-write under stdio), True/False => explicit override.
    """
    import os

    from ..paths import resolve_store

    store = resolve_store()
    os.environ["CAIRN_DB"] = db or str(store.db)
    os.environ["CAIRN_KNOWLEDGE"] = str(store.knowledge)
    # Default: the shared SSE daemon runs read-only (contention-safe); a
    # foreground stdio server keeps read-write for interactive use.
    if read_only is None:
        read_only = bool(port)
    os.environ["CAIRN_READ_ONLY"] = "1" if read_only else "0"
    from ..mcp_server.server import run

    run(transport="sse" if port else "stdio", port=port)


# --- daemon lifecycle subcommands -----------------------------------------
@serve.command("start")
@click.option("--port", default=lc.DEFAULT_PORT, type=int, help=f"SSE port (default {lc.DEFAULT_PORT}).")
@click.option("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1).")
def serve_start(port, host):
    """Install and start the persistent SSE daemon (macOS launchd).

    The daemon auto-starts at login and restarts on crash (KeepAlive). It runs
    one `cairn serve --port N` process shared by all MCP clients, replacing the
    one-stdio-server-per-client model that caused "database is locked".

    Idempotent: safe to run when already running.
    """
    import time

    from ..mcp_server import lifecycle as lc

    if not lc.is_macos():
        click.echo("cairn serve start is macOS-only (launchd). For other platforms,")
        click.echo(f"run `cairn serve --port {lc.DEFAULT_PORT}` manually under a process supervisor.")
        sys.exit(1)

    # Set env so resolve_store() in the daemon resolves the SAME workspace.
    from ..paths import resolve_store

    store = resolve_store()
    os.environ.setdefault("CAIRN_DB", str(store.db))

    if lc.is_loaded() and lc.sse_responds(port, host):
        pid = lc.running_pid()
        click.echo(f"cairn daemon already running (pid {pid}) at {lc.sse_url(port, host)}")
        return

    # Clean any orphan stdio servers first so they don't hold the WAL.
    strays = lc.find_strays(str(store.db))
    if strays:
        click.echo(f"killing {len(strays)} orphan cairn serve process(es): {strays}")
        lc.sweep_strays(str(store.db))

    # (Re)write the plist with current port/host + cairn path + workspace env,
    # then load. Workspace env is critical: under launchd cwd is "/", so
    # without CAIRN_WORKSPACE the daemon can't find the store.
    lc.write_plist(lc.render_plist(
        port=port, host=host,
        workspace=str(store.workspace),
        db_path=str(store.db),
        knowledge_path=str(store.knowledge),
    ))
    if not lc.load():
        click.echo("ERROR: launchctl load failed. Check the plist and try `cairn serve status`.")
        sys.exit(1)

    # Wait for the SSE port to come up.
    for _ in range(20):
        if lc.sse_responds(port, host):
            break
        time.sleep(0.5)
    else:
        click.echo("ERROR: daemon loaded but SSE port not responding after 10s.")
        click.echo(f"See logs: {lc.log_path()}")
        sys.exit(1)

    pid = lc.running_pid()
    click.echo(f"cairn daemon started (pid {pid}) at {lc.sse_url(port, host)}")
    click.echo(f"  logs: {lc.log_path()}")
    click.echo("  auto-restarts on crash; starts at login. Stop with `cairn serve stop`.")


@serve.command("stop")
def serve_stop():
    """Stop the SSE daemon and kill stray `cairn serve` processes."""
    import time

    from ..mcp_server import lifecycle as lc

    if not lc.is_macos():
        click.echo("cairn serve stop is macOS-only.")
        sys.exit(1)

    from ..paths import resolve_store

    store = resolve_store()

    # Unload launchd job (graceful SIGTERM from launchd).
    loaded = lc.is_loaded()
    if loaded:
        lc.unload()
        click.echo("unloaded LaunchAgent (launchd sent SIGTERM)")
    else:
        click.echo("LaunchAgent not loaded")

    # Sweep orphan stdio servers holding the DB.
    strays = lc.find_strays(str(store.db))
    if strays:
        click.echo(f"killing {len(strays)} stray cairn serve process(es): {strays}")
        lc.sweep_strays(str(store.db))

    # Wait briefly for the port to release.
    time.sleep(0.5)
    click.echo("stopped.")


@serve.command("status")
@click.option("--port", default=lc.DEFAULT_PORT, type=int, help=f"Expected SSE port (default {lc.DEFAULT_PORT}).")
@click.option("--host", default="127.0.0.1")
def serve_status(port, host):
    """Show daemon health: launchd state, pid, port, strays, DB lock holders."""
    from ..mcp_server import lifecycle as lc

    from ..paths import resolve_store

    store = resolve_store()

    loaded = lc.is_loaded()
    pid = lc.running_pid()
    responds = lc.sse_responds(port, host)
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

    health = "HEALTHY" if (loaded and responds and not strays) else "UNHEALTHY"
    click.echo(f"cairn daemon: {health}")
    click.echo(f"  LaunchAgent loaded : {loaded}")
    click.echo(f"  daemon pid         : {pid}")
    click.echo(f"  SSE responds       : {responds}  ({lc.sse_url(port, host)})")
    click.echo(f"  stray cairn serve pids: {strays if strays else 'none'}")
    click.echo(f"  DB lock holders    : {db_holders if db_holders else 'none'}")
    click.echo(f"  plist              : {lc.plist_path()}")
    click.echo(f"  log                : {lc.log_path()}")
    if strays:
        click.echo("  -> run `cairn serve stop` to kill strays")


@serve.command("restart")
@click.option("--port", default=lc.DEFAULT_PORT, type=int, help=f"SSE port (default {lc.DEFAULT_PORT}).")
@click.option("--host", default="127.0.0.1")
@click.pass_context
def serve_restart(ctx, port, host):
    """Stop then start the daemon."""
    ctx.invoke(serve_stop)
    ctx.invoke(serve_start, port=port, host=host)