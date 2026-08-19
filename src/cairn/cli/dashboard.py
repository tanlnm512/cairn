"""Dashboard CLI: `cairn dashboard` — local read-only web UI (FR-001)."""
from __future__ import annotations

import ipaddress
from pathlib import Path

import click

from .main import main
from ..dashboard.app import DEFAULT_HOST, DEFAULT_PORT


def _resolve_db(db: str | None) -> str:
    """Return ``db``, falling back to the workspace's central store."""
    from ..paths import resolve_store

    return db or str(resolve_store().db)


def _require_loopback(host: str) -> None:
    """Refuse non-loopback binds: the dashboard serves localhost only."""
    if host == "localhost":
        return
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise click.UsageError(
            f"refusing to bind --host {host}: the dashboard is localhost-only"
        )


@main.command()
@click.option("--db", default=None, help="SQLite DB path (default: central store for this workspace).")
@click.option("--host", default=DEFAULT_HOST, help=f"Bind host, loopback only (default {DEFAULT_HOST}).")
@click.option("--port", default=DEFAULT_PORT, type=int, help=f"Bind port (default {DEFAULT_PORT}).")
def dashboard(db, host, port):
    """Start the local read-only web dashboard and print its URL.

    Blocks until interrupted (Ctrl-C). Serves on loopback only, over a
    read-only connection to the graph DB, so it never contends with writer
    commands (`cairn build` / `cairn embed` / `cairn serve`).
    """
    _require_loopback(host)
    path = _resolve_db(db)
    click.echo(f"cairn dashboard: http://{host}:{port}  (db: {path})")
    click.echo("  read-only; Ctrl-C to stop")
    if not Path(path).exists():
        click.echo(
            f"  note: db not found at {path} — views show the empty state;"
            " run `cairn build` to index this workspace"
        )

    # Server stack (starlette/jinja2/uvicorn — transitive deps of mcp) loads
    # only here, so importing cairn.cli never pulls it in.
    from ..dashboard.app import create_app

    import uvicorn

    uvicorn.run(create_app(db_path=path), host=host, port=port)
