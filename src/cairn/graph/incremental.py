"""Incremental graph updates: git diff, reindex_paths, and file watcher sync."""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

from ..paths import resolve_store as _resolve_store
from ..utils.git import _run_git
from . import builder
from . import scanner as scanner_mod
from .schema import get_db, note_contention, build_lock

logger = logging.getLogger(__name__)


def reindex_paths(
    conn: sqlite3.Connection,
    workspace: str,
    paths: list[str],
) -> dict:
    """Reindex absolute paths and return update, embedding, and error counts."""
    import uuid
    from datetime import datetime, timezone

    def _new_id() -> str:
        return uuid.uuid4().hex

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    reindexed = 0
    deleted = 0
    embedded_symbols = 0
    deferred_embeds = 0
    errors: list[str] = []
    # Per-call parser-availability memo (one registry probe per language).
    _lang_available: dict[str, bool] = {}

    # Group paths by repo for batched resolver re-run.
    repo_edges_by_file: dict[str, dict[str, list]] = {}
    # Deleted and recreated names seed incoming-edge repair.
    repo_changed_target_names: dict[str, set[str]] = {}

    for abs_path in paths:
        abs_path = str(abs_path)
        resolved = _repo_relative_path(workspace, abs_path)
        if resolved is None:
            continue
        repo, rel_to_repo = resolved

        cur = conn.cursor()
        # Prefer the stored portable path and repository id.
        row = _find_tracked_file_row(cur, workspace, abs_path)
        stored_repo = row["repo_id"] if row else repo
        stored_path = row["path"] if row else rel_to_repo  # normalize for delete
        file_id = row["id"] if row else None

        deleted_names: set[str] = set()
        if file_id:
            for r in cur.execute(
                "SELECT name FROM symbols WHERE file_id = ?", (file_id,)
            ):
                if r["name"]:
                    deleted_names.add(r["name"])

        # Keep each file replacement atomic.
        try:
            conn.execute("BEGIN")
            if file_id:
                cur.execute(
                    "DELETE FROM edges WHERE source_id IN (SELECT id FROM symbols WHERE file_id = ?)",
                    (file_id,),
                )
                # Backfill names before nulling targets so repair can find them.
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
                    from .embeddings import _purge_embedding_rows

                    # Base rows + their vec0 entries go through the shared
                    # helper (collect-before-delete, no-op when ANN is off).
                    _purge_embedding_rows(
                        conn,
                        "symbol_id IN (SELECT id FROM symbols WHERE file_id = ?)",
                        (file_id,),
                    )
                    # embeddings_mv also FK-references symbols(id) (no
                    # cascade); no vecmv rowid cleanup exists (vecmv_ tables
                    # rebuild wholesale).
                    cur.execute(
                        "DELETE FROM embeddings_mv WHERE symbol_id IN "
                        "(SELECT id FROM symbols WHERE file_id = ?)",
                        (file_id,),
                    )
                except sqlite3.OperationalError as e:
                    note_contention("incremental.delete_embeddings", error=e)
                    logger.debug("embeddings table missing", exc_info=True)
                # rationale's FKs (file_id, nullable symbol_id) have no
                # cascade; clear its rows before the symbols/files deletes.
                # Re-derive rides insert_parsed_file below.
                cur.execute("DELETE FROM rationale WHERE file_id = ?", (file_id,))
                cur.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))
                cur.execute("DELETE FROM parse_errors WHERE file_path = ?", (stored_path,))
                cur.execute("DELETE FROM files WHERE id = ?", (file_id,))

            # Check if file still exists on disk.
            if not Path(abs_path).exists():
                # Only count as deleted if we actually removed DB state (the file
                # was indexed before this call). A ghost path that was never in the
                # DB is a no-op, not a deletion.
                if file_id is not None:
                    deleted += 1
                    # Deleted names still need resolver repair.
                    if deleted_names:
                        repo_changed_target_names.setdefault(stored_repo, set()).update(deleted_names)
                # Also remove from pending_sync if tracking.
                try:
                    conn.execute(
                        "DELETE FROM pending_sync WHERE path IN (?, ?)",
                        (abs_path, stored_path),
                    )
                except sqlite3.OperationalError as e:
                    note_contention("incremental.pending_sync_delete", error=e)
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

            from .builder import insert_parsed_file
            # Parser unavailability is probed through the registry (not the
            # factory) because get_parser RAISES for a missing grammar wheel;
            # the drop arm below must be reachable, not bypassed.
            from ..parsers._registry import is_language_available

            available = _lang_available.get(language)
            if available is None:
                available = _lang_available[language] = is_language_available(
                    language
                )
            parser = builder.get_parser(language) if available else None
            if parser is None:
                # Fresh builds skip unavailable parsers; strict refresh converges.
                try:
                    conn.execute(
                        "DELETE FROM pending_sync WHERE path IN (?, ?)",
                        (rel_to_repo, abs_path),
                    )
                except sqlite3.OperationalError as e:
                    note_contention("incremental.pending_sync_clear", error=e)
                    logger.debug("pending_sync table missing", exc_info=True)
                conn.execute("COMMIT")
                deleted += 1
                if file_id is not None and deleted_names:
                    repo_changed_target_names.setdefault(
                        stored_repo, set()
                    ).update(deleted_names)
                continue

            file_hash = file_sha256(Path(abs_path))

            pf = parser.parse(abs_path)
            name_to_symbol_ids: dict = {}
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
            except sqlite3.OperationalError as e:
                note_contention("incremental.pending_sync_clear", error=e)
                logger.debug("pending_sync table missing", exc_info=True)
                pass
            conn.execute("COMMIT")
        except Exception as e:
            # Roll back the whole delete+reinsert so a failed re-parse leaves
            # the old rows intact rather than a half-deleted gap.
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            import traceback
            # Bound here, not in the parse section, so a failure raised
            # before that import -- e.g. the delete leg -- surfaces as itself.
            from .builder import insert_parse_error
            # insert_parse_error opens its own implicit transaction; safe after
            # the ROLLBACK above.
            try:
                insert_parse_error(cur, stored_repo, rel_to_repo, str(e), traceback.format_exc())
                conn.commit()
            except sqlite3.Error:
                logger.debug("failed to record parse error", exc_info=True)
            errors.append(f"{abs_path}: {e}")
            continue

        # Post-COMMIT embed leg: the file is durable here, so a failure in
        # this block defers the embeds and is never a parse failure.
        try:
            # Self-committing embeds stay outside the reindex transaction.
            if name_to_symbol_ids:
                new_ids = [
                    sid
                    for entries in name_to_symbol_ids.values()
                    for (sid, _, _) in entries
                ]
                from .embeddings import embed_symbols, embeddings_available
                if embeddings_available():
                    try:
                        summary = embed_symbols(conn, new_ids)
                    except Exception:
                        logger.debug("embed_symbols failed; deferring", exc_info=True)
                        deferred_embeds += len(new_ids)
                        # Drop partially buffered writes so an open transaction
                        # cannot break the next file leg's BEGIN.
                        try:
                            conn.rollback()
                        except sqlite3.Error:
                            pass
                    else:
                        embedded_symbols += summary["embedded"]
                        # Settle a failed embedding commit before this rollback.
                        if conn.in_transaction:
                            try:
                                conn.commit()
                            except sqlite3.OperationalError:
                                conn.rollback()
                                deferred_embeds += len(new_ids)
                else:
                    deferred_embeds += len(new_ids)
            # Repair only names whose global candidate count changed.
            changed_names = set(deleted_names) | set(name_to_symbol_ids.keys())
            if changed_names:
                repo_changed_target_names.setdefault(stored_repo, set()).update(changed_names)
        except Exception:
            logger.debug("post-commit embed leg failed; deferring", exc_info=True)
            deferred_embeds += sum(
                len(entries) for entries in name_to_symbol_ids.values()
            )
            # Drop partially buffered writes so an open transaction cannot
            # break the next file leg's BEGIN.
            try:
                conn.rollback()
            except sqlite3.Error:
                pass

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

    # Imports-edge refresh: the reindexed files' module symbols and their
    # import rows changed, so recompute the repo's kind='imports' edges
    # (delete+reinsert per materialize_import_edges' idempotence contract).
    from .builder import materialize_import_edges
    for repo_name in repo_edges_by_file:
        try:
            materialize_import_edges(conn, repo=repo_name)
            conn.commit()
        except Exception as e:
            errors.append(f"imports-edges/{repo_name}: {e}")

    if deferred_embeds:
        logger.warning(
            "Deferred embedding %d symbol(s): no usable embedding backend "
            "this pass; run `cairn embed` once a backend is reachable",
            deferred_embeds,
        )
    return {"reindexed": reindexed, "deleted": deleted, "embedded_symbols": embedded_symbols, "deferred_embeds": deferred_embeds, "errors": errors}



