"""Regression tests for search_symbols FTS5 underscore-token handling.

Phase 1c: verifies that underscored patterns like *core_ui_v4* return results
(the old LIKE-based search broke on these; FTS5 + _pattern_to_fts fixed it).

Phase 1d: verifies the LIKE-union fallback for camelCase substring matches.
FTS5's ``*`` only matches from the *start* of an indexed token, and unicode61
does not split camelCase, so a pattern like ``*UseCase*`` (or even bare
``UseCase``, no wildcard) previously matched only names literally starting
with "UseCase" -- missing ``UpdateProfileUseCase``/``GetPhotosUseCase``
entirely. ``search_symbols`` now unions in a LIKE-based substring pass
whenever the pattern isn't a pure trailing-prefix pattern.
"""
from __future__ import annotations

import sqlite3


def _seed_symbols(conn: sqlite3.Connection) -> None:
    """Insert test symbols with underscore-heavy and camelCase-heavy names."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/CoreUiV4.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (1, 1, 'core_ui_v4', 'class', 'xyz.core.ui.v4.CoreUiV4', 1, 100)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (2, 1, 'core_ui_v4_theme', 'object', 'xyz.core.ui.v4.CoreUiV4Theme', 102, 200)"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (2, 'test', '/tmp/test/Avatar.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (3, 2, 'Avatar', 'class', 'xyz.Avatar', 1, 50)"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (3, 'test', '/tmp/test/UseCases.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (4, 3, 'UpdateProfileUseCase', 'class', 'xyz.domain.usecase.UpdateProfileUseCase', 1, 20)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (5, 3, 'GetPhotosUseCase', 'class', 'xyz.domain.usecase.GetPhotosUseCase', 22, 40)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (6, 3, 'UseCase', 'interface', 'xyz.core.UseCase', 42, 44)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (7, 3, 'RegisterViewModel', 'class', 'xyz.presentation.RegisterViewModel', 46, 60)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (8, 3, 'RegisterScreen', 'function', 'xyz.presentation.RegisterScreen', 62, 70)"
    )
    # A member whose *name* alone gives no hint -- only reachable via its
    # qualified_name ("...UpdateProfileUseCase.invoke"). Guards against a
    # naive LIKE-only fix that would drop qualified-name-only matches.
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (9, 3, 'invoke', 'method', 'xyz.domain.usecase.UpdateProfileUseCase.invoke', 5, 10)"
    )
    # Ensure FTS index is populated (build rebuilds it from the content table).
    conn.commit()


def _seeded_fts_conn(fresh_db) -> sqlite3.Connection:
    """Seed symbols and rebuild the FTS index from the shadow content table."""
    _seed_symbols(fresh_db)
    try:
        fresh_db.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    except sqlite3.OperationalError:
        pass  # FTS5 not available in this build
    return fresh_db


def test_underscore_pattern_returns_results(fresh_db):
    """*core_ui_v4* must return at least one symbol — FTS5 phrase splitting."""
    from cairn.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols(conn, "*core_ui_v4*")
    names = [r["name"] for r in rows]
    assert len(names) >= 1, f"Expected at least 1 result for '*core_ui_v4*', got {len(names)}"


def test_plain_pattern_still_works(fresh_db):
    """Non-underscore patterns must still work (regression guard)."""
    from cairn.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols(conn, "*Avatar*")
    names = [r["name"] for r in rows]
    assert "Avatar" in names, f"Expected 'Avatar' in results, got {names}"


def test_middle_wildcard_matches_camelcase_substring(fresh_db):
    """*UseCase* must find every camelCase name containing "UseCase", not
    just the symbol literally named "UseCase" (the Phase 1d bug)."""
    from cairn.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    names = {r["name"] for r in search_symbols(conn, "*UseCase*")}
    assert {"UpdateProfileUseCase", "GetPhotosUseCase", "UseCase"} <= names


def test_leading_wildcard_matches_camelcase_substring(fresh_db):
    """*UseCase (leading wildcard only) must match the same superset."""
    from cairn.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    names = {r["name"] for r in search_symbols(conn, "*UseCase")}
    assert {"UpdateProfileUseCase", "GetPhotosUseCase", "UseCase"} <= names


def test_no_wildcard_matches_camelcase_substring(fresh_db):
    """A bare pattern with no wildcard at all is still expected to behave
    like a substring search (the historical/documented LIKE '%...%' feel),
    so "UseCase" alone must also find UpdateProfileUseCase."""
    from cairn.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    names = {r["name"] for r in search_symbols(conn, "UseCase")}
    assert "UpdateProfileUseCase" in names


def test_infix_wildcard_matches(fresh_db):
    """Update*UseCase (wildcard in the middle) must still match."""
    from cairn.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    names = {r["name"] for r in search_symbols(conn, "Update*UseCase")}
    assert "UpdateProfileUseCase" in names


def test_trailing_prefix_pattern_path_unchanged(fresh_db):
    """Register* is a genuine prefix pattern: it must still go through the
    fast FTS path (_pattern_to_fts returns a plain prefix query) and find
    both Register* symbols."""
    from cairn.graph.lexical import _pattern_to_fts
    from cairn.graph.queries import search_symbols

    assert _pattern_to_fts("Register*") == "Register*"

    conn = _seeded_fts_conn(fresh_db)
    names = {r["name"] for r in search_symbols(conn, "Register*")}
    assert {"RegisterViewModel", "RegisterScreen"} <= names


def test_qualified_name_match_preserved(fresh_db):
    """A pattern that only matches via qualified_name (not the bare name)
    must still be found -- guards against a naive LIKE-only fix, which only
    matches s.name and would silently drop this hit."""
    from cairn.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    names = {r["name"] for r in search_symbols(conn, "*usecase*")}
    assert "invoke" in names, (
        f"expected the 'invoke' member (qualified_name contains 'usecase') "
        f"to survive the union, got {names}"
    )


def test_kind_filter_applies_to_fallback_rows(fresh_db):
    """kind filtering must also apply to rows added by the LIKE fallback."""
    from cairn.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols(conn, "*UseCase*", kind="class")
    kinds = {r["kind"] for r in rows}
    assert kinds <= {"class"}
    names = {r["name"] for r in rows}
    assert "UseCase" not in names  # it's an interface, not a class


def test_limit_is_respected_after_merge(fresh_db):
    """The merged (FTS + LIKE) result set must still respect ``limit``."""
    from cairn.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols(conn, "*UseCase*", limit=2)
    assert len(rows) <= 2


def test_no_duplicate_ids_after_merge(fresh_db):
    """A symbol found by both the FTS pass and the LIKE fallback must only
    appear once in the merged results."""
    from cairn.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols(conn, "*Avatar*")
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Term mode (T008, FR-001): search_symbols_terms / _terms_to_fts.
#
# The OR-of-quoted-prefix path for enriched sentence queries. The contract
# split is deliberate: everything above this line pins search_symbols /
# _pattern_to_fts UNCHANGED (the 8 production callers' regression proof);
# everything below pins the NEW term-mode entry point only.
# ---------------------------------------------------------------------------

_SPEC_SENTENCE = "where is the function that parses an unencoded URL string"
_SPEC_TERMS = "parses unencoded URL string".split()  # enrich(sentence).sparse_query


def test_terms_to_fts_is_or_of_quoted_prefixes():
    """The TC-001 mechanism: per-term quoted prefix queries, OR-combined --
    never one folded phrase."""
    from cairn.graph.lexical import _terms_to_fts

    assert _terms_to_fts(["parse", "url"]) == '"parse"* OR "url"*'
    assert _terms_to_fts(_SPEC_TERMS) == (
        '"parses"* OR "unencoded"* OR "URL"* OR "string"*'
    )


def test_terms_to_fts_contrasted_with_the_phrase_defect():
    """Before/after on the spec's sentence query: _pattern_to_fts folds the
    whole sentence into ONE quoted phrase (matches no symbol name), while
    the term expression is an OR-style per-token query. Both shapes are
    pinned verbatim -- the acceptance proof for FR-001/TC-001."""
    from cairn.graph.lexical import _pattern_to_fts, _terms_to_fts

    assert _pattern_to_fts(_SPEC_SENTENCE) == (
        '"where is the function that parses an unencoded URL string"*'
    )
    expr = _terms_to_fts(_SPEC_TERMS)
    assert " OR " in expr
    assert expr != _pattern_to_fts(_SPEC_SENTENCE)
    assert "where is the function" not in expr  # no phrase folding


def test_terms_to_fts_splits_and_dedupes_within_terms():
    """A multi-token term (compound with separators) splits into its
    alphanumeric tokens; tokens dedupe case-insensitively, first casing
    kept, query order preserved (FTS5 unicode61 MATCH case-folds ASCII)."""
    from cairn.graph.lexical import _terms_to_fts

    assert _terms_to_fts(["parse_url", "parseUrl"]) == '"parse"* OR "url"* OR "parseUrl"*'
    assert _terms_to_fts(["URL", "url"]) == '"URL"*'


def test_terms_to_fts_injection_safe():
    """No FTS metacharacter from a user term can reach the MATCH string:
    every emitted token is strictly [A-Za-z0-9]+ and double-quoted (which
    also neutralizes FTS keywords -- a quoted "OR" is a string, not the
    OR operator). A raw injection like ``ea" OR 1=1 --`` would otherwise be
    an fts5 syntax error or a semantic escape from the term list."""
    from cairn.graph.lexical import _terms_to_fts

    expr = _terms_to_fts(['ea" OR 1=1 --', "x*y(z)", "parse"])
    assert expr == '"ea"* OR "OR"* OR "1"* OR "x"* OR "y"* OR "z"* OR "parse"*'
    # Every quoted token is bare alphanumeric: no meta survived.
    import re as _re

    for tok in _re.findall(r'"([^"]*)"\*', expr):
        assert _re.fullmatch(r"[A-Za-z0-9]+", tok), tok


def test_terms_to_fts_none_when_no_usable_token():
    """The _pattern_to_fts None contract: no usable token means the caller
    falls back (never a MATCH on an empty/garbage expression)."""
    from cairn.graph.lexical import _terms_to_fts

    assert _terms_to_fts([]) is None
    assert _terms_to_fts(["", "   "]) is None
    assert _terms_to_fts(['"', "*(", "%%%"]) is None


def test_term_mode_returns_rows_where_the_phrase_defect_returns_none(fresh_db):
    """The empty-BM25 defect fixed at the lexical layer: on a fixture whose
    symbol names/docstrings ARE the sentence's terms, today's string mode
    folds the sentence into one quoted phrase (plus a LIKE substring union
    over the whole sentence) and finds NOTHING, while term mode finds the
    name matches."""
    from cairn.graph.lexical import search_symbols, search_symbols_terms

    conn = _seeded_fts_conn(fresh_db)
    # "core ui v4" terms exist as symbols (core_ui_v4*); the full sentence
    # exists as no name/docstring substring.
    sentence = "the screen that renders the core ui v4 settings"
    assert [r["name"] for r in search_symbols(conn, sentence)] == []
    names = {r["name"] for r in search_symbols_terms(conn, ["screen", "core", "ui", "v4", "settings"])}
    assert {"core_ui_v4", "core_ui_v4_theme"} <= names


def test_term_mode_rows_carry_the_bm25_rank_column(fresh_db):
    """Term mode uses the same bm25-ranked join as search_symbols: rows are
    best-first by bm25() and carry the rank column the sparse leg relies on."""
    from cairn.graph.lexical import search_symbols_terms

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols_terms(conn, ["core", "ui", "v4"])
    assert rows
    assert "rank" in rows[0].keys()
    ranks = [r["rank"] for r in rows]
    assert ranks == sorted(ranks)  # bm25(): better = more negative, best-first


def test_term_mode_respects_kind_and_limit(fresh_db):
    from cairn.graph.lexical import search_symbols_terms

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols_terms(conn, ["core", "ui", "v4"], kind="object")
    assert {r["name"] for r in rows} == {"core_ui_v4_theme"}
    assert len(search_symbols_terms(conn, ["usecase"], limit=2)) <= 2


def test_term_mode_matches_qualified_name_tokens(fresh_db):
    """The FTS columns serve the whole corpus, not just bare names: terms
    hit qualified_name sub-tokens (unicode61 splits on dots and case-folds
    ASCII) exactly as the phrase path's indexed columns do."""
    from cairn.graph.lexical import search_symbols_terms

    conn = _seeded_fts_conn(fresh_db)
    # "invoke" is reachable only via its qualified_name (...UseCase.invoke).
    names = {r["name"] for r in search_symbols_terms(conn, ["invoke", "avatar"])}
    assert "invoke" in names
    assert "Avatar" in names


def test_term_mode_never_raises_on_hostile_terms(fresh_db):
    """End-to-end injection safety: hostile term strings flow through to a
    valid MATCH, never an fts5 syntax error -- the sanitize-then-quote
    defense, exercised through the public entry point. The hostile terms
    deliberately carry no legit token, so the row set proves only the real
    term matched (a hostile term WITH a legit token -- ``core" OR 1=1 --``
    -- matches the core symbols, which is correct OR-term semantics, not an
    injection)."""
    from cairn.graph.lexical import search_symbols_terms

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols_terms(conn, ['ea" OR 1=1 --', "NEAR(", "Avatar"])
    assert {r["name"] for r in rows} == {"Avatar"}
