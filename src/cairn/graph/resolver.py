"""Import-aware edge resolver.

Resolves an edge to exactly one candidate across priority tiers, leaving
otherwise-unresolved edges as ``resolution='ambiguous'`` (precise by default).

Tiers: SAME-FILE -> IMPORT-AWARE -> SAME-REPO -> GLOBAL -> AMBIGUOUS.
Resolution is decided per tier: one candidate -> answer; many -> mark
ambiguous and stop; zero -> try the next broader tier.
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Tuple


def build_symbol_index(conn: sqlite3.Connection) -> Dict[str, List[Tuple[str, str, str, str]]]:
    """Build a global bare-name -> symbol index.

    Returns ``{name: [(symbol_id, repo, file_id, qualified_name), ...]}``.
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
    """Build a per-file import-path index ``{file_id: [imported_path, ...]}``.

    When ``repo_id`` is given, only that repo's imports are loaded. The
    resolver only ever looks up a source file's OWN imports, so scoping is exact.
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


# Symbol kinds that are members of a type (never types themselves).
_MEMBER_KINDS = ("method", "function", "property", "variable", "field", "constant")


def build_members_index(
    conn: sqlite3.Connection,
    repo_id: Optional[str] = None,
) -> Dict[Tuple[str, str], List[str]]:
    """Build a {(enclosing_type_simple_name, member_name): [symbol_id, ...]} index.

    A member's qualified_name looks like ``pkg.Outer.Inner.member``; the
    enclosing type is the second-to-last segment. An entry is only added when
    the enclosing segment is an actual type symbol in the graph (keys off real
    type names rather than a hardcoded type-kind allowlist, so it works across
    languages). When ``repo_id`` is given, both the type-name set and the member
    rows are scoped to that repo.
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
    depend on the inheritance edges being resolved first. When ``repo_id`` is
    given, the index is scoped to that repo's inheritance edges.
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

    Cycle-safe via ``_seen``.
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
    """
    members_by_type = members_by_type or {}
    ancestors = ancestors or {}

    cands = symbols_by_name.get(target_name)
    if not cands:
        return None, "unresolved"  # external/stdlib call (e.g. listOf, fillMaxWidth)

    # --- Tier 0: type-aware (receiver dispatch) ---------------------------
    # A known receiver type is the STRONGEST resolution signal, so it runs
    # first, ahead of same-file. Resolve `target_name` against that type's
    # members and its extends/implements ancestors; abstains when there's no
    # receiver type or no typed match.
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
    # The source file's imports may pin which definition is in scope. We only
    # trust this when it narrows to exactly one candidate.
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

    1. DIRECT -- the file imports the symbol itself (import path ends in the
       target name).
    2. CONTAINING -- the file imports the enclosing type and the target is a
       member of it (import path is a prefix of the candidate's qualified_name).

    Candidates with the highest score win; ties are returned and treated as
    ambiguous.
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
            # DIRECT: imported_name is a suffix of qname (symbol imported as-is).
            suffix_len = _common_suffix_len(qsegs, tail)
            # CONTAINING: imported_name is a PREFIX segment of qname (member of
            # an imported type).
            prefix_len = 0
            if qsegs:
                # Find where the import tail aligns as a contiguous subsequence
                # in qsegs; score by how much of the import path corroborates.
                for i in range(len(qsegs) - len(tail) + 1):
                    if qsegs[i:i + len(tail)] == tail:
                        prefix_len = len(tail) + i
                        break
                # Fallback: match just the last segment of the import tail
                # against qsegs[0] for type-scoped qnames with package-qualified
                # imports.
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
    ``(edge_id, source_symbol_id, target_name, line, column)`` tuples. Writes
    ``(target_id, target_name, resolution)`` back to each edge row; on exact
    resolution ``target_name`` is cleared, otherwise preserved for fuzzy
    fallback. Returns a counts dict
    ``{'exact': n, 'ambiguous': n, 'unresolved': n}``.

    Edge tuples may be 5-tuples (no receiver_type) or 6-tuples; both tolerated.

    The import/member/ancestor indexes are scoped to ``repo``; the symbol index
    is deliberately left unscoped so the same-repo (3) and global (4) tiers see
    the full workspace symbol set.
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


def repair_incoming_edges(
    conn: sqlite3.Connection,
    repo: str,
    changed_target_names: List[str],
) -> Dict[str, int]:
    """Re-resolve edges in ``repo`` that may point at freshly re-created symbols.

    When a file is re-indexed incrementally, its old symbols are deleted and
    re-created with NEW ids. Edges in *other* files that previously resolved to
    those symbols had their ``target_id`` nulled (resolution='unresolved') so
    the FK wouldn't dangle. Without this repair pass those callers stay
    permanently 'unresolved' (so precise callers() drops them) until the caller's
    own file is edited or a full rebuild runs.

    ``changed_target_names`` is the set of bare symbol names that were deleted
    and re-created. Only edges whose ``target_name`` matches one of them (or was
    left unresolved) need a second look -- this keeps the repair proportional to
    the change rather than a full re-resolve of the repo.

    Returns a counts dict ``{'exact': n, 'ambiguous': n, 'unresolved': n}``
    over just the edges it touched.
    """
    if not changed_target_names:
        return {"exact": 0, "ambiguous": 0, "unresolved": 0}

    # Build indexes scoped to the repo. The symbol index is deliberately global
    # so the same-repo and global tiers see the full workspace set, mirroring
    # resolve_repo_edges.
    symbols_by_name = build_symbol_index(conn)
    imports_by_file = build_import_index(conn, repo_id=repo)
    members_by_type = build_members_index(conn, repo_id=repo)
    ancestors = build_ancestor_index(conn, repo_id=repo)

    # Candidate edges: any edge in this repo whose target_name is one of the
    # re-created names. These are exactly the edges the incremental path nulled
    # out. Limit to the repo to bound the scan (idx_edges_kind is not selective
    # here; target_name has no dedicated index, so this is a scan of the repo's
    # edges, which is the right granularity).
    name_placeholders = ",".join("?" for _ in changed_target_names)
    rows = conn.execute(
        f"""SELECT e.id AS eid, e.source_id AS src, e.target_name AS tname,
                   e.line AS line, e.column AS col, f.id AS file_id, f.repo_id AS repo
            FROM edges e
            JOIN symbols s ON e.source_id = s.id
            JOIN files f ON s.file_id = f.id
            WHERE f.repo_id = ?
              AND e.target_name IN ({name_placeholders})""",
        (repo, *changed_target_names),
    ).fetchall()

    if not rows:
        return {"exact": 0, "ambiguous": 0, "unresolved": 0}

    stats = {"exact": 0, "ambiguous": 0, "unresolved": 0}
    updates: List[Tuple[Optional[str], Optional[str], str, str]] = []
    for r in rows:
        target_name = r["tname"]
        if not target_name:
            continue
        target_id, label = resolve_edge(
            target_name, r["file_id"], repo, symbols_by_name, imports_by_file,
            None, members_by_type, ancestors,
        )
        stored_name = None if target_id else target_name
        updates.append((target_id, stored_name, label, r["eid"]))
        stats[label] += 1

    if updates:
        conn.executemany(
            "UPDATE edges SET target_id = ?, target_name = ?, resolution = ? WHERE id = ?",
            updates,
        )
    return stats