def incremental_update(
    repo: Optional[str] = None,
    workspace: str = scanner_mod.DEFAULT_WORKSPACE,
    db_path: Optional[str] = None,
    diff_ref: Optional[str] = None,
) -> dict:
    """Reindex changed files and refresh overlays and derived indexes."""
    started = time.time()
    conn = get_db(db_path, busy_timeout_ms=20000)
    try:
        repos = (
            [repo]
            if repo
            else [
                scanner_mod.repository_id(r)
                for r in scanner_mod.discover_repos(workspace)
            ]
        )
        all_paths: list[str] = []
        for r in repos:
            repo_path = scanner_mod.resolve_repo_path(workspace, r)
            changed = _changed_source_files(repo_path, conn=conn, diff_ref=diff_ref)
            if not changed:
                continue
            for f in changed:
                all_paths.append(str(repo_path / f))

        with build_lock(db_path or str(_resolve_store().db)):
            # Old derived-index inputs disappear after reindex.
            pre = _capture_derived_prestate(conn, workspace, all_paths)

            result = reindex_paths(conn, workspace, all_paths)

            # Re-apply the configured SCIP overlay (existing indexes only --
            # never a generation) before derived-index maintenance, so closure
            # and dataflow see the post-overlay edge population.
            scip_errors: list[str] = []
            if result["reindexed"] or result["deleted"]:
                try:
                    builder._apply_scip_overlay(
                        conn,
                        workspace,
                        {r: None for r in repos},
                        verbose=False,
                        generate_missing=False,
                    )
                except Exception as e:
                    logger.debug("scip overlay re-application failed", exc_info=True)
                    scip_errors.append(f"scip_overlay: {e}")

            # Maintain affected rows; rebuild only missing derived tables.
            derived_errors: list[str] = []
            if result["reindexed"] or result["deleted"]:
                if pre["dataflow_built"]:
                    derived_errors = _maintain_derived_indexes(conn, workspace, all_paths, pre)
                else:
                    derived_errors = _rebuild_derived_indexes(conn, pre["closure_built"])
    finally:
        conn.close()

    builder.record_build_run(
        db_path,
        "incremental",
        started_at=started,
        duration_s=time.time() - started,
        files=result["reindexed"],
        skipped=result["deleted"],
    )
    return {
        "repos_scanned": len(repos),
        "files_reindexed": result["reindexed"],
        "files_deleted": result["deleted"],
        "errors": result["errors"] + scip_errors + derived_errors,
        "deferred_embeds": result["deferred_embeds"],
    }


