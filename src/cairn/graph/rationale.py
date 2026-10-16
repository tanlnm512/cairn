"""Read helpers for rationale records, ordered by line."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Iterable, List

_COLUMNS = "file_id, symbol_id, line, kind, text"

# Batch size for IN () clauses, under SQLite's 999 host-parameter limit.
_IN_CHUNK = 400


def records_for_symbol_ids(
    conn: sqlite3.Connection, symbol_ids: Iterable[str]
) -> List[Dict[str, Any]]:
    """Rationale rows attributed to the given symbol ids, ordered by line."""
    ids = list(symbol_ids)
    if not ids:
        return []
    rows: List[Dict[str, Any]] = []
    for start in range(0, len(ids), _IN_CHUNK):
        chunk = ids[start : start + _IN_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows.extend(
            dict(row)
            for row in conn.execute(
                f"SELECT {_COLUMNS} FROM rationale "
                f"WHERE symbol_id IN ({placeholders}) "
                "ORDER BY line, symbol_id, kind",
                chunk,
            ).fetchall()
        )
    # Chunked queries order within a chunk only; re-sort the merged rows.
    rows.sort(key=lambda r: (r["line"], r["symbol_id"], r["kind"]))
    return rows


def records_for_file(
    conn: sqlite3.Connection, file_id: str
) -> List[Dict[str, Any]]:
    """Rationale rows for one file id, ordered by line."""
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM rationale "
        "WHERE file_id = ? ORDER BY line, kind",
        (file_id,),
    ).fetchall()
    return [dict(row) for row in rows]
