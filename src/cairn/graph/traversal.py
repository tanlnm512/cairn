"""Graph traversal: symbol lookup and caller/callee/impact queries.

All functions take a sqlite3.Connection (from schema.get_db) and return
sqlite3.Row objects (dict-like).
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional, Tuple


# Edge kinds that represent in-codebase structural relationships. Service/
# topology edge kinds (http_call, service_call) are excluded by default; pass
# ``include_service_edges=True`` to follow them. Both ``"calls"`` (tree-sitter
# parsers) and ``"call"`` (the SCIP importer) are included.
STRUCTURAL_EDGE_KINDS: Tuple[str, ...] = ("calls", "call", "extends", "implements")


def _escape_like(value: str) -> str:
    """Escape LIKE meta-characters so ``value`` matches literally.

    ``\\``, ``%`` and ``_`` are escaped by prefixing a backslash; the
    accompanying LIKE clause must use ``ESCAPE '\\'``. This keeps a symbol
    name like ``foo_bar`` or ``rate_50%`` from matching unintended rows
    (``_`` is a single-char wildcard, ``%`` is a multi-char wildcard).
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def find_definition(conn: sqlite3.Connection, name: str, limit: int = 50) -> List[sqlite3.Row]:
    """Find symbols matching `name`. Exact match on name first, then
    qualified_name, then a prefix/substring match. Ordered by best match.
    """
    cur = conn.cursor()
    # Exact name match.
    exact = cur.execute(
        """SELECT s.*, f.path AS file_path, f.repo_id AS repo
           FROM symbols s JOIN files f ON s.file_id = f.id
           WHERE s.name = ? ORDER BY s.kind LIMIT ?""",
        (name, limit),
    ).fetchall()
    if exact:
        return list(exact)

    # Qualified name exact match (e.g. "ApiFactory.create").
    qual = cur.execute(
        """SELECT s.*, f.path AS file_path, f.repo_id AS repo
           FROM symbols s JOIN files f ON s.file_id = f.id
           WHERE s.qualified_name = ? LIMIT ?""",
        (name, limit),
    ).fetchall()
    if qual:
        return list(qual)

    # Fuzzy: substring on name. The user-supplied name is escaped first so
    # any literal ``%``/``_`` in it match themselves rather than acting as
    # LIKE wildcards (``ESCAPE '\\'`` enables the backslash escape char).
    escaped = _escape_like(name)
    fuzzy = cur.execute(
        """SELECT s.*, f.path AS file_path, f.repo_id AS repo
           FROM symbols s JOIN files f ON s.file_id = f.id
           WHERE s.name LIKE ? ESCAPE '\\' ORDER BY s.kind LIMIT ?""",
        (f"%{escaped}%", limit),
    ).fetchall()
    return list(fuzzy)


def get_callers(
    conn: sqlite3.Connection,
    name: str,
    limit: int = 200,
    fuzzy: bool = False,
    kind: Optional[str] = None,
) -> List[sqlite3.Row]:
    """Return edges whose target is a symbol named `name`.

    Precise mode (default, ``fuzzy=False``): only edges whose ``target_id`` is
    resolved to a symbol named `name` (no false positives from homonymous
    methods). Fuzzy mode (``fuzzy=True``): also matches edges whose
    ``target_name`` equals ``name`` (useful for tracing every call site of a
    common name, e.g. all ``.let`` usages).

    ``kind`` (optional) filters by edge kind; ``None`` returns all kinds. Each
    row reports the caller symbol, its file:line, and repo.
    """
    cur = conn.cursor()
    kind_clause = "AND e.kind = ?" if kind else ""
    kind_params: Tuple[str, ...] = (kind,) if kind else ()
    if fuzzy:
        rows = cur.execute(
            f"""SELECT e.line AS edge_line, e.column AS edge_column, e.kind AS edge_kind,
                      s.name AS caller_name, s.kind AS caller_kind, s.id AS caller_id,
                      f.path AS file_path, f.repo_id AS repo, e.resolution AS resolution
               FROM edges e
               JOIN symbols s ON e.source_id = s.id
               JOIN files f ON s.file_id = f.id
               WHERE (e.target_name = ?
                  OR e.target_id IN (SELECT id FROM symbols WHERE name = ?))
                  {kind_clause}
               LIMIT ?""",
            (name, name, *kind_params, limit),
        ).fetchall()
    else:
        rows = cur.execute(
            f"""SELECT e.line AS edge_line, e.column AS edge_column, e.kind AS edge_kind,
                      s.name AS caller_name, s.kind AS caller_kind, s.id AS caller_id,
                      f.path AS file_path, f.repo_id AS repo, e.resolution AS resolution
               FROM edges e
               JOIN symbols s ON e.source_id = s.id
               JOIN files f ON s.file_id = f.id
               WHERE e.target_id IN (SELECT id FROM symbols WHERE name = ?)
                  {kind_clause}
               LIMIT ?""",
            (name, *kind_params, limit),
        ).fetchall()
    return list(rows)


