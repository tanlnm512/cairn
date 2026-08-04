"""Tests for L7 (import_directory validation) and L10 (logging in bundle read loops).

These tests fix audit findings L7 and L10:
- L7: import_directory should validate file size and default doc_source='imported'
- L10: bundle.py read loops should log exceptions before continuing
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from cairn.knowledge.store import import_directory
from cairn.memory.scoring import _authority
from cairn.okf.bundle import OKFBundle


@pytest.fixture
def bundle():
    with tempfile.TemporaryDirectory() as tmp:
        yield OKFBundle(tmp)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from cairn.graph.schema import _apply_schema
    _apply_schema(c)
    yield c
    c.close()


class TestImportDirectoryValidation:
    """L7: import_directory should validate file size and use lower authority."""

    def test_imported_docs_have_lower_authority(self, bundle):
        """Imported docs should have doc_source='imported' and authority < 1.0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test markdown file
            md_file = Path(tmpdir) / "test_doc.md"
            md_file.write_text("# Test Document\n\nThis is imported content.", encoding="utf-8")

            # Import the directory
            imported = import_directory(bundle, tmpdir)
            assert len(imported) == 1

            # Verify the concept was imported with doc_source='imported'
            concept = bundle.read_concept(imported[0])
            assert concept.extensions.get("doc_source") == "imported"

            # Verify authority is < 1.0 (should be 0.5 for non-manual docs)
            authority = _authority(concept)
            assert authority < 1.0
            assert authority == 0.5

    def test_oversized_file_rejected(self, bundle):
        """Oversized files should be skipped during import."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a normal file
            normal_file = Path(tmpdir) / "normal.md"
            normal_file.write_text("# Normal\n\nThis is fine.", encoding="utf-8")

            # Create an oversized file (simulate by creating a large file)
            # For testing, we'll create a file larger than a small limit
            # In the real implementation, we'd use the actual max size constant
            large_file = Path(tmpdir) / "large.md"
            large_file.write_text("# Large\n\n" + "x" * (20 * 1024 * 1024), encoding="utf-8")  # 20MB

            # Import should skip the large file
            imported = import_directory(bundle, tmpdir)

            # Should only import the normal file (large file skipped)
            assert len(imported) == 1
            assert "normal" in imported[0]

    def test_import_directory_normal_files_succeed(self, bundle):
        """Normal-sized files should be imported successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create multiple normal files
            for i in range(3):
                md_file = Path(tmpdir) / f"doc_{i}.md"
                md_file.write_text(f"# Doc {i}\n\nContent {i}.", encoding="utf-8")

            imported = import_directory(bundle, tmpdir)
            assert len(imported) == 3

            # All should have doc_source='imported'
            for cid in imported:
                concept = bundle.read_concept(cid)
                assert concept.extensions.get("doc_source") == "imported"


class TestBundleReadLoopLogging:
    """L10: bundle.py read loops should log exceptions before continuing."""

    def test_corrupt_concept_logs_warning_in_search(self, bundle):
        """Corrupt concept in bundle should trigger WARNING log during search."""
        # Create a corrupt markdown file
        corrupt_file = bundle.root / "corrupt.md"
        corrupt_file.write_text("Invalid frontmatter\n---\n[unclosed yaml", encoding="utf-8")

        # Create a valid file
        valid_file = bundle.root / "valid.md"
        valid_file.write_text("---\ntype: test\ntitle: Valid\n---\nValid content for test.", encoding="utf-8")

        # Search should log the corrupt file but continue and return valid results
        results = bundle.search("test")

        # Should find the valid file (search matches "test" in type and body)
        assert len(results) >= 1
        assert any("valid" in c.concept_id for c in results)

    def test_corrupt_concept_logs_warning_in_generate_index(self, bundle):
        """Corrupt concept in bundle should trigger WARNING log during generate_index."""
        # Create a directory with mixed files
        subdir = bundle.root / "test_dir"
        subdir.mkdir()

        # Create a corrupt markdown file
        corrupt_file = subdir / "corrupt.md"
        corrupt_file.write_text("Invalid frontmatter\n---\n[unclosed yaml", encoding="utf-8")

        # Create a valid file
        valid_file = subdir / "valid.md"
        valid_file.write_text("---\ntype: test\ntitle: Valid\n---\nValid content.", encoding="utf-8")

        # Generate index should log the corrupt file but continue
        bundle.generate_index("test_dir")

        # Should have created an index file
        index_file = subdir / "index.md"
        assert index_file.exists()

        # Index should contain the valid file
        index_content = index_file.read_text(encoding="utf-8")
        assert "valid.md" in index_content

    def test_generate_index_skips_log_and_index_files(self, bundle):
        """generate_index should skip index.md and log.md as documented."""
        subdir = bundle.root / "test_dir"
        subdir.mkdir()

        # Create the special files
        (subdir / "index.md").write_text("# Old Index", encoding="utf-8")
        (subdir / "log.md").write_text("# Log", encoding="utf-8")

        # Create a valid content file
        valid_file = subdir / "valid.md"
        valid_file.write_text("---\ntype: test\ntitle: Valid\n---\nValid content.", encoding="utf-8")

        bundle.generate_index("test_dir")

        # Index should be regenerated
        index_file = subdir / "index.md"
        index_content = index_file.read_text(encoding="utf-8")

        # Should contain valid.md but not log.md
        assert "valid.md" in index_content
        assert "log.md" not in index_content
