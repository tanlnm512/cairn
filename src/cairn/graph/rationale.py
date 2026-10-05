"""Read helpers for rationale records, ordered by line."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Iterable, List

_COLUMNS = "file_id, symbol_id, line, kind, text"


def records_for_symbol_ids(
    conn: sqlite3.Connection, symbol_ids: Iterable[str]
) -> List[Dict[str, Any]]:
    """Rationale rows attributed to the given symbol ids, ordered by line."""
    ids = list(symbol_ids)
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM rationale "
        f"WHERE symbol_id IN ({placeholders}) "
        "ORDER BY line, symbol_id, kind",
        ids,
    ).fetchall()
    return [dict(row) for row in rows]


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
