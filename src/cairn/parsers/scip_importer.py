"""SCIP (Sourcegraph Code Intelligence Protocol) index importer for cairn.

Converts SCIP index files/payloads into cairn symbols and exact call edges.
Supports both raw SCIP JSON payloads and standard SCIP protobuf structures.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict

# --- SCIP symbol_roles bitmask ----------------------------------------------
# Only the bits we care about for classification. See the SCIP protocol:
# https://github.com/sourcegraph/scip/blob/main/scip.proto
#   1   (1 << 0) Definition
#   2   (1 << 1) Import
#   4   (1 << 2) ForwardDefinition
#   64  (1 << 6) ReadAccess
#   128 (1 << 7) WriteAccess
# Non-definition occurrences also cover plain references and relation roles.
# A "call" is not a dedicated SCIP bit; we reserve kind='call' for occurrences
# that are neither import/read/write nor a definition, i.e. relation-style
# references such as implementations/usages. Pure references, reads and writes
# are emitted as kind='reference' so they are not mistaken for call edges.
_SCIP_ROLE_DEFINITION = 1
_SCIP_ROLE_IMPORT = 2
_SCIP_ROLE_READ_ACCESS = 64
_SCIP_ROLE_WRITE_ACCESS = 128
# Access bits bundled together (reference-ish, not a call).
_SCIP_ROLE_ACCESS_MASK = _SCIP_ROLE_READ_ACCESS | _SCIP_ROLE_WRITE_ACCESS


def import_scip_data(conn: sqlite3.Connection, scip_dict: Dict[str, Any], repo_id: str = "default") -> dict:
    """Import a SCIP index dictionary into cairn database.

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
            if not isinstance(roles, int):
                roles = 0
            is_def = bool(roles & _SCIP_ROLE_DEFINITION)

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
                # Classify the non-definition occurrence from its SCIP role
                # bits rather than assuming every reference is a call. Imports,
                # read accesses and write accesses are reference-ish; only the
                # remaining relation-style occurrences are treated as calls.
                if roles & _SCIP_ROLE_IMPORT or roles & _SCIP_ROLE_ACCESS_MASK:
                    edge_kind = "reference"
                else:
                    edge_kind = "call"
                edge_id = f"{file_id}:{sym_name}:{start_line}:{start_col}"
                cur.execute(
                    "INSERT OR REPLACE INTO edges (id, source_id, target_name, kind, line, column, resolution) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'exact')",
                    (edge_id, last_def_id, sym_name, edge_kind, start_line, start_col),
                )
                edges_added += 1

    conn.commit()
    return {
        "files_added": files_added,
        "symbols_added": symbols_added,
        "edges_added": edges_added,
    }


def import_scip_file(conn: sqlite3.Connection, scip_path: str, repo_id: str = "default") -> dict:
    """Import a SCIP JSON index file into cairn database."""
    path = Path(scip_path)
    if not path.exists():
        raise FileNotFoundError(f"SCIP index file not found: {scip_path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    return import_scip_data(conn, data, repo_id=repo_id)
