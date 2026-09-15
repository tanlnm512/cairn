"""Graph bootstrap for Git linked worktrees."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .. import paths


def linked_worktree_main(worktree: str | Path) -> Path | None:
    """Return the main checkout for a linked worktree, else None."""
    pointer = Path(worktree) / ".git"
    if not pointer.is_file():
        return None
    try:
        text = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = (Path(worktree) / text[len("gitdir:") :].strip()).resolve()
    if gitdir.parent.name != "worktrees":
        return None
    main_gitdir = gitdir.parent.parent
    main = main_gitdir.parent
    if not (main / ".git").exists():
        return None
    return main


def _copy_store_read_only(main_db: Path, worktree_db: Path) -> None:
    worktree_db.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"{main_db.resolve().as_uri()}?immutable=1", uri=True)
    destination = sqlite3.connect(str(worktree_db))
    try:
        source.backup(destination)
        destination.commit()
    except BaseException:
        destination.close()
        source.close()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{worktree_db}{suffix}").unlink(missing_ok=True)
        raise
    finally:
        destination.close()
        source.close()


def _retarget_repo_ids(conn: sqlite3.Connection, main: Path, worktree: Path) -> None:
    replacements = {
        row[0]: f"{worktree.name}/{row[0][len(main.name) + 1:]}"
        if row[0].startswith(f"{main.name}/")
        else worktree.name
        for row in conn.execute("SELECT id FROM repos").fetchall()
    }
    for old_id, new_id in replacements.items():
        conn.execute("UPDATE repos SET id = ? WHERE id = ?", (new_id, old_id))
        for table in ("files", "parse_errors", "skipped_files", "pending_sync"):
            conn.execute(
                f"UPDATE {table} SET repo_id = ? WHERE repo_id = ?", (new_id, old_id)
            )
    conn.commit()


def prepare_worktree_graph(worktree: str | Path) -> bool:
    """Seed a new worktree store from its main checkout and repair drift."""
    worktree = Path(worktree).resolve()
    main = linked_worktree_main(worktree)
    if main is None:
        return False

    main_store = paths.resolve_store(main)
    worktree_store = paths.resolve_store(worktree)
    if worktree_store.db.exists() or not main_store.db.exists():
        return False
    if worktree_store.db.resolve() == main_store.db.resolve():
        return False

    started = time.time()
    _copy_store_read_only(main_store.db, worktree_store.db)
    conn = sqlite3.connect(str(worktree_store.db))
    conn.row_factory = sqlite3.Row
    try:
        _retarget_repo_ids(conn, main, worktree)
        from .watcher import refresh_for_query

        report = refresh_for_query(conn, str(worktree), repair=True)
    finally:
        conn.close()

    from .builder import record_build_run

    record_build_run(
        str(worktree_store.db),
        "incremental",
        started_at=started,
        duration_s=time.time() - started,
        files=len(report.drifted_paths),
    )
    return True