def _rebuild_derived_indexes(
    conn: sqlite3.Connection, closure_built: bool
) -> list[str]:
    """Rebuild absent derived indexes independently and return errors."""
    errors: list[str] = []
    try:
        from .dataflow import build_dataflow_index
        build_dataflow_index(conn)
    except Exception as e:
        logger.debug("dataflow rebuild failed", exc_info=True)
        errors.append(f"dataflow: {e}")
    if closure_built:
        try:
            from .dataflow import build_transitive_closure
            build_transitive_closure(conn)
        except Exception as e:
            logger.debug("transitive closure rebuild failed", exc_info=True)
            errors.append(f"transitive_closure: {e}")
    return errors


# --- Incremental derived-index maintenance (affected-set capture + maintain) ---


def _repo_relative_path(workspace: str, abs_path: str) -> tuple[str, str] | None:
    """Return an absolute path's repository and repo-relative path."""
    repo = scanner_mod.infer_repo_for_path(abs_path, workspace)
    if not repo:
        return None
    repo_path = os.path.normpath(str(scanner_mod.resolve_repo_path(workspace, repo)))
    abs_norm = os.path.normpath(abs_path)
    try:
        rel = Path(abs_norm).relative_to(repo_path)
    except ValueError:
        return None
    return repo, str(rel)


