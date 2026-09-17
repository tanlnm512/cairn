"""Context pack CLI command."""

from __future__ import annotations

import json
from dataclasses import asdict

import click

from .main import DEFAULT_DB_PATH, get_db, main


@main.command("pack")
@click.option(
    "--task",
    required=True,
    help="Task description to pack context for.",
)
@click.option(
    "--budget",
    type=click.IntRange(min=1),
    required=True,
    help="Token budget for the rendered pack.",
)
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option(
    "--knowledge",
    default=str(DEFAULT_DB_PATH.parent / ".knowledge"),
    help="Knowledge store directory.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the pack result as JSON.")
def pack(task, budget, db, knowledge, as_json):
    """Build a deterministic, token-budgeted context pack for a task."""
    from ..okf.bundle import OKFBundle
    from ..pack import build_pack, render_pack

    conn = get_db(db, read_only=True)
    try:
        bundle = OKFBundle(knowledge)
        try:
            result = build_pack(conn, bundle, task, budget)
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
    finally:
        conn.close()
    if as_json:
        click.echo(json.dumps(asdict(result), indent=2))
        return
    click.echo(render_pack(result), nl=False)
