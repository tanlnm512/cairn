"""Precomputed dataflow index for public/exported symbols.

Materialises within-repo impact chains and cross-repo consumer repos for each
public symbol into the `dataflow` table. Built by build_dataflow_index();
queried via get_dataflow(). Fully deterministic from the code graph.

Also owns the `transitive_edges` closure table: build_transitive_closure()
materialises multi-hop caller→callee reachability to a fixed depth, and
impact_from_closure() answers ancestor ("who reaches this symbol") queries
from it in one indexed statement -- the fast path impact_analysis() routes to
when its preconditions hold.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Dict, List, Optional, Sequence

from .traversal import STRUCTURAL_EDGE_KINDS

# The closure is materialised to this *closure distance*. impact_analysis()
# index mode is eligible for DFS ``max_depth`` values one below it: DFS
# records a direct caller at depth 0 (= closure distance 1), so a query at
# ``max_depth=D`` needs ancestors up to closure distance D+1.
CLOSURE_MAX_DEPTH = 4


def _public_symbols(conn: sqlite3.Connection) -> List[Dict[str, str]]:
    """Return symbols that are considered "public" / exported.

    Selection heuristic:
    - Java/Kotlin: modifiers contain 'public'
    - Python: name does NOT start with underscore (convention for private)
    - Other languages: all symbols (conservative default)

    Returns list of {"name": str, "repo": str}.
    """
    rows = conn.execute("""
        SELECT s.name, s.modifiers, r.name AS repo, r.language
        FROM symbols s
        JOIN files f ON s.file_id = f.id
        JOIN repos r ON f.repo_id = r.id
        WHERE s.kind IN ('function', 'method', 'class', 'interface', 'enum')
    """).fetchall()

    result = []
    for row in rows:
        name = row["name"]
        repo = row["repo"]
        modifiers = row["modifiers"] or ""
        lang = row["language"] or ""

        # Java/Kotlin: only include if modifiers contain 'public'
        if lang in ("java", "kotlin") and "public" not in modifiers:
            continue
        # Python: exclude leading-underscore names (private convention)
        if lang == "python" and name.startswith("_"):
            continue
        result.append({"name": name, "repo": repo})
    return result


def build_dataflow_index(
    conn: sqlite3.Connection, progress=None, max_symbols: int = 2000
) -> int:
    """Build the dataflow table from scratch for all public symbols.

    Iterates public symbols, computes within-repo impact (impact_analysis) and
    cross-repo consumers (cross_repo_deps), and upserts into the dataflow table.

    ``progress`` is an optional callable(n_done) for CLI progress reporting.

    ``max_symbols`` caps the number of public symbols processed per call. Each
    symbol triggers a per-symbol BFS (impact_analysis), so an unbounded loop
    never completes for large repos; this converts a hang into bounded work.
    If truncated, a warning is emitted and the returned count reflects only the
    symbols actually indexed (the dataflow table is partial but still usable).

    Returns the number of symbols indexed.
    """
    from .queries import impact_analysis, cross_repo_deps  # avoid circular import

    symbols = _public_symbols(conn)
    truncated = len(symbols) > max_symbols
    if truncated:
        import sys

        print(
            f"warning: dataflow index is partial -- "
            f"{len(symbols)} public symbols found, capping at {max_symbols}. "
            f"Re-run or raise max_symbols for full coverage.",
            file=sys.stderr,
        )
        symbols = symbols[:max_symbols]
    count = 0
    now = time.time()

    for sym in symbols:
        name = sym["name"]
        repo = sym["repo"]

        # Within-repo: impacted symbols from impact_analysis
        try:
            impact = impact_analysis(conn, name, max_depth=5)
            within = list({item["symbol"] for item in impact.get("impacted", []) if item.get("symbol") != name})
        except Exception:
            within = []

        # Cross-repo: consumer repos
        try:
            xref = cross_repo_deps(conn, repo)
            cross = [d["repo"] for d in xref.get("dependents", [])]
        except Exception:
            cross = []

        conn.execute("""
            INSERT OR REPLACE INTO dataflow (symbol, repo, within_repo, cross_repo, updated)
            VALUES (?, ?, ?, ?, ?)
        """, (name, repo, json.dumps(within), json.dumps(cross), now))
        count += 1
        if progress:
            progress(count)

    conn.commit()
    return count


def build_transitive_closure(conn: sqlite3.Connection, max_depth: int = CLOSURE_MAX_DEPTH) -> int:
    """Precompute multi-hop call graph edges into transitive_edges matrix table.

    Joins on resolved target_id (resolution='exact') to avoid name collisions
    producing spurious edges; falls back to target_name for unresolved edges.

    Only **structural** edge kinds (``calls``/``call``/``extends``/
    ``implements``, per :data:`traversal.STRUCTURAL_EDGE_KINDS`) are seeded and
    extended, matching ``impact_analysis``'s default edge filter -- the table's
    read path (:func:`impact_from_closure`) serves exactly those queries.
    Service/topology edges never enter the closure; queries that opt into them
    (``include_service_edges=True``) take the DFS path instead.
    """
    cur = conn.cursor()
    # The per-depth extension filters on transitive_edges.distance; create the
    # index idempotently here so this function self-optimizes.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_transitive_distance ON transitive_edges(distance)"
    )
    kind_ph = ",".join("?" for _ in STRUCTURAL_EDGE_KINDS)
    kind_params = tuple(STRUCTURAL_EDGE_KINDS)
    cur.execute("DELETE FROM transitive_edges")

    # Seed with direct structural edges (distance=1)
    cur.execute(f"""
        INSERT OR IGNORE INTO transitive_edges (source_id, target_name, target_id, distance)
        SELECT
            e.source_id,
            COALESCE(s.name, e.target_name) AS target_name,
            e.target_id,
            1
        FROM edges e
        LEFT JOIN symbols s ON s.id = e.target_id
        WHERE (e.target_id IS NOT NULL OR (e.target_name IS NOT NULL AND e.target_name != ''))
          AND e.kind IN ({kind_ph})
    """, kind_params)
    total_inserted = cur.rowcount

    for d in range(1, max_depth):
        # Extend by one hop. For resolved edges (target_id IS NOT NULL), follow
        # only edges.source_id = target_id (exact ID match) to avoid name collisions.
        # For unresolved edges, fall back to name-based matching.
        batch_inserted = 0

        # Case 1: follow resolved edges (target_id IS NOT NULL)
        cur.execute(f"""
            INSERT OR IGNORE INTO transitive_edges (source_id, target_name, target_id, distance)
            SELECT
                t.source_id,
                COALESCE(s_target.name, e.target_name) AS target_name,
                e.target_id,
                t.distance + 1
            FROM transitive_edges t
            JOIN edges e ON e.source_id = t.target_id
            LEFT JOIN symbols s_target ON s_target.id = e.target_id
            WHERE t.distance = ? AND t.target_id IS NOT NULL
              AND e.kind IN ({kind_ph})
        """, (d, *kind_params))
        batch_inserted += cur.rowcount

        # Case 2: fallback for unresolved edges (target_id IS NULL). Only
        # follow when the target_name maps to EXACTLY ONE symbol -- a name with
        # multiple definitions is a collision, and following any one of them
        # would re-introduce the name-collision inflation the precise-by-default
        # design exists to prevent. Ambiguous names are skipped (left
        # unextended) rather than guessed.
        cur.execute(f"""
            INSERT OR IGNORE INTO transitive_edges (source_id, target_name, target_id, distance)
            SELECT
                t.source_id,
                COALESCE(s_target.name, e.target_name) AS target_name,
                e.target_id,
                t.distance + 1
            FROM transitive_edges t
            JOIN (
                SELECT name FROM symbols
                GROUP BY name HAVING COUNT(*) = 1
            ) uniq ON uniq.name = t.target_name
            JOIN symbols s_mid ON s_mid.name = uniq.name
            JOIN edges e ON e.source_id = s_mid.id
            LEFT JOIN symbols s_target ON s_target.id = e.target_id
            WHERE t.distance = ?
                AND t.target_id IS NULL
                AND (e.target_id IS NOT NULL OR (e.target_name IS NOT NULL AND e.target_name != ''))
                AND e.kind IN ({kind_ph})
        """, (d, *kind_params))
        batch_inserted += cur.rowcount

        if batch_inserted == 0:
            break
        total_inserted += batch_inserted

    conn.commit()
    return total_inserted


def closure_available(conn: sqlite3.Connection) -> bool:
    """True when the transitive closure table is populated and safe to read.

    Cheap indexed probe. False on never-built databases (the reader then falls
    back to DFS until the next ``cairn build``/``update`` materialises it).
    """
    try:
        return (
            conn.execute("SELECT 1 FROM transitive_edges LIMIT 1").fetchone()
            is not None
        )
    except sqlite3.Error:
        return False


def impact_from_closure(
    conn: sqlite3.Connection,
    seed_ids: Sequence[str],
    max_depth: int,
    limit: int,
) -> Optional[dict]:
    """Answer an impact query from the precomputed closure in one statement.

    Returns the same shape as :func:`traversal.impact_analysis` --
    ``{impacted, cycles, total, truncated}`` -- with these documented index-mode
    semantics (the DFS path remains available for exact parity):

    - ``depth`` is the **shortest** caller distance (MIN over closure rows,
      minus the final hop into the seed so a direct caller is depth 0, matching
      DFS's numbering), not the DFS first-visit path length: shortest ≤ DFS
      depth for the same node, and is the more meaningful "minimum hops to a
      caller" number.
    - ``cycles`` is always empty -- the closure cannot attribute back-edges.
      Callers gate on :func:`closure_has_seed_cycle` and take the DFS path
      when a cycle exists so cycle reporting is preserved.
    - Coverage is a **superset** of precise DFS at the same depth cap: it also
      includes unique-name-mediated hops (closure Case 2) that precise DFS
      prunes, and is not subject to DFS's per-node 200-caller fetch cap.
    - Rows are ordered by (depth, symbol, file) -- deterministic.

    ``seed_ids`` are the symbol ids the entry name resolves to (as
    ``find_definition`` would return them); seeds themselves are excluded from
    the results, matching DFS's pre-visited seed handling.
    """
    if not seed_ids:
        return {"impacted": [], "cycles": [], "total": 0, "truncated": False}
    seed_ph = ",".join("?" for _ in seed_ids)
    # Fetch limit+1 so truncation is exact (DFS approximates it the same way
    # it approximates everything: by noticing more work mid-walk).
    rows = conn.execute(
        f"""
        SELECT s.name AS symbol, f.path AS file, f.repo_id AS repo,
               MIN(t.distance) - 1 AS depth
        FROM transitive_edges t
        JOIN symbols s ON s.id = t.source_id
        JOIN files f ON s.file_id = f.id
        WHERE t.target_id IN ({seed_ph})
          AND t.source_id NOT IN ({seed_ph})
          AND t.distance <= ?
        GROUP BY t.source_id
        ORDER BY depth, symbol, file
        LIMIT ?
        """,
        (*seed_ids, *seed_ids, max_depth + 1, limit + 1),
    ).fetchall()
    truncated = len(rows) > limit
    impacted = [
        {"symbol": r["symbol"], "file": r["file"], "repo": r["repo"], "depth": r["depth"]}
        for r in rows[:limit]
    ]
    return {
        "impacted": impacted,
        "cycles": [],
        "total": len(impacted),
        "truncated": truncated,
    }


def closure_has_seed_cycle(
    conn: sqlite3.Connection, seed_ids: Sequence[str]
) -> bool:
    """True when any seed reaches another seed through the closure.

    Used as the gate that keeps cycle-reporting queries on the DFS path: the
    closure can detect that A→…→B exists among seeds but cannot report the
    back-edge symbol/depth pairs impact consumers get from DFS.
    """
    if not seed_ids:
        return False
    seed_ph = ",".join("?" for _ in seed_ids)
    return (
        conn.execute(
            f"""
            SELECT 1 FROM transitive_edges
            WHERE source_id IN ({seed_ph}) AND target_id IN ({seed_ph}) LIMIT 1
            """,
            tuple(seed_ids) * 2,
        ).fetchone()
        is not None
    )


def get_dataflow(conn: sqlite3.Connection, symbol: str) -> Optional[Dict]:
    """Look up precomputed dataflow for a symbol.

    Returns dict with keys: symbol, repo, within_repo (list), cross_repo (list),
    updated (float timestamp), or None if the symbol has no entry.
    """
    row = conn.execute(
        "SELECT * FROM dataflow WHERE symbol = ?", (symbol,)
    ).fetchone()

    if row is None:
        return None

    return {
        "symbol": row["symbol"],
        "repo": row["repo"],
        "within_repo": json.loads(row["within_repo"] or "[]"),
        "cross_repo": json.loads(row["cross_repo"] or "[]"),
        "updated": row["updated"],
    }
