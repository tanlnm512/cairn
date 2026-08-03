"""Dataflow CLI: the dataflow group (build/lookup)."""
from __future__ import annotations

import click
import json
import os
import subprocess
import sys
from pathlib import Path

from .main import DEFAULT_DB_PATH, DEFAULT_KNOWLEDGE_PATH, builder, get_db, main, queries, scanner_mod
from ._helpers import _human_bytes, _mods, _shorten  # noqa: F401

@main.group()
def dataflow():
    """Precomputed dataflow index for public symbols."""


@dataflow.command(name="build")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
def dataflow_build(db):
    """Build the dataflow index from scratch."""
    from ..graph.dataflow import build_dataflow_index

    conn = get_db(db)
    count = build_dataflow_index(conn)
    conn.close()
    click.echo(f"Dataflow index built: {count} public symbols indexed.")


@dataflow.command()
@click.argument("symbol")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def dataflow_lookup(symbol, db, as_json):
    """Look up precomputed dataflow for a symbol."""
    from ..graph.dataflow import get_dataflow

    conn = get_db(db)
    df = get_dataflow(conn, symbol)
    conn.close()
    if df is None:
        click.echo(f"No dataflow entry for '{symbol}'. Run `cairn dataflow build` first.", err=True)
        sys.exit(1)
    if as_json:
        click.echo(json.dumps(df, indent=2, default=str))
        return
    click.echo(f"Symbol: {df['symbol']}  (repo: {df['repo']})")
    if df["within_repo"]:
        click.echo(f"Within-repo impact ({len(df['within_repo'])} symbols):")
        for s in df["within_repo"][:20]:
            click.echo(f"  {s}")
        if len(df["within_repo"]) > 20:
            click.echo(f"  ... and {len(df['within_repo']) - 20} more")
    else:
        click.echo("Within-repo impact: (none)")
    if df["cross_repo"]:
        click.echo(f"Cross-repo consumers: {', '.join(df['cross_repo'])}")
    else:
        click.echo("Cross-repo consumers: (none)")
