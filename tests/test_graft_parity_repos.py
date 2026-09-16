"""Nested-repository discovery and linked-worktree graph seeding contracts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from cairn.graph.builder import build_graph
from cairn.graph.scanner import (
    discover_repos,
    repository_id,
    resolve_repo_path,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Cairn Test")


def _nested_workspace(tmp_path: Path) -> Path:
    dependency = tmp_path / "dependency"
    _init_repo(dependency)
    (dependency / "dep.py").write_text("def dependency_function():\n    return 1\n")
    _git(dependency, "add", ".")
    _git(dependency, "commit", "-m", "dependency")

    parent = tmp_path / "parent"
    _init_repo(parent)
    (parent / "parent.py").write_text("def parent_function():\n    return 1\n")
    _git(
        parent,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "../dependency",
        "libs/dependency",
    )

    child = parent / "nested" / "child"
    _init_repo(child)
    (child / "child.py").write_text("def child_function():\n    return 2\n")

    dependency_sha = _git(dependency, "rev-parse", "HEAD")
    _git(parent, "config", "-f", ".gitmodules", "submodule.missing.path", "missing/dep")
    _git(parent, "config", "-f", ".gitmodules", "submodule.missing.url", "../missing")
    _git(parent, "update-index", "--add", "--cacheinfo", f"160000,{dependency_sha},missing/dep")
    return parent


@contextmanager
def _build(parent: Path, db_path: Path):
    build_graph(workspace=str(parent), db_path=str(db_path), verbose=False)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _file_rows(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        (row["repo_id"], row["path"])
        for row in conn.execute("SELECT repo_id, path FROM files").fetchall()
    }


@pytest.fixture()
def nested_workspace(tmp_path: Path) -> Path:
    return _nested_workspace(tmp_path)


def test_nested_repositories_are_excluded_by_default(
    tmp_path: Path, nested_workspace: Path
) -> None:
    parent = nested_workspace

    assert discover_repos(str(parent)) == [parent]

    with _build(parent, tmp_path / "default-off.db") as conn:
        assert _file_rows(conn) == {("parent", "parent.py")}


def test_opted_in_repositories_use_prefixed_ids(
    tmp_path: Path, nested_workspace: Path
) -> None:
    parent = nested_workspace
    (parent / "cairn.json").write_text(
        json.dumps({"include_nested_repos": True}), encoding="utf-8"
    )
    child = parent / "nested" / "child"
    dependency = parent / "libs" / "dependency"

    assert set(discover_repos(str(parent))) == {parent, child, dependency}
    assert resolve_repo_path(str(parent), "parent/nested/child") == child
    assert resolve_repo_path(str(parent), "parent/libs/dependency") == dependency

    with _build(parent, tmp_path / "opt-in.db") as conn:
        assert _file_rows(conn) == {
            ("parent", "parent.py"),
            ("parent/nested/child", "child.py"),
            ("parent/libs/dependency", "dep.py"),
        }


def test_nested_repository_drift_uses_the_prefixed_repo_id(
    tmp_path: Path, nested_workspace: Path
) -> None:
    from cairn.graph.watcher import refresh_for_query

    parent = nested_workspace
    child = parent / "nested" / "child"
    (parent / "cairn.json").write_text(
        json.dumps({"include_nested_repos": True}), encoding="utf-8"
    )

    with _build(parent, tmp_path / "nested-drift.db") as conn:
        (child / "child.py").write_text(
            "def refreshed_child():\n    return 2\n", encoding="utf-8"
        )
        report = refresh_for_query(conn, str(parent), repair=True)

        assert report.repaired is True
        assert [Path(path).name for path in report.drifted_paths] == [
            "child.py"
        ]
        names = {
            row[0]
            for row in conn.execute(
                "SELECT s.name FROM symbols s "
                "JOIN files f ON f.id = s.file_id "
                "WHERE f.repo_id = 'parent/nested/child' "
                "AND s.kind != 'module'"
            ).fetchall()
        }

    assert names == {"refreshed_child"}


def test_nested_repository_incremental_update_uses_the_prefixed_repo_id(
    tmp_path: Path, nested_workspace: Path
) -> None:
    from cairn.graph.incremental import incremental_update

    parent = nested_workspace
    child = parent / "nested" / "child"
    (parent / "cairn.json").write_text(
        json.dumps({"include_nested_repos": True}), encoding="utf-8"
    )
    db_path = tmp_path / "nested-incremental.db"

    with _build(parent, db_path) as conn:
        (child / "child.py").write_text(
            "def incrementally_refreshed():\n    return 2\n",
            encoding="utf-8",
        )
        summary = incremental_update(workspace=str(parent), db_path=str(db_path))
        names = {
            row[0]
            for row in conn.execute(
                "SELECT s.name FROM symbols s "
                "JOIN files f ON f.id = s.file_id "
                "WHERE f.repo_id = 'parent/nested/child' "
                "AND s.kind != 'module'"
            ).fetchall()
        }

    assert summary["files_reindexed"] == 1
    assert summary["errors"] == []
    assert names == {"incrementally_refreshed"}


def test_nested_repository_hook_uses_the_prefixed_repo_id(
    nested_workspace: Path,
) -> None:
    from cairn.hooks.git_hooks import install_hooks, uninstall_hooks

    parent = nested_workspace
    child = parent / "nested" / "child"
    (parent / "cairn.json").write_text(
        json.dumps({"include_nested_repos": True}), encoding="utf-8"
    )
    child_id = repository_id(
        next(path for path in discover_repos(str(parent)) if path == child)
    )

    assert child_id == "parent/nested/child"
    assert install_hooks([child_id], str(parent)) == [child_id]
    hook_path = child / ".git" / "hooks" / "post-commit"
    assert '--repo "parent/nested/child"' in hook_path.read_text(
        encoding="utf-8"
    )
    assert uninstall_hooks([child_id], str(parent)) == [child_id]


def test_uninitialized_submodule_is_skipped_when_opted_in(
    tmp_path: Path, nested_workspace: Path
) -> None:
    parent = nested_workspace
    (parent / "cairn.json").write_text(
        json.dumps({"include_nested_repos": True}), encoding="utf-8"
    )

    discovered = discover_repos(str(parent))
    assert set(discovered) == {
        parent,
        parent / "nested" / "child",
        parent / "libs" / "dependency",
    }
    assert all("missing" not in path.parts for path in discovered)

    with _build(parent, tmp_path / "uninitialized.db") as conn:
        repo_ids = {
            row[0] for row in conn.execute("SELECT id FROM repos").fetchall()
        }
        assert "parent/missing/dep" not in repo_ids


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.usefixtures("hash_backend")
def test_linked_worktree_seeds_and_refreshes_without_mutating_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cairn import paths

    main = tmp_path / "main"
    _init_repo(main)
    (main / "stable.py").write_text("def stable_function():\n    return 1\n")
    (main / "app.py").write_text("def original_function():\n    return 2\n")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "main")
    worktree = tmp_path / "worktree"
    _git(main, "worktree", "add", "-b", "worktree", str(worktree))

    monkeypatch.setenv("CAIRN_WORKSPACE", str(main))
    monkeypatch.setenv("CAIRN_SESSION", "main-session")
    build_graph(workspace=str(main), verbose=False)
    main_store = paths.resolve_store(main).home
    worktree_store = paths.resolve_store(worktree).home
    main_digest = _tree_digest(main_store)
    assert worktree_store.exists() is False

    (worktree / "app.py").write_text(
        "def drifted_function():\n    return 3\n", encoding="utf-8"
    )
    monkeypatch.setenv("CAIRN_WORKSPACE", str(worktree))
    monkeypatch.setenv("CAIRN_SESSION", "worktree-session")
    from cairn.cli import main

    result = CliRunner().invoke(
        main,
        [
            "build",
            "--workspace",
            str(worktree),
            "--db",
            str(worktree_store / ".kg"),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output

    assert _tree_digest(main_store) == main_digest
    worktree_conn = sqlite3.connect(str(worktree_store / ".kg"))
    worktree_conn.row_factory = sqlite3.Row
    try:
        sessions = {
            row[0]
            for row in worktree_conn.execute(
                "SELECT session_id FROM build_runs"
            ).fetchall()
        }
        symbol_names = {
            row[0]
            for row in worktree_conn.execute(
                "SELECT name FROM symbols WHERE kind != 'module'"
            ).fetchall()
        }
    finally:
        worktree_conn.close()

    assert sessions == {"main-session", "worktree-session"}
    assert {"stable_function", "drifted_function"} <= symbol_names
    assert "original_function" not in symbol_names