def get_callees(
    conn: sqlite3.Connection,
    name: str,
    limit: int = 200,
    fuzzy: bool = False,
    kind: Optional[str] = None,
) -> List[sqlite3.Row]:
    """Return edges whose source is a symbol named `name` (what it calls).

    Precise mode (default): only edges with a resolved ``target_id``. Fuzzy
    mode also returns unresolved outgoing calls (useful for exploring a
    function's behavior including stdlib/external calls).

    ``kind`` (optional) filters by edge kind; ``None`` returns all kinds.
    """
    cur = conn.cursor()
    target_clause = "" if fuzzy else "AND e.target_id IS NOT NULL"
    kind_clause = "AND e.kind = ?" if kind else ""
    kind_params: Tuple[str, ...] = (kind,) if kind else ()
    rows = cur.execute(
        f"""SELECT e.line AS edge_line, e.column AS edge_column, e.kind AS edge_kind,
                   COALESCE(t.name, e.target_name) AS callee_name,
                   COALESCE(t.kind, 'unknown') AS callee_kind,
                   e.target_id AS resolved,
                   e.resolution AS resolution,
                   f.path AS file_path, f.repo_id AS repo
            FROM edges e
            JOIN symbols s ON e.source_id = s.id
            JOIN files f ON s.file_id = f.id
            LEFT JOIN symbols t ON e.target_id = t.id
            WHERE s.name = ? {target_clause} {kind_clause}
            LIMIT ?""",
        (name, *kind_params, limit),
    ).fetchall()
    return list(rows)