def _find_tracked_file_row(cur, workspace: str, abs_path: str):
    """Return the tracked row for an absolute path, or None."""
    resolved = _repo_relative_path(workspace, abs_path)
    if resolved is None:
        return None
    repo, rel_to_repo = resolved
    row = cur.execute(
        "SELECT id, repo_id, path FROM files WHERE path = ? AND repo_id = ?",
        (rel_to_repo, repo),
    ).fetchone()
    if row is None:
        row = cur.execute(
            "SELECT id, repo_id, path FROM files WHERE path = ? AND repo_id = ?",
            (abs_path, repo),
        ).fetchone()
    return row


def _capture_derived_prestate(
    conn: sqlite3.Connection, workspace: str, paths: list[str]
) -> dict:
    """Snapshot old graph inputs needed to compute affected sets."""
    from .dataflow import _chunked

    cur = conn.cursor()
    tracked_rows: set[tuple[str, str]] = set()
    old_ids: set[str] = set()
    old_names: set[str] = set()
    for abs_path in paths:
        row = _find_tracked_file_row(cur, workspace, str(abs_path))
        if row is None:
            continue
        tracked_rows.add((row["repo_id"], row["path"]))
    for repo_id, rel_path in tracked_rows:
        frow = cur.execute(
            "SELECT id FROM files WHERE path = ? AND repo_id = ?", (rel_path, repo_id)
        ).fetchone()
        if frow is None:
            continue
        for r in cur.execute(
            "SELECT id, name FROM symbols WHERE file_id = ?", (frow["id"],)
        ):
            if r["id"]:
                old_ids.add(r["id"])
            if r["name"]:
                old_names.add(r["name"])

    repair_sources: set[str] = set()
    repair_edge_ids: set[str] = set()
    for chunk in _chunked(old_ids):
        ph = ",".join("?" for _ in chunk)
        for r in cur.execute(
            f"SELECT id, source_id FROM edges WHERE target_id IN ({ph})", chunk
        ):
            repair_edge_ids.add(r[0])
            repair_sources.add(r[1])
    for chunk in _chunked(old_names):
        ph = ",".join("?" for _ in chunk)
        for r in cur.execute(
            f"SELECT id, source_id FROM edges WHERE target_name IN ({ph})", chunk
        ):
            repair_edge_ids.add(r[0])
            repair_sources.add(r[1])

    ancestor_targets = old_ids | repair_sources
    ancestor_ids: set[str] = set()
    for chunk in _chunked(ancestor_targets):
        ph = ",".join("?" for _ in chunk)
        ancestor_ids.update(
            r[0]
            for r in cur.execute(
                f"SELECT DISTINCT source_id FROM transitive_edges WHERE target_id IN ({ph})",
                chunk,
            )
        )

    old_targets: set[str] = set()
    for chunk in _chunked(old_ids):
        ph = ",".join("?" for _ in chunk)
        old_targets.update(
            r[0]
            for r in cur.execute(
                f"SELECT DISTINCT target_id FROM edges "
                f"WHERE source_id IN ({ph}) AND target_id IS NOT NULL",
                chunk,
            )
        )

    def _has_rows(table: str) -> bool:
        try:
            return cur.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
        except sqlite3.Error:
            return False

    return {
        "old_ids": old_ids,
        "old_names": old_names,
        "repair_sources": repair_sources,
        "repair_edge_ids": repair_edge_ids,
        "ancestor_ids": ancestor_ids,
        "old_targets": old_targets,
        "closure_built": _has_rows("transitive_edges"),
        "dataflow_built": _has_rows("dataflow"),
    }


