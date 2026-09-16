"""Rank skill-candidate symbols by structural centrality in two tiers.

Contract: ``rank_candidates(conn, candidates) -> RankedSymbols`` returns the
candidates whose definitions resolve in the graph, ordered by score desc
then qualified name asc (a total order), plus the tier that scored them:

- ``closure``: transitive impact — the count of distinct symbols that reach
  the candidate in the ``transitive_edges`` store built by
  :func:`cairn.graph.dataflow.build_transitive_closure` — used when
  :func:`cairn.graph.dataflow.closure_available` holds.
- ``degree``: direct in/out degree per symbol from the ``build_repo_map``
  projection rows — the degrade tier for closure-absent workspaces.

Read-only: issues SELECTs only; never mutates closure or graph tables.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..graph.dataflow import closure_available
from ..graph.repo_map import _projection

CLOSURE_TIER = "closure"
DEGREE_TIER = "degree"


@dataclass
class RankedSymbols:
    """Candidates ranked by structural centrality, plus the tier that scored them."""

    symbols: list[str]
    tier: str


def rank_candidates(conn: sqlite3.Connection, candidates: list[str]) -> RankedSymbols:
    """Rank candidate qualified names by centrality; drop unresolvable ones.

    ``tier`` is ``closure`` when the transitive closure store served the
    scores, else ``degree``.
    """
    unique = sorted(set(candidates))
    if closure_available(conn):
        ranked = _rank_from_closure(conn, unique)
        tier = CLOSURE_TIER
    else:
        ranked = _rank_from_degrees(conn, unique)
        tier = DEGREE_TIER
    return RankedSymbols(symbols=ranked, tier=tier)


def _rank_from_closure(conn: sqlite3.Connection, candidates: list[str]) -> list[str]:
    """Order candidates by their distinct transitive-ancestor count."""
    scored: list[tuple[str, int]] = []
    for candidate in candidates:
        seed_ids = _candidate_symbol_ids(conn, candidate)
        if not seed_ids:
            continue
        placeholders = ",".join("?" for _ in seed_ids)
        row = conn.execute(
            f"""
            SELECT COUNT(DISTINCT t.source_id) AS impact
            FROM transitive_edges t
            WHERE t.target_id IN ({placeholders})
              AND t.source_id NOT IN ({placeholders})
            """,
            (*seed_ids, *seed_ids),
        ).fetchone()
        scored.append((candidate, row["impact"]))
    return _ordered(scored)


def _rank_from_degrees(conn: sqlite3.Connection, candidates: list[str]) -> list[str]:
    """Order candidates by direct in/out degree from the build_repo_map rows."""
    wanted = set(candidates)
    degrees: dict[str, int] = {}
    for row in _projection(conn).symbols:
        if row.qualified_name in wanted:
            degrees[row.qualified_name] = (
                degrees.get(row.qualified_name, 0) + row.incoming + row.outgoing
            )
    return _ordered([(c, degrees[c]) for c in candidates if c in degrees])


def _candidate_symbol_ids(conn: sqlite3.Connection, candidate: str) -> list[str]:
    """Symbol ids matching a candidate's name or qualified name, exactly."""
    rows = conn.execute(
        "SELECT id FROM symbols WHERE name = ? OR qualified_name = ?",
        (candidate, candidate),
    ).fetchall()
    return [row["id"] for row in rows]


def _ordered(scored: list[tuple[str, int]]) -> list[str]:
    """Names sorted by score desc then name asc."""
    return [
        name for name, _ in sorted(scored, key=lambda item: (-item[1], item[0]))
    ]
