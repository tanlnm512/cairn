"""CLI command for the deterministic repository orientation map."""

from __future__ import annotations

import json

import click

from .main import DEFAULT_DB_PATH, get_db, main
from .query import _refresh_conn
from ..graph.repo_map import (
    DEFAULT_CLUSTER_CAP,
    DEFAULT_HUB_CAP,
    DEFAULT_HOTSPOT_CAP,
    build_repo_map,
)


@main.command(name="map")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
@click.option(
    "--max-clusters",
    type=click.IntRange(min=1),
    default=DEFAULT_CLUSTER_CAP,
    show_default=True,
    help="Maximum directory clusters per repository.",
)
@click.option(
    "--max-hubs",
    type=click.IntRange(min=1),
    default=DEFAULT_HUB_CAP,
    show_default=True,
    help="Maximum in-degree hubs per cluster.",
)
@click.option(
    "--max-hotspots",
    type=click.IntRange(min=1),
    default=DEFAULT_HOTSPOT_CAP,
    show_default=True,
    help="Maximum workspace-wide hotspots.",
)
@click.option(
    "--refresh/--no-refresh",
    default=None,
    help="Reindex drifted files before answering.",
)
def map_orientation(
    db, as_json, max_clusters, max_hubs, max_hotspots, refresh
):
    """Show directory clusters, hubs, and workspace hotspots."""
    from ..graph.watcher import _read_only_env

    read_only = _read_only_env()
    conn = get_db(db, read_only=read_only)
    try:
        _refresh_conn(conn, False if read_only else refresh)
        payload = build_repo_map(
            conn,
            cluster_cap=max_clusters,
            hub_cap=max_hubs,
            hotspot_cap=max_hotspots,
        )
    finally:
        conn.close()
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo(_text(payload))


def _text(payload: dict) -> str:
    lines = []
    for scope in payload["repos"]:
        lines.append(f"Repository {scope['repo']}")
        for cluster in scope["clusters"]:
            lines.append(
                f"  {cluster['path']}: {cluster['files']} files, "
                f"{cluster['symbols']} symbols, {cluster['edges']} edges"
            )
            for hub in cluster["hubs"]:
                lines.append(
                    f"    hub {hub['qualified_name']} "
                    f"({hub['path']}, in-degree {hub['incoming']})"
                )
            lines.append(f"    dropped hubs: {cluster['dropped_hubs']}")
        lines.append(f"  dropped clusters: {scope['dropped_clusters']}")
    lines.append("Workspace hotspots")
    for hotspot in payload["hotspots"]:
        lines.append(
            f"  {hotspot['repo']}:{hotspot['path']} "
            f"{hotspot['qualified_name']} (in-degree {hotspot['incoming']})"
        )
    lines.append(f"  dropped hotspots: {payload['dropped_hotspots']}")
    return "\n".join(lines)
