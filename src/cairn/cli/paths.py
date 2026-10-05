"""Symbol path query CLI command."""

from __future__ import annotations

import click

from ..graph.dataflow import CLOSURE_MAX_DEPTH
from ..graph.taint import DEFAULT_PATH_LIMIT, find_symbol_paths, resolve_pattern_symbols
from .main import DEFAULT_DB_PATH, get_db, main


@main.command("path")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option(
    "--from",
    "from_pattern",
    required=True,
    help="Origin pattern: substring of a symbol or qualified name.",
)
@click.option(
    "--to",
    "to_pattern",
    required=True,
    help="Destination pattern: substring of a symbol or qualified name.",
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
    help="Edge budget between endpoints (default: %d)." % CLOSURE_MAX_DEPTH,
)
@click.option(
    "--limit",
    type=int,
    default=DEFAULT_PATH_LIMIT,
    help="Maximum printed paths (default: %d)." % DEFAULT_PATH_LIMIT,
)
def path(db, from_pattern, to_pattern, fuzzy, max_depth, limit):
    """Print shortest structural paths between two symbol patterns."""
    if max_depth is not None and max_depth < 1:
        raise click.UsageError("--max-depth must be >= 1.")
    if limit < 1:
        raise click.UsageError("--limit must be >= 1.")

    effective_depth = max_depth if max_depth is not None else CLOSURE_MAX_DEPTH

    conn = get_db(db)
    try:
        from_syms = resolve_pattern_symbols(conn, from_pattern)
        to_syms = resolve_pattern_symbols(conn, to_pattern)
        if not from_syms or not to_syms:
            for side, pattern, syms in (
                ("from", from_pattern, from_syms),
                ("to", to_pattern, to_syms),
            ):
                if not syms:
                    click.echo(f"no symbols match {side} pattern '{pattern}'")
            return
        paths = find_symbol_paths(
            conn,
            {row["id"] for row in from_syms},
            {row["id"] for row in to_syms},
            fuzzy=fuzzy,
            max_depth=effective_depth,
        )
    finally:
        conn.close()

    if not paths:
        click.echo(
            f"no path within {effective_depth} hops "
            f"from '{from_pattern}' to '{to_pattern}'"
        )
        return

    for number, symbol_path in enumerate(paths[:limit], start=1):
        click.echo(
            f"path {number} ({from_pattern} -> {to_pattern}, "
            f"{len(symbol_path.hops)} hops):"
        )
        for hop in symbol_path.hops:
            line = hop.line if hop.line is not None else "?"
            click.echo(f"  {hop.file}:{line} {hop.symbol} [{hop.resolution}]")
