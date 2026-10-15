"""Contracts for the ``cairn sync`` command's change detection and derived-index refresh."""
from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import click
import pytest

from cairn.graph.schema import init_db

# cairn.cli.system re-exports the `sync` Command over the submodule attribute,
# so tests reach the module through sys.modules.
sync_mod = importlib.import_module("cairn.cli.system.sync")


def _conn(tmp_path):
    return init_db(str(tmp_path / "store.db"))


def _patch_incremental(monkeypatch, calls, *, dataflow_built=True, derived_errors=()):
    import cairn.graph.incremental as incremental

    monkeypatch.setattr(
        incremental,
        "_capture_derived_prestate",
        lambda conn, workspace, paths: calls.append("prestate")
        or {"dataflow_built": dataflow_built, "closure_built": True},
    )
    monkeypatch.setattr(
        incremental,
        "reindex_paths",
        lambda conn, workspace, paths: calls.append("reindex")
        or {
            "reindexed": 1,
            "deleted": 0,
            "embedded_symbols": 0,
            "deferred_embeds": 0,
            "errors": [],
        },
    )
    monkeypatch.setattr(
        incremental,
        "_maintain_derived_indexes",
        lambda conn, workspace, paths, pre: calls.append("maintain") or list(derived_errors),
    )
    monkeypatch.setattr(
        incremental,
        "_rebuild_derived_indexes",
        lambda conn, closure_built: calls.append("rebuild") or list(derived_errors),
    )


def _patch_overlay(monkeypatch, calls):
    import cairn.graph.builder as builder

    monkeypatch.setattr(
        builder,
        "_apply_scip_overlay",
        lambda conn, workspace, repos, verbose, generate_missing: calls.append("overlay") or None,
    )


def test_run_sync_refreshes_overlay_then_maintains_derived(tmp_path, monkeypatch):
    """Sync must re-apply the SCIP overlay and maintain the closure, prestate before reindex."""
    calls: list[str] = []
    _patch_incremental(monkeypatch, calls)
    _patch_overlay(monkeypatch, calls)
    conn = _conn(tmp_path)
    try:
        sync_mod._run_sync(conn, str(tmp_path), ["src/a.py"], str(tmp_path / "store.db"))
    finally:
        conn.close()

    assert calls == ["prestate", "reindex", "overlay", "maintain"]


def test_run_sync_rebuilds_absent_derived_indexes(tmp_path, monkeypatch):
    """With no prior dataflow index, sync rebuilds instead of maintaining."""
    calls: list[str] = []
    _patch_incremental(monkeypatch, calls, dataflow_built=False)
    _patch_overlay(monkeypatch, calls)
    conn = _conn(tmp_path)
    try:
        sync_mod._run_sync(conn, str(tmp_path), ["src/a.py"], str(tmp_path / "store.db"))
    finally:
        conn.close()

    assert "rebuild" in calls
    assert "maintain" not in calls


def test_run_sync_surfaces_derived_errors(tmp_path, monkeypatch):
    """A failed derived refresh is reported, never swallowed."""
    import cairn.cli.display as display_mod
    calls: list[str] = []
    _patch_incremental(monkeypatch, calls, derived_errors=["dataflow: boom"])
    _patch_overlay(monkeypatch, calls)
    warnings: list[str] = []
    monkeypatch.setattr(display_mod, "warning", warnings.append)
    conn = _conn(tmp_path)
    try:
        sync_mod._run_sync(conn, str(tmp_path), ["src/a.py"], str(tmp_path / "store.db"))
    finally:
        conn.close()

    assert any("1 errors" in message for message in warnings)


def test_repo_file_changes_raises_when_files_table_unreadable():
    """An unreadable files table is a loud error, not a silent no-changes sync."""
    from cairn.cli.system.sync import _repo_file_changes

    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(click.ClickException, match="cannot read indexed files"):
            _repo_file_changes(conn, "/nonexistent-workspace", Path("/nonexistent-workspace/repo"))
    finally:
        conn.close()
