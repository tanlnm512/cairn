"""Regression tests for the compass/wiki critic's fact-checking rigor.

Covers the two gaps closed in src/compass/critic.py (2026-07-24):
1. Symbol/file extraction used to miss qualified names, lowerCamelCase
   members, and several supported file extensions -- those references never
   even reached the matcher, so they were silently never checked.
2. File matching used a bare basename substring, so an unrelated file
   sharing a basename could satisfy a reference to a completely different
   path.
"""
from __future__ import annotations

import sqlite3

from cairn.compass.critic import (
    _extract_file_refs,
    _extract_symbol_refs,
    _file_exists,
    _symbol_exists,
    critic_concept,
)
from cairn.okf.concept import OKFConcept


def _seed_fixture(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '/tmp/r1')")
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r2', 'r2', '/tmp/r2')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES "
        "(1, 'r1', '/tmp/r1/src/graph/queries.py', 'python')"
    )
    # Deliberately a different queries.py under a different repo/package, to
    # prove basename-only matching would have been a false positive.
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES "
        "(2, 'r2', '/tmp/r2/other/unrelated/queries.py', 'python')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES "
        "(3, 'r1', '/tmp/r1/src/ApiClient.ts', 'typescript')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (1, 1, 'safeApiCall', 'function', 'xyz.ApiClient.safeApiCall', 1, 10)"
    )
    conn.commit()


def _conn_with_fixture(fresh_db) -> sqlite3.Connection:
    _seed_fixture(fresh_db)
    return fresh_db


class TestFileRefExtraction:
    def test_extracts_new_language_extensions(self):
        body = "See `src/ApiClient.ts` and `src/main.dart` and `Foo.mm` for details."
        refs = _extract_file_refs(body)
        assert "src/ApiClient.ts" in refs
        assert "src/main.dart" in refs
        assert "Foo.mm" in refs

    def test_still_skips_cli_and_build_commands(self):
        body = "Run `cg embed` or `./gradlew build` first."
        refs = _extract_file_refs(body)
        assert refs == []


class TestSymbolRefExtraction:
    def test_extracts_qualified_and_lowercamel_and_snake_case(self):
        body = (
            "Calls `ApiClient.safeApiCall()` which wraps `safe_api_call` "
            "and delegates to `retryWithBackoff`."
        )
        refs = _extract_symbol_refs(body)
        assert "ApiClient.safeApiCall()" in refs
        assert "safe_api_call" in refs
        assert "retryWithBackoff" in refs

    def test_does_not_double_count_file_refs_as_symbols(self):
        body = "See `src/graph/queries.py` for the query implementation."
        assert _extract_symbol_refs(body) == []


class TestFileExists:
    def test_full_path_matches_correct_file_only(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        assert _file_exists(conn, "src/graph/queries.py") is True

    def test_basename_alone_matches_via_suffix_not_pure_substring(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        # Two files named queries.py exist (different repos) -- a bare
        # basename reference is genuinely ambiguous and should still resolve
        # (it matches *a* real file), but a full path fragment must pick the
        # right one specifically.
        assert _file_exists(conn, "queries.py") is True
        assert _file_exists(conn, "src/graph/queries.py") is True
        assert _file_exists(conn, "other/unrelated/queries.py") is True

    def test_nonexistent_path_fragment_rejected(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        # Old basename-only substring matching would have let this through
        # since a queries.py exists somewhere -- but this specific fragment
        # doesn't match either real file's path suffix.
        assert _file_exists(conn, "completely/wrong/dir/queries.py") is False

    def test_hallucinated_file_rejected(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        assert _file_exists(conn, "src/DoesNotExist.kt") is False


class TestSymbolExists:
    def test_bare_name_matches(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        assert _symbol_exists(conn, "safeApiCall") is True

    def test_call_syntax_stripped(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        assert _symbol_exists(conn, "safeApiCall()") is True

    def test_qualified_name_matches(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        assert _symbol_exists(conn, "ApiClient.safeApiCall") is True

    def test_partially_qualified_suffix_matches(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        assert _symbol_exists(conn, "xyz.ApiClient.safeApiCall") is True

    def test_hallucinated_symbol_rejected(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        assert _symbol_exists(conn, "totallyMadeUpMethod") is False


class TestCriticConceptIntegration:
    def test_hallucinated_file_ref_flagged_as_error(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        concept = OKFConcept(
            type="Compass",
            title="test",
            body="See `src/DoesNotExist.kt` for the entry point.",
        )
        result = critic_concept(concept, conn)
        assert any("DoesNotExist.kt" in e for e in result.errors)
        assert result.passed is False

    def test_real_qualified_symbol_ref_not_flagged(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        concept = OKFConcept(
            type="Compass",
            title="test",
            body=(
                "# What Does This Module Do?\nCalls `ApiClient.safeApiCall()`.\n"
                "# Common Modification Patterns\n...\n"
                "# Build-Failure Patterns\n...\n"
                "# Cross-Module Dependencies\n...\n"
                "# Tribal Knowledge\n...\n"
            ),
        )
        result = critic_concept(concept, conn)
        assert result.warnings == []
        assert result.errors == []
