"""Lexical symbol search (FTS5 + bm25 ranking, LIKE fallback)."""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Iterable, List, Optional

from .schema import note_contention, _is_lock_contention

_logger = logging.getLogger(__name__)


def _is_fts_prefix_pattern(pattern: str) -> bool:
    """True iff ``pattern`` is a pure single-trailing-wildcard token (``"Api*"``)."""
    p = pattern.strip()
    if not (p.endswith("*") and "*" not in p[:-1] and "_" not in p):
        return False
    token = p[:-1].strip()
    return bool(token) and not any(c in token for c in ' ":()')


def _pattern_to_fts(pattern: str) -> Optional[str]:
    """Convert a user search pattern into an FTS5 MATCH query string."""
    import re

    p = pattern.strip()
    if not p:
        return None

    # Trailing prefix wildcard already present: "Api*" stays as-is (one token).
    if _is_fts_prefix_pattern(p):
        return f"{p[:-1].strip()}*"

    # The prefix star must sit outside the quoted FTS phrase.
    cleaned = re.sub(r"[*%]", " ", p)
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", cleaned) if t]
    if not tokens:
        return None
    return '"' + " ".join(tokens) + '"*'


def _terms_to_fts(terms: Iterable[str]) -> Optional[str]:
    """Build an OR-combined per-term-prefix FTS5 MATCH query."""
    tokens: List[str] = []
    seen: set = set()
    for term in terms:
        for token in re.split(r"[^A-Za-z0-9]+", term):
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            tokens.append(token)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"*' for t in tokens)


def _search_like(
    conn: sqlite3.Connection, pattern: str, kind: Optional[str], limit: int
) -> List[sqlite3.Row]:
    """LIKE fallback (used when FTS5 is unavailable or MATCH errors)."""
    sql_pattern = "%".join(
        segment.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        for segment in pattern.split("*")
    )
    if "*" not in pattern:
        sql_pattern = f"%{sql_pattern}%"
    cur = conn.cursor()
    if kind:
        rows = cur.execute(
            """SELECT s.*, f.path AS file_path, f.repo_id AS repo
               FROM symbols s JOIN files f ON s.file_id = f.id
               WHERE s.name LIKE ? ESCAPE '\\' AND s.kind = ?
               ORDER BY s.name LIMIT ?""",
            (sql_pattern, kind, limit),
        ).fetchall()
    else:
        rows = cur.execute(
            """SELECT s.*, f.path AS file_path, f.repo_id AS repo
               FROM symbols s JOIN files f ON s.file_id = f.id
               WHERE s.name LIKE ? ESCAPE '\\'
               ORDER BY s.name LIMIT ?""",
            (sql_pattern, limit),
        ).fetchall()
    return list(rows)


def _fts_symbol_rows(
    conn: sqlite3.Connection, fts_query: str, kind: Optional[str], limit: int
) -> List[sqlite3.Row]:
    """Run the bm25-ranked FTS join for one MATCH expression (shared SQL)."""
    sql = (
        "SELECT s.*, f.path AS file_path, f.repo_id AS repo, "
        "bm25(symbols_fts) AS rank "
        "FROM symbols_fts "
        "JOIN symbols s ON s.rowid = symbols_fts.rowid "
        "JOIN files f ON s.file_id = f.id "
        "WHERE symbols_fts MATCH ?"
    )
    params: list = [fts_query]
    if kind:
        sql += " AND s.kind = ?"
        params.append(kind)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    return list(conn.execute(sql, params).fetchall())


def _fts_search_or_like(
    conn: sqlite3.Connection,
    fts_query: str,
    like_pattern: str,
    kind: Optional[str],
    limit: int,
    contention_site: str,
    warn_key: str,
) -> List[sqlite3.Row]:
    """FTS query with the graceful LIKE degrade (shared by both search modes)."""
    try:
        return _fts_symbol_rows(conn, fts_query, kind, limit)
    except sqlite3.OperationalError as e:
        if _is_lock_contention(e):
            note_contention(contention_site, error=e)
        else:
            try:
                from cairn.telemetry import warn_once

                warn_once(
                    warn_key,
                    _logger,
                    "FTS5 query failed (%s) -- degrading to the LIKE scan; "
                    "results stay correct but unranked." % e,
                )
            except Exception:
                pass
        # FTS5 missing, table absent, or malformed query: degrade to LIKE.
        return _search_like(conn, like_pattern, kind, limit)


def search_symbols(
    conn: sqlite3.Connection, pattern: str, kind: Optional[str] = None, limit: int = 100
) -> List[sqlite3.Row]:
    """Search symbols by name/docstring, ranked by relevance (FTS5 + bm25)."""
    fts_query = _pattern_to_fts(pattern)
    if fts_query is None:
        return _search_like(conn, pattern, kind, limit)

    rows = _fts_search_or_like(
        conn,
        fts_query,
        pattern,
        kind,
        limit,
        contention_site="lexical.fts_search",
        warn_key="lexical.fts_non_contention",
    )

    if not _is_fts_prefix_pattern(pattern) and len(rows) < limit:
        seen_ids = {r["id"] for r in rows}
        for r in _search_like(conn, pattern, kind, limit):
            if r["id"] not in seen_ids:
                rows.append(r)
                seen_ids.add(r["id"])
                if len(rows) >= limit:
                    break
    return rows


def search_symbols_terms(
    conn: sqlite3.Connection,
    terms: Iterable[str],
    kind: Optional[str] = None,
    limit: int = 100,
) -> List[sqlite3.Row]:
    """Search symbols by an OR-combined term list, ranked by bm25."""
    term_list = [t.strip() for t in terms if t and t.strip()]
    joined = " ".join(term_list)
    fts_query = _terms_to_fts(term_list)
    if fts_query is None:
        return _search_like(conn, joined, kind, limit)
    return _fts_search_or_like(
        conn,
        fts_query,
        joined,
        kind,
        limit,
        contention_site="lexical.fts_term_search",
        warn_key="lexical.fts_term_non_contention",
    )
