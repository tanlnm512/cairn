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
        if not _row_is_public(row):
            continue
        result.append({"name": row["name"], "repo": row["repo"]})
    return result


def _row_is_public(row) -> bool:
    """The public/exported predicate shared by _public_symbols and the
    incremental maintainer, so a maintained dataflow table contains exactly
    the names a full build would have indexed (no stale rows for symbols that
    stopped being public, e.g. a rename to a ``_private`` name).

    ``row`` needs ``name``, ``modifiers`` and the repo's ``language``.
    """
    name = row["name"]
    modifiers = row["modifiers"] or ""
    lang = row["language"] or ""
    # Java/Kotlin: only include if modifiers contain 'public'
    if lang in ("java", "kotlin") and "public" not in modifiers:
        return False
    # Python: exclude leading-underscore names (private convention)
    if lang == "python" and name.startswith("_"):
        return False
    return True


# Keep IN () batches well under SQLite's default host-parameter limit (999 on
# older builds) so affected-set queries work on every SQLite the CLI can ship
# with. Sorting makes the batches deterministic (easier to reason about in
# logs/tests) even though correctness does not depend on order.
_SQLITE_IN_CHUNK = 400


def _chunked(items, size: int = _SQLITE_IN_CHUNK):
    """Yield sorted lists of at most ``size`` items for batched IN () queries."""
    items = sorted(set(items))
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _compute_dataflow_row(
    conn: sqlite3.Connection, name: str, repo: str
) -> tuple[list[str], list[str]]:
    """Compute one symbol's dataflow payload: (within_repo, cross_repo).

    Shared by the full builder (:func:`build_dataflow_index`) and the
    incremental maintainer (:func:`maintain_dataflow_index`) so the two paths
    can never drift on semantics -- the property-parity tests diff a maintained
    table against a fresh full build row-for-row, which only holds if both
    call this one function.
    """
    from .queries import impact_analysis, cross_repo_deps  # avoid circular import

    try:
        impact = impact_analysis(conn, name, max_depth=5)
        within = list({item["symbol"] for item in impact.get("impacted", []) if item.get("symbol") != name})
    except Exception:
        within = []

    try:
        xref = cross_repo_deps(conn, repo)
        cross = [d["repo"] for d in xref.get("dependents", [])]
    except Exception:
        cross = []
    return within, cross


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
        within, cross = _compute_dataflow_row(conn, sym["name"], sym["repo"])

        conn.execute("""
            INSERT OR REPLACE INTO dataflow (symbol, repo, within_repo, cross_repo, updated)
            VALUES (?, ?, ?, ?, ?)
        """, (sym["name"], sym["repo"], json.dumps(within), json.dumps(cross), now))
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


