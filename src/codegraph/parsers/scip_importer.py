"""SCIP (Sourcegraph Code Intelligence Protocol) index importer for codegraph.

Converts SCIP index files/payloads into codegraph symbols and exact call edges.
Supports both raw SCIP JSON payloads and standard SCIP protobuf structures.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


def import_scip_data(conn: sqlite3.Connection, scip_dict: Dict[str, Any], repo_id: str = "default") -> dict:
    """Import a SCIP index dictionary into codegraph database.

    Creates/updates `files`, `symbols`, and `edges` with `resolution='exact'`.
    Returns summary statistics dict.
    """
    cur = conn.cursor()
    symbols_added = 0
    edges_added = 0
    files_added = 0

    documents = scip_dict.get("documents", [])
    for doc in documents:
        rel_path = doc.get("relative_path") or doc.get("path")
        if not rel_path:
            continue

        lang = doc.get("language") or "unknown"
        file_id = f"{repo_id}:{rel_path}"

        cur.execute(
            "INSERT OR IGNORE INTO repos (id, name, path) VALUES (?, ?, ?)",
            (repo_id, repo_id, f"/{repo_id}"),
        )
        cur.execute(
            "INSERT OR REPLACE INTO files (id, path, repo_id, hash, line_count, language) "
            "VALUES (?, ?, ?, 'scip_imported', 0, ?)",
            (file_id, rel_path, repo_id, lang),
        )
        files_added += 1

        # Ensure a root file symbol exists to back reference edges
        root_sym_id = f"{file_id}:root"
        cur.execute(
            "INSERT OR IGNORE INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end) "
            "VALUES (?, ?, 'root', 'root', 'module', 1, 1)",
            (root_sym_id, file_id),
        )

        occurrences = doc.get("occurrences", [])
        last_def_id = root_sym_id
        for occ in occurrences:
            symbol_str = occ.get("symbol", "")
            if not symbol_str:
                continue

            range_val = occ.get("range", [0, 0, 0, 0])
            start_line = range_val[0] + 1 if range_val else 1
            start_col = range_val[1] if len(range_val) > 1 else 0

            roles = occ.get("symbol_roles", 0)
            is_def = bool(roles & 1) if isinstance(roles, int) else False

            sym_name = symbol_str.rstrip("#").rstrip(".").split(" ")[-1].split("/")[-1]

            if is_def:
                sym_id = f"{file_id}:{sym_name}:{start_line}"
                cur.execute(
                    "INSERT OR REPLACE INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end) "
                    "VALUES (?, ?, ?, ?, 'scip_symbol', ?, ?)",
                    (sym_id, file_id, sym_name, symbol_str, start_line, start_line),
                )
                symbols_added += 1
                last_def_id = sym_id
            else:
                edge_id = f"{file_id}:{sym_name}:{start_line}:{start_col}"
                cur.execute(
                    "INSERT OR REPLACE INTO edges (id, source_id, target_name, kind, line, column, resolution) "
                    "VALUES (?, ?, ?, 'call', ?, ?, 'exact')",
                    (edge_id, last_def_id, sym_name, start_line, start_col),
                )
                edges_added += 1

    conn.commit()
    return {
        "files_added": files_added,
        "symbols_added": symbols_added,
        "edges_added": edges_added,
    }


def import_scip_file(conn: sqlite3.Connection, scip_path: str, repo_id: str = "default") -> dict:
    """Import a SCIP JSON index file into codegraph database."""
    path = Path(scip_path)
    if not path.exists():
        raise FileNotFoundError(f"SCIP index file not found: {scip_path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    return import_scip_data(conn, data, repo_id=repo_id)
