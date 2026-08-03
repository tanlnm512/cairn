"""Lexical symbol search (FTS5 + bm25 ranking, LIKE fallback).

Handles pattern-to-FTS conversion and the bm25-ranked symbol search that
``search_symbols`` exposes; degrades to a LIKE scan when FTS5 is unavailable
or the MATCH query errors.
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional


def _is_fts_prefix_pattern(pattern: str) -> bool:
    """True iff ``pattern`` is a pure single-trailing-wildcard token (``"Api*"``).

    This is the one shape where an FTS5 prefix query (``token*``) is actually
    correct: FTS5's ``*`` only matches from the *start* of an indexed token,
    and unicode61 tokenization does not split camelCase, so a prefix query
    can only ever find names that literally start with ``token``. Any other
    pattern shape (a leading/middle wildcard, or no wildcard at all) is
    asking for substring semantics that FTS5's prefix operator cannot express
    against an un-split camelCase token -- see ``search_symbols``, which
    unions in a LIKE-based fallback for those cases instead of pretending
    FTS5 can do it.
    """
    p = pattern.strip()
    if not (p.endswith("*") and "*" not in p[:-1] and "_" not in p):
        return False
    token = p[:-1].strip()
    return bool(token) and not any(c in token for c in ' ":()')


def _pattern_to_fts(pattern: str) -> Optional[str]:
    """Convert a user search pattern into an FTS5 MATCH query string.

    FTS5 + unicode61 tokenization splits on underscores (but NOT camelCase),
    so we rebuild the query as a phrase with a prefix on the last token:

      ``"Api*"``           -> ``Api*``            (kept as FTS5 prefix query)
      ``"*core_ui_v4*"``   -> ``"core ui v4*"``   (split into a prefix phrase)
      ``"ApiFactory"``     -> ``"ApiFactory*"``   (prefix matches the full name)
      ``"Legacy"``         -> ``"Legacy*"``       (matches LegacySymbol)
      ``"login user"``     -> ``"login user*"``   (multi-token phrase)

    Note this is *still* a prefix query under the hood in every case above --
    it only ever matches names/tokens that literally *start with* the given
    text. It does NOT give substring/suffix matching against an un-split
    camelCase identifier (``"UseCase"`` will not find ``UpdateProfileUseCase``
    this way); ``search_symbols`` compensates for that by unioning in a
    LIKE-based substring fallback whenever this isn't a pure prefix pattern.
    Returns None if the pattern yields no usable tokens (caller falls back to
    LIKE only).
    """
    import re

    p = pattern.strip()
    if not p:
        return None

    # Trailing prefix wildcard already present: "Api*" stays as-is (one token).
    if _is_fts_prefix_pattern(p):
        return f"{p[:-1].strip()}*"

    # General case: strip wildcards and split on underscores / non-alphanumerics
    # into FTS tokens. Quote as a phrase so token order is preserved, and append
    # '*' AFTER the closing quote so it acts as a phrase-prefix query. NOTE:
    # the '*' must be outside the quotes -- '"foo*"' is a no-op phrase in
    # FTS5, only '"foo"'* matches tokens beginning with the phrase. This is
    # still prefix-only (see docstring above) -- search_symbols unions in the
    # LIKE fallback to cover the substring cases this can't reach.
    cleaned = re.sub(r"[*%]", " ", p)
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", cleaned) if t]
    if not tokens:
        return None
    return '"' + " ".join(tokens) + '"*'


def _search_like(
    conn: sqlite3.Connection, pattern: str, kind: Optional[str], limit: int
) -> List[sqlite3.Row]:
    """LIKE fallback (used when FTS5 is unavailable or MATCH errors).

    Preserves graceful degradation so search does not crash on a SQLite build
    without FTS5.
    """
    sql_pattern = pattern.replace("*", "%")
    if "%" not in sql_pattern and "_" not in sql_pattern:
        sql_pattern = f"%{sql_pattern}%"
    cur = conn.cursor()
    if kind:
        rows = cur.execute(
            """SELECT s.*, f.path AS file_path, f.repo_id AS repo
               FROM symbols s JOIN files f ON s.file_id = f.id
               WHERE s.name LIKE ? AND s.kind = ?
               ORDER BY s.name LIMIT ?""",
            (sql_pattern, kind, limit),
        ).fetchall()
    else:
        rows = cur.execute(
            """SELECT s.*, f.path AS file_path, f.repo_id AS repo
               FROM symbols s JOIN files f ON s.file_id = f.id
               WHERE s.name LIKE ?
               ORDER BY s.name LIMIT ?""",
            (sql_pattern, limit),
        ).fetchall()
    return list(rows)


def search_symbols(
    conn: sqlite3.Connection, pattern: str, kind: Optional[str] = None, limit: int = 100
) -> List[sqlite3.Row]:
    """Search symbols by name/docstring, ranked by relevance (FTS5 + bm25).

    ``*`` in the pattern is interpreted as an FTS5 prefix query when trailing
    (``"Api*"``) or split into tokens otherwise (``"*core_ui_v4*"`` -> phrase
    ``"core ui v4"``). Results are ranked by bm25 (most relevant first) and
    joined back to the symbols/files rows for full data.

    FTS5's ``*`` only matches from the *start* of an indexed token, and
    unicode61 does not split camelCase -- so for anything other than a pure
    trailing-prefix pattern (see ``_is_fts_prefix_pattern``), the FTS query
    above is prefix-only and silently misses real substring matches
    (``"UseCase"``/``"*UseCase*"`` do not find ``UpdateProfileUseCase``, with
    or without wildcards). When the pattern isn't a pure prefix pattern and
    FTS came back under ``limit``, this unions in ``_search_like`` (true
    substring matching against ``s.name``) to cover that gap -- FTS rows
    first (bm25-ranked), then LIKE rows not already present, capped at
    ``limit``. This is strictly additive: it never returns fewer rows than
    the FTS-only query did.

    Falls back to the LIKE search if FTS5 is unavailable or the MATCH query
    errors (e.g. the FTS table is missing).
    """
    fts_query = _pattern_to_fts(pattern)
    if fts_query is None:
        return _search_like(conn, pattern, kind, limit)

    sql = (
        "SELECT s.*, f.path AS file_path, f.repo_id AS repo, "
        "bm25(symbols_fts) AS rank "
        "FROM symbols_fts "
        "JOIN symbols s ON s.rowid = symbols_fts.rowid "
        "JOIN files f ON s.file_id = f.id "
        "WHERE symbols_fts MATCH ?"
    )
    params = [fts_query]  # type: list
    if kind:
        sql += " AND s.kind = ?"
        params.append(kind)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    try:
        rows = list(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError:
        # FTS5 missing, table absent, or malformed query: degrade to LIKE.
        return _search_like(conn, pattern, kind, limit)

    if not _is_fts_prefix_pattern(pattern) and len(rows) < limit:
        seen_ids = {r["id"] for r in rows}
        for r in _search_like(conn, pattern, kind, limit):
            if r["id"] not in seen_ids:
                rows.append(r)
                seen_ids.add(r["id"])
                if len(rows) >= limit:
                    break
    return rows
