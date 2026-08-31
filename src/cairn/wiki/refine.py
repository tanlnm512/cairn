"""Validator for an LLM-refined wiki catalog outline.

``validate_refined_outline`` merges a refined outline (a JSON array of
``{title, description, module, seeds?}`` entries) onto the deterministic
page plan and returns the effective page plan in refined order. An entry is
kept when its ``module`` matches a real ``files.path`` prefix (``path =
module OR path LIKE 'module/%'``; the empty module is the repo-wide overview
and is always valid) and every ``seeds.files`` path resolves via
``refs.file_exists``; seed symbols are not existence-checked. A rejected
entry is replaced by the deterministic plan's entry at the same position.
Kept entries are rebuilt with the planner's record shape: omitted ``seeds``
inherit from the deterministic entry for the same module, and
``input_hash`` is recomputed over the canonical JSON of the record without
the hash.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from ..refs import file_exists
from .catalog import _like_under_prefix, _page, _slug

_OVERVIEW_PAGE_ID = "overview"


def _module_in_graph(conn: sqlite3.Connection, module: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM files WHERE path = ? OR path LIKE ? ESCAPE '\\' LIMIT 1",
        (module, _like_under_prefix(module)),
    ).fetchone()
    return row is not None


def _effective_entry(
    entry: Any,
    index: int,
    deterministic_plan: List[Dict[str, Any]],
    det_by_module: Dict[str, Dict[str, Any]],
    conn: sqlite3.Connection,
) -> Optional[Dict[str, Any]]:
    """The effective record for one refined entry, or None to keep the
    deterministic plan's entry for the same position."""

    def fallback() -> Optional[Dict[str, Any]]:
        return deterministic_plan[index] if index < len(deterministic_plan) else None

    if not isinstance(entry, dict):
        return fallback()
    module = entry.get("module")
    if not isinstance(module, str):
        return fallback()
    if module != "" and not _module_in_graph(conn, module):
        return fallback()
    raw_seeds = entry.get("seeds")
    if raw_seeds is None:
        inherited = det_by_module.get(module)
        seeds = dict(inherited["seeds"]) if inherited else {"files": [], "symbols": []}
    elif isinstance(raw_seeds, dict):
        seeds = {
            "files": list(raw_seeds.get("files", [])),
            "symbols": list(raw_seeds.get("symbols", [])),
        }
    else:
        return fallback()
    if not all(isinstance(p, str) and file_exists(conn, p) for p in seeds["files"]):
        return fallback()
    return _page(
        page_id=_OVERVIEW_PAGE_ID if module == "" else _slug(module),
        title=entry.get("title", ""),
        description=entry.get("description", ""),
        module=module,
        seeds=seeds,
    )


def validate_refined_outline(
    refined: List[Any],
    deterministic_plan: List[Dict[str, Any]],
    conn: sqlite3.Connection,
) -> List[Dict[str, Any]]:
    """Validate a refined catalog outline against the graph.

    Returns the effective page plan in refined order; every record carries
    the planner's six fields (``page_id``/``title``/``description``/
    ``module``/``seeds``/``input_hash``). An entry that fails validation is
    replaced by ``deterministic_plan``'s entry at the same index (dropped
    when the refined outline is longer than the deterministic plan).
    """
    det_by_module = {entry["module"]: entry for entry in deterministic_plan}
    effective: List[Dict[str, Any]] = []
    for index, entry in enumerate(refined):
        record = _effective_entry(
            entry, index, deterministic_plan, det_by_module, conn
        )
        if record is not None:
            effective.append(record)
    return effective
