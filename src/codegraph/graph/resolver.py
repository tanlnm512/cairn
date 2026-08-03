"""Import-aware edge resolver.

The key insight: the data needed to resolve edges precisely is *already in
the graph* -- import paths (full dotted, e.g.
``xyz.be.customer.networking.ApiFactory``) and symbol qualified_names. We just
have to connect them.

Resolution strategy (in priority order). An edge is resolved only when there is
exactly one plausible candidate; otherwise it is left unresolved on purpose
(``resolution='ambiguous'``). This trades raw resolution count for trust --
a precise-by-default query model.

  1. SAME-FILE   — exactly one symbol with ``name`` in the source file.
  2. IMPORT-AWARE — the source file imports a path whose tail matches ``name``;
                   resolve to the symbol whose qualified_name tail matches that
                   import path. Disambiguates symbol-imported vs local.
  3. SAME-REPO   — exactly one symbol with ``name`` anywhere else in the repo.
  4. GLOBAL      — exactly one symbol with ``name`` in the whole workspace.
  5. AMBIGUOUS   — more than one candidate survived a tier -> unresolved.

Resolution is decided per tier: if a tier yields exactly one candidate, that's
the answer. If it yields many, we mark ``ambiguous`` and stop (we do NOT fall
through to a broader tier -- that would only add more noise). If a tier yields
zero candidates, we try the next broader tier.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Tuple


def build_symbol_index(conn: sqlite3.Connection) -> Dict[str, List[Tuple[str, str, str, str]]]:
    """Build a global bare-name -> symbol index.

    Returns ``{name: [(symbol_id, repo, file_id, qualified_name), ...]}``.
    This is the resolution substrate; both tiers (same-repo / global) filter it.
    The qualified_name (present for 100% of symbols) powers import-aware matches.
    """
    index: Dict[str, List[Tuple[str, str, str, str]]] = {}
    rows = conn.execute(
        """SELECT s.id AS sid, s.name AS name, s.qualified_name AS qname,
                  f.repo_id AS repo, f.id AS file_id
           FROM symbols s JOIN files f ON s.file_id = f.id"""
    ).fetchall()
    for r in rows:
        index.setdefault(r["name"], []).append(
            (r["sid"], r["repo"], r["file_id"], r["qname"])
        )
    return index


def build_import_index(
    conn: sqlite3.Connection, repo_id: Optional[str] = None
) -> Dict[str, List[str]]:
    """Build a per-file import-path index.

    Returns ``{file_id: [imported_path, ...]}``. ``imported_path`` is the full
    dotted form captured by the parser (e.g. ``retrofit2.Retrofit`` or
    ``xyz.be.customer.networking.ApiFactory``).

    When ``repo_id`` is given, only that repo's imports are loaded (joined via
    ``files``). The resolver only ever looks up a source file's OWN imports
    (``imports_by_file.get(source_file_id)``), and source files belong to the
    repo being resolved, so scoping is exact -- no cross-repo import is ever
    consulted. This keeps incremental re-runs from scanning the whole corpus.
    """
    imports: Dict[str, List[str]] = {}
    if repo_id is not None:
        rows = conn.execute(
            """SELECT i.file_id AS file_id, i.imported_path AS imported_path
               FROM imports i JOIN files f ON i.file_id = f.id
               WHERE f.repo_id = ?""",
            (repo_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT file_id, imported_path FROM imports"
        ).fetchall()
    for r in rows:
        imports.setdefault(r["file_id"], []).append(r["imported_path"])
    return imports


# Symbol kinds that are members of a type (never types themselves). Anything
# NOT in this set is treated as a type/namespace for enclosing-scope purposes,
# so the members index auto-adapts to whatever type kind a parser emits
# (class/interface/enum/protocol/struct/object/...).
_MEMBER_KINDS = ("method", "function", "property", "variable", "field", "constant")


def build_members_index(
    conn: sqlite3.Connection,
    repo_id: Optional[str] = None,
) -> Dict[Tuple[str, str], List[str]]:
    """Build a {(enclosing_type_simple_name, member_name): [symbol_id, ...]} index.

    A member's qualified_name looks like ``pkg.Outer.Inner.member``; the
    enclosing type is the second-to-last segment. This is the substrate for the
    type-aware resolution tier: given a known receiver type and a member name,
    look up which symbol(s) define it.

    Note ``function`` is kept as a member kind on purpose -- some parsers
    (e.g. TypeScript) classify methods as ``function``. To avoid indexing a
    top-level function under its *package* (``pkg.foo`` -> spurious key
    ``('pkg', 'foo')``), an entry is only added when the enclosing segment is an
    actual **type** symbol in the graph -- i.e. a symbol whose kind is not a
    member kind. This keys off real type names rather than a hardcoded type-kind
    allowlist, so it works across languages.

    When ``repo_id`` is given, both the type-name set and the member rows are
    scoped to that repo via ``files``. Scoping is safe here: a receiver type
    resolved during repo X's pass whose members live in another repo is a rare
    cross-repo-inheritance case, and a miss degrades gracefully (no typed match
    -> fall through to the name-based tiers) rather than corrupting a result.
    """
    repo_join = ""
    repo_where = ""
    params: tuple = ()
    if repo_id is not None:
        repo_join = " JOIN files f ON s.file_id = f.id"
        repo_where = " AND f.repo_id = ?"
        params = (repo_id,)

    # Names of symbols that are types (anything not a member kind). Packages are
    # not symbols, so a package segment never lands in this set.
    placeholders = ",".join("?" * len(_MEMBER_KINDS))
    type_names = {
        r["name"]
        for r in conn.execute(
            f"SELECT DISTINCT s.name AS name FROM symbols s{repo_join} "
            f"WHERE s.kind NOT IN ({placeholders}){repo_where}",
            (*_MEMBER_KINDS, *params),
        ).fetchall()
    }

    idx: Dict[Tuple[str, str], List[str]] = {}
    member_ph = ",".join("?" * len(_MEMBER_KINDS))
    rows = conn.execute(
        f"""SELECT s.id AS sid, s.name AS member, s.qualified_name AS qname
            FROM symbols s{repo_join}
            WHERE s.kind IN ({member_ph}){repo_where}""",
        (*_MEMBER_KINDS, *params),
    ).fetchall()
    for r in rows:
        q = (r["qname"] or "").replace("/", ".")
        segs = q.split(".") if q else []
        if len(segs) >= 2 and segs[-2] in type_names:
            idx.setdefault((segs[-2], r["member"]), []).append(r["sid"])
    return idx


def build_ancestor_index(
    conn: sqlite3.Connection, repo_id: Optional[str] = None
) -> Dict[str, List[str]]:
    """Build a {child_type_name: [parent_type_name, ...]} index from inheritance edges.

    Reads ``target_name`` directly (the bare parent name), so it does NOT
    depend on the inheritance edges being resolved first -- safe to call at
    the top of ``resolve_repo_edges`` before any UPDATE is flushed.

    When ``repo_id`` is given, the index is scoped to that repo's inheritance
    edges (the child symbol's repo). Scoping is exact for resolving repo X's
    calls: an inheritance edge lives with its child symbol, so every
    ``extends``/``implements`` edge relevant to repo X is captured.
    """
    anc: Dict[str, List[str]] = {}
    if repo_id is not None:
        rows = conn.execute(
            """SELECT src.name AS child, e.target_name AS parent
               FROM edges e JOIN symbols src ON e.source_id = src.id
               JOIN files f ON src.file_id = f.id
               WHERE f.repo_id = ?
                 AND e.kind IN ('extends', 'implements')
                 AND e.target_name IS NOT NULL""",
            (repo_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT src.name AS child, e.target_name AS parent
               FROM edges e JOIN symbols src ON e.source_id = src.id
               WHERE e.kind IN ('extends', 'implements')
                 AND e.target_name IS NOT NULL"""
        ).fetchall()
    for r in rows:
        if r["child"] and r["parent"]:
            anc.setdefault(r["child"], []).append(r["parent"])
    return anc