def maintain_transitive_closure(
    conn: sqlite3.Connection,
    affected_source_ids,
    max_depth: int = CLOSURE_MAX_DEPTH,
) -> int:
    """Incrementally recompute closure rows for a bounded set of sources (PERF-3).

    A full :func:`build_transitive_closure` wipes the whole table and re-derives
    every source -- minutes on a 1000-file repo for a one-file edit. This
    function re-derives only ``affected_source_ids``: DELETE their rows, then
    run the *same* seed + per-depth extension SQL as the builder, restricted to
    those sources.

    WHY per-source restriction is exact (the parity argument): every rule in
    ``build_transitive_closure`` writes rows carrying the ``source_id`` it was
    seeded from -- the seed reads ``edges.source_id``, and each extension hop
    propagates ``t.source_id`` unchanged. No rule ever mixes two sources, so
    the builder's output is the union of independent per-source computations.
    Re-deriving one source's rows in isolation with identical SQL (same
    STRUCTURAL_EDGE_KINDS filter, same INSERT OR IGNORE-by-increasing-distance
    ordering, so MIN-distance rows survive exactly as the builder leaves them)
    reproduces bit-for-bit what a full rebuild would produce for that source.
    The one cross-source input is Case 2's global name-uniqueness subquery,
    which is read live and therefore identical for both paths.

    Correctness for deleted and newly-created symbols (why deleting/re-deriving
    only affected sources suffices):

    - **Deleted symbols** (old ids of an edited file): as a *source*, their rows
      vanish with the DELETE -- their ids are in the affected set by
      construction. As a *target*, a stale row ``(S, v, d)`` referencing a
      deleted ``v`` implies ``S`` reached ``v`` pre-edit, so ``S`` is an
      ancestor of a changed symbol and was captured into the affected set
      before the edit; its rows (including the stale one) are re-derived.
      Hence no row referencing a deleted target survives under an unaffected
      source.
    - **New symbols**: fresh rows only appear under (a) the new symbols
      themselves (in the set -- they are the re-indexed file's symbols) and
      (b) sources reaching them, i.e. direct callers whose edges now point at
      them (captured post-resolve) and, transitively, the closure ancestors of
      those callers (captured pre-edit). Anything outside that set could not
      have gained a path to the new symbol.

    The caller is responsible for the affected-set capture (see
    ``incremental._maintain_derived_indexes``); passing too small a set leaves
    stale rows, too large a set only costs re-derivation. When the closure
    table is empty/never built, callers must fall back to the full build --
    there is no (assumed-correct) pre-state to compute ancestors from.

    Returns the number of rows inserted.
    """
    affected = sorted({i for i in affected_source_ids if i})
    if not affected:
        return 0
    cur = conn.cursor()
    # Same idempotent index the full builder creates; the per-depth extension
    # filters on transitive_edges.distance.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_transitive_distance ON transitive_edges(distance)"
    )
    kind_ph = ",".join("?" for _ in STRUCTURAL_EDGE_KINDS)
    kind_params = tuple(STRUCTURAL_EDGE_KINDS)
    total_inserted = 0

    # Stage the affected ids in a temp table instead of IN () lists. WHY not
    # IN-lists: each extension statement then runs ONCE over the whole set
    # (chunked lists re-ran the expensive statements per chunk), and the
    # planner can probe transitive_edges' PK per affected id. The table is
    # per-connection and dropped before returning, so concurrent connections
    # (watcher, serve) never see it.
    cur.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _cairn_maint_affected (id TEXT PRIMARY KEY)"
    )
    try:
        cur.execute("DELETE FROM _cairn_maint_affected")
        cur.executemany(
            "INSERT OR IGNORE INTO _cairn_maint_affected (id) VALUES (?)",
            ((i,) for i in affected),
        )
        # Drop every stale row for these sources first -- including rows of
        # sources that no longer exist (deleted symbols).
        cur.execute(
            "DELETE FROM transitive_edges WHERE source_id IN "
            "(SELECT id FROM _cairn_maint_affected)"
        )

        # Seed with direct structural edges (distance=1) -- same SQL as the
        # builder, plus the source restriction.
        cur.execute(f"""
            INSERT OR IGNORE INTO transitive_edges (source_id, target_name, target_id, distance)
            SELECT
                e.source_id,
                COALESCE(s.name, e.target_name) AS target_name,
                e.target_id,
                1
            FROM edges e
            LEFT JOIN symbols s ON s.id = e.target_id
            WHERE e.source_id IN (SELECT id FROM _cairn_maint_affected)
              AND (e.target_id IS NOT NULL OR (e.target_name IS NOT NULL AND e.target_name != ''))
              AND e.kind IN ({kind_ph})
        """, kind_params)
        total_inserted += cur.rowcount

        for d in range(1, max_depth):
            batch_inserted = 0

            # Case 1: follow resolved edges (target_id IS NOT NULL). The
            # builder's join order (t driven, then edges by source) is pinned
            # with CROSS JOIN: without it SQLite may drive from a full scan of
            # structural edges, which is what made maintenance pathologically
            # slow on call-dense repos (the statement is semantically
            # identical either way -- per-source independence means the join
            # order cannot change the rows produced).
            cur.execute("DROP TABLE IF EXISTS _cairn_maint_c1")
            cur.execute("""
                CREATE TEMP TABLE _cairn_maint_c1 AS
                SELECT source_id, target_id FROM transitive_edges
                WHERE distance = ? AND target_id IS NOT NULL
                  AND source_id IN (SELECT id FROM _cairn_maint_affected)
            """, (d,))
            cur.execute(f"""
                INSERT OR IGNORE INTO transitive_edges (source_id, target_name, target_id, distance)
                SELECT
                    f.source_id,
                    COALESCE(s_target.name, e.target_name) AS target_name,
                    e.target_id,
                    ?
                FROM _cairn_maint_c1 f
                CROSS JOIN edges e ON e.source_id = f.target_id
                LEFT JOIN symbols s_target ON s_target.id = e.target_id
                WHERE e.kind IN ({kind_ph})
            """, (d + 1, *kind_params))
            batch_inserted += cur.rowcount

            # Case 2: unique-name-mediated hop for unresolved rows (target_id
            # IS NULL). Same planner pinning: the frontier (affected sources'
            # unresolved rows at distance d) drives, then the unique-name
            # check and the mid symbol's edges are index probes. Semantically
            # identical to the builder's single statement.
            cur.execute("DROP TABLE IF EXISTS _cairn_maint_c2")
            cur.execute("""
                CREATE TEMP TABLE _cairn_maint_c2 AS
                SELECT DISTINCT source_id, target_name FROM transitive_edges
                WHERE distance = ? AND target_id IS NULL
                  AND source_id IN (SELECT id FROM _cairn_maint_affected)
            """, (d,))
            cur.execute(f"""
                INSERT OR IGNORE INTO transitive_edges (source_id, target_name, target_id, distance)
                SELECT
                    f.source_id,
                    COALESCE(s_target.name, e.target_name) AS target_name,
                    e.target_id,
                    ?
                FROM _cairn_maint_c2 f
                CROSS JOIN symbols s_mid ON s_mid.name = f.target_name
                CROSS JOIN edges e ON e.source_id = s_mid.id
                LEFT JOIN symbols s_target ON s_target.id = e.target_id
                WHERE (SELECT COUNT(*) FROM symbols c WHERE c.name = f.target_name) = 1
                  AND (e.target_id IS NOT NULL OR (e.target_name IS NOT NULL AND e.target_name != ''))
                  AND e.kind IN ({kind_ph})
            """, (d + 1, *kind_params))
            batch_inserted += cur.rowcount

            if batch_inserted == 0:
                break
            total_inserted += batch_inserted
    finally:
        for tmp in ("_cairn_maint_c1", "_cairn_maint_c2", "_cairn_maint_affected"):
            try:
                cur.execute(f"DROP TABLE IF EXISTS {tmp}")
            except sqlite3.Error:
                pass

    conn.commit()
    return total_inserted


