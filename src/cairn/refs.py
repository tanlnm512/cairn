"""Neutral reference-extraction + graph-verification helpers."""
from __future__ import annotations

import re
import sqlite3
from typing import List, Optional, Sequence, Tuple

# --- shared patterns ------------------------------------------------------

BACKTICK_RE = re.compile(r"`([^`]+)`")

# LIKE escape char: literal '%'/'_' in refs must not act as wildcards.
LIKE_ESCAPE_CHAR = "\\"


def _escape_like(value: str, escape: str = LIKE_ESCAPE_CHAR) -> str:
    """Escape LIKE wildcard metacharacters for use inside `LIKE ? ESCAPE '\\'`."""
    if not value:
        return ""
    return (
        value.replace(escape, escape * 2)
        .replace("%", escape + "%")
        .replace("_", escape + "_")
    )

# Extensions for all languages cairn parses (see pyproject.toml
# tree-sitter deps).
FILE_EXTENSIONS = (
    ".kt", ".java", ".swift", ".py", ".ts", ".tsx", ".js", ".jsx", ".dart", ".m", ".mm",
)

# A bare identifier, or a dotted qualified name (Outer.inner.member), with an
# optional trailing call-syntax `()`. Covers CapitalizedTypes, lowerCamelCase
# and snake_case members, and qualified references -- not just single
# capitalized words.
SYMBOL_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?$"
)


# --- extraction -----------------------------------------------------------

def extract_file_refs(body: str) -> List[str]:
    """Extract backtick-quoted tokens that look like file paths.

    Order-preserving dedupe: a path cited repeatedly is returned once, at
    its first occurrence.
    """
    refs: List[str] = []
    seen = set()
    for m in BACKTICK_RE.findall(body):
        if "/" in m or m.endswith(FILE_EXTENSIONS):
            # Skip build/CLI commands like ./gradlew or `cairn embed`.
            if m.startswith("./") or m.startswith("cairn "):
                continue
            if m not in seen:
                seen.add(m)
                refs.append(m)
    return refs


def extract_symbol_refs(body: str) -> List[str]:
    """Extract backtick-quoted tokens that look like a symbol/qualified name.

    Excludes anything already claimed as a file ref (has "/" or a file
    extension) so a path doesn't get double-checked as a bogus symbol too.
    """
    refs = []
    for m in BACKTICK_RE.findall(body):
        if "/" in m or m.endswith(FILE_EXTENSIONS):
            continue
        if SYMBOL_RE.match(m):
            refs.append(m)
    return refs


# --- graph existence checks ----------------------------------------------

def _path_match_sql(alias: str = "path") -> str:
    """SQL fragment matching a column to a path ref (4 params, via
    _path_match_params):
    1. exact file path
    2. file-path suffix
    3. root-anchored directory prefix
    4. mid-path directory prefix
    """
    return (
        f"({alias} = ? OR {alias} LIKE ? ESCAPE '\\' "
        f"OR {alias} LIKE ? ESCAPE '\\' OR {alias} LIKE ? ESCAPE '\\')"
    )


def _path_match_params(ref: str) -> Tuple[str, str, str, str]:
    e = _escape_like(ref)
    return (ref, f"%/{e}", f"{e}/%", f"%/{e}/%")


def file_exists(conn: sqlite3.Connection, ref: str) -> bool:
    """True if `ref` resolves to a real file or directory.

    Directories exist only as prefixes of stored file paths. Match arms
    (segment-boundary only, never bare substring):
    1. exact file path
    2. file-path suffix (`src/graph/queries.py`)
    3. directory prefix (`src/graph`)
    4. repo-qualified (`repo/...`) re-validated within that repo
    """
    ref = ref.strip("/")
    if not ref:
        return False
    cur = conn.cursor()
    row = cur.execute(
        f"SELECT 1 FROM files WHERE {_path_match_sql()} LIMIT 1",
        _path_match_params(ref),
    ).fetchone()
    if row is not None:
        return True
    # Repo-qualification bridge: `repo/...` -> validate `...` within repo.
    rid, _, rest = ref.partition("/")
    if not rest:
        return False
    row = cur.execute(
        f"SELECT 1 FROM files WHERE repo_id = ? AND {_path_match_sql()} LIMIT 1",
        (rid, *_path_match_params(rest)),
    ).fetchone()
    return row is not None


