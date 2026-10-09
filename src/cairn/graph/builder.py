"""Graph builder: scan -> parse -> store -> resolve edges."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from ..parsers.base import ParsedFile
from ..parsers.factory import get_parser
from ..parsers import routes as routes_mod
from ..parsers import service_calls as service_calls_mod
from .repository import GraphRepository
from . import scanner as scanner_mod
from . import resolver as resolver_mod
from . import lsp as lsp_mod
from .schema import init_db, get_build_db, get_db, backup_to, build_lock, note_contention
from ..paths import resolve_store as _resolve_store

_logger = logging.getLogger(__name__)
_AUTO_LSP_TRANSPORT = object()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _scan_workspace_with_skips(
    workspace: str, repo_filter: Optional[str] = None
) -> tuple[list, list]:
    """Return workspace files to index and their skip records."""

    all_files = []
    all_skips = []
    if repo_filter:
        repo_path = scanner_mod.resolve_repo_path(workspace, repo_filter)
        if (repo_path / ".git").exists():
            files, skips = scanner_mod.iter_files_and_skips(repo_path)
            all_files.extend(files)
            all_skips.extend(skips)
    else:
        for repo in scanner_mod.discover_repos(workspace):
            files, skips = scanner_mod.iter_files_and_skips(repo)
            all_files.extend(files)
            all_skips.extend(skips)
    return all_files, all_skips


def _record_skips(cur, skips: list) -> int:
    """Return the number of skip rows recorded without failing the build."""
    recorded = 0
    for s in skips:
        try:
            cur.execute(
                """INSERT INTO skipped_files
                     (id, repo_id, path, reason, size_bytes, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (_new_id(), s.repo, s.rel_path, s.reason, s.size_bytes, _now()),
            )
            recorded += 1
        except Exception:
            # A bad skip row is not worth failing the build over.
            continue
    return recorded


def _parse_all(files, verbose: bool, progress=None) -> tuple[list, int]:
    """Parse all files. Returns (parsed_results, parse_errors_count)."""
    log = _log if verbose else lambda *a: None
    emit = progress or (lambda *a, **k: None)

    parsed_results = []
    tasks = [(fi.path, fi.rel_path, fi.language, fi.repo, fi.hash) for fi in files]

    if len(tasks) > 10:
        import multiprocessing
        import os
        from concurrent.futures import ProcessPoolExecutor, as_completed

        # Configurable, uncapped worker count. Honors CAIRN_WORKERS
        # (mirrors the reference tool's CBM_WORKERS); falls back to cpu_count()
        # when unset or invalid.
        cpu = multiprocessing.cpu_count()
        try:
            requested = int(os.environ.get("CAIRN_WORKERS", cpu))
        except ValueError:
            requested = cpu
        num_workers = max(1, min(requested, 256))
        log(f"  parsing {len(tasks)} files with {num_workers} workers...")

        # path -> hash built once (O(n)); avoids a per-future linear scan over
        # `tasks` that would make the loop O(n^2).
        hash_by_path = {t[0]: t[4] for t in tasks}

        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            futures = [
                executor.submit(_parse_file_worker, (t[0], t[1], t[2], t[3]))
                for t in tasks
            ]
            for i, fut in enumerate(as_completed(futures)):
                path, rel_path, language, repo, pf, err, st = fut.result()
                fi_hash = hash_by_path[path]
                parsed_results.append((path, rel_path, language, repo, fi_hash, pf, err, st))
                if (i + 1) % 500 == 0:
                    log(f"    parsed {i + 1} / {len(tasks)} files")
                emit("parse_progress", done=i + 1, total=len(tasks))
    else:
        for i, t in enumerate(tasks):
            path, rel_path, language, repo, fi_hash = t
            path, rel_path, language, repo, pf, err, st = _parse_file_worker((path, rel_path, language, repo))
            parsed_results.append((path, rel_path, language, repo, fi_hash, pf, err, st))
            emit("parse_progress", done=i + 1, total=len(tasks))

    # Tuple shape: (path, rel_path, language, repo, fi_hash, pf, err, st).
    # Count the `err` slot (index 6), not `pf` (index 5) -- pf is the
    # successful-parse payload, non-None on success.
    parse_errors = sum(1 for r in parsed_results if r[6] is not None)
    emit("parse_done", parsed=len(parsed_results), errors=parse_errors)
    return parsed_results, parse_errors