def _members_of(
    recv_type: str,
    member: str,
    members_by_type: Dict[Tuple[str, str], List[str]],
    ancestors: Dict[str, List[str]],
    _seen: Optional[set] = None,
) -> List[str]:
    """Symbol ids for ``member`` on ``recv_type``, walking ancestors breadth-first.

    Cycle-safe via ``_seen``. Returns the first non-empty hit found on
    ``recv_type`` itself, else recurses into its declared parents.
    """
    _seen = _seen if _seen is not None else set()
    if recv_type in _seen:
        return []
    _seen.add(recv_type)
    hits = list(members_by_type.get((recv_type, member), []))
    if hits:
        return hits
    for parent in ancestors.get(recv_type, []):
        hits = _members_of(parent, member, members_by_type, ancestors, _seen)
        if hits:
            return hits
    return []


def resolve_edge(
    target_name: str,
    source_file_id: str,
    source_repo: str,
    symbols_by_name: Dict[str, List[Tuple[str, str, str, str]]],
    imports_by_file: Dict[str, List[str]],
    receiver_type: Optional[str] = None,
    members_by_type: Optional[Dict[Tuple[str, str], List[str]]] = None,
    ancestors: Optional[Dict[str, List[str]]] = None,
) -> Tuple[Optional[str], str]:
    """Resolve one edge's target.

    Returns ``(target_id, resolution_label)`` where ``resolution_label`` is one
    of ``'exact'``, ``'ambiguous'``, ``'unresolved'``. When ``ambiguous`` or
    ``unresolved``, ``target_id`` is ``None``.

    ``receiver_type``/``members_by_type``/``ancestors`` are all optional and
    defaulted so existing callers (e.g. ``incremental.py``) keep compiling
    unchanged. When ``receiver_type`` is None the resolution path is
    byte-for-byte identical to the type-aware-tier-disabled behavior.
    """
    members_by_type = members_by_type or {}
    ancestors = ancestors or {}

    cands = symbols_by_name.get(target_name)
    if not cands:
        return None, "unresolved"  # external/stdlib call (e.g. listOf, fillMaxWidth)

    # --- Tier 0: type-aware (receiver dispatch) ---------------------------
    # A known receiver type is the STRONGEST resolution signal, so it runs
    # first -- ahead of same-file, whose name-collision ambiguity would
    # otherwise short-circuit (e.g. two `render` methods in one file defeat a
    # typed `p.render()` call before this tier is reached). If the edge carries
    # a known receiver type (e.g. `user.profile` -> receiver_type='Profile'),
    # resolve `target_name` against that type's members and its
    # extends/implements ancestors. Abstains (falls through to the name-based
    # tiers) when there's no receiver type or no typed match, so behavior for
    # untyped edges is byte-for-byte unchanged.
    if receiver_type:
        typed = _members_of(receiver_type, target_name, members_by_type, ancestors)
        # Keep only candidates that are ALSO in the bare-name set (consistency
        # guard: never resolve to a symbol that isn't even named target_name).
        typed_ids = {c[0] for c in cands} & set(typed)
        if len(typed_ids) == 1:
            return next(iter(typed_ids)), "exact"
        if len(typed_ids) > 1:
            return None, "ambiguous"
        # no typed match -> fall through to the name-based tiers below

    # --- Tier 1: same-file ------------------------------------------------
    same_file = [c for c in cands if c[2] == source_file_id]
    if len(same_file) == 1:
        return same_file[0][0], "exact"
    if len(same_file) > 1:
        return None, "ambiguous"

    # --- Tier 2: import-aware --------------------------------------------
    # The source file's imports may pin which definition is in scope. An import
    # like ``xyz.be.c.networking.ApiFactory`` (tail ``ApiFactory``) resolves to
    # a symbol whose qualified_name ends with the matching suffix. We only trust
    # this when it narrows to exactly one candidate.
    my_imports = imports_by_file.get(source_file_id)
    if my_imports:
        import_match = _import_aware_candidates(target_name, my_imports, cands)
        if len(import_match) == 1:
            return import_match[0][0], "exact"
        if len(import_match) > 1:
            return None, "ambiguous"
        # import_match == [] : imports don't mention this name; fall through.

    # --- Tier 3: same-repo -----------------------------------------------
    same_repo = [c for c in cands if c[1] == source_repo]
    if len(same_repo) == 1:
        return same_repo[0][0], "exact"
    if len(same_repo) > 1:
        return None, "ambiguous"

    # --- Tier 4: global ---------------------------------------------------
    if len(cands) == 1:
        return cands[0][0], "exact"
    return None, "ambiguous"


