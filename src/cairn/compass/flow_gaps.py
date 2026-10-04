"""Flow coverage gap detection: find undocumented business flows."""

from __future__ import annotations

import sqlite3
from typing import Dict, List

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


def _coverage_resource(name: str, file: str, files: set) -> str:
    """The coverage key shared by detection and generation: bare name when
    unique across files, basename suffix on collisions, parent-dir suffix
    when basenames also collide."""
    if len(files) <= 1:
        return name
    base = file.split("/")[-1] if file else "?"
    bcounts: dict[str, int] = {}
    for f in files:
        b = f.split("/")[-1] if f else "?"
        bcounts[b] = bcounts.get(b, 0) + 1
    if bcounts.get(base, 0) > 1:
        parts = [p for p in file.split("/") if p]
        disambiguator = "/".join(parts[-2:]) if len(parts) >= 2 else base
        return _flow_resource(name, file, disambiguator=disambiguator)
    return _flow_resource(name, file)


def detect_flow_gaps(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    min_edges: int = 5,
) -> Dict[str, List[dict]]:
    """Find functions and methods meeting outgoing edge threshold lacking a flow compass.

    Returns a dict with 'uncovered' and 'covered' candidate lists sorted by
    out_edges descending. Resources use the same coverage key
    (:func:`_coverage_resource`) the generator writes.
    """
    candidates = _get_flow_candidates(conn, min_edges)

    # Build the set of already-documented flow resources.
    covered_resources: set[str] = set()
    for cid in bundle.list_concepts(prefix="compass/flow-"):
        try:
            concept = bundle.read_concept(cid)
            if concept.resource:
                covered_resources.add(concept.resource)
        except Exception:
            continue

    uncovered: List[dict] = []
    covered: List[dict] = []
    for c in candidates:
        entry = dict(c)
        files = _name_files(conn, c["name"])
        resource = _coverage_resource(c["name"], c["file"], files)
        entry["resource"] = resource
        entry["colliding"] = len(files) > 1
        entry["covered"] = resource in covered_resources
        if entry["covered"]:
            covered.append(entry)
        else:
            uncovered.append(entry)

    return {"uncovered": uncovered, "covered": covered}


def _get_flow_candidates(conn: sqlite3.Connection, min_edges: int) -> List[dict]:
    """Find functions and methods with resolved outgoing edge count >= min_edges.

    Groups by symbol ID to keep duplicate symbol names distinct.
    """
    cur = conn.cursor()
    rows = cur.execute(
        """SELECT s.id, s.name, s.kind, f.path AS file_path, f.repo_id AS repo,
                  COUNT(e.id) AS out_edges
           FROM symbols s
           JOIN files f ON s.file_id = f.id
           JOIN edges e ON e.source_id = s.id
           WHERE e.target_id IS NOT NULL
             AND s.kind IN ('function', 'method')
           GROUP BY s.id
           HAVING out_edges >= ?
           ORDER BY out_edges DESC""",
        (min_edges,),
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
