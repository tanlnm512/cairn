"""Flow coverage gap detection: find undocumented business flows."""

from __future__ import annotations

import sqlite3
from typing import Dict, List

from ..okf.bundle import OKFBundle


def _flow_resource(name: str, file: str, disambiguator: str | None = None) -> str:
    """Build a collision-safe resource key for a flow compass."""
    suffix = disambiguator if disambiguator else (file.split("/")[-1] if file else "?")
    return f"{name}#{suffix}"


def detect_flow_gaps(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    min_edges: int = 5,
) -> Dict[str, List[dict]]:
    """Find functions and methods meeting outgoing edge threshold lacking a flow compass.

    Returns a dict with 'uncovered' and 'covered' candidate lists sorted by
    out_edges descending.
    """
    candidates = _get_flow_candidates(conn, min_edges)

    # Detect which names collide (appear in multiple files) so we can build
    # collision-safe resource keys.
    name_files: dict[str, set[str]] = {}
    for c in candidates:
        name_files.setdefault(c["name"], set()).add(c["file"])
    colliding = {name for name, files in name_files.items() if len(files) > 1}

    # If basenames also collide for a given name, use parent directory disambiguation.
    name_basename_counts: dict[str, dict[str, int]] = {}
    for name in colliding:
        bcounts: dict[str, int] = {}
        for f in name_files[name]:
            base = f.split("/")[-1] if f else "?"
            bcounts[base] = bcounts.get(base, 0) + 1
        name_basename_counts[name] = bcounts

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
        # Collision-safe resource: name#suffix if colliding, else name.
        if c["name"] in colliding:
            base = c["file"].split("/")[-1] if c["file"] else "?"
            if name_basename_counts[c["name"]].get(base, 0) > 1:
                parts = [p for p in c["file"].split("/") if p]
                disambiguator = "/".join(parts[-2:]) if len(parts) >= 2 else base
                resource = _flow_resource(c["name"], c["file"], disambiguator=disambiguator)
            else:
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
