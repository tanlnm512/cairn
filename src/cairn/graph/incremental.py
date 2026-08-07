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

    Idempotent and safe to call from the watcher thread (as long as the watcher
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
    # Names of symbols that were deleted+recreated per repo. The repair pass
    # re-resolves INCOMING edges (from other files) whose target pointed at one
    # of these names; without it they stay 'unresolved' until the caller's own
    # file is edited or a full rebuild runs (see resolver.repair_incoming_edges).
    repo_changed_target_names: dict[str, set[str]] = {}

    for abs_path in paths:
        abs_path = str(abs_path)
        repo = scanner_mod.infer_repo_for_path(abs_path, workspace)
        if not repo:
            continue
        repo_path = str(scanner_mod.resolve_repo_path(workspace, repo))
        try:
            Path(abs_path).relative_to(repo_path)
        except ValueError:
            continue

        cur = conn.cursor()
        # files.path is stored as REPO-RELATIVE (the portable-path contract);
        # reindex_paths receives ABSOLUTE paths. Normalize the incoming abs_path
        # to repo-relative first (the common case). Fall back to matching the
        # stored absolute form for DBs not yet rebuilt to portable paths.
        from pathlib import Path as _P
        rel_to_repo = str(_P(abs_path).relative_to(repo_path)) if abs_path.startswith(repo_path) else _P(abs_path).name

        # Primary: repo-relative (current build contract).
        row = cur.execute(
            "SELECT id, repo_id, path FROM files WHERE path = ?", (rel_to_repo,)
        ).fetchone()
        if row is None:
            # Fallback: DBs not yet rebuilt store absolute paths.
            row = cur.execute(
                "SELECT id, repo_id, path FROM files WHERE path = ?", (abs_path,)
            ).fetchone()
        # Use the STORED repo_id for downstream inserts so FK constraints on
        # files.repo_id -> repos.id hold (the inferred 'repo' may not exist in
        # repos at all). If no existing row, keep the inferred repo but ensure
        # a repos row exists (the insert_parsed_file path creates one).
        stored_repo = row["repo_id"] if row else repo
        stored_path = row["path"] if row else rel_to_repo  # normalize for delete
        file_id = row["id"] if row else None

        # Snapshot the bare names of symbols currently in this file BEFORE
        # deleting them. The repair pass uses these to re-resolve INCOMING
        # edges (from other files) that pointed at a re-created symbol; without
        # it they stay 'unresolved' until a full rebuild (see
        # resolver.repair_incoming_edges).
        deleted_names: set[str] = set()
        if file_id:
            for r in cur.execute(
                "SELECT name FROM symbols WHERE file_id = ?", (file_id,)
            ):
                if r["name"]:
                    deleted_names.add(r["name"])

        # BEGIN one transaction around the whole delete+re-parse+insert so a
        # crash mid-file either keeps the old rows or installs the new ones,
        # never leaves a gap (old deleted, new not yet written). Commit/rollback
        # is explicit at each exit below.
        try:
            conn.execute("BEGIN")
            if file_id:
                cur.execute(
                    "DELETE FROM edges WHERE source_id IN (SELECT id FROM symbols WHERE file_id = ?)",
                    (file_id,),
                )
                # Cross-file edges/imports pointing INTO this file's symbols (as
                # target) aren't touched by the DELETEs above. Null out those
                # references before deleting the symbols below, or the FK
                # (edges.target_id / imports.resolved_symbol_id -> symbols.id,
                # no cascade) raises "FOREIGN KEY constraint failed". Nulling
                # matches the unresolved-target convention: target_name/
                # imported_path survive for the resolver to re-link.
                #
                # IMPORTANT: a resolved edge has target_name already cleared to
                # NULL (the resolver drops the bare name once target_id is set).
                # Backfill target_name from the symbol we're about to delete
                # BEFORE nulling target_id, so the repair pass can still match
                # these edges by name and re-resolve them once the symbol is
                # re-created. Without this, an incremental reindex of a callee
                # file permanently orphans its incoming edges (target_id AND
                # target_name both NULL -> unresolvable, invisible to precise
                # callers, and even fuzzy mode can't recover the name).
                cur.execute(
                    "UPDATE edges SET "
                    "  target_name = COALESCE(target_name, "
                    "    (SELECT name FROM symbols WHERE id = edges.target_id)), "
                    "  target_id = NULL, resolution = 'unresolved' "
                    "WHERE target_id IN (SELECT id FROM symbols WHERE file_id = ?)",
                    (file_id,),
                )
                cur.execute(
                    "UPDATE imports SET resolved_symbol_id = NULL WHERE resolved_symbol_id IN (SELECT id FROM symbols WHERE file_id = ?)",
                    (file_id,),
                )
                cur.execute("DELETE FROM imports WHERE file_id = ?", (file_id,))
                # Clear embeddings for these symbols BEFORE deleting them, or the
                # FK (embeddings.symbol_id -> symbols.id) blocks the symbol delete.
                # Re-embedding after reindex repopulates them.
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

            # Check if file still exists on disk.
            if not Path(abs_path).exists():
                # Only count as deleted if we actually removed DB state (the file
                # was indexed before this call). A ghost path that was never in
                # the DB is a no-op, not a deletion.
                if file_id is not None:
                    deleted += 1
                # Also remove from pending_sync if tracking.
                try:
                    conn.execute(
                        "DELETE FROM pending_sync WHERE path IN (?, ?)",
                        (abs_path, stored_path),
                    )
                except sqlite3.OperationalError:
                    logger.debug("pending_sync table missing", exc_info=True)
                    pass  # table not present on this schema
                conn.execute("COMMIT")
                continue

            # Re-parse and insert.
            from .scanner import file_sha256, EXTENSION_MAP, resolve_file_language

            suffix = Path(abs_path).suffix
            if suffix not in EXTENSION_MAP:
                conn.execute("COMMIT")
                continue
            language = resolve_file_language(suffix, abs_path)

            file_hash = file_sha256(Path(abs_path))
            from .builder import get_parser, insert_parsed_file, insert_parse_error

            parser = get_parser(language)
            if not parser:
                conn.execute("COMMIT")
                continue

            pf = parser.parse(abs_path)
            name_to_symbol_ids = {}
            # Store files.path as repo-relative (portable); abs_path is only
            # used to stat the file for size/mtime inside insert_parsed_file.
            insert_parsed_file(
                cur, stored_repo, rel_to_repo, abs_path, language, file_hash, pf,
                name_to_symbol_ids, repo_edges_by_file,
            )
            reindexed += 1
            # Clear pending_sync for successfully reindexed files. Match both
            # the rel form (current build contract) and abs form (DBs not yet
            # rebuilt to portable paths) so either clears its rows.
            try:
                conn.execute("DELETE FROM pending_sync WHERE path IN (?, ?)", (rel_to_repo, abs_path))
            except sqlite3.OperationalError:
                logger.debug("pending_sync table missing", exc_info=True)
                pass
            conn.execute("COMMIT")
            # Record the names that were deleted+recreated for the repair pass.
            if deleted_names:
                repo_changed_target_names.setdefault(stored_repo, set()).update(deleted_names)
        except Exception as e:
            # Roll back the whole delete+reinsert so a failed re-parse leaves
            # the old rows intact rather than a half-deleted gap.
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            import traceback
            # insert_parse_error opens its own implicit transaction; safe after
            # the ROLLBACK above.
            try:
                insert_parse_error(cur, stored_repo, rel_to_repo, str(e), traceback.format_exc())
                conn.commit()
            except sqlite3.Error:
                logger.debug("failed to record parse error", exc_info=True)
            errors.append(f"{abs_path}: {e}")

    # Run resolver per repo (batched): re-resolves the edges of the files that
    # were just re-parsed.
    for repo_name, edges_by_file in repo_edges_by_file.items():
        try:
            from . import resolver as resolver_mod
            resolver_mod.resolve_repo_edges(conn, repo_name, edges_by_file)
            conn.commit()
        except Exception as e:
            errors.append(f"resolver/{repo_name}: {e}")

    # Repair pass: re-resolve INCOMING edges from other files whose target was
    # a symbol that got deleted+recreated with a new id. Without this, precise
    # callers of an edited symbol silently drop after an incremental update.
    for repo_name, names in repo_changed_target_names.items():
        if not names:
            continue
        try:
            from . import resolver as resolver_mod
            resolver_mod.repair_incoming_edges(conn, repo_name, sorted(names))
            conn.commit()
        except Exception as e:
            errors.append(f"repair/{repo_name}: {e}")

    return {"reindexed": reindexed, "deleted": deleted, "errors": errors}




def incremental_update(
    repo: Optional[str] = None,
    workspace: str = scanner_mod.DEFAULT_WORKSPACE,
    db_path: Optional[str] = None,
) -> dict:
    """Re-index only changed files since the last build.

    Uses `git diff` to find changed source files, deletes their old symbols/edges,
    and re-parses + inserts them. After reindexing it also refreshes the derived
    indexes (dataflow + transitive closure) so cached impact lookups and multi-hop
    traversals reflect the change -- previously these were only rebuilt by a full
    `cairn build`, leaving `cairn update` serving stale derived data.

    Returns a summary dict including any per-file errors (re-parse failures,
    resolver failures). Uses a longer busy_timeout than interactive MCP tool
    calls so it can wait out lock contention from concurrently-running
    `cairn serve` processes rather than fail after 5s.
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

    # Refresh derived indexes when something actually changed. An incremental
    # edit can change which symbols are public, who calls whom, and which edges
    # are exact -- so the precomputed dataflow rows and transitive closure must
    # be rebuilt, or cached lookups silently serve stale answers. Best-effort:
    # a failure here is reported as an error but does not undo the reindex.
    derived_errors: list[str] = []
    if result["reindexed"] or result["deleted"]:
        derived_errors = _rebuild_derived_indexes(conn)

    conn.close()
    return {
        "repos_scanned": len(repos),
        "files_reindexed": result["reindexed"],
        "files_deleted": result["deleted"],
        "errors": result["errors"] + derived_errors,
    }


def _rebuild_derived_indexes(conn: sqlite3.Connection) -> list[str]:
    """Rebuild dataflow + transitive closure. Returns a list of error strings.

    Best-effort: mirrors the post-build steps in cli/core.py so an incremental
    update keeps the derived indexes consistent with the freshly reindexed
    graph. Each phase is independent; a failure in one doesn't skip the other.
    """
    errors: list[str] = []
    try:
        from .dataflow import build_dataflow_index, build_transitive_closure
        build_dataflow_index(conn)
    except Exception as e:
        logger.debug("dataflow rebuild failed", exc_info=True)
        errors.append(f"dataflow: {e}")
    try:
        from .dataflow import build_transitive_closure
        build_transitive_closure(conn)
    except Exception as e:
        logger.debug("transitive closure rebuild failed", exc_info=True)
        errors.append(f"transitive_closure: {e}")
    return errors


def _changed_source_files(repo_path: Path, conn=None) -> List[str]:
    """Return repo-relative paths of changed source files since last index.

    Primary signal: ``git diff --name-only HEAD``. Falls back to size/mtime
    comparison against the ``files`` table when git is unavailable or the repo
    has no HEAD yet. Without the fallback, such repos silently report "0 changed
    files" on every ``cairn update``.

    ``conn`` (optional) is needed only for the fallback path. If omitted and
    git is unavailable, returns [] -- callers that want the fallback must pass
    the open connection.
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

    A file is "changed" if its on-disk size or mtime differs from the stored
    row by more than the 0.5s mtime tolerance, or if a tracked file no longer
    exists, or if a new source file appears that isn't in the table. Returns
    repo-relative paths.
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

    Single-file entry point used by `cairn update --file`. Delegates to
    reindex_paths internally.
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

    Detects which repos have uncommitted changes and rebuilds just those --
    faster than a full rebuild, and reuses the tested builder path.
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

