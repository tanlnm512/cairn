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
        body = "Run `cairn embed` or `./gradlew build` first."
        refs = _extract_file_refs(body)
        assert refs == []

    def test_repeated_refs_dedupe_order_preserving(self):
        body = (
            "See `src/graph/queries.py` first, then `src/ApiClient.ts`, "
            "and `src/graph/queries.py` again."
        )
        refs = _extract_file_refs(body)
        assert refs == ["src/graph/queries.py", "src/ApiClient.ts"]


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

    def test_directory_ref_resolves_via_prefix(self, fresh_db):
        # Directories exist only as prefixes of stored file paths.
        conn = _conn_with_fixture(fresh_db)
        assert _file_exists(conn, "src/graph") is True
        assert _file_exists(conn, "other/unrelated") is True

    def test_hallucinated_directory_rejected(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        assert _file_exists(conn, "no/such/dir") is False

    def test_directory_prefix_matches_on_segment_boundary(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        # `src/grap` must not resolve via a substring of `src/graph/...`.
        assert _file_exists(conn, "src/grap") is False

    def test_repo_qualified_ref_bridges_to_repo(self, fresh_db):
        # files.path is repo-relative here, so only the repo bridge can
        # resolve `repo/path` refs (the pkg/inner row is repo-relative).
        conn = _conn_with_fixture(fresh_db)
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES "
            "(4, 'r1', 'pkg/inner/util.py', 'python')"
        )
        conn.commit()
        assert _file_exists(conn, "r1/pkg/inner/util.py") is True
        assert _file_exists(conn, "r1/pkg/inner") is True  # bridged directory
        # The bridge scopes: r2 has no pkg/inner.
        assert _file_exists(conn, "r2/pkg/inner/util.py") is False

    def test_like_wildcards_in_ref_are_literal(self, fresh_db):
        # '%'/'_' in refs are literals, not wildcards.
        conn = _conn_with_fixture(fresh_db)
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES "
            "(4, 'r1', '/tmp/r1/app/services/extra/mod.py', 'python')"
        )
        conn.commit()
        assert _file_exists(conn, "app/services_extra") is False
        assert _file_exists(conn, "app/services/extra") is True
        assert _file_exists(conn, "app/%") is False

    def test_ref_slash_and_empty_normalization(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        assert _file_exists(conn, "src/graph/") is True
        assert _file_exists(conn, "") is False
        assert _file_exists(conn, "/") is False


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

    def test_directory_ref_not_flagged_as_hallucination(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        concept = OKFConcept(
            type="Compass",
            title="test",
            body="The queries live in `src/graph` (see `src/graph/queries.py`).",
        )
        result = critic_concept(concept, conn)
        assert result.errors == []

    def test_repeated_dead_path_reported_once(self, fresh_db):
        conn = _conn_with_fixture(fresh_db)
        concept = OKFConcept(
            type="Compass",
            title="test",
            body=(
                "See `src/DoesNotExist.kt` for the entry point; "
                "`src/DoesNotExist.kt` is also the exit."
            ),
        )
        result = critic_concept(concept, conn)
        assert result.passed is False
        assert (
            sum(1 for e in result.errors if "DoesNotExist.kt" in e) == 1
        ), f"expected the dead path reported once, got {result.errors}"

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

    def test_unknown_symbol_ref_warns_not_blocks(self, fresh_db):
        """Phase 1.3.2: an unknown symbol ref produces a non-blocking warning,
        not a blocking error. This is the asymmetric critic contract -- file
        refs block (see test_hallucinated_file_ref_flagged_as_error), symbol
        refs only warn. A high-quality body still passes with the warning.
        """
        conn = _conn_with_fixture(fresh_db)
        # 5-section body (quality 1.0) citing a symbol that does not exist in
        # the graph. The warning raises the pass threshold to 0.7, but quality
        # 1.0 clears it -- so passed stays True and the warning is informational.
        concept = OKFConcept(
            type="Compass",
            title="test",
            body=(
                "# What Does This Module Do?\nCalls `TotallyMadeUpSymbol()`.\n"
                "# Common Modification Patterns\n...\n"
                "# Build-Failure Patterns\n...\n"
                "# Cross-Module Dependencies\n...\n"
                "# Tribal Knowledge\n...\n"
            ),
        )
        result = critic_concept(concept, conn)
        # The symbol ref is unknown -> warning present.
        assert any("TotallyMadeUpSymbol" in w for w in result.warnings), (
            f"expected a warning naming the unknown symbol, got {result.warnings}"
        )
        # It is NOT an error (file refs are errors; symbol refs are warnings).
        assert result.errors == []
        # And it does not block: high quality clears the raised threshold.
        assert result.passed is True, (
            "symbol-ref warnings are non-blocking by design; a high-quality body "
            "should pass despite the warning"
        )