def unresolved_file_refs(conn: sqlite3.Connection, refs: List[str]) -> List[str]:
    """Refs with no graph file match, deduped, input order preserved."""
    return [ref for ref in dict.fromkeys(refs) if not file_exists(conn, ref)]


def symbol_exists(conn: sqlite3.Connection, name: str) -> bool:
    """Check a symbol/qualified-name reference against both name columns.

    Strips a trailing call-syntax `()` (backtick refs often look like
    `safeApiCall()`). For a dotted qualified reference
    (`ApiClient.safeApiCall`), checks the full qualified_name as a suffix
    match (handles fully- or partially-qualified refs) and falls back to
    matching just the last segment against `name` -- a bare `safeApiCall`
    reference should still resolve even without its containing type.
    """
    cur = conn.cursor()
    bare = name[:-2] if name.endswith("()") else name

    row = cur.execute(
        "SELECT 1 FROM symbols WHERE name = ? OR qualified_name = ? "
        "OR qualified_name LIKE ? LIMIT 1",
        (bare, bare, f"%.{bare}"),
    ).fetchone()
    if row is not None:
        return True

    if "." in bare:
        last_segment = bare.rsplit(".", 1)[-1]
        row = cur.execute(
            "SELECT 1 FROM symbols WHERE name = ? LIMIT 1", (last_segment,)
        ).fetchone()
        return row is not None

    return False


# --- successor resolution ---------------------------------------------------

def successor_candidates(
    conn: sqlite3.Connection, ref: str, file_scope_refs: Sequence[str] = ()
) -> List[str]:
    """Live symbols sharing a dead symbol ref's identity anchors.

    Anchors knowable for a dead ref: the qualified-name prefix of a dotted
    ref, and the file scope — the concept's still-live file refs. A
    candidate must satisfy every knowable anchor; a ref with no knowable
    anchor yields no candidates. Returns successor identities
    (qualified_name, else name), one per distinct symbol.
    """
    bare = ref[:-2] if ref.endswith("()") else ref
    prefix, _, _ = bare.rpartition(".")
    clauses = []
    params: List[str] = []
    if prefix:
        esc = _escape_like(prefix)
        clauses.append(
            "(s.qualified_name LIKE ? ESCAPE '\\'"
            " OR s.qualified_name LIKE ? ESCAPE '\\')"
        )
        params.extend((esc + ".%", "%." + esc + ".%"))
    scope_clauses = []
    for file_ref in file_scope_refs:
        if not file_exists(conn, file_ref):
            continue
        scope_clauses.append(_path_match_sql("f.path"))
        params.extend(_path_match_params(file_ref))
        # Repo-qualified ref (`repo/src/x.py`): validate the remainder
        # within that repo, matching file_exists' bridge arm.
        rid, _, rest = file_ref.partition("/")
        if rest:
            scope_clauses.append(
                "f.repo_id = ? AND " + _path_match_sql("f.path")
            )
            params.extend((rid, *_path_match_params(rest)))
    if scope_clauses:
        clauses.append("(" + " OR ".join(scope_clauses) + ")")
    if not clauses:
        return []
    rows = conn.execute(
        "SELECT s.id, s.name, s.qualified_name"
        " FROM symbols s JOIN files f ON f.id = s.file_id"
        " WHERE " + " AND ".join(clauses),
        params,
    ).fetchall()
    by_id = {row[0]: (row[2] or row[1]) for row in rows}
    return list(by_id.values())


def resolve_successor(conn: sqlite3.Connection, body: str) -> Optional[str]:
    """Successor symbol for a memory body's dead symbol refs, or None.

    A dead ref contributes its link only when exactly one candidate shares
    its identity anchors; the body links only when the contributed links
    name one and the same symbol.
    """
    dead = [
        ref for ref in extract_symbol_refs(body)
        if not symbol_exists(conn, ref)
    ]
    if not dead:
        return None
    file_refs = extract_file_refs(body)
    links = []
    for ref in dead:
        candidates = successor_candidates(conn, ref, file_refs)
        if len(candidates) == 1:
            links.append(candidates[0])
    distinct = list(dict.fromkeys(links))
    return distinct[0] if len(distinct) == 1 else None
