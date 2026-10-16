"""Worktree store seeding: repo-id retargeting and failure cleanup."""
from __future__ import annotations

import subprocess
import sqlite3

import pytest

from cairn.graph.builder import build_graph
from cairn.graph.worktree import prepare_worktree_graph


def _git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@pytest.fixture()
def multi_repo_workspace(tmp_path):
    """Workspace whose store holds the main repo plus a sibling bare repo id."""
    ws = tmp_path / "ws"
    ws.mkdir()
    main = ws / "mainrepo"
    main.mkdir()
    (main / ".git").mkdir()
    (main / "code.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    sibling = ws / "sibling"
    sibling.mkdir()
    (sibling / ".git").mkdir()
    (sibling / "other.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    from cairn import paths

    db = paths.resolve_store(str(main)).db
    build_graph(workspace=str(ws), db_path=str(db), verbose=False)
    return ws, main


def test_seeding_retargets_main_and_drops_sibling_repos(tmp_path, multi_repo_workspace):
    ws, main = multi_repo_workspace
    _git(main, "init", "-q", "--initial-branch=main")
    _git(main, "add", ".")
    _git(main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x")
    wt = ws / "wt"
    _git(main, "worktree", "add", "-q", str(wt))

    assert prepare_worktree_graph(str(wt)) is True

    from cairn import paths

    wt_store = paths.resolve_store(str(wt)).db
    assert wt_store.exists(), "worktree store should be seeded"
    conn = sqlite3.connect(str(wt_store))
    try:
        repos = [r[0] for r in conn.execute("SELECT id FROM repos")]
        assert repos == ["wt"]
        orphan_files = conn.execute(
            "SELECT COUNT(*) FROM files WHERE repo_id = 'sibling'"
        ).fetchone()[0]
        assert orphan_files == 0
    finally:
        conn.close()
