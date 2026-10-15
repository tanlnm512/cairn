"""Rank skill-candidate symbols by structural centrality in two tiers."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..graph.dataflow import closure_available
from ..graph.repo_map import projection

CLOSURE_TIER = "closure"
DEGREE_TIER = "degree"

# Per-query cap on seed ids in an IN () clause, under SQLite's 999
# host-parameter limit.
_SEED_ID_CHUNK = 400


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
        ancestors: set[str] = set()
        for i in range(0, len(seed_ids), _SEED_ID_CHUNK):
            chunk = seed_ids[i : i + _SEED_ID_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT DISTINCT t.source_id FROM transitive_edges t "
                f"WHERE t.target_id IN ({placeholders})",
                chunk,
            ).fetchall()
            ancestors.update(row["source_id"] for row in rows)
        scored.append((candidate, len(ancestors.difference(seed_ids))))
    return _ordered(scored)


def _rank_from_degrees(conn: sqlite3.Connection, candidates: list[str]) -> list[str]:
    """Order candidates by direct in/out degree from the build_repo_map rows."""
    wanted = set(candidates)
    degrees: dict[str, int] = {}
    for row in projection(conn).symbols:
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
