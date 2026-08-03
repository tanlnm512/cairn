"""Tree CLI: directory/package structure command."""
from __future__ import annotations

import click
import json
import os
import subprocess
import sys
from pathlib import Path

from .main import DEFAULT_DB_PATH, DEFAULT_KNOWLEDGE_PATH, builder, get_db, main, queries, scanner_mod
from ._helpers import _human_bytes, _mods, _shorten  # noqa: F401

@main.command()
@click.argument("repo")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
def tree(repo, db):
    """Directory/package structure of REPO with symbol counts."""
    conn = get_db(db)
    buckets = queries.get_tree(conn, repo)
    conn.close()
    if not buckets:
        click.echo(f"No data for repo '{repo}'.", err=True)
        sys.exit(1)
    for key, count in buckets[:30]:
        click.echo(f"  {count:>6}  {key}")

