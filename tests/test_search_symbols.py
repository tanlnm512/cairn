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
    from codegraph.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols(conn, "*core_ui_v4*")
    names = [r["name"] for r in rows]
    assert len(names) >= 1, f"Expected at least 1 result for '*core_ui_v4*', got {len(names)}"


def test_plain_pattern_still_works(fresh_db):
    """Non-underscore patterns must still work (regression guard)."""
    from codegraph.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols(conn, "*Avatar*")
    names = [r["name"] for r in rows]
    assert "Avatar" in names, f"Expected 'Avatar' in results, got {names}"


def test_middle_wildcard_matches_camelcase_substring(fresh_db):
    """*UseCase* must find every camelCase name containing "UseCase", not
    just the symbol literally named "UseCase" (the Phase 1d bug)."""
    from codegraph.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    names = {r["name"] for r in search_symbols(conn, "*UseCase*")}
    assert {"UpdateProfileUseCase", "GetPhotosUseCase", "UseCase"} <= names


def test_leading_wildcard_matches_camelcase_substring(fresh_db):
    """*UseCase (leading wildcard only) must match the same superset."""
    from codegraph.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    names = {r["name"] for r in search_symbols(conn, "*UseCase")}
    assert {"UpdateProfileUseCase", "GetPhotosUseCase", "UseCase"} <= names


def test_no_wildcard_matches_camelcase_substring(fresh_db):
    """A bare pattern with no wildcard at all is still expected to behave
    like a substring search (the historical/documented LIKE '%...%' feel),
    so "UseCase" alone must also find UpdateProfileUseCase."""
    from codegraph.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    names = {r["name"] for r in search_symbols(conn, "UseCase")}
    assert "UpdateProfileUseCase" in names


def test_infix_wildcard_matches(fresh_db):
    """Update*UseCase (wildcard in the middle) must still match."""
    from codegraph.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    names = {r["name"] for r in search_symbols(conn, "Update*UseCase")}
    assert "UpdateProfileUseCase" in names


def test_trailing_prefix_pattern_path_unchanged(fresh_db):
    """Register* is a genuine prefix pattern: it must still go through the
    fast FTS path (_pattern_to_fts returns a plain prefix query) and find
    both Register* symbols."""
    from codegraph.graph.lexical import _pattern_to_fts
    from codegraph.graph.queries import search_symbols

    assert _pattern_to_fts("Register*") == "Register*"

    conn = _seeded_fts_conn(fresh_db)
    names = {r["name"] for r in search_symbols(conn, "Register*")}
    assert {"RegisterViewModel", "RegisterScreen"} <= names


def test_qualified_name_match_preserved(fresh_db):
    """A pattern that only matches via qualified_name (not the bare name)
    must still be found -- guards against a naive LIKE-only fix, which only
    matches s.name and would silently drop this hit."""
    from codegraph.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    names = {r["name"] for r in search_symbols(conn, "*usecase*")}
    assert "invoke" in names, (
        f"expected the 'invoke' member (qualified_name contains 'usecase') "
        f"to survive the union, got {names}"
    )


def test_kind_filter_applies_to_fallback_rows(fresh_db):
    """kind filtering must also apply to rows added by the LIKE fallback."""
    from codegraph.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols(conn, "*UseCase*", kind="class")
    kinds = {r["kind"] for r in rows}
    assert kinds <= {"class"}
    names = {r["name"] for r in rows}
    assert "UseCase" not in names  # it's an interface, not a class


def test_limit_is_respected_after_merge(fresh_db):
    """The merged (FTS + LIKE) result set must still respect ``limit``."""
    from codegraph.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols(conn, "*UseCase*", limit=2)
    assert len(rows) <= 2


def test_no_duplicate_ids_after_merge(fresh_db):
    """A symbol found by both the FTS pass and the LIKE fallback must only
    appear once in the merged results."""
    from codegraph.graph.queries import search_symbols

    conn = _seeded_fts_conn(fresh_db)
    rows = search_symbols(conn, "*Avatar*")
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))
