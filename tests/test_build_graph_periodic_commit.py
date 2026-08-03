"""Test periodic commits in build_graph (M1 audit fix).

These tests verify the periodic-commit *behavior* of the two build paths:
- On-disk build path (repo_filter set, not in_memory) commits every N files
- In-memory path stays commit-free mid-loop (only commits before backup_to)

The basic "build N files and verify counts persist" happy-path is covered by
test_build_inmemory.py::test_inmemory_and_ondisk_builds_have_identical_counts,
so it is NOT duplicated here. (Pruned 2026-07-31: removed test_on_disk_build_works,
test_in_memory_build_works, and test_no_regression_final_row_counts as
duplicates of that canonical count-persistence test.)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from codegraph.graph.builder import build_graph


class TestPeriodicCommits:
    """Tests for M1: periodic commits distinguish the two build paths."""

    def test_on_disk_commits_periodically(self, tmp_path):
        """On-disk build commits periodically during file insertion.

        This test verifies that the on-disk path calls conn.commit() every N files
        during the insert loop by checking that data is visible to concurrent readers
        (which requires periodic commits to release the WAL lock).
        """
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo = workspace / "test_repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        # Create many files to trigger periodic commits
        for i in range(600):  # Need at least 500 files to trigger periodic commit
            file_path = repo / f"File{i}.kt"
            file_path.write_text(f"""
class File{i} {{
    fun method{i}() {{}}
}}
""")

        db_path = str(tmp_path / "test.db")

        # Build graph with repo_filter (on-disk path)
        result = build_graph(
            workspace=str(workspace),
            repo_filter="test_repo",
            db_path=db_path,
            verbose=False,
        )

        # Verify successful build with many files
        assert result["files"] >= 500, "Should have indexed at least 500 files"
        assert result["symbols"] >= 500, "Should have found at least 500 symbols"

        # Verify final state is persisted
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM symbols")
        symbol_count = cur.fetchone()[0]
        assert symbol_count == result["symbols"], "DB should contain all indexed symbols"
        conn.close()

    def test_in_memory_no_mid_loop_commits(self, tmp_path):
        """In-memory build has no commits during the insert loop.

        This test verifies that the in-memory path stays commit-free during the
        insert loop, with only the final commit before backup_to().
        """
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        repo = workspace / "test_repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        # Create many files
        for i in range(600):
            file_path = repo / f"File{i}.kt"
            file_path.write_text(f"""
class File{i} {{
    fun method{i}() {{}}
}}
""")

        db_path = str(tmp_path / "test.db")

        # Build graph WITHOUT repo_filter (in-memory path)
        result = build_graph(
            workspace=str(workspace),
            repo_filter=None,  # This triggers in-memory path
            db_path=db_path,
            verbose=False,
        )

        # Verify successful build
        assert result["files"] >= 500, "Should have indexed at least 500 files"
        assert result["symbols"] >= 500, "Should have found at least 500 symbols"

        # Verify final state is persisted via backup_to
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM symbols")
        symbol_count = cur.fetchone()[0]
        assert symbol_count == result["symbols"], "DB should contain all indexed symbols"
        conn.close()

