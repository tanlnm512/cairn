"""Incremental graph updates: git diff, reindex_paths, and file watcher sync.

`reindex_paths` is the common entry point for both `cairn update` (git-diff) and
the file watcher's debounced sync. The watcher lives in `watcher.py` and calls
`reindex_paths` from its flush loop; the MCP server uses it for catch-up at boot.
"""
from __future__ import annotations

import logging
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
                    # Collect (model, rowid) pairs BEFORE the delete: the vec0
                    # index keys on embeddings.rowid, and a stale vec entry can
                    # later pair a REUSED rowid with an unrelated vector (wrong
                    # results, not just missing ones). No-op when ANN is off.
                    doomed = cur.execute(
                        "SELECT model, rowid FROM embeddings WHERE symbol_id IN "
                        "(SELECT id FROM symbols WHERE file_id = ?)",
                        (file_id,),
                    ).fetchall()
                    cur.execute(
                        "DELETE FROM embeddings WHERE symbol_id IN "
                        "(SELECT id FROM symbols WHERE file_id = ?)",
                        (file_id,),
                    )
                    if doomed:
                        from .ann_index import delete_index_rows

                        for model in {r["model"] for r in doomed}:
                            delete_index_rows(
                                conn,
                                model,
                                [r["rowid"] for r in doomed if r["model"] == model],
                            )
                except sqlite3.OperationalError as e:
                    note_contention("incremental.delete_embeddings", error=e)
                    logger.debug("embeddings table missing", exc_info=True)
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
                    # Register the deleted file's symbol names for the repair
                    # pass even though nothing was re-created. The DELETE above
                    # nulled+backfilled every incoming edge to those symbols;
                    # without registering the names here those edges stay
                    # 'unresolved' until their OWN files are edited or a full
                    # rebuild runs -- while a fresh build would re-resolve them
                    # (e.g. to a same-named symbol elsewhere, or to 'ambiguous'
                    # if the deletion removed one of two duplicates). This makes
                    # a deleted file behave exactly like a modified one.
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

            file_hash = file_sha256(Path(abs_path))
            from .builder import get_parser, insert_parsed_file, insert_parse_error

            parser = get_parser(language)
            if not parser:
                conn.execute("COMMIT")
                continue

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
            # Record BOTH the removed and the freshly-introduced names for the
            # repair pass. The removed names cover edges whose targets were
            # deleted+re-created (classic repair); the freshly-introduced names
            # cover the resolution flip the other way -- an edge elsewhere that
            # was ambiguous because the name did not exist (or existed once and
            # a second definition just appeared) must be re-resolved to match
            # what a fresh build would decide. Only names whose candidate count
            # actually changed are registered, so the repair stays proportional
            # to the edit.
            changed_names = set(deleted_names) | set(name_to_symbol_ids.keys())
            if changed_names:
                repo_changed_target_names.setdefault(stored_repo, set()).update(changed_names)
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

    The write phase runs under the schema build lock (LOCK_NB, non-blocking)
    so a repo build's _clear_repo can never interleave with these writes --
    the lock's own contract ("a build racing an update"). A concurrent build
    raises RuntimeError; the caller surfaces it as "retry later". The diff
    scan stays OUTSIDE the lock so a long scan doesn't hold it.
    """
    started = time.time()
    conn = get_db(db_path, busy_timeout_ms=20000)
    try:
        repos = [repo] if repo else [r.name for r in scanner_mod.discover_repos(workspace)]
        all_paths: list[str] = []
        for r in repos:
            repo_path = scanner_mod.resolve_repo_path(workspace, r)
            changed = _changed_source_files(repo_path, conn=conn)
            if not changed:
                continue
            for f in changed:
                all_paths.append(str(repo_path / f))

        with build_lock(db_path or str(_resolve_store().db)):
            # Snapshot the derived-index pre-state BEFORE reindex_paths deletes
            # the changed files' rows -- closure ancestors and the old ids/names
            # of the changed files' symbols are only computable while the old
            # rows still exist (see _capture_derived_prestate).
            pre = _capture_derived_prestate(conn, workspace, all_paths)

            result = reindex_paths(conn, workspace, all_paths)

            # Refresh derived indexes when something actually changed. An incremental
            # edit can change which symbols are public, who calls whom, and which edges
            # are exact -- so the precomputed dataflow rows and transitive closure must
            # be brought back in sync, or cached lookups silently serve stale answers.
            # PERF-3: this is now an *incremental* maintenance restricted to the
            # affected symbol set, not the full wipe+rebuild it used to be (which
            # cost minutes per single-file edit on a 1000-file repo). Only a
            # never-built derived index still takes the full path. Best-effort:
            # a failure here is reported as an error but does not undo the reindex.
            derived_errors: list[str] = []
            if result["reindexed"] or result["deleted"]:
                if pre["closure_built"] and pre["dataflow_built"]:
                    derived_errors = _maintain_derived_indexes(conn, workspace, all_paths, pre)
                else:
                    derived_errors = _rebuild_derived_indexes(conn)
    finally:
        conn.close()

    # Persist an 'incremental' build_runs row. Best-effort (record_build_run
    # swallows all errors). reindex_paths returns reindexed/deleted counts but
    # no resolution mix or parse-error breakdown, so those columns stay NULL --
    # best-available per spec 6.2 rather than a refactor of the progress
    # contract. Recorded here (not inside the shared reindex_paths) so the
    # `cairn sync` CLI path records its own 'sync' row without double-counting.
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
        "errors": result["errors"] + derived_errors,
    }


def _rebuild_derived_indexes(conn: sqlite3.Connection) -> list[str]:
    """Full rebuild of dataflow + transitive closure. Returns error strings.

    Best-effort: mirrors the post-build steps in cli/core.py so an incremental
    update keeps the derived indexes consistent with the freshly reindexed
    graph. Each phase is independent; a failure in one doesn't skip the other.

    PERF-3: this is now the FALLBACK path, used only when a derived table was
    never built (empty) -- there is no assumed-correct pre-state to compute an
    affected set from. The normal update flow runs the incremental maintenance
    in :func:`_maintain_derived_indexes` instead.
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


