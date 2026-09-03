"""Neutral reference-extraction + graph-verification helpers.

Shared by the compass critic (L2) and memory scoring (L4) so the two layers
agree on what counts as a "verified" file/symbol reference without either
importing the other.

Scope: deterministic backtick-ref extraction and graph existence checks only.
Critic-specific heuristics (prose-heavy warnings, thresholds) stay in
``compass/critic.py`` -- they are not shared with memory scoring.
"""
from __future__ import annotations

import re
import sqlite3
from typing import List, Tuple

# --- shared patterns ------------------------------------------------------

BACKTICK_RE = re.compile(r"`([^`]+)`")

# Escape char for LIKE pattern matching. Refs come from generated prose, so
# literal '%'/'_' in a path (e.g. `app/services_extra`) must not act as
# wildcards.
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
    """SQL fragment matching a column to a path ref on any of:
    exact file, file-suffix, root-anchored directory prefix, or mid-path
    directory prefix. Takes 4 params via _path_match_params."""
    return (
        f"({alias} = ? OR {alias} LIKE ? ESCAPE '\\' "
        f"OR {alias} LIKE ? ESCAPE '\\' OR {alias} LIKE ? ESCAPE '\\')"
    )


def _path_match_params(ref: str) -> Tuple[str, str, str, str]:
    e = _escape_like(ref)
    return (ref, f"%/{e}", f"{e}/%", f"%/{e}/%")


def file_exists(conn: sqlite3.Connection, ref: str) -> bool:
    """True if `ref` names a real file OR directory in the graph.

    The graph stores files only, so a directory exists iff some indexed file
    lives under it: `src/graph` resolves via a segment-boundary prefix
    (`src/graph/queries.py`), never a bare substring. A file ref matches
    exactly or as a path suffix -- `queries.py` can't be satisfied by an
    unrelated path that merely contains it.

    A repo-qualified ref (`polaris-app/app/adk`) -- the form multi-repo facts
    naturally cite -- is bridged by re-validating the remainder within that
    repo: files.path is repo-relative, so the qualified form never matches
    literally.
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
