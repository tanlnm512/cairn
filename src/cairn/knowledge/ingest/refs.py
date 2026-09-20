"""Doc-to-code reference verification at ingest."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from cairn.refs import extract_file_refs
from cairn.refs import extract_symbol_refs
from cairn.refs import file_exists
from cairn.refs import symbol_exists


def resolve_doc_refs(conn: sqlite3.Connection, body: str) -> List[Dict[str, Any]]:
    """Verified ``{ref, kind, verified}`` entries for a body's backticked refs.

    File-shaped tokens (``extract_file_refs``) must satisfy ``file_exists``;
    symbol-shaped tokens (``extract_symbol_refs``) must satisfy
    ``symbol_exists``. Unresolved refs are dropped: absent from the
    returned entries and therefore from every stored surface (the
    frontmatter ``verified`` family, ``knowledge_doc_refs``). Order
    follows first mention, files before symbols; a ref cited repeatedly
    yields one entry. A ``None`` connection (no graph to resolve against)
    yields no entries.
    """
    if conn is None:
        return []
    entries: List[Dict[str, Any]] = []
    for ref in extract_file_refs(body):
        if file_exists(conn, ref):
            entries.append({"ref": ref, "kind": "file", "verified": True})
    for ref in dict.fromkeys(extract_symbol_refs(body)):
        if symbol_exists(conn, ref):
            entries.append({"ref": ref, "kind": "symbol", "verified": True})
    return entries