def impact_analysis(
    conn: sqlite3.Connection,
    name: str,
    max_depth: int = 10,
    fuzzy: bool = False,
    limit: int = 500,
    include_service_edges: bool = False,
    use_index: Optional[bool] = None,
) -> dict:
    """Recursive caller traversal with cycle detection.

    Visits each symbol at most once (keyed by symbol **id**, not name). Precise
    mode (default) only walks resolved edges; ``fuzzy=True`` also follows
    unresolved name-only edges. By default only **structural** edges
    (``calls``, ``extends``, ``implements``) are followed; pass
    ``include_service_edges=True`` to also follow ``http_call``/``service_call``.

    ``limit`` caps total impacted rows; ``truncated`` in the return flags this.

    **Index mode.** When the precomputed ``transitive_edges`` closure can serve
    the query -- precise, structural-only, ``max_depth <= 3``, the name has
    exact-name symbol matches, the closure is materialised, and no seed reaches
    another seed (cycle gate) -- the answer comes from
    :func:`dataflow.impact_from_closure` in one indexed statement instead of a
    per-visited-symbol DFS. Index mode returns shortest-path depths,
    (depth, symbol, file)-ordered rows, empty ``cycles``, and may be a superset
    of DFS coverage (unique-name hops; no per-node 200-caller cap) -- see that
    function's docstring. ``use_index=False`` forces the classic DFS (used by
    the golden parity tests); ``use_index=True`` genuinely forces the index
    when technically servable -- including past the cycle gate, accepting
    ``cycles=[]`` -- and silently takes the DFS path otherwise (fuzzy/service/
    deep queries can never be served from the closure).

    Returns {impacted: [...], cycles: [...], total: int, truncated: bool}.
    Each impacted entry: {symbol, file, repo, depth}.
    """
    if fuzzy or use_index is not False:
        from .dataflow import (
            CLOSURE_MAX_DEPTH,
            closure_available,
            closure_has_seed_cycle,
            impact_from_closure,
        )

        # DFS records callers at depth 0..max_depth, i.e. ancestors up to
        # closure distance max_depth+1 -- the eligibility bound is one below
        # the materialised depth (see dataflow.CLOSURE_MAX_DEPTH).
        if (
            not fuzzy
            and not include_service_edges
            and max_depth + 1 <= CLOSURE_MAX_DEPTH
        ):
            seeds = find_definition(conn, name, limit=limit)
            # find_definition falls back to qualified-name/substring matches;
            # get_callers-based DFS only ever matches exact names, so require
            # an exact-name symbol before serving from the closure.
            exact = conn.execute(
                "SELECT id FROM symbols WHERE name = ? LIMIT 1", (name,)
            ).fetchone()
            if exact is not None and closure_available(conn):
                seed_ids = [s["id"] for s in seeds]
                # use_index=True genuinely forces: the cycle gate keeps auto
                # mode on the DFS path (cycle reporting), but a forced query
                # accepts cycles=[] (documented) -- the escape hatch for
                # benchmarks and debugging.
                if use_index is True or not closure_has_seed_cycle(conn, seed_ids):
                    return impact_from_closure(conn, seed_ids, max_depth, limit)

    allowed = None if include_service_edges else STRUCTURAL_EDGE_KINDS
    visited: set[str] = set()   # globally visited symbol ids — prevents re-traversal
    on_path: set[str] = set()   # current DFS path symbol ids — cycle detection
    results = []
    cycles_seen: set[str] = set()
    cycles = []
    truncated = False

    def traverse(sym_id: str, sym_name: str, depth: int):
        nonlocal truncated
        if truncated:
            return
        if depth > max_depth:
            return
        if sym_id in on_path:
            # Genuine back-edge: a caller that's already on our DFS path.
            if sym_id not in cycles_seen:
                cycles_seen.add(sym_id)
                cycles.append({"symbol": sym_name, "depth": depth})
            return
        if sym_id in visited:
            return  # already fully explored via another path
        visited.add(sym_id)
        on_path.add(sym_id)
        callers = get_callers(conn, sym_name, fuzzy=fuzzy)
        for c in callers:
            # Filter to structural kinds unless the caller opted in to service
            # edges.
            if allowed is not None and c["edge_kind"] not in allowed:
                continue
            if len(results) >= limit:
                truncated = True
                break
            results.append(
                {
                    "symbol": c["caller_name"],
                    "file": c["file_path"],
                    "repo": c["repo"],
                    "depth": depth,
                }
            )
            traverse(c["caller_id"], c["caller_name"], depth + 1)
        on_path.discard(sym_id)

    # Seed: the entry name may resolve to several symbols. Mark every matching
    # id as visited/on-path so the traversal does not re-enter the seed.
    for seed in find_definition(conn, name, limit=limit):
        visited.add(seed["id"])
        on_path.add(seed["id"])
    for c in get_callers(conn, name, fuzzy=fuzzy):
        if allowed is not None and c["edge_kind"] not in allowed:
            continue
        if len(results) >= limit:
            truncated = True
            break
        results.append(
            {
                "symbol": c["caller_name"],
                "file": c["file_path"],
                "repo": c["repo"],
                "depth": 0,
            }
        )
        traverse(c["caller_id"], c["caller_name"], 1)

    return {
        "impacted": results,
        "cycles": cycles,
        "total": len(results),
        "truncated": truncated,
    }


def find_definition_by_id(conn: sqlite3.Connection, sym_id: str) -> List[sqlite3.Row]:
    """Find a symbol by its database ID. Returns at most one row."""
    cur = conn.cursor()
    return list(cur.execute(
        """SELECT s.*, f.path AS file_path, f.repo_id AS repo
           FROM symbols s JOIN files f ON s.file_id = f.id
           WHERE s.id = ? LIMIT 1""",
        (sym_id,),
    ).fetchall())


