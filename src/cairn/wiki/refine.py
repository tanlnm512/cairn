"""Validator for an LLM-refined wiki catalog outline.

``validate_refined_outline`` merges a refined outline (a JSON array of
``{title, description, module, seeds?}`` entries) onto the deterministic
page plan and returns the effective page plan in refined order. An entry is
kept when its ``module`` matches a real ``files.path`` prefix (``path =
module OR path LIKE 'module/%'``; the empty module is the repo-wide overview
and is always valid) and every ``seeds.files`` path resolves via
``refs.file_exists``; seed symbols are not existence-checked. A rejected
entry is replaced by the deterministic plan's entry for the SAME MODULE —
never a positional neighbor — and a deterministic entry whose module the
refinement dropped is appended, so a refinement can reorder and reseed but
never silently lose a planned page. Kept entries are rebuilt with the
planner's record shape: omitted ``seeds`` inherit from the deterministic
entry for the same module, and ``input_hash`` is recomputed over the
canonical JSON of the record without the hash.
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
    det_by_module: Dict[str, Dict[str, Any]],
    conn: sqlite3.Connection,
) -> Optional[Dict[str, Any]]:
    """The effective record for one refined entry, or None when the entry
    is rejected (the deterministic plan still owns its module's page via
    the append pass in ``validate_refined_outline``)."""
    if not isinstance(entry, dict):
        return None
    module = entry.get("module")
    if not isinstance(module, str):
        return None
    if module != "" and not _module_in_graph(conn, module):
        return None
    raw_seeds = entry.get("seeds")
    if raw_seeds is None:
        inherited = det_by_module.get(module)
        seeds = (
            dict(inherited["seeds"])
            if inherited
            else {"files": [], "symbols": [], "docs": []}
        )
    elif isinstance(raw_seeds, dict):
        seeds = {
            "files": list(raw_seeds.get("files", [])),
            "symbols": list(raw_seeds.get("symbols", [])),
            "docs": list(raw_seeds.get("docs", [])),
        }
    else:
        return None
    if not all(isinstance(p, str) and file_exists(conn, p) for p in seeds["files"]):
        return None
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
    the planner's record shape (``page_id``/``title``/``description``/
    ``module``/``source``/``seeds``/``input_hash``). A rejected entry is
    replaced by the deterministic entry for the same module, and any
    deterministic entry the refinement dropped is appended, so a page is
    never silently lost to a positional shift.
    """
    det_by_module = {entry["module"]: entry for entry in deterministic_plan}
    effective: List[Dict[str, Any]] = []
    for entry in refined:
        record = _effective_entry(entry, det_by_module, conn)
        if record is not None:
            effective.append(record)
    covered = {record["module"] for record in effective}
    for entry in deterministic_plan:
        if entry["module"] not in covered:
            effective.append(entry)
    return effective