def _reachable_symbol_names(
    conn: sqlite3.Connection, seed_ids: set[str], max_hops: int = 5
) -> set[str]:
    """Names reachable from ``seed_ids`` over resolved structural edges within
    ``max_hops`` (impact_analysis's depth), via batched IN-list BFS."""
    from .dataflow import _chunked
    from .traversal import STRUCTURAL_EDGE_KINDS

    cur = conn.cursor()
    kind_ph = ",".join("?" for _ in STRUCTURAL_EDGE_KINDS)
    kinds = tuple(STRUCTURAL_EDGE_KINDS)
    names: set[str] = set()
    seen: set[str] = set()
    frontier = {i for i in seed_ids if i}
    for _hop in range(max_hops + 1):
        if not frontier:
            break
        for chunk in _chunked(frontier):
            ph = ",".join("?" for _ in chunk)
            names.update(
                r[0]
                for r in cur.execute(
                    f"SELECT name FROM symbols WHERE id IN ({ph}) AND name IS NOT NULL",
                    chunk,
                )
            )
        nxt: set[str] = set()
        for chunk in _chunked(frontier):
            ph = ",".join("?" for _ in chunk)
            nxt.update(
                r[0]
                for r in cur.execute(
                    f"SELECT DISTINCT target_id FROM edges "
                    f"WHERE source_id IN ({ph}) AND target_id IS NOT NULL "
                    f"AND kind IN ({kind_ph})",
                    (*chunk, *kinds),
                )
            )
        nxt -= seen
        seen |= frontier
        frontier = nxt
    return names


