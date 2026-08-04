"""Tests for memory scoring module, especially _graph_verification alignment with critic extractors."""
from __future__ import annotations

import pytest
from cairn.memory.scoring import _graph_verification
from cairn.okf.concept import OKFConcept


class TestGraphVerificationAlignment:
    """Tests that _graph_verification uses the same reference extraction as the critic.

    Regression guard: the old implementation only recognized 4 file extensions
    (.kt/.java/.swift/.py) and only CapitalizedWord symbols.
    The critic's extractors support 11 extensions and snake_case/qualified names.
    """

    def test_ts_js_dart_files_counted(self, fresh_db):
        """Files with .ts/.js/.dart extensions should be counted as file references."""
        # Add test files to the DB
        cur = fresh_db.cursor()
        cur.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
            ("f1", "repo1", "src/ApiClient.ts", "typescript"),
        )
        cur.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
            ("f2", "repo1", "src/utils.js", "javascript"),
        )
        cur.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
            ("f3", "repo1", "src/model.dart", "dart"),
        )
        fresh_db.commit()

        # Create a concept with these references
        concept = OKFConcept(
            type="test",
            concept_id="test-1",
            title="Test",
            body=(
                "See `src/ApiClient.ts` for the API client implementation. "
                "The `src/utils.js` module has helper functions. "
                "Check `src/model.dart` for data models."
            ),
        )

        score = _graph_verification(concept, fresh_db)
        # All three should be verified (score = 1.0)
        assert score == 1.0, f".ts/.js/.dart files should be counted, got {score}"

    def test_snake_case_symbols_counted(self, fresh_db):
        """Snake_case symbols (Python functions) should be counted as symbol references."""
        # First add a file and repo
        cur = fresh_db.cursor()
        cur.execute(
            "INSERT INTO repos (id, name, path) VALUES (?, ?, ?)",
            ("repo1", "test-repo", "/tmp/test"),
        )
        cur.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
            ("f1", "repo1", "src/module.py", "python"),
        )

        # Add snake_case symbols to the DB
        cur.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?, ?)",
            ("s1", "f1", "safe_api_call", "module.safe_api_call", "function"),
        )
        cur.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?, ?)",
            ("s2", "f1", "parse_response", "utils.parse_response", "function"),
        )
        fresh_db.commit()

        # Create a concept with snake_case references
        concept = OKFConcept(
            type="test",
            concept_id="test-2",
            title="Test",
            body=(
                "Use `safe_api_call()` for network requests. "
                "The `parse_response()` function handles JSON parsing."
            ),
        )

        score = _graph_verification(concept, fresh_db)
        # Both should be verified
        assert score == 1.0, f"snake_case symbols should be counted, got {score}"

    def test_qualified_names_counted(self, fresh_db):
        """Qualified names like `ApiClient.safeApiCall` should be counted."""
        # First add a file and repo
        cur = fresh_db.cursor()
        cur.execute(
            "INSERT INTO repos (id, name, path) VALUES (?, ?, ?)",
            ("repo1", "test-repo", "/tmp/test"),
        )
        cur.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
            ("f1", "repo1", "src/ApiClient.kt", "kotlin"),
        )

        # Add symbols with qualified names
        cur.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?, ?)",
            ("s1", "f1", "safeApiCall", "ApiClient.safeApiCall", "function"),
        )
        cur.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?, ?)",
            ("s2", "f1", "buildRequest", "HttpClient.buildRequest", "function"),
        )
        fresh_db.commit()

        # Create a concept with qualified name references
        concept = OKFConcept(
            type="test",
            concept_id="test-3",
            title="Test",
            body=(
                "Call `ApiClient.safeApiCall()` to make requests. "
                "The `HttpClient.buildRequest()` method prepares HTTP calls."
            ),
        )

        score = _graph_verification(concept, fresh_db)
        # Both should be verified
        assert score == 1.0, f"qualified names should be counted, got {score}"

    def test_mixed_extensions_and_symbols(self, fresh_db):
        """Test that all supported file types and symbols work together."""
        # First add a repo and files
        cur = fresh_db.cursor()
        cur.execute(
            "INSERT INTO repos (id, name, path) VALUES (?, ?, ?)",
            ("repo1", "test-repo", "/tmp/test"),
        )
        # Add a mix of files and symbols
        cur.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
            ("f1", "repo1", "src/Component.tsx", "typescript"),
        )
        cur.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
            ("f2", "repo1", "src/Api.java", "java"),
        )
        cur.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?, ?)",
            ("s1", "f2", "fetch_data", "api.fetch_data", "function"),
        )
        cur.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?, ?)",
            ("s2", "f2", "RequestBuilder", "http.RequestBuilder", "class"),
        )
        fresh_db.commit()

        concept = OKFConcept(
            type="test",
            concept_id="test-4",
            title="Test",
            body=(
                "The `src/Component.tsx` UI uses the `src/Api.java` backend. "
                "Call `fetch_data()` from the API module. "
                "Use `RequestBuilder` to construct HTTP requests."
            ),
        )

        score = _graph_verification(concept, fresh_db)
        # All four should be verified
        assert score == 1.0, f"mixed extensions and symbols should work, got {score}"

    def test_unverified_refs_lower_score(self, fresh_db):
        """References that don't exist should lower the verification score."""
        cur = fresh_db.cursor()
        # Add only one of the referenced files
        cur.execute(
            "INSERT INTO repos (id, name, path) VALUES (?, ?, ?)",
            ("repo1", "test-repo", "/tmp/test"),
        )
        cur.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
            ("f1", "repo1", "src/ApiClient.ts", "typescript"),
        )
        fresh_db.commit()

        concept = OKFConcept(
            type="test",
            concept_id="test-5",
            title="Test",
            body=(
                "See `src/ApiClient.ts` (exists) and `src/Missing.ts` (does not exist). "
                "Also check `missing_function()` which is not in the graph."
            ),
        )

        score = _graph_verification(concept, fresh_db)
        # Only 1 out of 3 references exists: 0.33
        assert score == pytest.approx(1/3, rel=1e-3), f"unverified refs should lower score, got {score}"

    def test_no_refs_returns_neutral(self, fresh_db):
        """Concepts with no backtick-quoted references should return neutral score."""
        concept = OKFConcept(
            type="test",
            concept_id="test-6",
            title="Test",
            body="This concept has no backtick-quoted references at all.",
        )

        score = _graph_verification(concept, fresh_db)
        # Should return 1.0 (neutral-positive) when there's nothing to verify
        assert score == 1.0, f"no refs should return neutral 1.0, got {score}"

    def test_extraction_matches_critic(self, fresh_db):
        """Verify the extraction logic matches the critic's extractor behavior.

        This is a direct test that ensures scoring._graph_verification uses
        the same extraction patterns as the critic.
        """
        from cairn.compass.critic import _extract_file_refs, _extract_symbol_refs

        cur = fresh_db.cursor()
        # Add test data
        cur.execute(
            "INSERT INTO repos (id, name, path) VALUES (?, ?, ?)",
            ("repo1", "test-repo", "/tmp/test"),
        )
        cur.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
            ("f1", "repo1", "src/Component.tsx", "typescript"),
        )
        cur.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?, ?)",
            ("s1", "f1", "my_function", "module.my_function", "function"),
        )
        fresh_db.commit()

        body = (
            "The `src/Component.tsx` file defines the UI. "
            "Use `my_function()` for processing. "
            "Check `src/Api.java` for API definitions."
        )

        # Extract using critic's functions
        file_refs = _extract_file_refs(body)
        symbol_refs = _extract_symbol_refs(body)

        # Verify extraction worked correctly
        assert "src/Component.tsx" in file_refs
        assert "src/Api.java" in file_refs
        assert "my_function()" in symbol_refs

        # Now verify _graph_verification uses the same extraction
        concept = OKFConcept(type="test", concept_id="test-7", title="Test", body=body)
        score = _graph_verification(concept, fresh_db)

        # Should count the existing file and symbol, but not the missing file
        # 2 verified (Component.tsx, my_function) / 3 total = 0.667
        assert score == pytest.approx(2/3, rel=1e-3), f"should match critic extraction, got {score}"