def maintain_dataflow_index(conn: sqlite3.Connection, affected_names) -> int:
    """Incrementally refresh dataflow rows for a set of symbol names (PERF-3).

    dataflow is keyed by symbol NAME, and a row's payload is the symbol's
    caller set (within_repo, via impact_analysis) plus its repo's consumers
    (cross_repo). An edit changes a row for name X iff X's caller chain
    changed -- i.e. X was renamed in/out, or some changed edge points at X or
    at one of X's ancestors... viewed from X's side: at X or something X
    reaches. The caller (``incremental._maintain_derived_indexes``) computes
    that name set (changed names + names reachable from changed-edge targets
    within impact_analysis's max_depth); this function then:

    - recomputes+upserts rows for affected names that still have at least one
      public symbol (same :func:`_row_is_public` predicate as the builder, so
      a name that stopped being public loses its row exactly like a full
      rebuild would omit it), and
    - deletes rows for affected names with no remaining public symbol.

    Rows for unaffected names are untouched: their inputs (their callers'
    edges) did not change, so the builder's output for them is unchanged.

    Returns the number of rows written or deleted.
    """
    names = sorted({n for n in affected_names if n})
    if not names:
        return 0
    count = 0
    now = time.time()

    for name in names:
        # Same selection query shape as _public_symbols, scoped to this name.
        # Multiple (name, repo) instances reproduce the builder's one-row-per-
        # instance INSERT OR REPLACE behavior (last write wins); for the
        # common single-repo case the rows are identical regardless of order.
        rows = conn.execute(
            """
            SELECT s.name, s.modifiers, r.name AS repo, r.language
            FROM symbols s
            JOIN files f ON s.file_id = f.id
            JOIN repos r ON f.repo_id = r.id
            WHERE s.kind IN ('function', 'method', 'class', 'interface', 'enum')
              AND s.name = ?
            """,
            (name,),
        ).fetchall()
        repos_done: set[str] = set()
        for row in rows:
            if not _row_is_public(row):
                continue
            repo = row["repo"]
            if repo in repos_done:
                continue  # one row per (name, repo); payload is name-keyed
            repos_done.add(repo)
            within, cross = _compute_dataflow_row(conn, name, repo)
            conn.execute(
                """
                INSERT OR REPLACE INTO dataflow (symbol, repo, within_repo, cross_repo, updated)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, repo, json.dumps(within), json.dumps(cross), now),
            )
            count += 1
        if not repos_done:
            # No remaining public symbol with this name: the row (if any) is
            # stale -- a full rebuild would not have written it.
            cur = conn.execute("DELETE FROM dataflow WHERE symbol = ?", (name,))
            if cur.rowcount:
                count += 1

    conn.commit()
    return count


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