def _import_aware_candidates(
    target_name: str,
    my_imports: List[str],
    cands: List[Tuple[str, str, str, str]],
) -> List[Tuple[str, str, str, str]]:
    """Narrow ``cands`` to those made reachable by one of the file's imports.

    Two reachability patterns are recognized:

    1. DIRECT -- the file imports the symbol itself. The import path *ends* in
       the target name. Example: ``import retrofit2.Retrofit``; a reference to
       ``Retrofit`` resolves to the symbol whose qualified_name ends in
       ``Retrofit``.

    2. CONTAINING -- the file imports the *enclosing type* and the target is a
       member of it. Example: ``import pkg.RepoA``; a call ``RepoA.create()``
       extracts target_name ``create`` (a method). The candidate
       ``RepoA.create`` is reachable because its qualified_name *starts with*
       the imported type ``RepoA``. This is the most common call pattern and the
       one tree-sitter extracts (it keeps the tail identifier of a navigation
       chain).

    For each candidate we score the longest import whose tail is either a
    suffix of (DIRECT) or a prefix of (CONTAINING) the candidate's
    qualified_name segments. Candidates with the highest score win; ties mean
    the import does not actually disambiguate, so all winners are returned and
    the caller treats them as ambiguous.

    ``cands`` tuples are ``(symbol_id, repo, file_id, qualified_name)``.
    """
    # Last segment of each import is the name it brings into scope. We keep the
    # full segment chain so a longer match scores higher (more specific import).
    import_tails: List[List[str]] = []
    for imp in my_imports:
        segs = imp.replace("/", ".").split(".")
        if segs:
            import_tails.append(segs)

    if not import_tails:
        return []  # file imports nothing

    best: List[Tuple[int, Tuple[str, str, str, str]]] = []
    for cand in cands:
        qname = (cand[3] or "").replace("/", ".")
        qsegs = qname.split(".") if qname else [target_name]
        if not qsegs or qsegs == [""]:
            qsegs = [target_name]
        longest = 0
        for tail in import_tails:
            # The imported name is the tail's last segment.
            imported_name = tail[-1]
            # DIRECT: imported_name is a suffix of qname (symbol imported as-is).
            suffix_len = _common_suffix_len(qsegs, tail)
            # CONTAINING: imported_name is a PREFIX segment of qname (member of
            # an imported type). e.g. tail [pkg, RepoA], qsegs [RepoA, create]:
            # the imported type name "RepoA" matches qsegs[0].
            # For package-qualified imports (e.g. tail [com, example, RepoA],
            # qsegs [RepoA, create]), we find where the import tail aligns as a
            # contiguous subsequence in qsegs and score by how much of the import
            # path corroborates.
            # FALLBACK: For type-scoped qnames where the import tail has package
            # segments not present in qsegs, match just the last segment of the
            # import tail (the type name) against qsegs[0] at lower confidence.
            prefix_len = 0
            if qsegs:
                # Find the position where the import tail aligns in qsegs
                # The import tail should be a contiguous subsequence starting
                # somewhere in qsegs
                for i in range(len(qsegs) - len(tail) + 1):
                    if qsegs[i:i + len(tail)] == tail:
                        # Score by how much of the import path corroborates:
                        # 1 segment minimum (the type name itself) plus any
                        # corroborating prefix segments that precede it in qsegs
                        prefix_len = len(tail) + i
                        break
                # Fallback: if full contiguous match fails, try matching just
                # the last segment of the import tail against qsegs[0].
                # This handles type-scoped qnames (e.g., 'RepoA.create') with
                # package-qualified imports (e.g., 'com.example.RepoA').
                if prefix_len == 0 and tail[-1] == qsegs[0]:
                    prefix_len = 1
            m = max(suffix_len, prefix_len)
            longest = max(longest, m)
        if longest >= 1:
            best.append((longest, cand))

    if not best:
        return []
    max_len = max(b[0] for b in best)
    return [cand for (ml, cand) in best if ml == max_len]


