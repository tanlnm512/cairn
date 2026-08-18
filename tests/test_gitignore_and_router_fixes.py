"""Tests for gitignore cache invalidation and router empty flag fixes (L6 + L9).

VAL-CO-006: Gitignore cache invalidator wired (L6)
VAL-CO-007: Router empty flag (L9)
"""
from __future__ import annotations

from unittest.mock import patch


from cairn.compass.router import route_query
from cairn.graph import watcher
from cairn.okf.bundle import OKFBundle


class TestGitignoreCacheInvalidation:
    """VAL-CO-006: invalidate_gitignore_cache called on .gitignore changes."""

    def test_gitignore_change_invalidates_cache(self, fresh_db, tmp_path):
        """When a .gitignore file is in the changed set, cache is invalidated."""
        # Set up a fake repo with a .gitignore
        repo_dir = tmp_path / "test_repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()
        gitignore_path = repo_dir / ".gitignore"
        gitignore_path.write_text("node_modules/\nbuild/\n")

        # Pre-populate the cache with some gitignore rules
        from cairn.graph import scanner as scanner_mod
        import pathspec
        # Simulate what _load_gitignores would create
        spec = pathspec.PathSpec.from_lines("gitignore", ["node_modules/\nbuild/\n"])
        scanner_mod._gitignore_cache[str(repo_dir)] = [(str(repo_dir), spec)]

        # Verify cache is populated
        assert str(repo_dir) in scanner_mod._gitignore_cache
        assert len(scanner_mod._gitignore_cache[str(repo_dir)]) == 1

        # Mock reindex_paths to avoid actual scanning
        with patch("cairn.graph.incremental.reindex_paths") as mock_reindex:
            mock_reindex.return_value = {"reindexed": 0, "deleted": 0}

            # Call ensure_fresh_force with the .gitignore in changed set
            # First, make _detect_changed return our .gitignore file
            with patch.object(watcher, "_detect_changed") as mock_detect:
                mock_detect.return_value = [str(gitignore_path)]
                watcher.ensure_fresh_force(fresh_db, str(tmp_path))

        # The fix should call invalidate_gitignore_cache when .gitignore changes
        # which pops the cache key, so it should no longer exist or be empty
        assert str(repo_dir) not in scanner_mod._gitignore_cache, (
            "gitignore cache should be invalidated when .gitignore file changes"
        )


class TestRouterEmptyFlag:
    """VAL-CO-007: Router response includes explicit empty: bool flag."""

    def test_router_empty_flag_true_when_no_results(self, fresh_db, tmp_path):
        """When all layers return nothing, router response has empty=True."""
        # Create an empty bundle
        bundle = OKFBundle(tmp_path)

        # Query that should return nothing from any layer
        query = "nonexistent_symbol_12345_xyz"

        result = route_query(query, fresh_db, bundle)

        # Currently this will FAIL because 'empty' key is not set
        assert "empty" in result, "Router response should include 'empty' flag"
        assert result["empty"] is True, (
            "empty should be True when all layers return no results"
        )

    def test_router_empty_flag_false_when_results_exist(self, fresh_db, tmp_path):
        """When any layer returns results, router response has empty=False."""
        # Create a bundle with at least one concept
        bundle = OKFBundle(tmp_path)
        # Write a dummy concept so memory search has something
        from cairn.okf.concept import OKFConcept

        concept = OKFConcept(
            concept_id="memory/tribal/test",
            type="Memory",
            title="test memory",
        )
        bundle.write_concept(concept)

        # Query that should match something
        query = "test"

        result = route_query(query, fresh_db, bundle)

        # Currently this will FAIL because 'empty' key is not set
        assert "empty" in result, "Router response should include 'empty' flag"
        assert result["empty"] is False, (
            "empty should be False when any layer returns results"
        )
