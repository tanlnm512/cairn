"""CLI command for span-grouped graph grep."""

from __future__ import annotations

import click

from .main import DEFAULT_DB_PATH, get_db, main
from .query import _refresh_conn


@main.command(name="grep")
@click.argument("pattern")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
@click.option("-i", "--ignore-case", is_flag=True, help="Ignore case.")
@click.option("--fixed", is_flag=True, help="Treat PATTERN as a literal.")
@click.option(
    "--in",
    "path_prefix",
    default="",
    help="Only search repository-relative paths under this prefix.",
)
@click.option(
    "--max-hits",
    type=click.IntRange(min=1),
    default=256,
    show_default=True,
    help="Maximum hits returned.",
)
@click.option(
    "--refresh/--no-refresh",
    default=None,
    help="Reindex drifted files before answering.",
)
def grep_pattern(
    pattern: str,
    db: str,
    as_json: bool,
    ignore_case: bool,
    fixed: bool,
    path_prefix: str,
    max_hits: int,
    refresh: bool | None,
):
    """Search indexed files and group matches by enclosing symbol."""
    from ..graph.watcher import _read_only_env
    from ..graph.grep import graph_grep
    from ..paths import resolve_workspace

    read_only = _read_only_env()
    conn = get_db(db, read_only=read_only)
    try:
        try:
            _refresh_conn(conn, False if read_only else refresh)
            payload = graph_grep(
                conn,
                str(resolve_workspace()),
                pattern,
                ignore_case=ignore_case,
                fixed=fixed,
                path_prefix=path_prefix,
                max_hits=max_hits,
            )
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
    finally:
        conn.close()
    if as_json:
        click.echo(_json(payload))
        return
    click.echo(_text(payload))


def _json(payload: dict) -> str:
    import json

    return json.dumps(payload, indent=2)


def _text(payload: dict) -> str:
    lines = [f"searched files: {payload['searched_files']}"]
    if payload["unreadable_files"]:
        lines.append(f"unreadable files: {payload['unreadable_files']}")
    for group in payload["groups"]:
        name = group["qualified_name"] or "<file>"
        lines.append(
            f"{group['repo']}:{group['path']} {name} "
            f"(in-degree {group['incoming']})"
        )
        for hit in group["hits"]:
            lines.append(f"  {hit['line']}:{hit['column']} {hit['text']}")
    lines.append(f"dropped hits: {payload['dropped_hits']}")
    lines.append(f"dropped groups: {payload['dropped_groups']}")
    return "\n".join(lines)
