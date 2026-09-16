"""Incremental synchronization command implementation."""
from __future__ import annotations

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
    except Exception:
        return []

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


def _run_sync(conn, workspace, changed, db):
    """Reindex changed files, refresh dataflow, and record the sync run."""
    from ...graph.incremental import reindex_paths
    from .. import display
    import time

    sync_started = time.time()
    with display.progress_bar(
        description=f"Syncing {len(changed)} files",
        total=len(changed),
        unit="files",
    ) as bar:
        # reindex_paths doesn't expose per-file progress; show an
        # indeterminate bar that completes when it returns.
        result = reindex_paths(conn, workspace, changed)
        bar.update(bar._cg_task_id, completed=len(changed))

    # Refresh the dataflow index if any files were reindexed.
    if result["reindexed"]:
        try:
            from ...graph.dataflow import build_dataflow_index

            df_count = build_dataflow_index(conn)
            display.dim(f"  dataflow index: {df_count:,} symbols")
        except Exception:
            pass
    display.success(
        f"Synced: {result['reindexed']} reindexed, {result['deleted']} deleted"
    )
    if result["errors"]:
        display.warning(f"{len(result['errors'])} errors")
        for error in result["errors"][:5]:
            display.dim(f"  {error}")

    # Persist a 'sync' build_runs row (best-effort; record_build_run
    # swallows all errors). reindex_paths returns reindexed/deleted only;
    # resolution mix / parse-error breakdown / phase_timings stay NULL
    # (the sync path has no scan/parse/resolve phase contract). Recorded
    # in the sync command rather than shared reindex_paths so `cairn
    # update` records its own 'incremental' row.
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
    """Manually re-index changed files (used when watcher is disabled or for scripting).

    Detects files changed since last index via size/mtime comparison and
    re-indexes them. Equivalent to what the watcher does automatically.
    """
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
