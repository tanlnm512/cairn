"""Diff blast-radius CLI command."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import click

from ..graph.blast import BlastBaseError, compute_blast, render_blast
from ..graph.taint import format_taint_warning
from .main import DEFAULT_DB_PATH, get_db


@click.command("blast")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--base", default=None, help="Compare against this ref's merge base.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "markdown", "mermaid", "json"]),
    default="text",
)
@click.option(
    "--fuzzy",
    is_flag=True,
    help="Also traverse unresolved name-only edges.",
)
@click.option(
    "--refresh/--no-refresh",
    default=None,
    help="Reindex drifted files before diffing.",
)
@click.option("--output", default=None, type=click.Path(path_type=Path))
def blast(db, base, output_format, fuzzy, refresh, output):
    """Show reverse dependents for symbols touched by a git diff."""
    from ..paths import resolve_workspace

    try:
        conn = get_db(db)
        try:
            result = compute_blast(
                conn,
                str(resolve_workspace()),
                base=base,
                fuzzy=fuzzy,
                refresh=refresh,
            )
        finally:
            conn.close()
    except BlastBaseError as error:
        raise click.ClickException(str(error)) from error

    paths = result.get("taint_paths") or []
    if output_format == "json":
        payload = dict(result)
        payload["taint_paths"] = [asdict(path) for path in paths]
        rendered = json.dumps(payload, indent=2, sort_keys=True)
    else:
        rendered = render_blast(result, output_format)
        if output_format in ("text", "markdown"):
            warning = format_taint_warning(paths)
            if warning:
                rendered = f"{rendered}\n{warning}\n"
    if output is None:
        click.echo(rendered)
    else:
        Path(output).write_text(rendered, encoding="utf-8")
