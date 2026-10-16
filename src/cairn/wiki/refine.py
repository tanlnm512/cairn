"""Validator for an LLM-refined wiki catalog outline.

Resolution is repo-scoped: a refined entry's module and seed files must
resolve within the planning repo's graph rows, never across all repos.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from ..refs import _path_match_params, _path_match_sql
from .catalog import _like_under_prefix, _next_page_id, _page, _slug

_OVERVIEW_PAGE_ID = "overview"


def _module_in_graph(conn: sqlite3.Connection, module: str, repo: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM files WHERE repo_id = ? "
        "AND (path = ? OR path LIKE ? ESCAPE '\\') LIMIT 1",
        (repo, module, _like_under_prefix(module)),
    ).fetchone()
    return row is not None


def _file_in_repo(conn: sqlite3.Connection, repo: str, ref: str) -> bool:
    """True when ``ref`` resolves within ``repo``: the segment-anchored
    arms of :func:`cairn.refs.file_exists`, constrained to the repo."""
    ref = ref.strip("/")
    if not ref:
        return False
    row = conn.execute(
        f"SELECT 1 FROM files WHERE repo_id = ? AND {_path_match_sql()} LIMIT 1",
        (repo, *_path_match_params(ref)),
    ).fetchone()
    if row is not None:
        return True
    rid, _, rest = ref.partition("/")
    if not rest or rid != repo:
        return False
    row = conn.execute(
        f"SELECT 1 FROM files WHERE repo_id = ? AND {_path_match_sql()} LIMIT 1",
        (repo, *_path_match_params(rest)),
    ).fetchone()
    return row is not None


def _effective_entry(
    entry: Any,
    det_by_module: Dict[str, Dict[str, Any]],
    conn: sqlite3.Connection,
    repo: str,
    used_ids: set,
) -> Optional[Dict[str, Any]]:
    """Effective record for a refined entry (None = rejected); page ids de-collide against used_ids."""
    if not isinstance(entry, dict):
        return None
    module = entry.get("module")
    if not isinstance(module, str):
        return None
    if module != "" and not _module_in_graph(conn, module, repo):
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
    if not all(
        isinstance(p, str) and _file_in_repo(conn, repo, p) for p in seeds["files"]
    ):
        return None
    # The overview id is reserved for the module-"" entry; _slug is not
    # injective so module collisions get a numeric suffix.
    page_id = (
        _OVERVIEW_PAGE_ID
        if module == ""
        else _next_page_id(_slug(module) or "module", used_ids)
    )
    return _page(
        page_id=page_id,
        title=entry.get("title", ""),
        description=entry.get("description", ""),
        module=module,
        seeds=seeds,
    )


def validate_refined_outline(
    refined: List[Any],
    deterministic_plan: List[Dict[str, Any]],
    conn: sqlite3.Connection,
    repo: str,
) -> List[Dict[str, Any]]:
    """Validate a refined outline: effective entries in order, rejects revert, ids unique."""
    det_by_module = {entry["module"]: entry for entry in deterministic_plan}
    effective: List[Dict[str, Any]] = []
    used_ids: set = set()
    covered = set()
    for entry in refined:
        record = _effective_entry(entry, det_by_module, conn, repo, used_ids)
        if record is None or record["module"] in covered:
            continue
        covered.add(record["module"])
        effective.append(record)
    for entry in deterministic_plan:
        if entry["module"] in covered:
            continue
        if entry["page_id"] in used_ids:
            # Rebuild (rehashing the record) under the collision-free id.
            entry = _page(
                page_id=_next_page_id(entry["page_id"], used_ids),
                title=entry["title"],
                description=entry["description"],
                module=entry["module"],
                seeds=entry["seeds"],
            )
        else:
            used_ids.add(entry["page_id"])
        effective.append(entry)
    return effective
