"""Precomputed dataflow index for public/exported symbols."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from typing import Dict, List, Optional, Sequence

from .traversal import STRUCTURAL_EDGE_KINDS

# The closure is materialised to this *closure distance*. impact_analysis()
# index mode is eligible for DFS ``max_depth`` values one below it: DFS
# records a direct caller at depth 0 (= closure distance 1), so a query at
# ``max_depth=D`` needs ancestors up to closure distance D+1.
CLOSURE_MAX_DEPTH = 4

# Default cap on public symbols processed per build_dataflow_index() call;
# overridable per call, via the CAIRN_DATAFLOW_MAX_SYMBOLS env var, or via
# `cairn dataflow build --max-symbols`.
DEFAULT_MAX_SYMBOLS = 2000
MAX_SYMBOLS_ENV = "CAIRN_DATAFLOW_MAX_SYMBOLS"


def resolve_max_symbols(explicit: Optional[int] = None) -> int:
    """Resolve the dataflow symbol cap: explicit arg > env var > default.

    An invalid env value (non-integer or non-positive) warns on stderr and
    falls back to DEFAULT_MAX_SYMBOLS.
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get(MAX_SYMBOLS_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_SYMBOLS
    try:
        value = int(raw)
    except ValueError:
        print(
            f"warning: {MAX_SYMBOLS_ENV}={raw!r} is not an integer; "
            f"using {DEFAULT_MAX_SYMBOLS}",
            file=sys.stderr,
        )
        return DEFAULT_MAX_SYMBOLS
    if value <= 0:
        print(
            f"warning: {MAX_SYMBOLS_ENV}={raw!r} must be positive; "
            f"using {DEFAULT_MAX_SYMBOLS}",
            file=sys.stderr,
        )
        return DEFAULT_MAX_SYMBOLS
    return value


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


def _has_edge_target(target_id, target_name) -> bool:
    """True when a closure row can carry this edge (an id or a non-empty name)."""
    return target_id is not None or (target_name is not None and target_name != "")


def _compute_dataflow_row(
    conn: sqlite3.Connection,
    name: str,
    repo: str,
    cross_cache: Optional[Dict[str, list]] = None,
) -> tuple[list[str], list[str]]:
    """Compute one symbol's dataflow payload: (within_repo, cross_repo).

    Shared by the full builder (:func:`build_dataflow_index`) and the
    incremental maintainer (:func:`maintain_dataflow_index`) so the two paths
    can never drift on semantics -- the property-parity tests diff a maintained
    table against a fresh full build row-for-row, which only holds if both
    call this one function.

    ``cross_repo_deps`` depends only on ``repo``, so callers looping over many
    symbols pass a ``cross_cache`` dict to memoize it per repo for the run;
    the cached value is identical to a fresh computation.
    """
    from .queries import impact_analysis, cross_repo_deps  # avoid circular import

    try:
        impact = impact_analysis(conn, name, max_depth=5)
        within = list({item["symbol"] for item in impact.get("impacted", []) if item.get("symbol") != name})
    except Exception:
        within = []

    if cross_cache is not None and repo in cross_cache:
        cross = cross_cache[repo]
    else:
        try:
            xref = cross_repo_deps(conn, repo)
            cross = [d["repo"] for d in xref.get("dependents", [])]
        except Exception:
            cross = []
        if cross_cache is not None:
            cross_cache[repo] = cross
    return within, cross


def build_dataflow_index(
    conn: sqlite3.Connection, progress=None, max_symbols: Optional[int] = None
) -> int:
    """Build the dataflow table from scratch for all public symbols.

    Iterates public symbols, computes within-repo impact (impact_analysis) and
    cross-repo consumers (cross_repo_deps), and upserts into the dataflow table.

    ``progress`` is an optional callable(n_done) for CLI progress reporting.

    ``max_symbols`` caps the number of public symbols processed per call. Each
    symbol triggers a per-symbol BFS (impact_analysis), so an unbounded loop
    never completes for large repos; this converts a hang into bounded work.
    None (the default) resolves the cap from the CAIRN_DATAFLOW_MAX_SYMBOLS
    env var, falling back to DEFAULT_MAX_SYMBOLS (see resolve_max_symbols).
    If truncated, a warning is emitted and the returned count reflects only the
    symbols actually indexed (the dataflow table is partial but still usable).

    Rows are upserted in batches (executemany); all rows commit once at the end.

    Returns the number of symbols indexed.
    """
    max_symbols = resolve_max_symbols(max_symbols)
    symbols = _public_symbols(conn)
    truncated = len(symbols) > max_symbols
    if truncated:
        print(
            f"warning: dataflow index is partial -- "
            f"{len(symbols)} public symbols found, capping at {max_symbols}. "
            f"Set {MAX_SYMBOLS_ENV} (or --max-symbols on `cairn dataflow build`) "
            f"above {len(symbols)} for full coverage.",
            file=sys.stderr,
        )
        symbols = symbols[:max_symbols]
    count = 0
    now = time.time()

    cross_cache: Dict[str, list] = {}
    batch: list[tuple] = []
    for sym in symbols:
        within, cross = _compute_dataflow_row(conn, sym["name"], sym["repo"], cross_cache)

        batch.append((sym["name"], sym["repo"], json.dumps(within), json.dumps(cross), now))
        count += 1
        if len(batch) >= _SQLITE_IN_CHUNK:
            conn.executemany("""
                INSERT OR REPLACE INTO dataflow (symbol, repo, within_repo, cross_repo, updated)
                VALUES (?, ?, ?, ?, ?)
            """, batch)
            batch.clear()
        if progress:
            progress(count)

    if batch:
        conn.executemany("""
            INSERT OR REPLACE INTO dataflow (symbol, repo, within_repo, cross_repo, updated)
            VALUES (?, ?, ?, ?, ?)
        """, batch)

    conn.commit()
    return count


def _run_levels(
    eligible,
    adjacency_for,
    case2_for,
    max_depth: int,
    before_level=None,
) -> List[tuple[str, str, Optional[str], int]]:
    """Seed + per-depth level loop shared by the full and scoped closure paths.

    ``(source_id, target_id, name)`` in global (kind, rowid) order;
    ``adjacency_for(target_id)`` yields that target's ``(target_id, name)``
    edges in rowid order; ``case2_for(unresolved_names)`` yields
    ``(mid_name, target_id, name)`` hops for those names in the full path's
    global edge-scan (rowid) order; ``before_level(target_ids)`` runs before
    each level's extension (the scoped path's adjacency prefetch).
    """
    rows: List[tuple[str, str, Optional[str], int]] = []
    level: Dict[tuple[str, str], Optional[str]] = {}
    for source_id, target_id, name in eligible:
        key = (source_id, name)
        if key not in level:
            level[key] = target_id
    rows.extend(
        (source_id, name, target_id, 1)
        for (source_id, name), target_id in level.items()
    )

    for distance in range(1, max_depth):
        if not level:
            break
        if before_level is not None:
            before_level({t for t in level.values() if t is not None})
        nxt: Dict[tuple[str, str], Optional[str]] = {}

        # Case 1: resolved rows extend through the adjacency of their target.
        for (source_id, name), target_id in level.items():
            if target_id is None:
                continue
            for e_target_id, e_name in adjacency_for(target_id):
                if e_name is None:  # NULL target_name is unstorable
                    continue
                key = (source_id, e_name)
                if key not in nxt:
                    nxt[key] = e_target_id

        # Case 2: unresolved rows hop through the unique symbol named
        # target_name; ambiguous names stay unextended rather than guessed.
        unresolved: Dict[str, list] = {}
        for (source_id, name), target_id in level.items():
            if target_id is None:
                unresolved.setdefault(name, []).append(source_id)
        for mid_name, target_id, name in case2_for(set(unresolved)):
            sources = unresolved.get(mid_name)
            if not sources:
                continue
            for source_id in sources:
                key = (source_id, name)
                if key not in nxt:
                    nxt[key] = target_id

        if not nxt:
            break
        rows.extend(
            (source_id, name, target_id, distance + 1)
            for (source_id, name), target_id in nxt.items()
        )
        level = nxt
    return rows


class _ScopedClosureGraph:
    """Edge/name reads scoped to the restrict set and the frontier it reaches."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        sources: set,
        structural_kinds: set,
    ):
        self._conn = conn
        self._kinds = tuple(sorted(structural_kinds))
        self._sources = set(sources)
        self._names: Dict[str, str] = {}
        self._adjacency: Dict[str, list] = {}
        self._fetched: set = set()

    def _kind_placeholders(self) -> str:
        return ",".join("?" for _ in self._kinds)

    def _names_for(self, ids) -> None:
        missing = [i for i in ids if i and i not in self._names]
        for chunk in _chunked(missing):
            ph = ",".join("?" for _ in chunk)
            for row in self._conn.execute(
                f"SELECT id, name FROM symbols WHERE id IN ({ph})", chunk
            ):
                self._names[row[0]] = row[1]

    def _scoped_edges(self, source_ids) -> list:
        """Structural edges sourced by source_ids as
        (source_id, target_id, target_name, kind, rowid), global (kind, rowid)
        order -- a subset of the full path's table scan, sorted by the same key."""
        rows: list = []
        for chunk in _chunked(source_ids):
            ph = ",".join("?" for _ in chunk)
            rows.extend(
                self._conn.execute(
                    f"SELECT source_id, target_id, target_name, kind, rowid FROM edges "
                    f"WHERE source_id IN ({ph}) AND kind IN ({self._kind_placeholders()})",
                    (*chunk, *self._kinds),
                ).fetchall()
            )
        rows.sort(key=lambda r: (r[3], r[4]))
        return rows

    def seed_edges(self):
        """Level-1 (source_id, target_id, name) entries for the restrict set."""
        raw = self._scoped_edges(self._sources)
        self._names_for({r[1] for r in raw if r[1]})
        return [
            (source_id, target_id, self._names.get(target_id, target_name))
            for source_id, target_id, target_name, _kind, _rowid in raw
            if _has_edge_target(target_id, target_name)
        ]

    def prefetch_adjacency(self, target_ids) -> None:
        """Load the outgoing edges of target_ids (once per id), per-source
        rowid order -- the order the full path's table scan produces."""
        missing = [i for i in target_ids if i not in self._fetched]
        if not missing:
            return
        raw = self._scoped_edges(missing)
        self._names_for({r[0] for r in raw} | {r[1] for r in raw if r[1]})
        pending: Dict[str, list] = {}
        for source_id, target_id, target_name, _kind, rowid in raw:
            name = self._names.get(target_id, target_name)
            pending.setdefault(source_id, []).append((rowid, target_id, name))
        for source_id, entries in pending.items():
            entries.sort(key=lambda e: e[0])
            self._adjacency[source_id] = [(tid, name) for _rowid, tid, name in entries]
        self._fetched.update(missing)

    def adjacency_for(self, target_id):
        return self._adjacency.get(target_id, ())

    def case2_for(self, unresolved_names):
        """(mid_name, target_id, name) hops for globally-unique unresolved
        names, merged in the full path's global edge-scan (rowid) order."""
        streams: list = []
        for mid_name in unresolved_names:
            sym_rows = self._conn.execute(
                "SELECT id FROM symbols WHERE name = ?", (mid_name,)
            ).fetchall()
            if len(sym_rows) != 1:
                continue
            raw = self._scoped_edges([sym_rows[0][0]])
            self._names_for({r[1] for r in raw if r[1]})
            for source_id, target_id, target_name, _kind, rowid in raw:
                if not _has_edge_target(target_id, target_name):
                    continue
                name = self._names.get(target_id, target_name)
                streams.append((rowid, (mid_name, target_id, name)))
        streams.sort(key=lambda s: s[0])
        return [payload for _rowid, payload in streams]


def _closure_rows(
    conn: sqlite3.Connection,
    max_depth: int,
    restrict_sources=None,
) -> List[tuple[str, str, Optional[str], int]]:
    """Compute transitive-closure rows in memory and return them as
    ``(source_id, target_name, target_id, distance)`` tuples.

    With ``restrict_sources=None`` the graph is read in full, then the seed +
    per-depth level loop runs over dict adjacency. First-wins per PK
    ``(source_id, target_name, distance)``, including tie-breaks: the seed
    walks edges kind-major then rowid order, the unique-name hop (Case 2)
    walks the global edge-scan (rowid) order, and the resolved hop (Case 1)
    walks the level frontier in creation order; Case 1 always precedes
    Case 2 at a level. A subset of an ordered sequence, sorted by the same
    key, is the global sequence restricted to that subset, so the
    ``restrict_sources`` branch reproduces the full result restricted to
    those sources row-for-row while reading only frontier-scoped edges.
    """
    structural_kinds = set(STRUCTURAL_EDGE_KINDS)
    restrict = None if restrict_sources is None else {s for s in restrict_sources if s}

    if restrict is not None:
        g = _ScopedClosureGraph(conn, restrict, structural_kinds)
        return _run_levels(
            g.seed_edges(),
            g.adjacency_for,
            g.case2_for,
            max_depth,
            before_level=g.prefetch_adjacency,
        )

    # One read pass: symbol names, then every edge in table (rowid) order.
    name_by_id: Dict[str, str] = {
        row[0]: row[1] for row in conn.execute("SELECT id, name FROM symbols")
    }
    name_counts: Dict[str, int] = {}
    for name in name_by_id.values():
        name_counts[name] = name_counts.get(name, 0) + 1
    unique_names = {n for n, c in name_counts.items() if c == 1}

    # adjacency preserves rowid order per source (Case 1 probes it that way);
    # eligible/case2 walk edges kind-major then rowid order. The read has
    # no WHERE so it is a table scan -- rowid order is guaranteed.
    adjacency: Dict[str, list] = {}
    per_kind: Dict[str, list] = {kind: [] for kind in structural_kinds}
    case2: list = []
    for source_id, target_id, target_name, kind in conn.execute(
        "SELECT source_id, target_id, target_name, kind FROM edges"
    ):
        if kind not in structural_kinds:
            continue
        # COALESCE(symbol.name, edges.target_name); never None for a live id.
        name = name_by_id.get(target_id, target_name)
        adjacency.setdefault(source_id, []).append((target_id, name))
        if not _has_edge_target(target_id, target_name):
            continue
        per_kind[kind].append((source_id, target_id, name))
        mid_name = name_by_id.get(source_id)
        if mid_name is not None and mid_name in unique_names:
            case2.append((mid_name, target_id, name))
    eligible = [e for kind in sorted(per_kind) for e in per_kind[kind]]

    return _run_levels(
        eligible,
        lambda tid: adjacency.get(tid, ()),
        lambda names: [c for c in case2 if c[0] in names],
        max_depth,
    )


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

    The row set comes from :func:`_closure_rows` and is written in one pass:
    a single DELETE, then one sorted executemany.
    """
    cur = conn.cursor()
    # Reader-side ancestor queries filter on distance; keep the index that
    # serves them materialized idempotently.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_transitive_distance ON transitive_edges(distance)"
    )
    rows = _closure_rows(conn, max_depth)
    # PK order; target_id may be NULL, so it must not take part in the sort.
    rows.sort(key=lambda r: (r[0], r[1], r[3]))
    cur.execute("DELETE FROM transitive_edges")
    cur.executemany(
        """
        INSERT OR IGNORE INTO transitive_edges (source_id, target_name, target_id, distance)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def maintain_transitive_closure(
    conn: sqlite3.Connection,
    affected_source_ids,
    max_depth: int = CLOSURE_MAX_DEPTH,
) -> int:
    """Incrementally recompute closure rows for a bounded set of sources.

    Deletes every row whose ``source_id`` is in ``affected_source_ids``, then
    re-derives exactly those sources through the same :func:`_closure_rows`
    core the full builder uses, making the result identical to a full
    :func:`build_transitive_closure` restricted to those ids. The caller owns
    the affected-set capture; when the closure table is empty/never built,
    callers must fall back to the full build. Returns the number of rows
    inserted.
    """
    affected = sorted({i for i in affected_source_ids if i})
    if not affected:
        return 0
    cur = conn.cursor()
    # Same idempotent index the full builder creates; reader queries filter on
    # transitive_edges.distance.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_transitive_distance ON transitive_edges(distance)"
    )
    # Drop every stale row for these sources first -- including rows of
    # sources that no longer exist (deleted symbols).
    for chunk in _chunked(affected):
        ph = ",".join("?" for _ in chunk)
        cur.execute(f"DELETE FROM transitive_edges WHERE source_id IN ({ph})", chunk)

    rows = _closure_rows(conn, max_depth, restrict_sources=affected)
    # PK order; target_id may be NULL, so it must not take part in the sort.
    rows.sort(key=lambda r: (r[0], r[1], r[3]))
    cur.executemany(
        """
        INSERT OR IGNORE INTO transitive_edges (source_id, target_name, target_id, distance)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def maintain_dataflow_index(conn: sqlite3.Connection, affected_names) -> int:
    """Incrementally refresh dataflow rows for a set of symbol names.

    dataflow is keyed by symbol NAME, so an edit changes a row for X exactly
    when X's caller chain changed; the caller (``incremental.
    _maintain_derived_indexes``) computes that name set. Affected names with a
    remaining public symbol are recomputed+upserted (same
    :func:`_row_is_public` predicate as the builder); names with none lose
    their row. Returns the number of rows written or deleted.
    """
    names = sorted({n for n in affected_names if n})
    if not names:
        return 0
    count = 0
    now = time.time()
    cross_cache: Dict[str, list] = {}

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
            within, cross = _compute_dataflow_row(conn, name, repo, cross_cache)
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
