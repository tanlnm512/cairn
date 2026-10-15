"""Incremental synchronization command implementation."""
from __future__ import annotations

import sqlite3

import click
from pathlib import Path

from ..main import DEFAULT_DB_PATH, get_db, main, scanner_mod


def _repo_file_changes(conn, workspace, repo_path):
    """Return files in one repository whose stored path, size, or mtime changed."""
    from ...graph import scanner as scanner_mod

    repo_name = scanner_mod.repository_id(repo_path)
    try:
        file_rows = conn.execute(
            "SELECT path, size, mtime FROM files WHERE repo_id = ?",
            (repo_name,),
        ).fetchall()
    except sqlite3.Error as e:
        raise click.ClickException(
            f"cannot read indexed files for repo '{repo_name}': {e}"
        ) from e

    changed = []
    existing = set()
    for row in file_rows:
        existing.add(row["path"])
        # files.path is repo-relative; resolve to absolute via the
        # single chokepoint for stat.
        path = Path(scanner_mod.resolve_file_path(workspace, repo_name, row["path"]))
        try:
            stat_result = path.stat()
        except OSError:
            changed.append(str(path))
            continue
        if stat_result.st_size != (row["size"] or 0):
            changed.append(str(path))
        elif abs(stat_result.st_mtime - (row["mtime"] or 0.0)) > 0.5:
            changed.append(str(path))

    # Detect new source files. Storage is repo-relative; the scanner
    # yields absolute, so compare on the relative form.
    for source in scanner_mod.iter_source_files(repo_path):
        relative = (
            str(source.relative_to(repo_path))
            if str(source).startswith(str(repo_path))
            else str(source)
        )
        if relative not in existing and str(source) not in existing:
            changed.append(str(source))
    return changed


def _collect_changed_files(conn, workspace):
    changed = []
    from ...graph import scanner as scanner_mod

    for repo_path in scanner_mod.discover_repos(workspace):
        changed.extend(_repo_file_changes(conn, workspace, repo_path))
    return changed


def _refresh_derived_indexes(conn, workspace, changed, pre):
    """Re-apply the SCIP overlay, then maintain or rebuild closure and dataflow; return errors."""
    from ...graph import builder
    from ...graph.incremental import (
        _maintain_derived_indexes,
        _rebuild_derived_indexes,
    )

    errors: list[str] = []
    repos = [scanner_mod.repository_id(r) for r in scanner_mod.discover_repos(workspace)]
    try:
        # Closure and dataflow must see the post-overlay edge population.
        builder._apply_scip_overlay(
            conn,
            workspace,
            {r: None for r in repos},
            verbose=False,
            generate_missing=False,
        )
    except Exception as e:
        errors.append(f"scip_overlay: {e}")
    if pre["dataflow_built"]:
        return errors + _maintain_derived_indexes(conn, workspace, changed, pre)
    return errors + _rebuild_derived_indexes(conn, pre["closure_built"])


def _run_sync(conn, workspace, changed, db):
    """Reindex changed files, refresh derived indexes, and record the sync run."""
    from ...graph.incremental import _capture_derived_prestate, reindex_paths
    from .. import display
    import time

    sync_started = time.time()
    # Capture before reindex: the affected sets are the pre-update graph.
    pre = _capture_derived_prestate(conn, workspace, changed)
    with display.progress_bar(
        description=f"Syncing {len(changed)} files",
        total=len(changed),
        unit="files",
    ) as bar:
        # reindex_paths doesn't expose per-file progress; show an
        # indeterminate bar that completes when it returns.
        result = reindex_paths(conn, workspace, changed)
        bar.update(bar._cg_task_id, completed=len(changed))

    errors = list(result["errors"])
    if result["reindexed"] or result["deleted"]:
        errors.extend(_refresh_derived_indexes(conn, workspace, changed, pre))

    display.success(
        f"Synced: {result['reindexed']} reindexed, {result['deleted']} deleted"
    )
    if errors:
        display.warning(f"{len(errors)} errors")
        for error in errors[:5]:
            display.dim(f"  {error}")

    # Best-effort 'sync' build_runs row: only reindexed/deleted counts are
    # meaningful; scan/parse/resolve columns stay NULL.
    from ...graph.builder import record_build_run

    record_build_run(
        db,
        "sync",
        started_at=sync_started,
        duration_s=time.time() - sync_started,
        files=result["reindexed"],
        skipped=result["deleted"],
    )


# --------------------------------------------------------------------------
# cairn sync (manual re-index escape hatch)
# --------------------------------------------------------------------------
@main.command()
@click.option("--workspace", default=scanner_mod.DEFAULT_WORKSPACE)
@click.option("--db", default=str(DEFAULT_DB_PATH))
def sync(workspace, db):
    """Manually re-index size/mtime-drifted files — what the watcher does, on demand."""
    from .. import display

    conn = get_db(db)
    try:
        changed = _collect_changed_files(conn, workspace)
        if not changed:
            display.success("No changes detected. Graph is up to date.")
            return
        _run_sync(conn, workspace, changed, db)
    finally:
        conn.close()


# Doctor performs read-only health checks. Optional-backend absence is a WARN;
# integrity or store-open failures are FAILs.