def _maintain_derived_indexes(
    conn: sqlite3.Connection,
    workspace: str,
    paths: list[str],
    pre: dict,
) -> list[str]:
    """Incrementally maintain built derived indexes and return errors."""
    from .dataflow import (
        _chunked,
        maintain_dataflow_index,
        maintain_transitive_closure,
    )

    cur = conn.cursor()
    new_ids: set[str] = set()
    new_names: set[str] = set()
    # Key off the POST-update files table: a brand-new file has no pre-update
    # row, so the pre-captured paths alone would skip its symbols.
    for abs_path in paths:
        row = _find_tracked_file_row(cur, workspace, str(abs_path))
        if row is None:
            continue  # file deleted (or failed re-parse): nothing new to index
        for r in cur.execute(
            "SELECT id, name FROM symbols WHERE file_id = ?", (row["id"],)
        ):
            if r["id"]:
                new_ids.add(r["id"])
            if r["name"]:
                new_names.add(r["name"])

    # Sources of edges pointing (by bare name) at names this edit introduced:
    # candidates for resolution flips and Case-2 uniqueness flips.
    name_repair_sources: set[str] = set()
    for chunk in _chunked(new_names - pre["old_names"]):
        ph = ",".join("?" for _ in chunk)
        name_repair_sources.update(
            r[0]
            for r in cur.execute(
                f"SELECT DISTINCT source_id FROM edges WHERE target_name IN ({ph})",
                chunk,
            )
        )

    changed_sources = pre["old_ids"] | pre["repair_sources"] | name_repair_sources
    # Ancestors of the post-captured sources, read from the pre-edit closure
    # (maintenance hasn't touched it yet, so it is still the trusted pre-state).
    post_ancestors: set[str] = set()
    for chunk in _chunked(name_repair_sources | new_ids):
        ph = ",".join("?" for _ in chunk)
        post_ancestors.update(
            r[0]
            for r in cur.execute(
                f"SELECT DISTINCT source_id FROM transitive_edges WHERE target_id IN ({ph})",
                chunk,
            )
        )

    affected_sources = changed_sources | new_ids | pre["ancestor_ids"] | post_ancestors

    errors: list[str] = []
    if pre["closure_built"]:
        # An absent closure stays absent: maintenance never materializes one.
        try:
            maintain_transitive_closure(conn, affected_sources)
        except Exception as e:
            logger.debug("transitive closure maintenance failed", exc_info=True)
            errors.append(f"transitive_closure: {e}")

    try:
        # Repaired edge ids are only identifiable after the repair.
        new_targets: set[str] = set()
        for chunk in _chunked(new_ids):
            ph = ",".join("?" for _ in chunk)
            new_targets.update(
                r[0]
                for r in cur.execute(
                    f"SELECT DISTINCT target_id FROM edges "
                    f"WHERE source_id IN ({ph}) AND target_id IS NOT NULL",
                    chunk,
                )
            )
        for chunk in _chunked(pre["repair_edge_ids"]):
            ph = ",".join("?" for _ in chunk)
            new_targets.update(
                r[0]
                for r in cur.execute(
                    f"SELECT DISTINCT target_id FROM edges "
                    f"WHERE id IN ({ph}) AND target_id IS NOT NULL",
                    chunk,
                )
            )
        affected_names = (
            pre["old_names"]
            | new_names
            | _reachable_symbol_names(conn, pre["old_targets"] | new_targets)
        )
        maintain_dataflow_index(conn, affected_names)
    except Exception as e:
        logger.debug("dataflow maintenance failed", exc_info=True)
        errors.append(f"dataflow: {e}")
    return errors


def _filter_source_paths(lines) -> List[str]:
    """Keep non-blank lines whose suffix is a mapped source extension, deduped
    in first-seen order."""
    changed = []
    for line in lines:
        line = line.strip()
        if (
            line
            and Path(line).suffix in scanner_mod.EXTENSION_MAP
            and line not in changed
        ):
            changed.append(line)
    return changed


def _changed_source_files(repo_path: Path, conn=None, diff_ref: Optional[str] = None) -> List[str]:
    """Return changed source paths using git or stat fallback."""
    if diff_ref is not None:
        out = _run_git(["diff", "--name-only", diff_ref], str(repo_path))
        if out is None:
            logger.warning(
                "git diff failed for range %r in %s; reindexing nothing for it",
                diff_ref, repo_path,
            )
            return []
        return _filter_source_paths(out.splitlines())

    out = _run_git(["diff", "--name-only", "HEAD"], str(repo_path))
    if out is not None:
        # git ran (may still be empty if truly nothing changed). git diff never
        # lists untracked files, so ask for those separately or newly created
        # source files would never be indexed.
        lines = list(out.splitlines())
        untracked = _run_git(
            ["ls-files", "--others", "--exclude-standard"], str(repo_path)
        )
        if untracked:
            lines.extend(untracked.splitlines())
        return _filter_source_paths(lines)

    # git diff failed (no git, no HEAD, not a repo). Fall back to size/mtime
    # comparison against the files table — the same signal `cairn sync` uses.
    if conn is None:
        return []
    return _changed_via_stat(repo_path, conn)


def _changed_via_stat(repo_path: Path, conn) -> List[str]:
    """Return changed paths by size, mtime, deletion, or new appearance."""
    repo_name = scanner_mod.repository_id(repo_path)
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
    """Reindex one file through the shared path update machinery."""
    abs_path = str(repo_path / rel_path)
    # Derive workspace from repo_path.parent (multi-repo) or use explicit value.
    effective_ws = workspace or str(repo_path.parent)
    reindex_paths(conn, effective_ws, [abs_path])
