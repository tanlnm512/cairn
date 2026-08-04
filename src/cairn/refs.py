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
from typing import List

# --- shared patterns ------------------------------------------------------

BACKTICK_RE = re.compile(r"`([^`]+)`")

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
    """Extract backtick-quoted tokens that look like file paths."""
    refs = []
    for m in BACKTICK_RE.findall(body):
        if "/" in m or m.endswith(FILE_EXTENSIONS):
            # Skip build/CLI commands like ./gradlew or `cairn embed`.
            if m.startswith("./") or m.startswith("cairn "):
                continue
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

def file_exists(conn: sqlite3.Connection, ref: str) -> bool:
    """Path-suffix match, not "contains this basename anywhere".

    A bare filename (no "/") still only has the basename to go on and stays
    a basename match -- but a path fragment (`src/graph/queries.py`) must
    match as a suffix of a real stored path, so it can't be satisfied by an
    unrelated file that merely shares a basename.
    """
    cur = conn.cursor()
    row = cur.execute(
        "SELECT 1 FROM files WHERE path = ? OR path LIKE ? LIMIT 1",
        (ref, f"%/{ref}"),
    ).fetchone()
    return row is not None


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
