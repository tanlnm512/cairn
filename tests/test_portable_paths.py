"""Portable .kg paths: stored paths are repo-relative so the DB file is
shareable across machines.

Verifies the build-time contract (files.path / repos.path /
parse_errors.file_path / skipped_files.path are repo-relative, no absolute
machine prefix) and the read-time contract (explore / find_definition /
embeddings resolve those relative paths back to absolute via
resolve_file_path, so source reading works on the machine that owns the
files). The cross-machine scenario is simulated by moving the .kg under a
different workspace path and asserting reads still resolve.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from cairn.graph.builder import build_graph
from cairn.graph.scanner import resolve_file_path, resolve_repo_path
from cairn.graph.schema import get_db


FIXTURE_FILES = {
    "Simple.kt": (
        "package com.example\n\n"
        "class Simple {\n"
        "    fun doWork() {}\n"
        "}\n"
    ),
}


def _make_single_repo_workspace(tmp_path: Path, name: str) -> Path:
    """Create a single-repo workspace (workspace root IS the git repo)."""
    workspace = tmp_path / name
    (workspace / ".git").mkdir(parents=True)
    for fname, contents in FIXTURE_FILES.items():
        (workspace / fname).write_text(contents)
    return workspace


def _make_multi_repo_workspace(tmp_path: Path, name: str) -> Path:
    """Create a multi-repo workspace (child dirs are the git repos)."""
    workspace = tmp_path / name
    repo = workspace / "demo"
    (repo / ".git").mkdir(parents=True)
    for fname, contents in FIXTURE_FILES.items():
        (repo / fname).write_text(contents)
    return workspace


# ---------------------------------------------------------------------------
# Build-time contract: stored paths are repo-relative (no absolute prefix).
# ---------------------------------------------------------------------------

def test_files_path_is_repo_relative_single_repo(tmp_path):
    """Regression: files.path must be stored repo-relative (no absolute machine
    prefix) so the .kg is portable. See BUGS.md#2026-08-06/portable-path-stale-comments."""
    workspace = _make_single_repo_workspace(tmp_path, "single")
    db_path = str(tmp_path / "single.db")
    build_graph(workspace=str(workspace), db_path=db_path)

    conn = get_db(db_path, read_only=True)
    rows = conn.execute("SELECT path FROM files").fetchall()
    conn.close()
    assert rows, "expected at least one indexed file"
    for r in rows:
        p = r["path"]
        assert not Path(p).is_absolute(), f"files.path should be relative, got {p}"
        assert p == "Simple.kt", f"expected repo-relative 'Simple.kt', got {p}"


def test_files_path_is_repo_relative_multi_repo(tmp_path):
    """Regression: same portability invariant as the single-repo case, under a
    multi-repo workspace. See BUGS.md#2026-08-06/portable-path-stale-comments."""
    workspace = _make_multi_repo_workspace(tmp_path, "multi")
    db_path = str(tmp_path / "multi.db")
    build_graph(workspace=str(workspace), db_path=db_path)

    conn = get_db(db_path, read_only=True)
    rows = conn.execute("SELECT path FROM files").fetchall()
    conn.close()
    assert rows
    for r in rows:
        p = r["path"]
        assert not Path(p).is_absolute(), f"files.path should be relative, got {p}"


def test_repos_path_is_workspace_relative(tmp_path):
    """Regression: repos.path must be workspace-relative too, not absolute.
    See BUGS.md#2026-08-06/portable-path-stale-comments."""
    workspace = _make_multi_repo_workspace(tmp_path, "multi")
    db_path = str(tmp_path / "multi.db")
    build_graph(workspace=str(workspace), db_path=db_path)

    conn = get_db(db_path, read_only=True)
    rows = conn.execute("SELECT id, path FROM repos").fetchall()
    conn.close()
    assert rows
    for r in rows:
        p = r["path"]
        assert not Path(p).is_absolute(), f"repos.path should be relative, got {p}"


def test_parse_errors_and_skipped_paths_are_relative(tmp_path):
    """Regression: parse_errors.file_path and skipped_files.path must also be
    stored relative, or cross-machine reads of error/skip rows break.
    See BUGS.md#2026-08-06/portable-path-stale-comments."""
    # A file with an unknown extension is skipped; a syntactically-broken file
    # is a parse error. Both should store repo-relative paths.
    workspace = tmp_path / "ws"
    (workspace / ".git").mkdir(parents=True)
    (workspace / "Broken.kt").write_text("class { !!! broken !!!")  # parse error
    (workspace / "ignored.log").write_text("noise")  # not a source ext -> skipped
    db_path = str(tmp_path / "pe.db")
    build_graph(workspace=str(workspace), db_path=db_path)

    conn = get_db(db_path, read_only=True)
    for r in conn.execute("SELECT file_path FROM parse_errors").fetchall():
        assert not Path(r["file_path"]).is_absolute(), (
            f"parse_errors.file_path should be relative, got {r['file_path']}"
        )
    for r in conn.execute("SELECT path FROM skipped_files").fetchall():
        assert not Path(r["path"]).is_absolute(), (
            f"skipped_files.path should be relative, got {r['path']}"
        )
    conn.close()