# ---------------------------------------------------------------------------
# PERF-3: incremental derived-index maintenance.
#
# The closure table maps source -> everything it reaches; dataflow maps a name
# to everyone who reaches it. An edit perturbs both only through (1) the
# changed files' symbols and (2) edges of *unchanged* symbols that the
# null+repair dance retargeted. Everything below exists to enumerate exactly
# those sources/names before and after reindex_paths runs.
# ---------------------------------------------------------------------------


def _find_tracked_file_row(cur, workspace: str, abs_path: str):
    """Resolve an absolute path to its tracked ``files`` row (or None).

    Mirrors reindex_paths' normalization (repo-relative primary, absolute
    fallback) so the pre/post snapshots agree with what reindex_paths actually
    deleted and re-inserted.
    """
    repo = scanner_mod.infer_repo_for_path(abs_path, workspace)
    if not repo:
        return None
    repo_path = str(scanner_mod.resolve_repo_path(workspace, repo))
    try:
        Path(abs_path).relative_to(repo_path)
    except ValueError:
        return None
    from pathlib import Path as _P

    rel_to_repo = str(_P(abs_path).relative_to(repo_path)) if abs_path.startswith(repo_path) else _P(abs_path).name
    row = cur.execute(
        "SELECT id, repo_id, path FROM files WHERE path = ?", (rel_to_repo,)
    ).fetchone()
    if row is None:
        row = cur.execute(
            "SELECT id, repo_id, path FROM files WHERE path = ?", (abs_path,)
        ).fetchone()
    return row


