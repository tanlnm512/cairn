"""CLI command for rationale marker records."""

from __future__ import annotations

import click

from .main import DEFAULT_DB_PATH, get_db, main


@main.command(name="rationale")
@click.option(
    "--symbol",
    default=None,
    help="Symbol name or qualified-name substring (case-insensitive).",
)
@click.option(
    "--file",
    "file_pattern",
    default=None,
    help="File path substring (case-insensitive).",
)
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON rows.")
def rationale(
    symbol: str | None,
    file_pattern: str | None,
    db: str,
    as_json: bool,
):
    """Print rationale comments (NOTE/WHY/HACK), ordered by line."""
    from ..graph.watcher import _read_only_env

    conn = get_db(db, read_only=_read_only_env())
    try:
        records = _collect(conn, symbol, file_pattern)
    finally:
        conn.close()
    if as_json:
        click.echo(_json(records))
        return
    if not records:
        click.echo("No rationale records found.")
        return
    click.echo(_text(records))


def _collect(conn, symbol: str | None, file_pattern: str | None) -> list[dict]:
    from ..graph.rationale import records_for_file, records_for_symbol_ids
    from ..graph.taint import resolve_pattern_symbols

    file_ids = _match_file_ids(conn, file_pattern)
    if symbol is not None:
        symbol_ids = [row["id"] for row in resolve_pattern_symbols(conn, symbol)]
        records = records_for_symbol_ids(conn, symbol_ids)
        if file_pattern is not None:
            records = [r for r in records if r["file_id"] in file_ids]
    elif file_pattern is not None:
        records = [r for fid in file_ids for r in records_for_file(conn, fid)]
    else:
        records = [
            r
            for row in conn.execute("SELECT id FROM files ORDER BY path")
            for r in records_for_file(conn, row["id"])
        ]
    paths = _file_paths(conn, {r["file_id"] for r in records})
    for record in records:
        record["path"] = paths.get(record["file_id"])
    records.sort(key=lambda r: (r["path"] or "", r["line"], r["kind"], r["text"]))
    return records


def _match_file_ids(conn, pattern: str | None) -> set[str]:
    if pattern is None:
        return set()
    rows = conn.execute(
        "SELECT id FROM files WHERE instr(lower(path), lower(?)) > 0 ORDER BY path",
        (pattern,),
    ).fetchall()
    return {row["id"] for row in rows}


def _file_paths(conn, file_ids: set[str]) -> dict[str, str]:
    if not file_ids:
        return {}
    placeholders = ",".join("?" * len(file_ids))
    rows = conn.execute(
        f"SELECT id, path FROM files WHERE id IN ({placeholders})",
        list(file_ids),
    ).fetchall()
    return {row["id"]: row["path"] for row in rows}


def _json(records: list[dict]) -> str:
    import json

    return json.dumps(records, indent=2)


def _text(records: list[dict]) -> str:
    return "\n".join(
        f"{r['path']}:{r['line']} [{r['kind']}] {r['text']}" for r in records
    )
