"""Flow coverage gap detection: find undocumented business flows.

The flow equivalent of :mod:`compass.gaps` (which finds uncovered *modules*).
This finds functions/methods with rich outgoing call chains that lack a flow
compass file -- candidate business flows worth documenting.

A "flow" here is any function or method whose resolved outgoing edge count
meets a threshold (default 5). The intuition: a method with <5 resolved
callees is usually a trivial getter/setter/wrapper, while one with many
resolved callees orchestrates real business logic (the `handleCommand` in a
ViewModel, a repository's `login`, a UseCase's `execute`, ...).

Name collisions are expected: Android codebases have 4+ `handleCommand`
methods across different ViewModels. We group by ``symbols.id`` (not name)
so each is a distinct candidate, and include the file path in the result so
the caller can disambiguate. Coverage tracking uses a collision-safe
``resource`` key (``name`` for unique names, ``name#file_basename`` when the
name appears in multiple files) so two ``handleCommand`` flows are tracked
independently.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List

from ..okf.bundle import OKFBundle


def _flow_resource(name: str, file: str) -> str:
    """Build a collision-safe resource key for a flow compass.

    For unique names this is just the name. For colliding names (same name in
    multiple files) it appends ``#file_basename`` so each is distinguishable.
    The caller pre-computes the collision set and passes ``disambiguate=True``.
    """
    fname = file.split("/")[-1] if file else "?"
    return f"{name}#{fname}"


def detect_flow_gaps(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    min_edges: int = 5,
) -> Dict[str, List[dict]]:
    """Find functions/methods with rich call chains but no flow compass.

    ``conn`` is the graph DB connection, ``bundle`` the OKF bundle (read for
    existing flow compass coverage), ``min_edges`` the minimum resolved outgoing
    edges to qualify (default 5).

    Returns ``{"uncovered": [...], "covered": [...]}`` where each entry is
    ``{"name", "kind", "file", "repo", "out_edges", "id", "covered": bool}``,
    both lists sorted by ``out_edges`` descending.
    """
    candidates = _get_flow_candidates(conn, min_edges)

    # Detect which names collide (appear in multiple files) so we can build
    # collision-safe resource keys.
    name_files: dict[str, set[str]] = {}
    for c in candidates:
        name_files.setdefault(c["name"], set()).add(c["file"])
    colliding = {name for name, files in name_files.items() if len(files) > 1}

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
        # Collision-safe resource: name#file_basename if colliding, else name.
        if c["name"] in colliding:
            resource = _flow_resource(c["name"], c["file"])
        else:
            resource = c["name"]
        entry["resource"] = resource
        entry["colliding"] = c["name"] in colliding
        entry["covered"] = resource in covered_resources
        if entry["covered"]:
            covered.append(entry)
        else:
            uncovered.append(entry)

    return {"uncovered": uncovered, "covered": covered}


def _get_flow_candidates(conn: sqlite3.Connection, min_edges: int) -> List[dict]:
    """Find all functions/methods with >= min_edges resolved outgoing edges.

    Groups by ``symbols.id`` so overloaded/colliding names (e.g. multiple
    ``handleCommand`` methods in different ViewModels) are distinct entries.
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
