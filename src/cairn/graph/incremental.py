"""Incremental graph updates: git diff, reindex_paths, and file watcher sync.

`reindex_paths` is the common entry point for both `cairn update` (git-diff) and
the file watcher's debounced sync. The watcher lives in `watcher.py` and calls
`reindex_paths` from its flush loop; the MCP server uses it for catch-up at boot.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import List, Optional

from ..utils.git import _run_git
from . import builder
from . import scanner as scanner_mod
from .schema import get_db

logger = logging.getLogger(__name__)


def reindex_paths(
    conn: sqlite3.Connection,
    workspace: str,
    paths: list[str],
) -> dict:
    """Re-index a set of absolute file paths. Handles repo resolution, deletion,
    and resolver re-run. Returns {'reindexed': n, 'deleted': m, 'errors': [...]}.

    Both the git-diff incremental path and the file watcher call this. It is
    idempotent and safe to call from the watcher thread (as long as the watcher
    opens its own connection).
    """
    import uuid
    from datetime import datetime, timezone

    def _new_id() -> str:
        return uuid.uuid4().hex

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    reindexed = 0
    deleted = 0
    errors: list[str] = []

    # Group paths by repo for batched resolver re-run.
    repo_edges_by_file: dict[str, dict[str, list]] = {}

    for abs_path in paths:
        abs_path = str(abs_path)
        repo = scanner_mod.infer_repo_for_path(abs_path, workspace)
        if not repo:
            continue
        repo_path = str(scanner_mod.resolve_repo_path(workspace, repo))
        try:
            rel_path = str(Path(abs_path).relative_to(repo_path))
        except ValueError:
            continue

        cur = conn.cursor()
        # Find existing file row by PATH (not repo_id). build_graph stores
        # repo_id='' for the single-repo workspace case (workspace='.' has no
        # relative-path component to derive a repo name from), while
        # infer_repo_for_path here returns the directory name ('cairn').
        # Keying the lookup on repo_id would miss the row, skip the delete of
        # old symbols, and then the re-insert would FK-violate on the stale
        # symbols still present. The file's path is its stable identity; match
        # on that. Try the inferred repo first (fast path when it agrees),
        # then fall back to a path-only lookup.
        #
        # Path normalization: build stores RELATIVE paths (e.g. 'service.py'
        # relative to the repo root), while reindex_paths receives ABSOLUTE
        # paths from the change detector. Match on both forms: the exact
        # abs_path, the basename, and abs_path relative to the repo root.
        from pathlib import Path as _P
        rel_to_repo = str(_P(abs_path).relative_to(repo_path)) if abs_path.startswith(repo_path) else _P(abs_path).name

        row = cur.execute(
            "SELECT id, repo_id, path FROM files WHERE repo_id = ? AND path = ?",
            (repo, abs_path),
        ).fetchone()
        if row is None:
            row = cur.execute(
                "SELECT id, repo_id, path FROM files WHERE path = ?", (abs_path,)
            ).fetchone()
        if row is None:
            # Stored as repo-relative; abs_path didn't match.
            row = cur.execute(
                "SELECT id, repo_id, path FROM files WHERE path = ?", (rel_to_repo,)
            ).fetchone()
        # Use the STORED repo_id for downstream inserts so FK constraints on
        # files.repo_id -> repos.id hold (the inferred 'repo' may not exist in
        # repos at all). If no existing row, keep the inferred repo but ensure
        # a repos row exists (the insert_parsed_file path creates one).
        stored_repo = row["repo_id"] if row else repo
        stored_path = row["path"] if row else rel_to_repo  # normalize for delete
        file_id = row["id"] if row else None
        if file_id:
            cur.execute(
                "DELETE FROM edges WHERE source_id IN (SELECT id FROM symbols WHERE file_id = ?)",
                (file_id,),
            )
            # Cross-file edges/imports pointing INTO this file's symbols (as
            # target) aren't touched by the DELETEs above -- only this file's
            # own outgoing edges are. Null out those references before
            # deleting the symbols below, or the FK (edges.target_id /
            # imports.resolved_symbol_id -> symbols.id, no cascade) raises
            # "FOREIGN KEY constraint failed". Nulling (not deleting the edge
            # row) matches the existing unresolved-target convention --
            # target_name/imported_path survive for the resolver to re-link
            # once the symbol reappears.
            cur.execute(
                "UPDATE edges SET target_id = NULL WHERE target_id IN (SELECT id FROM symbols WHERE file_id = ?)",
                (file_id,),
            )
            cur.execute(
                "UPDATE imports SET resolved_symbol_id = NULL WHERE resolved_symbol_id IN (SELECT id FROM symbols WHERE file_id = ?)",
                (file_id,),
            )
            cur.execute("DELETE FROM imports WHERE file_id = ?", (file_id,))
            # Clear embeddings for these symbols BEFORE deleting them, or the
            # FK (embeddings.symbol_id -> symbols.id) blocks the symbol delete.
            # Re-embedding after reindex repopulates them; leaving stale rows
            # would point at the wrong symbol after the id is reused.
            try:
                cur.execute(
                    "DELETE FROM embeddings WHERE symbol_id IN "
                    "(SELECT id FROM symbols WHERE file_id = ?)",
                    (file_id,),
                )
            except sqlite3.OperationalError:
                logger.debug("embeddings table missing", exc_info=True)
            cur.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
            cur.execute("DELETE FROM parse_errors WHERE file_path = ?", (stored_path,))
            cur.execute("DELETE FROM files WHERE id = ?", (file_id,))
            conn.commit()

        # Check if file still exists on disk.
        if not Path(abs_path).exists():
            # Only count as deleted if we actually removed DB state (the file
            # was indexed before this call). A ghost path that was never in the
            # DB is a no-op, not a deletion.
            if file_id is not None:
                deleted += 1
            # Also remove from pending_sync if tracking.
            try:
                conn.execute("DELETE FROM pending_sync WHERE path = ?", (abs_path,))
                conn.commit()
            except sqlite3.OperationalError:
                logger.debug("pending_sync table missing", exc_info=True)
                pass  # table not present on this schema
            continue

        # Re-parse and insert.
        from .scanner import file_sha256, EXTENSION_MAP

        suffix = Path(abs_path).suffix
        if suffix not in EXTENSION_MAP:
            continue
        language = EXTENSION_MAP[suffix]

        file_hash = file_sha256(Path(abs_path))
        from .builder import get_parser, insert_parsed_file, insert_parse_error

        parser = get_parser(language)
        if not parser:
            continue

        try:
            pf = parser.parse(abs_path)
            name_to_symbol_ids = {}
            insert_parsed_file(
                cur, stored_repo, abs_path, language, file_hash, pf,
                name_to_symbol_ids, repo_edges_by_file,
            )
            conn.commit()
            reindexed += 1
            # Clear pending_sync for successfully reindexed files.
            try:
                conn.execute("DELETE FROM pending_sync WHERE path = ?", (abs_path,))
                conn.commit()
            except sqlite3.OperationalError:
                logger.debug("pending_sync table missing", exc_info=True)
                pass
        except Exception as e:
            import traceback
            insert_parse_error(cur, stored_repo, abs_path, str(e), traceback.format_exc())
            conn.commit()
            errors.append(f"{abs_path}: {e}")

    # Run resolver per repo (batched).
    for repo_name, edges_by_file in repo_edges_by_file.items():
        try:
            from . import resolver as resolver_mod
            resolver_mod.resolve_repo_edges(conn, repo_name, edges_by_file)
            conn.commit()
        except Exception as e:
            errors.append(f"resolver/{repo_name}: {e}")

    return {"reindexed": reindexed, "deleted": deleted, "errors": errors}




def incremental_update(
    repo: Optional[str] = None,
    workspace: str = scanner_mod.DEFAULT_WORKSPACE,
    db_path: Optional[str] = None,
) -> dict:
    """Re-index only changed files since the last build.

    Uses `git diff` to find changed source files, deletes their old symbols/edges,
    and re-parses + inserts them. Returns a summary.

    Uses a longer busy_timeout than interactive MCP tool calls: this is a
    background CLI command that can afford to wait out lock contention from
    concurrently-running `cairn serve` processes (SSE daemon + per-editor stdio
    clients) rather than fail after 5s.
    """
    conn = get_db(db_path, busy_timeout_ms=20000)
    repos = [repo] if repo else [r.name for r in scanner_mod.discover_repos(workspace)]
    all_paths: list[str] = []
    for r in repos:
        repo_path = scanner_mod.resolve_repo_path(workspace, r)
        changed = _changed_source_files(repo_path, conn=conn)
        if not changed:
            continue
        for f in changed:
            all_paths.append(str(repo_path / f))
    result = reindex_paths(conn, workspace, all_paths)
    conn.close()
    return {"repos_scanned": len(repos), "files_reindexed": result["reindexed"]}


def _changed_source_files(repo_path: Path, conn=None) -> List[str]:
    """Return repo-relative paths of changed source files since last index.

    Primary signal: ``git diff --name-only HEAD`` (covers uncommitted edits +
    the last commit). Falls back to size/mtime comparison against the ``files``
    table when git is unavailable or the repo has no HEAD yet (a fresh
    checkout with no commits, or a non-git source tree). Without the fallback,
    such repos silently report "0 changed files" on every ``cairn update`` — the
    git call exits non-zero and is swallowed as "no changes".

    ``conn`` (optional) is needed only for the fallback path. If omitted and
    git is unavailable, returns [] (callers that want the fallback must pass
    the open connection, since detection is per-repo and the DB is shared).
    """
    out = _run_git(["diff", "--name-only", "HEAD"], str(repo_path))
    if out is not None:
        # git ran (may still be empty if truly nothing changed).
        changed = []
        for line in out.splitlines():
            line = line.strip()
            if line and Path(line).suffix in scanner_mod.EXTENSION_MAP:
                changed.append(line)
        return changed

    # git diff failed (no git, no HEAD, not a repo). Fall back to size/mtime
    # comparison against the files table — the same signal `cairn sync` uses.
    if conn is None:
        return []
    return _changed_via_stat(repo_path, conn)


def _changed_via_stat(repo_path: Path, conn) -> List[str]:
    """Size/mtime-based change detection against the ``files`` table.

    Mirrors the logic in ``cairn sync`` (cli/system.py): a file is "changed" if
    its on-disk size or mtime differs from the stored row by more than the
    0.5s mtime tolerance, or if a tracked file no longer exists, or if a new
    source file appears that isn't in the table. Returns repo-relative paths.
    """
    repo_name = repo_path.name
    try:
        file_rows = conn.execute(
            "SELECT path, size, mtime FROM files WHERE repo_id = ?",
            (repo_name,),
        ).fetchall()
    except Exception:
        return []  # files table missing / unreadable — can't detect.

    changed: list[str] = []
    existing_rel: set[str] = set()
    for row in file_rows:
        stored_path = row["path"]
        existing_rel.add(stored_path)
        p = repo_path / stored_path if not Path(stored_path).is_absolute() else Path(stored_path)
        if not p.exists():
            changed.append(stored_path)  # deleted since last index
            continue
        try:
            st = p.stat()
            if st.st_size != (row["size"] or 0):
                changed.append(stored_path)
            elif abs(st.st_mtime - (row["mtime"] or 0.0)) > 0.5:
                changed.append(stored_path)
        except OSError:
            continue

    # New source files not yet in the table.
    try:
        for src in scanner_mod.iter_source_files(repo_path):
            rel = str(src.relative_to(repo_path)) if str(src).startswith(str(repo_path)) else str(src)
            if rel not in existing_rel:
                changed.append(rel)
    except Exception:
        pass

    return changed


def _reindex_file(
    conn: sqlite3.Connection, repo: str, repo_path: Path, rel_path: str,
    workspace: str = "",
):
    """Delete old symbols/edges/imports/errors for a file and re-parse + insert it.

    Single-file path kept for backward compat (used by `cairn update --file`).
    Delegates to reindex_paths internally.
    """
    abs_path = str(repo_path / rel_path)
    # Derive workspace from repo_path.parent (multi-repo) or use explicit value.
    effective_ws = workspace or str(repo_path.parent)
    reindex_paths(conn, effective_ws, [abs_path])


def incremental_via_rebuild(
    repo: Optional[str] = None,
    workspace: str = scanner_mod.DEFAULT_WORKSPACE,
    db_path: Optional[str] = None,
) -> dict:
    """Pragmatic incremental: rebuild only the repo(s) that changed.

    Detects which repos have uncommitted changes and rebuilds just those.
    Faster than full rebuild, correct, and reuses the tested builder path.
    """
    repos_all = [r.name for r in scanner_mod.discover_repos(workspace)]
    if repo:
        target_repos = [repo]
    else:
        target_repos = [
            r for r in repos_all
            if _changed_source_files(scanner_mod.resolve_repo_path(workspace, r))
        ]
    if not target_repos:
        return {"repos_rebuilt": 0, "msg": "no changes detected"}
    for r in target_repos:
        builder.build_graph(workspace=workspace, repo_filter=r, db_path=db_path)
    return {"repos_rebuilt": target_repos}

