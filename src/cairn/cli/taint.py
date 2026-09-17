"""Taint path query CLI command."""

from __future__ import annotations

import sys

import click

from ..graph.dataflow import CLOSURE_MAX_DEPTH
from ..graph.taint import build_registry, find_paths
from .main import DEFAULT_DB_PATH, get_db, main


@main.command("taint")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option(
    "--from",
    "from_pattern",
    required=True,
    help="Source pattern: registry category token or exact call name.",
)
@click.option(
    "--to",
    "to_pattern",
    required=True,
    help="Sink pattern: registry category token or exact call name.",
)
@click.option(
    "--fuzzy",
    is_flag=True,
    help="Also traverse ambiguous and unresolved edges.",
)
@click.option(
    "--max-depth",
    type=int,
    default=None,
    help="Edge budget between entry and terminator (default: %d)." % CLOSURE_MAX_DEPTH,
)
def taint(db, from_pattern, to_pattern, fuzzy, max_depth):
    """Trace inter-procedural taint paths from a source pattern to a sink pattern."""
    from ..graph.config import load_config
    from ..paths import resolve_workspace

    if max_depth is not None and max_depth < 1:
        raise click.UsageError("--max-depth must be >= 1.")

    config = load_config(resolve_workspace())
    registry = build_registry(config.taint_sources, config.taint_sinks)

    unknown = [
        f"unknown {kind} pattern '{pattern}' (known {kind} categories: "
        f"{', '.join(sorted(categories)) or 'none'})"
        for kind, pattern, categories, names in (
            ("source", from_pattern, registry.sources, registry.source_names()),
            ("sink", to_pattern, registry.sinks, registry.sink_names()),
        )
        if pattern not in categories and pattern not in names
    ]
    if unknown:
        raise click.ClickException("; ".join(unknown))

    conn = get_db(db)
    try:
        paths = find_paths(
            conn,
            registry,
            from_pattern,
            to_pattern,
            fuzzy=fuzzy,
            max_depth=max_depth if max_depth is not None else CLOSURE_MAX_DEPTH,
        )
    finally:
        conn.close()

    if not paths:
        click.echo(
            f"No taint paths from '{from_pattern}' to '{to_pattern}'.", err=True
        )
        sys.exit(1)

    for number, path in enumerate(paths, start=1):
        click.echo(
            f"taint path {number} ({from_pattern} -> {to_pattern}, "
            f"{len(path.hops)} hops):"
        )
        for hop in path.hops:
            click.echo(f"  {hop.file}:{hop.symbol} [{hop.resolution}]")