# ---------------------------------------------------------------------------
# Read-time resolution: relative paths resolve back to absolute for disk I/O.
# ---------------------------------------------------------------------------

def test_resolve_file_path_roundtrip(tmp_path):
    """resolve_file_path reconstructs the absolute path from a stored relative one."""
    workspace = _make_multi_repo_workspace(tmp_path, "rt")
    db_path = str(tmp_path / "rt.db")
    build_graph(workspace=str(workspace), db_path=db_path)

    conn = get_db(db_path, read_only=True)
    row = conn.execute(
        "SELECT f.path, f.repo_id FROM files f LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None

    abs_path = resolve_file_path(str(workspace), row["repo_id"], row["path"])
    assert Path(abs_path).is_absolute()
    assert Path(abs_path).exists(), f"resolved path should exist on disk: {abs_path}"


def test_resolve_file_path_passes_legacy_absolute_through():
    """A legacy absolute stored path is returned unchanged (backward compat)."""
    assert resolve_file_path("/some/ws", "repo", "/abs/legacy/path.kt") == "/abs/legacy/path.kt"


# ---------------------------------------------------------------------------
# Cross-machine simulation: copy the .kg under a different workspace path and
# verify reads still resolve to the (moved) files.
# ---------------------------------------------------------------------------

def test_cross_machine_copy_resolves(tmp_path, monkeypatch):
    """The .kg is portable: after copying it + the source to a new location,
    reads resolve via the new workspace root."""
    # Build on "machine A".
    machine_a = tmp_path / "machineA"
    workspace_a = _make_single_repo_workspace(machine_a, "proj")
    db_path_a = str(machine_a / "graph.db")
    build_graph(workspace=str(workspace_a), db_path=db_path_a)

    # Copy the DB and the source tree to "machine B" at a different path.
    machine_b = tmp_path / "machineB"
    workspace_b = machine_b / "proj"
    shutil.copytree(workspace_a, workspace_b)
    db_path_b = str(machine_b / "graph.db")
    shutil.copy(db_path_a, db_path_b)

    # The DB stores paths relative to machine A's repo root, but the files now
    # live under machine B. Resolution must land on machine B's copy.
    conn = get_db(db_path_b, read_only=True)
    row = conn.execute("SELECT f.path, f.repo_id FROM files f LIMIT 1").fetchone()
    conn.close()

    resolved_b = resolve_file_path(str(workspace_b), row["repo_id"], row["path"])
    assert str(workspace_a) not in resolved_b, (
        "resolved path should not carry machine A's absolute prefix"
    )
    assert resolved_b.startswith(str(workspace_b)), (
        f"resolved path should be under machine B's workspace: {resolved_b}"
    )
    assert Path(resolved_b).exists(), "file should be reachable on machine B"


def test_explore_reads_source_after_move(tmp_path, monkeypatch):
    """explore._read_source_spans opens files by resolving stored relative
    paths against the current workspace. Simulate a move by pointing
    CAIRN_WORKSPACE at the new location."""
    machine_a = tmp_path / "origin"
    workspace_a = _make_single_repo_workspace(machine_a, "proj")
    db_path = str(machine_a / "graph.db")
    build_graph(workspace=str(workspace_a), db_path=db_path)

    # Move the source tree (the DB stays put, like a shared .kg copy).
    machine_b = tmp_path / "dest"
    workspace_b = machine_b / "proj"
    shutil.copytree(workspace_a, workspace_b)

    # Point resolution at the new location.
    monkeypatch.setenv("CAIRN_WORKSPACE", str(workspace_b))
    from cairn.graph.explore import _read_source_spans

    conn = get_db(db_path, read_only=True)
    sym = conn.execute("SELECT s.id FROM symbols s LIMIT 1").fetchone()
    assert sym is not None
    out = _read_source_spans(conn, [sym["id"]], budget=100)
    conn.close()
    # Source was read successfully from the new location.
    assert out, "expected source spans to be read after the move"
    for entries in out.values():
        assert any(e["lines"] for e in entries), "expected non-empty source lines"