class TestCriticDedup:
    """Tests that scoring.py imports verification functions from critic.py rather than duplicating them.

    Regression guard: scoring.py previously had duplicate _file_exists and
    _symbol_exists implementations with identical SQL. These should be imported from
    critic.py instead to eliminate drift risk.
    """

    def test_file_exists_imported_from_critic(self):
        """Verify scoring._file_exists IS critic._file_exists (same function object)."""
        from cairn.memory.scoring import _file_exists as scoring_file_exists
        from cairn.compass.critic import _file_exists as critic_file_exists

        # Identity check: must be the exact same function object
        assert scoring_file_exists is critic_file_exists, (
            "scoring._file_exists should be the same function object as critic._file_exists"
        )

    def test_symbol_exists_imported_from_critic(self):
        """Verify scoring._symbol_exists IS critic._symbol_exists (same function object)."""
        from cairn.memory.scoring import _symbol_exists as scoring_symbol_exists
        from cairn.compass.critic import _symbol_exists as critic_symbol_exists

        # Identity check: must be the exact same function object
        assert scoring_symbol_exists is critic_symbol_exists, (
            "scoring._symbol_exists should be the same function object as critic._symbol_exists"
        )

    def test_no_duplicate_definitions(self):
        """Verify scoring.py has no duplicate function definitions for _file_exists and _symbol_exists."""
        import inspect
        from cairn import memory

        # Get the source code of scoring.py
        scoring_source = inspect.getsource(memory.scoring)

        # Look for "def _file_exists" in the source - should not find any definitions
        assert "def _file_exists" not in scoring_source, (
            "scoring.py should not define _file_exists (it should be imported from critic.py)"
        )

        # Look for "def _symbol_exists" in the source - should not find any definitions
        assert "def _symbol_exists" not in scoring_source, (
            "scoring.py should not define _symbol_exists (it should be imported from critic.py)"
        )