def _insert_results(
    conn,
    parsed_results,
    verbose: bool,
    in_memory: bool,
    progress=None,
) -> tuple[int, int, int, int, Dict[str, Dict[str, List[tuple]]]]:
    """Insert parsed files and return counts plus unresolved repo edges."""
    cur = conn.cursor()
    log = _log if verbose else lambda *a: None
    emit = progress or (lambda *a, **k: None)

    symbol_count = 0
    edge_count = 0
    import_count = 0
    file_count = 0

    # repo_edges_by_file: {repo -> {source_file_id -> [(edge_id, source_sid,
    #   target_name, line, column, receiver_type, call_arity), ...]}} -- the
    #   resolver consumes this; elements 6-7 are in-memory parser signals.
    repo_edges_by_file: Dict[str, Dict[str, List[tuple]]] = {}
    # name -> [(symbol_id, repo, file_id)] for same-file source lookup.
    name_to_symbol_ids: Dict[str, List[tuple]] = {}

    # Second pass: insert results sequentially into SQLite
    total_to_insert = len(parsed_results)
    for idx, (path, rel_path, language, repo, fi_hash, pf, err, st) in enumerate(parsed_results, start=1):
        if pf is None:
            # Parse error: log to database and console. Store the repo-relative
            # path so parse_errors stays portable (same contract as files.path).
            log(f"  skip/error {rel_path}: {err}")
            insert_parse_error(cur, repo, rel_path, err or "Unknown parse error", st)
            emit("insert_progress", done=idx, total=total_to_insert, symbols=symbol_count, edges=edge_count)
            continue

        # Framework-aware route detection: routes are ordinary kind='route'
        # symbols and kind='references' edges merged in before insertion.
        try:
            route_extraction = routes_mod.detect_routes(pf, language)
            if route_extraction:
                pf.symbols.extend(route_extraction.routes)
                pf.edges.extend(route_extraction.references)
        except Exception as e:
            # Route detection is best-effort sugar on top of the real parse;
            # never let it fail the whole file's indexing.
            log(f"  route detection failed for {rel_path}: {e}")

        # Service-topology edge detection: http_call/service_call are ordinary
        # kinds merged in before insertion. By default impact_analysis/trace_flow
        # exclude these from blast radius; queryable via get_callees/get_callers(kind=...).
        try:
            sc_extraction = service_calls_mod.detect_service_calls(pf, language)
            if sc_extraction:
                pf.edges.extend(sc_extraction.edges)
        except Exception as e:
            # Best-effort, never fail indexing.
            log(f"  service-call detection failed for {rel_path}: {e}")

        try:
            sc, ec, ic = insert_parsed_file(
                cur,
                repo,
                rel_path,
                path,
                language,
                fi_hash,
                pf,
                name_to_symbol_ids,
                repo_edges_by_file,
            )
            symbol_count += sc
            edge_count += ec
            import_count += ic
        except Exception as e:
            log(f"  error inserting {rel_path}: {e}")
            insert_parse_error(cur, repo, rel_path, f"Insertion error: {e}")
            emit("insert_progress", done=idx, total=total_to_insert, symbols=symbol_count, edges=edge_count)
            continue

        file_count += 1
        # In-memory builds persist once; disk commits bound WAL lock hold time.
        if not in_memory and file_count % 500 == 0:
            conn.commit()
        if file_count % 100 == 0:
            log(f"  ... inserted {file_count} files, {symbol_count} symbols, {edge_count} edges")
        emit("insert_progress", done=idx, total=total_to_insert, symbols=symbol_count, edges=edge_count)

    if not in_memory:
        conn.commit()

    return file_count, symbol_count, edge_count, import_count, repo_edges_by_file


def _resolve_all(
    conn,
    repo_edges_by_file: Dict[str, Dict[str, List[tuple]]],
    in_memory: bool,
    verbose: bool,
    progress=None,
) -> dict:
    """Resolve queued edges per repository and return resolution counts."""
    log = _log if verbose else lambda *a: None
    emit = progress or (lambda *a, **k: None)

    resolution_stats = {"exact": 0, "ambiguous": 0, "unresolved": 0}
    for repo_name, edges_by_file in repo_edges_by_file.items():
        log(f"  resolving edges for {repo_name}...")
        emit("resolve_start", repo=repo_name)
        repo_stats = resolver_mod.resolve_repo_edges(
            conn, repo_name, edges_by_file
        )
        if repo_stats:
            for k, v in repo_stats.items():
                resolution_stats[k] = resolution_stats.get(k, 0) + v
        emit("resolve_done", repo=repo_name, stats=repo_stats or {})
        if not in_memory:
            conn.commit()

    return resolution_stats


_SCIP_SKIP_RUNTIME_MISSING = "scip_runtime_missing"
_SCIP_SKIP_IMPORT_FAILED = "scip_import_failed"
_SCIP_SKIP_INDEX_MISSING = "scip_index_missing"


def _record_scip_skip(cur, repo_id: str, path: str, reason: str) -> None:
    """One skipped_files row per (repo, path, reason) for an index the overlay
    could not apply (best-effort; a repeat replaces the earlier row)."""
    try:
        cur.execute(
            "DELETE FROM skipped_files WHERE repo_id = ? AND path = ? AND reason = ?",
            (repo_id, path, reason),
        )
        cur.execute(
            """INSERT INTO skipped_files
                 (id, repo_id, path, reason, size_bytes, recorded_at)
               VALUES (?, ?, ?, ?, NULL, ?)""",
            (_new_id(), repo_id, path, reason, _now()),
        )
    except Exception:
        pass