def _capture_derived_prestate(
    conn: sqlite3.Connection, workspace: str, paths: list[str]
) -> dict:
    """Snapshot everything the affected-set computation needs from the OLD graph.

    Must run BEFORE reindex_paths: after it, the changed files' old symbol ids
    are gone, their incoming edges have been nulled (losing the target_id the
    ancestor query needs), and the closure is due for maintenance.

    Captures:
    - ``old_ids``/``old_names``: symbol ids and bare names of the changed
      files' tracked rows;
    - ``repair_sources``: sources (ANY file) of edges that the null+repair
      dance can retarget -- edges resolving into ``old_ids``, plus unresolved
      edges whose ``target_name`` matches ``old_names`` (those are exactly the
      rows ``resolver.repair_incoming_edges`` selects, and they can end up
      pointing at a different symbol -- or none -- than before);
    - ``ancestor_ids``: closure ancestors of (old ids + repair sources). A
      source's forward reachability changes when a changed edge sits on one of
      its paths, i.e. when it reaches the changed edge's source; the closure
      answers "who reaches X" in one indexed query;
    - ``repair_edge_ids``: the ids of the repairable rows themselves, so their
      POST-repair targets can be read precisely (after repair, resolved edges
      have ``target_name`` cleared and nulled edges have no ``target_id`` --
      only the row id survives to identify them);
    - ``old_targets``: resolved targets of the changed files' OWN edges (the
      deleted edges' callees -- their dataflow rows lose the deleted callers).
      Deliberately NOT the targets of every repair-source edge: an untouched
      edge of a repair-source did not change, and seeding from all of them
      explodes the dataflow-affected set on name-heavy corpora;
    - ``closure_built``/``dataflow_built``: never-built detection for the
      full-rebuild fallback.
    """
    from .dataflow import _chunked

    cur = conn.cursor()
    tracked_paths: set[str] = set()
    old_ids: set[str] = set()
    old_names: set[str] = set()
    for abs_path in paths:
        row = _find_tracked_file_row(cur, workspace, str(abs_path))
        if row is None:
            continue
        tracked_paths.add(row["path"])
    for rel_path in tracked_paths:
        frow = cur.execute("SELECT id FROM files WHERE path = ?", (rel_path,)).fetchone()
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
    """Names of symbols reachable from ``seed_ids`` within ``max_hops``.

    Follows resolved structural edges only -- the same edge population
    ``impact_analysis``'s precise structural walk uses -- because dataflow's
    within_repo payload is computed by ``impact_analysis(name, max_depth=5)``:
    a changed edge (U->V) perturbs dataflow(X) exactly when X is V or lies up
    to 5 resolved-structural hops downstream of V. Batched IN-list BFS keeps
    it to one query per (hop x chunk).
    """
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
    """Incrementally maintain both derived indexes for a completed reindex.

    Runs AFTER reindex_paths (resolver + incoming-edge repair included). Two
    affected sets are computed, then handed to the dataflow module's
    maintainers:

    **Closure sources** = old ids | new ids | repair sources | name-repair
    sources | closure ancestors of all of those. The "name-repair sources"
    post-capture is the sneaky one: edges of *unchanged* files whose
    ``target_name`` matches a name the edit INTRODUCED. Those edges were never
    nulled, but the resolver's repair pass re-resolves them (a new same-named
    symbol changes their candidate set), and independently the closure's
    Case-2 unique-name extension changes behavior when a name's global
    definition count crosses 1 -- both flip the source's forward reachability.
    Their ancestors are read from the still-unmaintained closure, which at
    this point still reflects the pre-edit graph.

    **Dataflow names** = old names | new names | names of the changed edges'
    resolved targets (old side pre-captured; new side = the re-indexed files'
    edge targets plus the post-repair targets of the captured repairable edge
    ids) | names reachable from those targets within impact_analysis's
    max_depth (see _reachable_symbol_names).

    Each phase is best-effort and independent, mirroring
    _rebuild_derived_indexes' error contract.
    """
    from .dataflow import (
        _chunked,
        maintain_dataflow_index,
        maintain_transitive_closure,
    )

    cur = conn.cursor()
    new_ids: set[str] = set()
    new_names: set[str] = set()
    # Re-resolve every changed path against the POST-update files table: a
    # brand-new file had no pre-update row to track, so keying off the
    # pre-captured paths alone would silently skip its symbols.
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
    try:
        maintain_transitive_closure(conn, affected_sources)
    except Exception as e:
        logger.debug("transitive closure maintenance failed", exc_info=True)
        errors.append(f"transitive_closure: {e}")

    try:
        # Changed-edge target seeds for the dataflow-affected computation:
        # the changed files' own edge targets (new state), plus the POST-repair
        # targets of the captured repairable edge ids (precise because after
        # repair those rows are unidentifiable by name/id -- only the row id
        # survives). Pre-captured old_targets completes the old side.
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

