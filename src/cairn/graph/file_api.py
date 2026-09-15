"""Read-only file symbol surfaces assembled from stored graph rows."""

from __future__ import annotations

import sqlite3
from typing import Any


def _signature(
    qualified_name: str, parameters: str | None, return_type: str | None
) -> str | None:
    parameters = parameters.strip() if parameters else None
    return_type = return_type.strip() if return_type else None
    if parameters is None and return_type is None:
        return None

    signature = qualified_name
    if parameters is not None:
        signature = f"{signature}({parameters})"
    if return_type is not None:
        signature = f"{signature} -> {return_type}"
    return signature


def file_api(
    conn: sqlite3.Connection, path: str, repo: str | None = None
) -> list[dict[str, Any]]:
    """Return every stored symbol for one indexed file in source order."""
    repos = [
        row["repo_id"]
        for row in conn.execute(
            "SELECT DISTINCT repo_id FROM files WHERE path = ? ORDER BY repo_id",
            (path,),
        )
    ]
    if repo is None and len(repos) > 1:
        candidates = ", ".join(repos)
        raise ValueError(
            f"Path '{path}' exists in multiple repositories: {candidates}. "
            "Pass repo with one of those ids."
        )
    selected_repo = repo if repo is not None else repos[0] if repos else None
    rows = conn.execute(
        """
        SELECT s.name,
               COALESCE(NULLIF(s.qualified_name, ''), s.name, '') AS qualified_name,
               s.kind,
               s.line_start,
               s.line_end,
               s.parameters,
               s.return_type
        FROM symbols AS s
        JOIN files AS f ON f.id = s.file_id
        WHERE f.path = ? AND (? IS NULL OR f.repo_id = ?)
        ORDER BY s.line_start, s.line_end, s.qualified_name, s.name
        """,
        (path, selected_repo, selected_repo),
    ).fetchall()
    return [
        {
            "name": row["name"],
            "kind": row["kind"],
            "qualified_name": row["qualified_name"],
            "signature": _signature(
                row["qualified_name"], row["parameters"], row["return_type"]
            ),
            "line_start": row["line_start"],
            "line_end": row["line_end"],
        }
        for row in rows
    ]
