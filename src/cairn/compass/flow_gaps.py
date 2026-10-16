"""Flow coverage gap detection: find undocumented business flows."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Sequence

from ..graph.traversal import STRUCTURAL_EDGE_KINDS
from ..okf.bundle import OKFBundle


def _flow_resource(name: str, file: str, disambiguator: str | None = None) -> str:
    """Build a collision-safe resource key for a flow compass."""
    suffix = disambiguator if disambiguator else (file.split("/")[-1] if file else "?")
    return f"{name}#{suffix}"


def _name_files(conn: sqlite3.Connection, name: str) -> set:
    """Files holding a symbol ``name`` — the collision evidence the
    coverage key is classified from (all definitions, not just
    above-threshold candidates, so both sides classify identically)."""
    rows = conn.execute(
        "SELECT DISTINCT f.path FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name = ?",
        (name,),
    ).fetchall()
    return {r["path"] for r in rows}


def _same_file_ids(conn: sqlite3.Connection, name: str, file: str) -> list:
    """Ids of the symbols named ``name`` defined in ``file`` — the same-name-same-file collision evidence."""
    return [
        r["id"]
        for r in conn.execute(
            "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.name = ? AND f.path = ?",
            (name, file),
        ).fetchall()
    ]


def _coverage_resource(
    name: str, file: str, files: set,
    local_ids: Sequence[str] = (), symbol_id: str = "",
) -> str:
    """The coverage key shared by detection and generation: bare name when unique, basename/parent-dir suffix on
    file collisions, plus the symbol id when one file holds several same-named symbols."""
    if len(files) <= 1 and len(local_ids) <= 1:
        return name
    base = file.split("/")[-1] if file else "?"
    bcounts: dict[str, int] = {}
    for f in files:
        b = f.split("/")[-1] if f else "?"
        bcounts[b] = bcounts.get(b, 0) + 1
    if bcounts.get(base, 0) > 1:
        parts = [p for p in file.split("/") if p]
        suffix = "/".join(parts[-2:]) if len(parts) >= 2 else base
    else:
        suffix = base
    if len(local_ids) > 1 and symbol_id:
        suffix = f"{suffix}-{symbol_id}"
    return _flow_resource(name, file, disambiguator=suffix)


def detect_flow_gaps(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    min_edges: int = 5,
) -> Dict[str, List[Any]]:
    """Flow candidates over the edge threshold without a compass; unreadable compass ids land under 'unreadable'."""
    candidates = _get_flow_candidates(conn, min_edges)

    # Build the set of already-documented flow resources.
    covered_resources: set[str] = set()
    unreadable: List[str] = []
    for cid in bundle.list_concepts(prefix="compass/flow-"):
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            unreadable.append(cid)
            continue
        if concept.resource:
            covered_resources.add(concept.resource)

    uncovered: List[dict] = []
    covered: List[dict] = []
    for c in candidates:
        entry = dict(c)
        files = _name_files(conn, c["name"])
        local_ids = _same_file_ids(conn, c["name"], c["file"])
        resource = _coverage_resource(
            c["name"], c["file"], files, local_ids=local_ids, symbol_id=c["id"],
        )
        entry["resource"] = resource
        entry["colliding"] = len(files) > 1 or len(local_ids) > 1
        entry["covered"] = resource in covered_resources
        if entry["covered"]:
            covered.append(entry)
        else:
            uncovered.append(entry)

    return {"uncovered": uncovered, "covered": covered, "unreadable": unreadable}


def _get_flow_candidates(conn: sqlite3.Connection, min_edges: int) -> List[dict]:
    """Functions/methods with >= min_edges resolved outgoing edges of the structural kinds the flow trace walks.

    Groups by symbol ID to keep duplicate symbol names distinct; reference-only
    edges never mint candidates the trace cannot document.
    """
    cur = conn.cursor()
    kind_ph = ",".join("?" * len(STRUCTURAL_EDGE_KINDS))
    rows = cur.execute(
        f"""SELECT s.id, s.name, s.kind, f.path AS file_path, f.repo_id AS repo,
                  COUNT(e.id) AS out_edges
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           JOIN edges e ON e.source_id = s.id
           WHERE e.target_id IS NOT NULL
             AND e.kind IN ({kind_ph})
             AND s.kind IN ('function', 'method')
           GROUP BY s.id
           HAVING out_edges >= ?
           ORDER BY out_edges DESC""",
        (*STRUCTURAL_EDGE_KINDS, min_edges),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "kind": r["kind"],
            "file": r["file_path"],
            "repo": r["repo"],
            "out_edges": r["out_edges"],
        }
        for r in rows
    ]
