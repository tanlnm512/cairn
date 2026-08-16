"""Lexical symbol search (FTS5 + bm25 ranking, LIKE fallback).

Handles pattern-to-FTS conversion and the bm25-ranked symbol search that
``search_symbols`` exposes; degrades to a LIKE scan when FTS5 is unavailable
or the MATCH query errors. ``search_symbols_terms`` (T008/FR-001) is the
term-mode entry point for enriched queries: OR-combined per-term prefix
queries instead of one folded phrase.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Iterable, List, Optional

from .schema import note_contention, _is_lock_contention

_logger = logging.getLogger(__name__)


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


def _terms_to_fts(terms: Iterable[str]) -> Optional[str]:
    """Build an OR-combined per-term-prefix FTS5 MATCH query (FR-001/T008).

    The term-mode counterpart to ``_pattern_to_fts`` for enriched queries:
    instead of folding a whole pattern into ONE quoted phrase (which a
    sentence-shaped query makes match no symbol name -- the empty-BM25
    defect), each term becomes an independent quoted prefix query and the
    terms are OR-combined, so BM25 can rank any symbol whose indexed
    name/qualified_name/docstring tokens *begin with* any query term:

      ``["parses", "unencoded", "URL", "string"]``
          -> ``'"parses"* OR "unencoded"* OR "URL"* OR "string"*'``

    Injection defense (user terms can carry ANY character via backticked
    spans in query_enrich, including FTS metacharacters): every term is
    reduced to its alphanumeric tokens -- ``re.split(r"[^A-Za-z0-9]+")``,
    the same tokenization ``_pattern_to_fts`` uses -- BEFORE it reaches the
    expression, so each emitted token is strictly ``[A-Za-z0-9]+``. Tokens
    are additionally double-quoted (which also neutralizes FTS keywords: a
    literal ``"OR"`` token is a string, not the OR operator), and the
    prefix ``*`` sits OUTSIDE the closing quote per the rule documented on
    ``_pattern_to_fts`` (``'"foo*"'`` is a no-op phrase; only ``'"foo"*'``
    matches tokens beginning with foo).

    Tokens dedupe case-insensitively (FTS5 unicode61 MATCH case-folds
    ASCII), first casing kept, query order preserved. Returns None when no
    usable token survives any term (caller falls back -- the same None
    contract ``_pattern_to_fts`` offers).
    """
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
    """LIKE fallback (used when FTS5 is unavailable or MATCH errors).

    Preserves graceful degradation so search does not crash on a SQLite build
    without FTS5.

    ``*`` in the pattern is treated as a wildcard (mapped to ``%``), but any
    literal ``%`` or ``_`` that came *from the user* is escaped so it matches
    itself rather than acting as a LIKE wildcard (``_`` would otherwise match
    any single char, ``%`` any run of chars). Order: escape meta-chars in the
    raw pattern first, then turn ``*`` into the ``%`` wildcard.
    """
    escaped = pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    sql_pattern = escaped.replace("*", "%")
    if "%" not in sql_pattern and "_" not in sql_pattern:
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
    """Run the bm25-ranked FTS join for one MATCH expression (shared SQL).

    Raises ``sqlite3.OperationalError`` through to the caller
    (``_fts_search_or_like`` owns the degrade decision).
    """
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
    """FTS query with the graceful LIKE degrade (shared by both search modes).

    On ``sqlite3.OperationalError`` the caller's ``like_pattern`` is scanned
    instead. Discriminates before calling this contention (mirrors
    schema.py's duplicate-column discrimination): only "locked"/"busy"
    errors are a real cross-process lock event. Anything else (FTS5 syntax
    error, a missing symbols_fts table, corruption) is a query failure
    whose LIKE degrade is BY DESIGN -- a quiet once-per-process warning is
    enough, and misattributing it to contention would pollute the
    lock_contention signal doctor aggregates on. ``contention_site`` /
    ``warn_key`` keep the two search modes independently visible in the
    once-per-process telemetry.
    """
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
    """Search symbols by an OR-combined term list, ranked by bm25 (T008/FR-001).

    The term-mode counterpart to ``search_symbols`` for enriched queries
    (``query_enrich``'s ``sparse_query`` term list): ``_terms_to_fts`` turns
    each term into a quoted FTS5 prefix query and OR-combines them, so a
    sentence-shaped query contributes its individual tokens to BM25 instead
    of being folded into ONE quoted phrase that matches no symbol name (the
    empty-BM25 defect -- see ``query_enrich``'s module docstring). Same
    bm25-ranked join, same row shape (including the ``rank`` column) as
    ``search_symbols``.

    Differences from ``search_symbols``, by construction:

    * No LIKE substring union: terms are per-token by construction, so the
      whole-pattern substring semantics of that union have no meaning for
      a term list (each term's substring reach is already covered by its
      FTS prefix query against the indexed tokens).
    * Injection defense lives in ``_terms_to_fts`` (sanitize to
      alphanumeric tokens, then quote).

    Degrades to ``_search_like`` on the space-joined terms when FTS5 is
    unavailable or the MATCH errors (the same graceful-degradation contract
    as ``search_symbols``; the LIKE shape is one substring of the joined
    terms -- conservative, and never worse than the quoted-phrase behavior
    the term mode replaces). With no usable token at all (every term
    metacharacters/whitespace), the LIKE fallback runs on the joined terms
    as well -- typically empty, mirroring ``_pattern_to_fts``'s None
    contract.
    """
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