def trace_flow(
    conn: sqlite3.Connection,
    entry: str,
    max_depth: int = 8,
    limit: int = 500,
    fuzzy: bool = False,
    entry_id: Optional[str] = None,
    include_service_edges: bool = False,
) -> dict:
    """Downward callee traversal from an entry symbol — the flow it executes.

    The inverse of :func:`impact_analysis` (callers upward, flat set): this
    walks callees downward and records the ordered call chain
    (``entry -> A -> B -> C``) across files and modules. BFS by symbol **id**
    (each id visited once) with the same cycle detection and ``limit`` cap as
    ``impact_analysis``. Branch points (a symbol with >1 distinct callee) and
    leaves (terminal callees) are surfaced separately. By default only
    **structural** edges are followed; pass ``include_service_edges=True`` to
    follow ``http_call``/``service_call`` too.

    Args:
        entry: the entry-point symbol name. Resolved via :func:`get_callees`.
        max_depth: deepest call hop to follow (default 8).
        limit: total nodes cap (default 500).
        fuzzy: when True, also follow unresolved name-only outgoing calls.
        entry_id: optional symbol DB ID. When set, the seed symbol is resolved
            by ID (via :func:`find_definition_by_id`) instead of by name.
        include_service_edges: when True, also follow ``http_call``/
            ``service_call`` edges (default False).

    Returns a dict with keys: entry, chain (ordered depth-tagged nodes),
    branches, leaves, modules, cycles, total, truncated.
    """
    allowed = None if include_service_edges else STRUCTURAL_EDGE_KINDS
    visited: dict[str, dict] = {}   # id -> chain entry (first-seen wins)
    on_path: set[str] = set()       # current DFS path symbol ids — cycle detection
    branches: list[dict] = []
    leaves: list[str] = []
    cycles: list[dict] = []
    cycles_seen: set[str] = set()
    truncated = False

    # Seed the chain with the entry symbol itself.
    if entry_id:
        entry_row = find_definition_by_id(conn, entry_id)
    else:
        entry_row = find_definition(conn, entry, limit=1)
    entry_file = entry_row[0]["file_path"] if entry_row else ""
    entry_repo = entry_row[0]["repo"] if entry_row else ""
    entry_kind = entry_row[0]["kind"] if entry_row else "function"
    entry_row_id = entry_row[0]["id"] if entry_row else entry
    visited[entry_row_id] = {
        "symbol": entry, "kind": entry_kind, "file": entry_file,
        "repo": entry_repo, "depth": 0, "parent": None,
    }

    def walk(sym_id: str, sym_name: str, depth: int):
        nonlocal truncated
        if truncated or depth >= max_depth:
            return
        if sym_id in on_path:
            if sym_id not in cycles_seen:
                cycles_seen.add(sym_id)
                cycles.append({"symbol": sym_name, "depth": depth})
            return

        on_path.add(sym_id)
        callees = get_callees(conn, sym_name, limit=50, fuzzy=fuzzy)

        if not callees:
            if sym_id != entry_row_id:
                leaves.append(sym_name)
            on_path.discard(sym_id)
            return

        # Dedup callees by identity (id when resolved, else name) within this
        # hop so one node is walked once even if reached via multiple edges.
        seen_callees: set[str] = set()
        outgoing: list[str] = []
        for c in callees:
            if allowed is not None and c["edge_kind"] not in allowed:
                continue
            cname = c["callee_name"]
            if not cname:
                continue
            # Prefer the resolved target id as the node identity; fall back to
            # a name-scoped key for unresolved (name-only) callees.
            cid = c["resolved"] or f"name:{cname}"
            if cid in seen_callees:
                continue
            seen_callees.add(cid)
            outgoing.append(cname)
            if len(visited) >= limit:
                truncated = True
                break
            if cid not in visited:
                # Resolve the callee's DEFINITION location (not the call site):
                # where the symbol is actually declared.
                defn = find_definition(conn, cname, limit=1)
                if defn:
                    d = defn[0]
                    cfile = d["file_path"]
                    crepo = d["repo"]
                    ckind = d["kind"]
                else:
                    cfile = c["file_path"]
                    crepo = c["repo"]
                    ckind = c["callee_kind"]
                visited[cid] = {
                    "symbol": cname,
                    "kind": ckind,
                    "file": cfile,
                    "repo": crepo,
                    "depth": depth + 1,
                    "parent": sym_name,
                }
            walk(cid, cname, depth + 1)

        if len(outgoing) > 1:
            branches.append({"symbol": sym_name, "callees": outgoing})

        on_path.discard(sym_id)

    walk(entry_row_id, entry, 0)

    chain = sorted(visited.values(), key=lambda x: (x["depth"], x["symbol"]))

    # Derive distinct modules (file dir, repo-relative-ish) from the chain.
    dirs: set[str] = set()
    for node in chain:
        f = node["file"] or ""
        if f:
            parts = f.split("/")
            # Take last 2 meaningful segments as the module hint.
            mod = "/".join(parts[-3:-1]) if len(parts) >= 3 else parts[-2] if len(parts) >= 2 else f
            dirs.add(f"{node['repo']}/{mod}" if node["repo"] else mod)

    return {
        "entry": entry,
        "chain": chain,
        "branches": branches,
        "leaves": sorted(set(leaves)),
        "modules": sorted(dirs),
        "cycles": cycles,
        "total": len(chain),
        "truncated": truncated,
    }
