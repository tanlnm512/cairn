"""Communities CLI command."""

from __future__ import annotations

import sys
from typing import Optional, Sequence

import click

from ..graph.communities import DEFAULT_TOP_K, refresh_communities
from .main import DEFAULT_DB_PATH, get_db, main

_HUB_SORT = (
    "ORDER BY sc.structural_degree DESC, f.path, s.qualified_name, sc.symbol_id "
    "LIMIT ?"
)


def _hub_rows(
    conn, community_id: Optional[int], top_k: int
) -> Sequence:
    """Top-K members by (-structural_degree, path, qualified_name); all communities when community_id is None."""
    where = "WHERE sc.community_id = ? " if community_id is not None else ""
    params = ((community_id,) if community_id is not None else ()) + (top_k,)
    return conn.execute(
        f"""
        SELECT s.qualified_name AS qualified_name,
               sc.structural_degree AS structural_degree
        FROM symbol_communities sc
        JOIN symbols s ON s.id = sc.symbol_id
        JOIN files f ON f.id = s.file_id
        {where}{_HUB_SORT}
        """,
        params,
    ).fetchall()


def _echo_hubs(rows, indent: str) -> None:
    for row in rows:
        click.echo(
            f"{indent}{row['qualified_name']} (degree {row['structural_degree']})"
        )


@main.command("communities")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option(
    "--top-k",
    type=click.IntRange(min=1),
    default=DEFAULT_TOP_K,
    show_default=True,
    help="Top-K hubs listed globally and per community.",
)
def communities(db, top_k):
    """Compute Louvain communities over structural edges and persist them."""
    try:
        count = refresh_communities(db)
    except ImportError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    if count == 0:
        click.echo("no communities found")
        return

    conn = get_db(db, read_only=True)
    try:
        click.echo(f"{count} communities")
        click.echo("Global hubs:")
        _echo_hubs(_hub_rows(conn, None, top_k), "  ")
        click.echo("Communities:")
        rows = conn.execute(
            "SELECT id, label, size FROM communities ORDER BY id"
        ).fetchall()
        for row in rows:
            click.echo(f"  {row['id']}: {row['label']} ({row['size']} symbols)")
            _echo_hubs(_hub_rows(conn, row["id"], top_k), "    ")
    finally:
        conn.close()