def _common_suffix_len(a: List[str], b: List[str]) -> int:
    """Count matching trailing segments of ``a`` and ``b`` (longest run)."""
    n = min(len(a), len(b))
    run = 0
    for j in range(1, n + 1):
        if a[-j] == b[-j]:
            run = j
        else:
            break
    return run


def resolve_repo_edges(
    conn: sqlite3.Connection,
    repo: str,
    edges_by_file: Dict[str, List[Tuple[str, str, str, int, int]]],
) -> Dict[str, int]:
    """Resolve and persist edges for one repo.

    ``edges_by_file`` maps ``source_file_id`` to a list of
    ``(edge_id, source_symbol_id, target_name, line, column)`` tuples. These are
    edges whose source symbol is already resolved (caller-side); only the target
    side is decided here.

    Writes ``(target_id, target_name, resolution)`` back to each edge row. When
    resolved, ``target_name`` is cleared (consistent with the existing resolved-
    edge convention) and ``resolution='exact'``. Otherwise ``target_id`` is NULL,
    ``target_name`` is preserved for fuzzy fallback, and ``resolution`` marks
    ambiguity.

    The symbol + import + type indices are built once per call (per repo
    build) and reused across all of the repo's files. Returns a counts dict
    ``{'exact': n, 'ambiguous': n, 'unresolved': n}``.

    Edge tuples may be 5-tuples (pre-Phase-10: no receiver_type) or 6-tuples
    (``..., receiver_type``); both are tolerated during rollout.

    The import / member / ancestor indexes are scoped to ``repo`` (H6): each is
    only ever consulted for the repo being resolved (a source file's own
    imports, a receiver type's own members, a child type's own inheritance
    edges), so scoping is exact and keeps incremental re-runs from scanning the
    whole corpus. ``build_symbol_index`` is deliberately left UNSCOPED: the
    same-repo tier (3) and the global singleton tier (4) both rely on the full
    workspace symbol set, and narrowing it would turn cross-repo ``exact``
    resolutions into ``unresolved``. See the tier comments in ``resolve_edge``.
    """
    symbols_by_name = build_symbol_index(conn)
    imports_by_file = build_import_index(conn, repo_id=repo)
    members_by_type = build_members_index(conn, repo_id=repo)
    ancestors = build_ancestor_index(conn, repo_id=repo)

    stats = {"exact": 0, "ambiguous": 0, "unresolved": 0}
    updates: List[Tuple[Optional[str], Optional[str], str, str]] = []
    for source_file_id, edges in edges_by_file.items():
        for edge_tuple in edges:
            edge_id, _source_sid, target_name = edge_tuple[0], edge_tuple[1], edge_tuple[2]
            receiver_type = edge_tuple[5] if len(edge_tuple) > 5 else None
            target_id, label = resolve_edge(
                target_name, source_file_id, repo, symbols_by_name, imports_by_file,
                receiver_type, members_by_type, ancestors,
            )
            # Resolved edges drop the bare name (queries join via target_id);
            # unresolved/ambiguous keep it so --fuzzy can still match by name.
            stored_name = None if target_id else target_name
            updates.append((target_id, stored_name, label, edge_id))
            stats[label] += 1

    conn.executemany(
        "UPDATE edges SET target_id = ?, target_name = ?, resolution = ? WHERE id = ?",
        updates,
    )
    return stats