def _apply_scip_overlay(
    conn,
    workspace: str,
    repos_seen: Mapping[str, object],
    verbose: bool,
    generate_missing: bool = True,
) -> Optional[Dict[str, int]]:
    """Apply configured SCIP indexes and return a degradation-safe report."""
    try:
        from .config import load_config

        raw = (load_config(workspace).scip or {}).get("indexes")
        indexes: Dict[str, str] = (
            {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        )
    except Exception as e:
        _log(f"  [scip] config load failed ({e}); overlay skipped")
        return None
    if not indexes:
        return None

    log = _log if verbose else lambda *a: None
    report: Dict[str, int] = {
        "edges": 0, "disagreements": 0, "upgrades": 0, "join_anomalies": 0,
    }
    # Skip attribution is workspace-scoped, not per-document, so a single
    # repo_id is only safe to guess in a single-repo workspace; a multi-repo
    # workspace leaves it blank rather than mis-attributing to an arbitrary repo.
    repo_id = next(iter(repos_seen), "") if len(repos_seen) == 1 else ""
    try:
        from ..parsers.scip_importer import (
            _INSTALL_HINT,
            import_scip_file,
            scip_available,
        )

        if not scip_available():
            _log(f"  [scip] runtime unavailable; using tree-sitter edges. "
                 f"{_INSTALL_HINT}")
            cur = conn.cursor()
            for rel_path in sorted(set(indexes.values())):
                _record_scip_skip(cur, repo_id, rel_path, _SCIP_SKIP_RUNTIME_MISSING)
            conn.commit()
            return report

        ws_root = Path(workspace).resolve()
        for lang, rel_path in sorted(indexes.items()):
            idx_path = ws_root / rel_path
            if not idx_path.exists():
                if not generate_missing:
                    _log(f"  [scip] {lang}: index {rel_path} missing; "
                         f"keeping tree-sitter edges")
                    _record_scip_skip(
                        conn.cursor(), repo_id, rel_path, _SCIP_SKIP_INDEX_MISSING
                    )
                    conn.commit()
                    continue
                from ..parsers.scip_indexers import generate_index_result

                result = generate_index_result(lang, idx_path, str(ws_root), log=log)
                if not result.ok:
                    if result.reason:
                        _record_scip_skip(
                            conn.cursor(), repo_id, rel_path, result.reason
                        )
                        conn.commit()
                        _log(f"  [scip] {lang}: index generation failed "
                             f"({result.reason}); keeping tree-sitter edges. "
                             f"{result.detail}")
                    else:
                        log(f"  [scip] {lang}: {result.detail}")
                    continue
            try:
                record = import_scip_file(conn, str(idx_path), workspace)
            except Exception as e:
                _log(f"  [scip] index import failed ({rel_path}): {e}; "
                     f"keeping tree-sitter edges")
                _record_scip_skip(
                    conn.cursor(), repo_id, rel_path, _SCIP_SKIP_IMPORT_FAILED
                )
                conn.commit()
                continue
            report["edges"] += record.get("edges", 0)
            report["disagreements"] += record.get("disagreements", 0)
            report["upgrades"] += record.get("upgrades", 0)
            anomalies = record.get("join_anomalies", 0)
            if anomalies:
                report["join_anomalies"] += anomalies
                _log(f"  [scip] {lang}: {anomalies} file(s) kept tree-sitter "
                     f"edges (join anomaly)")
            log(f"  [scip] {lang}: {report['edges']} edges "
                f"({report['disagreements']} disagreements, "
                f"{report['upgrades']} upgrades)")
    except Exception as e:
        _log(f"  [scip] overlay skipped: {e}")
    return report


def _build_graph_impl(
    conn,
    workspace: str = scanner_mod.DEFAULT_WORKSPACE,
    repo_filter: Optional[str] = None,
    db_path: Optional[str] = None,
    verbose: bool = False,
    progress=None,
    lsp: Optional[bool] = None,
    lsp_transport: object = _AUTO_LSP_TRANSPORT,
) -> dict:
    """Build or rebuild the graph in the caller-owned connection."""
    resolved_db = db_path or str(_resolve_store().db)
    in_memory = repo_filter is None
    cur = conn.cursor()
    log = _log if verbose else lambda *a: None
    emit = progress or (lambda *a, **k: None)

    files, skips = _scan_workspace_with_skips(workspace, repo_filter=repo_filter)
    if not files:
        log("No source files found.")
        skip_count = _record_skips(cur, skips) if skips else 0
        conn.commit()
        if skip_count and in_memory:
            backup_to(conn, resolved_db)
        return {
            "repos": 0,
            "files": 0,
            "symbols": 0,
            "edges": 0,
            "imports": 0,
            "skipped": skip_count,
            "parse_errors": 0,
            "resolution": {"exact": 0, "ambiguous": 0, "unresolved": 0},
        }

    emit("scan", files=len(files), skips=len(skips))

    # Bucket files by repo once so the per-repo language inference below is
    # O(files) total rather than O(repos x files).
    repos_seen: Dict[str, scanner_mod.FileInfo] = {}
    files_by_repo: Dict[str, List[scanner_mod.FileInfo]] = {}
    for f in files:
        if f.repo not in repos_seen:
            repos_seen[f.repo] = f
        files_by_repo.setdefault(f.repo, []).append(f)

    # Workspace-relative repos.path keeps the graph store portable.
    ws_root = Path(workspace).resolve()
    for repo_name, sample in repos_seen.items():
        from ..utils.git import get_remote_url

        try:
            rel_repo_path = str(Path(sample.repo_path).resolve().relative_to(ws_root))
        except ValueError:
            # repo_path not under workspace (shouldn't happen for discovered
            # repos, but be defensive): store "." so resolution still yields the
            # workspace root as a fallback.
            rel_repo_path = "."
        cur.execute(
            """INSERT INTO repos (id, name, path, language, git_remote, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, path=excluded.path,
                 language=excluded.language, git_remote=excluded.git_remote,
                 indexed_at=excluded.indexed_at""",
            (
                repo_name,
                repo_name,
                rel_repo_path,
                scanner_mod.infer_repo_language(files_by_repo.get(repo_name, [])),
                get_remote_url(sample.repo_path),
                _now(),
            ),
        )
    conn.commit()

    if repo_filter:
        # The marker must survive every later commit and clear after the last write.
        _set_repo_build_state(conn, repo_filter)
        _clear_repo(conn, repo_filter)  # on-disk, single repo: still needed
    elif not in_memory:
        for repo_name in repos_seen:
            _clear_repo(conn, repo_name)

    # Record auditable exclusions. Done after _clear_repo so the rows reflect
    # this build's filtering, fresh each time.
    skip_count = _record_skips(cur, skips)
    if skip_count:
        log(f"  recorded {skip_count} skipped files "
            f"(gitignored / default-skip / config-exclude / size-cap)")
    conn.commit()

    symbol_count = 0
    edge_count = 0
    import_count = 0
    file_count = 0
    skip_count_summary = skip_count

    # First pass: parse all files
    parsed_results, parse_errors = _parse_all(files, verbose, progress)

    # Second pass: insert results into database
    file_count, symbol_count, edge_count, import_count, repo_edges_by_file = _insert_results(
        conn, parsed_results, verbose, in_memory, progress
    )

    # Third pass: resolve all edge targets
    resolution_stats = _resolve_all(conn, repo_edges_by_file, in_memory, verbose, progress)

    lsp_report = None
    if lsp is None or lsp:
        # Tri-state: True forces the pass unbounded, None (the default) runs
        # it under the configured budgets, False skips it entirely.
        budgets: dict = {}
        if lsp is None:
            from .config import load_config

            lsp_cfg = (load_config(workspace).lsp) or {}
            budgets = {
                "time_budget": lsp_cfg.get("budget_seconds", lsp_mod.DEFAULT_BUDGET_SECONDS),
                "edge_budget": lsp_cfg.get("edge_budget", lsp_mod.DEFAULT_EDGE_BUDGET),
            }
        if lsp_transport is _AUTO_LSP_TRANSPORT:
            lsp_report = lsp_mod.upgrade_ambiguous_edges(conn, workspace, **budgets)
        else:
            lsp_report = lsp_mod.upgrade_ambiguous_edges(
                conn, workspace, transport=lsp_transport, **budgets
            )
        upgraded = lsp_report.get("upgraded", 0)
        if upgraded:
            resolution_stats["exact"] += upgraded
            resolution_stats["ambiguous"] -= upgraded

    try:
        import_edges = materialize_import_edges(conn)
        log(f"  materialized {import_edges} module imports edges")
    except Exception as e:
        log(f"  imports-edge materialization failed: {e}")

    # Fifth pass: configured SCIP indexes as an edges-only calls/references
    # overlay over the resolved graph. Best-effort by contract: a missing
    # runtime or corrupt index degrades to tree-sitter, never fails the build.
    scip_report = _apply_scip_overlay(conn, workspace, repos_seen, verbose)

    if repo_filter:
        _clear_repo_build_state(conn, repo_filter)

    if in_memory:
        # Close out the single implicit transaction that's been open across
        # the whole build (no periodic commits for in-memory builds) before
        # handing the connection to the backup API.
        conn.commit()
        emit("persist")
        log("  persisting in-memory graph to disk...")
        backup_to(conn, resolved_db)         # single dump

    summary = {
        "repos": len(repos_seen),
        "files": file_count,
        "symbols": symbol_count,
        "edges": edge_count,
        "imports": import_count,
        "skipped": skip_count_summary,
        "parse_errors": parse_errors,
        "resolution": resolution_stats,
    }
    if lsp_report is not None:
        summary["lsp"] = lsp_report
    if scip_report is not None:
        summary["scip"] = scip_report
    log(f"Done: {summary}")
    return summary


def build_graph(
    workspace: str = scanner_mod.DEFAULT_WORKSPACE,
    repo_filter: Optional[str] = None,
    db_path: Optional[str] = None,
    verbose: bool = False,
    progress=None,
    lsp: Optional[bool] = None,
    lsp_transport: object = _AUTO_LSP_TRANSPORT,
) -> dict:
    """Build or rebuild the graph and return summary and phase timing stats."""
    resolved_db = db_path or str(_resolve_store().db)
    in_memory = repo_filter is None
    if (
        in_memory
        and Path(resolved_db).resolve() == _resolve_store(workspace).db.resolve()
    ):
        from .worktree import prepare_worktree_graph

        prepare_worktree_graph(workspace)

    started_epoch = time.time()
    phase_ts: dict[str, float] = {}
    user_progress = progress

    def _timing_progress(phase, *args, **kwargs):
        _record_phase_ts(phase_ts, phase, time.time())
        if user_progress is not None:
            return user_progress(phase, *args, **kwargs)

    if in_memory:
        # Full rebuild: bulk-load into memory, then persist once via backup_to
        # (which takes the build lock itself for the on-disk swap).
        conn = get_build_db()
        try:
            summary = _build_graph_impl(
                conn=conn,
                workspace=workspace,
                repo_filter=repo_filter,
                db_path=db_path,
                verbose=verbose,
                progress=_timing_progress,
                lsp=lsp,
                lsp_transport=lsp_transport,
            )
        finally:
            conn.close()
    else:
        # Single-repo rebuild: writes the live DB directly (can't clobber other
        # repos), so take the advisory build lock to serialize against concurrent
        # builds/updates of the same DB.
        with build_lock(resolved_db):
            conn = init_db(resolved_db)
            try:
                summary = _build_graph_impl(
                    conn=conn,
                    workspace=workspace,
                    repo_filter=repo_filter,
                    db_path=db_path,
                    verbose=verbose,
                    progress=_timing_progress,
                    lsp=lsp,
                    lsp_transport=lsp_transport,
                )
            finally:
                conn.close()

    duration_s = time.time() - started_epoch
    _record_build(resolved_db, "build", summary, started_epoch, duration_s, phase_ts)
    return summary


def _record_build(
    db_path: Optional[str],
    kind: str,
    summary: dict,
    started_epoch: float,
    duration_s: float,
    phase_ts: dict[str, float],
) -> None:
    """Persist build summary counts without raising."""
    resolution = summary.get("resolution") or {}
    phase_timings = _phase_durations(phase_ts, started_epoch, started_epoch + duration_s)
    record_build_run(
        db_path,
        kind,
        started_at=started_epoch,
        duration_s=duration_s,
        phase_timings=phase_timings or None,
        repos=summary.get("repos"),
        files=summary.get("files"),
        symbols=summary.get("symbols"),
        edges=summary.get("edges"),
        resolution_exact=resolution.get("exact"),
        resolution_ambiguous=resolution.get("ambiguous"),
        resolution_unresolved=resolution.get("unresolved"),
        parse_errors=summary.get("parse_errors"),
        skipped=summary.get("skipped"),
    )


# Phase markers whose FIRST occurrence bounds a phase. Everything else (done
# markers, progress ticks) is recorded last-seen so a multi-repo resolve_done
# spans the full window rather than just the first repo.
_PHASE_FIRST_SEEN = frozenset({"scan", "parse_done", "resolve_start", "persist"})


def _record_phase_ts(phase_ts: dict[str, float], phase: str, ts: float) -> None:
    if phase in _PHASE_FIRST_SEEN:
        phase_ts.setdefault(phase, ts)
    else:
        phase_ts[phase] = ts


def _phase_durations(phase_ts: dict[str, float], started: float, ended: float) -> dict:
    """Return millisecond-rounded durations for observed build phases."""
    scan = phase_ts.get("scan")
    parse_done = phase_ts.get("parse_done")
    resolve_start = phase_ts.get("resolve_start")
    # resolve_done is last-seen (multi-repo): the final repo's completion.
    resolve_done = phase_ts.get("resolve_done")
    out: dict[str, float] = {}
    if scan:
        out["scan"] = round(scan - started, 3)
    if scan and parse_done:
        out["parse"] = round(parse_done - scan, 3)
    if parse_done and resolve_start:
        out["insert"] = round(resolve_start - parse_done, 3)
    if resolve_start and resolve_done:
        out["resolve"] = round(resolve_done - resolve_start, 3)
    if resolve_done:
        out["persist"] = round(ended - resolve_done, 3)
    return out


def _cairn_workers() -> Optional[int]:
    """Return the effective parse worker count, or None when unset."""
    raw = os.environ.get("CAIRN_WORKERS")
    if not raw:
        return None
    try:
        return max(1, min(int(raw), 256))
    except ValueError:
        return None


def _iso_ts(epoch: Optional[float]) -> str:
    """ISO-8601 UTC timestamp from an epoch (or now), matching ``_now()`` shape."""
    if epoch is None:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def record_build_run(
    db_path: Optional[str],
    kind: str,
    *,
    started_at: Optional[float] = None,
    duration_s: Optional[float] = None,
    phase_timings: Optional[dict] = None,
    repos: Optional[int] = None,
    files: Optional[int] = None,
    symbols: Optional[int] = None,
    edges: Optional[int] = None,
    resolution_exact: Optional[int] = None,
    resolution_ambiguous: Optional[int] = None,
    resolution_unresolved: Optional[int] = None,
    parse_errors: Optional[int] = None,
    skipped: Optional[int] = None,
    workers: Optional[int] = None,
    session_id: Optional[str] = None,
) -> None:
    """Persist one best-effort build-run row and never raise."""
    try:
        from ..telemetry import sink as _sink

        if _sink.is_telemetry_off():
            return
    except Exception:
        pass
    try:
        conn = get_db(db_path)
        try:
            conn.execute(
                """INSERT INTO build_runs
                   (kind, started_at, duration_s, phase_timings, repos, files,
                    symbols, edges, resolution_exact, resolution_ambiguous,
                    resolution_unresolved, parse_errors, skipped, workers,
                    session_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    kind,
                    _iso_ts(started_at),
                    duration_s,
                    json.dumps(phase_timings) if phase_timings is not None else None,
                    repos,
                    files,
                    symbols,
                    edges,
                    resolution_exact,
                    resolution_ambiguous,
                    resolution_unresolved,
                    parse_errors,
                    skipped,
                    workers if workers is not None else _cairn_workers(),
                    session_id or os.environ.get("CAIRN_SESSION", "unknown"),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        _logger.debug("build_runs insert failed (kind=%s)", kind, exc_info=True)


def _parse_file_worker(args: tuple[str, str, str, str]) -> tuple[str, str, str, str, Optional[ParsedFile], Optional[str], Optional[str]]:
    """Parse one worker argument tuple and return its result tuple."""
    import traceback
    path, rel_path, language, repo = args
    try:
        parser = get_parser(language)
        if parser is None:
            return path, rel_path, language, repo, None, f"No parser for {language}", None
        pf = parser.parse(path)
        return path, rel_path, language, repo, pf, None, None
    except Exception as e:
        err_msg = str(e)
        st = traceback.format_exc()
        return path, rel_path, language, repo, None, err_msg, st


def ensure_repo_row(cur, repo: str) -> None:
    """Insert a placeholder repos row when absent so repo_id FKs hold."""
    cur.execute(
        """INSERT INTO repos (id, name, path, language, git_remote, indexed_at)
           VALUES (?, ?, ?, '', NULL, ?)
           ON CONFLICT(id) DO NOTHING""",
        (repo, repo, ".", _now()),
    )


def insert_parsed_file(
    cur,
    repo: str,
    rel_path: str,
    abs_path: str,
    language: str,
    file_hash: str,
    pf: ParsedFile,
    name_to_symbol_ids: Dict[str, List[tuple]],
    repo_edges_by_file: Dict[str, Dict[str, List[tuple]]],
) -> tuple[int, int, int]:
    """Insert one parsed file and return symbol, edge, and import counts."""
    ensure_repo_row(cur, repo)
    file_id = _new_id()
    # Populate size and mtime for catch-up reconciliation.
    try:
        st = Path(abs_path).stat()
        file_size = st.st_size
        file_mtime = st.st_mtime
    except OSError:
        file_size, file_mtime = 0, 0.0
    repository = GraphRepository()
    repository.insert_files(
        cur,
        [(file_id, repo, rel_path, language, file_hash, pf.line_count, _now(), file_size, file_mtime)],
    )

    # Accumulate rows and flush with executemany (one round-trip per table
    # instead of one execute() per row).
    sym_rows: List[tuple] = []
    imp_rows: List[tuple] = []
    edge_rows: List[tuple] = []

    # --- symbols: recording ids for same-file source resolution -----------
    # Derive parent_scope and a file-level imports_summary at build time when
    # the parser didn't supply them; `body` stays None unless the parser set it.
    file_imports_summary = ", ".join(
        imp.imported_path for imp in pf.imports[:20]
    ) or None  # file-level summary, identical for every symbol in this file
    # qualified_name -> symbol id, for contains-edge parent resolution.
    qname_ids: Dict[str, str] = {}
    for sym in pf.symbols:
        sym_id = _new_id()
        # Enclosing scope is everything before the last "." of the qualified
        # name (e.g. "com.foo.Bar.baz" -> "com.foo.Bar"). None when there is
        # no qualifier (top-level symbol) or the parser already set it.
        if sym.parent_scope is None and sym.qualified_name and "." in sym.qualified_name:
            sym.parent_scope = sym.qualified_name.rsplit(".", 1)[0]
        if sym.imports_summary is None:
            sym.imports_summary = file_imports_summary
        if sym.qualified_name:
            qname_ids.setdefault(sym.qualified_name, sym_id)
        sym_rows.append((
            sym_id,
            file_id,
            sym.name,
            sym.qualified_name,
            sym.kind,
            sym.line_start,
            sym.line_end,
            sym.column_start,
            sym.column_end,
            sym.docstring,
            json.dumps(sym.modifiers),
            json.dumps(sym.metadata) if sym.metadata is not None else None,
            sym.parameters,
            sym.return_type,
            sym.parent_scope,
            sym.imports_summary,
            sym.body,
            sym.arity,
        ))
        name_to_symbol_ids.setdefault(sym.name, []).append((sym_id, repo, file_id))

        # Append after declared symbols so a same-stem code symbol wins edges.
    module_id = _new_id()
    module_name = Path(rel_path).stem
    module_qname = _module_dotted(rel_path)
    sym_rows.append((
        module_id,
        file_id,
        module_name,
        module_qname,
        "module",
        1,
        1,
        0,
        0,
        None,
        "[]",
        None,
        None,
        None,
        None,
        file_imports_summary,
        None,
        None,
    ))
    name_to_symbol_ids.setdefault(module_name, []).append((module_id, repo, file_id))

    # --- imports ------------------------------------------------------------
    for imp in pf.imports:
        imp_rows.append((_new_id(), file_id, imp.imported_path, None, imp.line, imp.local_alias))

        # Keep lookup file-local to avoid quadratic global symbol scans.
    in_file: Dict[str, List[str]] = {}
    for sym, row in zip(pf.symbols, sym_rows):
        in_file.setdefault(sym.name, []).append(row[0])
    # The module symbol's entry lands last: a code symbol with the same name
    # keeps first-wins ownership of that name's edges.
    in_file.setdefault(module_name, []).append(module_id)

    for sym, row in zip(pf.symbols, sym_rows):
        child_id = row[0]
        if sym.parent_scope:
            parent_id = qname_ids.get(sym.parent_scope)
            if parent_id is None:
                parent_id = in_file.get(
                    sym.parent_scope.rsplit(".", 1)[-1], [None]
                )[0]
        else:
            parent_id = module_id
        if parent_id:
            edge_rows.append((
                _new_id(), parent_id, child_id, sym.name, "contains",
                sym.line_start, sym.column_start, "exact",
            ))

    # --- edges: source resolved now; target left NULL for the resolver -----
    file_edges = repo_edges_by_file.setdefault(repo, {}).setdefault(file_id, [])
    for edge in pf.edges:
        source_ids = in_file.get(edge.source_name, [])
        source_id = source_ids[0] if source_ids else None
        if source_id is None:
            if not edge.source_name:
                # Module-level code: attach to the file's module symbol.
                source_id = module_id
            else:
                continue  # owner name not declared in this file; cannot attach
        edge_id = _new_id()
        edge_rows.append((
            edge_id, source_id, None, edge.target_name, edge.kind,
            edge.line, edge.column, None,
        ))
        # Tuple slots 6-8 carry resolver signals; missing values abstain.
        file_edges.append((
            edge_id, source_id, edge.target_name, edge.line, edge.column,
            getattr(edge, "receiver_type", None),
            getattr(edge, "call_arity", None),
            getattr(edge, "generic_tier", False),
        ))

    if sym_rows:
        repository.insert_symbols(cur, sym_rows)
    if imp_rows:
        repository.insert_imports(cur, imp_rows)
    if edge_rows:
        repository.insert_edges(cur, edge_rows)
    if pf.rationale:
        repository.insert_rationale(cur, _rationale_rows(pf, file_id, sym_rows))

    return len(sym_rows), len(edge_rows), len(imp_rows)


# Symbol kinds a rationale record can be attributed to: innermost containing
# callable or type wins; module/property/variable/route kinds never attribute.
_CALLABLE_OR_TYPE_KINDS = frozenset({
    "function", "method", "constructor",
    "class", "interface", "enum", "protocol", "mixin", "implementation",
})


def _rationale_rows(pf: ParsedFile, file_id: str, sym_rows: List[tuple]) -> List[tuple]:
    """Attribute each record to the innermost containing callable/type, else None."""
    spans = [
        (row[0], sym.line_start, sym.line_end)
        for sym, row in zip(pf.symbols, sym_rows)
        if sym.kind in _CALLABLE_OR_TYPE_KINDS
    ]
    rows: List[tuple] = []
    for record in pf.rationale:
        symbol_id = None
        best = None
        for sym_id, line_start, line_end in spans:
            width = line_end - line_start
            if (
                line_start <= record.line <= line_end
                and (best is None or width < best)
            ):
                symbol_id, best = sym_id, width
        rows.append((_new_id(), file_id, symbol_id, record.line, record.kind, record.text))
    return rows


def _module_dotted(rel_path: str) -> str:
    """Return a package-aware dotted module path."""
    parts = list(Path(rel_path).with_suffix("").parts)
    if len(parts) > 1 and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _norm_module_token(token: str) -> str:
    """Normalize an import token to a dotted module path."""
    t = token.strip().strip("'\"").replace("\\", ".")
    segs = [s for s in t.replace("/", ".").split(".") if s]
    return ".".join(segs)


def _dotted_suffixes(dotted: str) -> List[str]:
    """All dotted suffixes, longest first ("a.b.c" -> ["a.b.c", "b.c", "c"])."""
    segs = [s for s in dotted.split(".") if s]
    return [".".join(segs[i:]) for i in range(len(segs))]


def _import_module_bases(raw: str) -> List[str]:
    """Return normalized import bases across supported parser dialects."""
    text = raw.strip().rstrip(";").strip()
    bases: List[str] = []

    def _add(module_token: str, name_tokens: List[str]) -> None:
        module = _norm_module_token(module_token)
        if not module:
            return
        for name in name_tokens:
            n = name.strip().strip("{}").split(" as ")[0].strip()
            if n:
                bases.append(f"{module}.{_norm_module_token(n)}")
        bases.append(module)

    if text.startswith("from "):
        rest = text[len("from "):].strip()
        module_tok, sep, names_part = rest.partition(" import ")
        _add(module_tok, names_part.split(",") if sep else [])
    elif text.startswith("import "):
        rest = text[len("import "):].strip()
        if " from " in rest:
            names_part, _, module_tok = rest.partition(" from ")
            _add(module_tok, names_part.strip("{} \t").split(","))
        else:
            rest = rest.split(" as ")[0].strip()
            if rest.startswith("static "):
                rest = rest[len("static "):].strip()
            bases.append(_norm_module_token(rest))
    else:
        bases.append(_norm_module_token(text))
    return [b for b in bases if b]


def materialize_import_edges(
    conn,
    repo: Optional[str] = None,
    file_ids: Optional[List[str]] = None,
) -> int:
    """Rebuild import edges and return the number inserted."""
    module_rows = conn.execute(
        """SELECT s.id AS mid, f.id AS fid, f.path, f.repo_id
           FROM symbols s JOIN files f ON s.file_id = f.id
           WHERE s.kind = 'module'"""
    ).fetchall()

    if repo is not None:
        source_rows = [r for r in module_rows if r["repo_id"] == repo]
    elif file_ids is not None:
        wanted = set(file_ids)
        source_rows = [r for r in module_rows if r["fid"] in wanted]
    else:
        source_rows = module_rows

    source_ids = [r["mid"] for r in source_rows]
    if not source_ids:
        return 0

    # candidate -> [file ids]; module symbol per file (first wins on the
    # impossible-in-practice duplicate-module-per-file case).
    module_by_fid: Dict[str, str] = {}
    files_by_candidate: Dict[str, List[str]] = {}
    module_qname_by_fid: Dict[str, str] = {}
    for r in module_rows:
        fid, mid = r["fid"], r["mid"]
        if fid in module_by_fid:
            continue
        module_by_fid[fid] = mid
        module_qname_by_fid[fid] = _module_dotted(r["path"])
        for cand in _dotted_suffixes(_module_dotted(r["path"])):
            files_by_candidate.setdefault(cand, []).append(fid)

    imp_scope = ""
    imp_params: List[str] = []
    if repo is not None:
        imp_scope = " WHERE i.file_id IN (SELECT id FROM files WHERE repo_id = ?)"
        imp_params.append(repo)
    elif file_ids is not None:
        ph = ",".join("?" for _ in file_ids)
        imp_scope = f" WHERE i.file_id IN ({ph})"
        imp_params.extend(file_ids)
    import_rows = conn.execute(
        f"SELECT i.file_id, i.imported_path, i.line FROM imports i{imp_scope}",
        imp_params,
    ).fetchall()

    ph = ",".join("?" for _ in source_ids)
    conn.execute(
        f"DELETE FROM edges WHERE kind = 'imports' AND source_id IN ({ph})",
        source_ids,
    )

    edge_rows: List[tuple] = []
    for r in import_rows:
        source_mid = module_by_fid.get(r["file_id"])
        if not source_mid:
            continue
        for base in _import_module_bases(r["imported_path"]):
            matched: Optional[str] = None
            for cand in _dotted_suffixes(base):
                fids = files_by_candidate.get(cand)
                if not fids:
                    continue  # try a shorter suffix
                if len(fids) == 1:
                    matched = fids[0]
                    break
                break  # ambiguous at this length; shorter is never safer
            if matched is None or matched == r["file_id"]:
                continue
            edge_rows.append((
                _new_id(), source_mid, module_by_fid[matched],
                module_qname_by_fid[matched], "imports",
                r["line"] or 0, 0, "exact",
            ))
            break  # first base that resolves wins
    if edge_rows:
        GraphRepository().insert_edges(conn, edge_rows)
    return len(edge_rows)


def insert_parse_error(cur, repo: str, path: str, error_message: str, stack_trace: str | None = None):
    ensure_repo_row(cur, repo)
    cur.execute(
        """INSERT INTO parse_errors (id, file_path, repo_id, error_message, stack_trace, timestamp)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (_new_id(), path, repo, error_message, stack_trace, _now()),
    )


def _set_repo_build_state(conn, repo_name: str) -> None:
    """Mark a repo as mid-rebuild (crash window). Committed immediately so the
    marker is durable before _clear_repo's commit makes old rows disappear."""
    conn.execute(
        """INSERT INTO repo_build_state (repo_id, state, started_at)
           VALUES (?, 'building', ?)
           ON CONFLICT(repo_id) DO UPDATE SET
             state='building', started_at=excluded.started_at""",
        (repo_name, _now()),
    )
    conn.commit()


def _clear_repo_build_state(conn, repo_name: str) -> None:
    """Clear the mid-rebuild marker once the rebuild's final commit landed."""
    conn.execute("DELETE FROM repo_build_state WHERE repo_id = ?", (repo_name,))
    conn.commit()


def repo_build_in_progress(conn, repo: str) -> bool:
    """Return whether a repo has an interrupted on-disk rebuild marker."""
    try:
        row = conn.execute(
            "SELECT 1 FROM repo_build_state WHERE repo_id = ? AND state = 'building'",
            (repo,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False  # table missing on a pre-marker DB
    return row is not None


def _clear_repo(conn, repo_name: str):
    """Delete one repo graph while preserving incoming edge names."""
    cur = conn.cursor()
    repo_symbol_ids_subquery = (
        "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id WHERE f.repo_id = ?"
    )
    # 0. Delete parse errors
    cur.execute("DELETE FROM parse_errors WHERE repo_id = ?", (repo_name,))
    # 0b. Delete recorded skips for this repo so a rebuild doesn't accumulate
    #     stale skip rows.
    cur.execute("DELETE FROM skipped_files WHERE repo_id = ?", (repo_name,))
    # Preserve target names while demoting resolution for deleted symbols.
    cur.execute(
        f"UPDATE edges SET target_name = "
        f"COALESCE(target_name, (SELECT name FROM symbols WHERE id = edges.target_id)), "
        f"target_id = NULL, resolution = 'unresolved' "
        f"WHERE target_id IN ({repo_symbol_ids_subquery})",
        (repo_name,),
    )
    # 2. Delete edges whose source is in this repo.
    cur.execute(
        f"DELETE FROM edges WHERE source_id IN ({repo_symbol_ids_subquery})",
        (repo_name,),
    )
    # 3. Delete imports for this repo.
    cur.execute(
        "DELETE FROM imports WHERE file_id IN (SELECT id FROM files WHERE repo_id = ?)",
        (repo_name,),
    )
    # 3b. Delete rationale rows before their FK parents (files/symbols).
    try:
        cur.execute(
            "DELETE FROM rationale WHERE file_id IN "
            "(SELECT id FROM files WHERE repo_id = ?)",
            (repo_name,),
        )
    except sqlite3.OperationalError:
        pass  # rationale table missing on a pre-marker DB
    # ANN rows must die with relational rows to avoid stale rowid reuse.
    try:
        # Sync the vec0 index for the doomed rowids (same rationale as the
        # incremental path): a stale vec entry can pair a REUSED rowid with an
        # unrelated vector. No-op when the ANN backend is off.
        doomed = cur.execute(
            "SELECT model, rowid FROM embeddings WHERE symbol_id IN "
            "(SELECT id FROM symbols WHERE file_id IN "
            "(SELECT id FROM files WHERE repo_id = ?))",
            (repo_name,),
        ).fetchall()
        cur.execute(
            "DELETE FROM embeddings WHERE symbol_id IN "
            "(SELECT id FROM symbols WHERE file_id IN "
            "(SELECT id FROM files WHERE repo_id = ?))",
            (repo_name,),
        )
        if doomed:
            from .ann_index import delete_index_rows

            for model in {r["model"] for r in doomed}:
                delete_index_rows(
                    conn,
                    model,
                    [r["rowid"] for r in doomed if r["model"] == model],
                )
        # embeddings_mv also FK-references symbols(id) (no cascade); no
        # vecmv rowid cleanup exists (vecmv_ tables rebuild wholesale).
        cur.execute(
            "DELETE FROM embeddings_mv WHERE symbol_id IN "
            "(SELECT id FROM symbols WHERE file_id IN "
            "(SELECT id FROM files WHERE repo_id = ?))",
            (repo_name,),
        )
    except sqlite3.OperationalError as e:
        note_contention("builder.delete_repo_embeddings", error=e)
        pass  # embeddings table missing on a DB that never had the semantic extra
    # 4. Now safe to delete symbols.
    cur.execute(
        "DELETE FROM symbols WHERE file_id IN (SELECT id FROM files WHERE repo_id = ?)",
        (repo_name,),
    )
    # 5. Delete files.
    cur.execute("DELETE FROM files WHERE repo_id = ?", (repo_name,))
    conn.commit()


def _log(*args):
    print(*args)
