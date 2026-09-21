"""SQL writes for the graph store's indexing tables."""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable


class GraphRepository:
    """Write graph rows through a caller-owned cursor."""

    def insert_files(
        self, cur: sqlite3.Cursor, rows: Iterable[tuple[Any, ...]]
    ) -> None:
        cur.executemany(
            """INSERT INTO files (id, repo_id, path, language, hash, line_count,
               indexed_at, size, mtime)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

    def insert_symbols(
        self, cur: sqlite3.Cursor, rows: Iterable[tuple[Any, ...]]
    ) -> None:
        cur.executemany(
            """INSERT INTO symbols
               (id, file_id, name, qualified_name, kind, line_start, line_end,
                column_start, column_end, docstring, modifiers, metadata,
                parameters, return_type, parent_scope, imports_summary, body,
                arity, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'tree_sitter')""",
            rows,
        )

    def insert_imports(
        self, cur: sqlite3.Cursor, rows: Iterable[tuple[Any, ...]]
    ) -> None:
        cur.executemany(
            """INSERT INTO imports (id, file_id, imported_path,
               resolved_symbol_id, line, local_alias)
               VALUES (?,?,?,?,?,?)""",
            rows,
        )

    def insert_edges(
        self, cur: sqlite3.Cursor, rows: Iterable[tuple[Any, ...]]
    ) -> None:
        cur.executemany(
            """INSERT INTO edges
               (id, source_id, target_id, target_name, kind, line, column,
                resolution, source)
               VALUES (?,?,?,?,?,?,?,?,'tree_sitter')""",
            rows,
        )
