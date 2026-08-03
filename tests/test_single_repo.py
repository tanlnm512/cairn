"""Tests for single-repo workspace support.

When ``cg init`` is run inside a single git repo (the most common case),
the workspace root itself is the repo — there are no child directories
with ``.git``.  These tests verify that discover_repos, resolve_repo_path,
infer_repo_for_path, and related functions handle this case correctly.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from codegraph.graph.scanner import (
    discover_repos,
    infer_repo_for_path,
    is_single_repo_workspace,
    resolve_repo_path,
)


class TestDiscoverReposSingleRepo:
    """discover_repos returns the workspace root when it is the only git repo."""

    def test_single_repo_workspace(self):
        """Workspace root has .git, no child repos -> returns [root]."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            repos = discover_repos(str(root))
            assert len(repos) == 1
            assert repos[0] == root

    def test_single_repo_with_source_files(self):
        """Single repo with source files is still discovered correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("def hello(): pass")
            repos = discover_repos(str(root))
            assert len(repos) == 1
            assert repos[0] == root

    def test_multi_repo_workspace_unaffected(self):
        """Workspace with child repos still returns only children (not root)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()  # root is also a git repo
            repo_a = root / "repo-a"
            repo_a.mkdir()
            (repo_a / ".git").mkdir()
            repo_b = root / "repo-b"
            repo_b.mkdir()
            (repo_b / ".git").mkdir()
            repos = discover_repos(str(root))
            assert len(repos) == 2
            assert repo_a in repos
            assert repo_b in repos
            # Root should NOT be included when there are child repos
            assert root not in repos

    def test_non_git_workspace(self):
        """Workspace without .git and no child repos -> returns []."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repos = discover_repos(str(root))
            assert repos == []

    def test_child_repo_without_root_git(self):
        """Child repos are discovered even when workspace root has no .git."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_a = root / "repo-a"
            repo_a.mkdir()
            (repo_a / ".git").mkdir()
            repos = discover_repos(str(root))
            assert len(repos) == 1
            assert repos[0] == repo_a


class TestIsSingleRepoWorkspace:
    """is_single_repo_workspace correctly distinguishes single vs multi-repo."""

    def test_single_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            assert is_single_repo_workspace(str(root)) is True

    def test_multi_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            child = root / "child"
            child.mkdir()
            (child / ".git").mkdir()
            assert is_single_repo_workspace(str(root)) is False

    def test_no_git(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert is_single_repo_workspace(tmpdir) is False


class TestResolveRepoPath:
    """resolve_repo_path maps repo name to filesystem path correctly."""

    def test_single_repo(self):
        """In single-repo mode, resolve returns the workspace root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            result = resolve_repo_path(str(root), root.name)
            assert result == root

    def test_multi_repo(self):
        """In multi-repo mode, resolve returns workspace/child."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            child = root / "child"
            child.mkdir()
            (child / ".git").mkdir()
            result = resolve_repo_path(str(root), "child")
            assert result == child


class TestInferRepoForPath:
    """infer_repo_for_path correctly identifies repo name for any file path."""

    def test_single_repo(self):
        """In single-repo mode, returns the workspace directory name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".git").mkdir()
            file_path = str(root / "src" / "main.py")
            repo = infer_repo_for_path(file_path, str(root))
            assert repo == root.name

    def test_multi_repo(self):
        """In multi-repo mode, returns the first path component."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            child = root / "child"
            child.mkdir()
            (child / ".git").mkdir()
            file_path = str(child / "src" / "main.py")
            repo = infer_repo_for_path(file_path, str(root))
            assert repo == "child"

    def test_path_outside_workspace(self):
        """Path outside workspace returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
        with tempfile.TemporaryDirectory() as other_dir:
            repo = infer_repo_for_path(str(Path(other_dir) / "file.py"), str(root))
            assert repo is None
