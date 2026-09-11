"""Graph builder: scan -> parse -> store -> resolve edges.

Orchestrates the indexing pipeline. For each repo:
  1. Scan source files (scanner)
  2. Parse each file (parser for its language)
  3. Insert symbols + imports + edges (edges initially unresolved)
  4. Resolve edge targets via the import-aware resolver (src/graph/resolver.py)

An edge is resolved only when exactly one candidate exists in a tier
(same-file -> import-aware -> same-repo -> global); otherwise it is marked
``ambiguous`` and left unresolved. Precise-by-default queries trust only
``resolution='exact'`` rows; ``--fuzzy`` re-enables matching by the preserved
``target_name``.

Crash-recovery contract (single-repo on-disk rebuilds): the on-disk path
commits mid-rebuild (every 500 files, to bound WAL lock hold time), so a crash
or killed build can leave the repo cleared-but-partial with no error on the
next open. ``repo_build_in_progress`` reports that state; the recovery is to
re-run ``cairn build --repo <repo>``.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..parsers.base import BaseParser, ParsedFile
from ..parsers.kotlin import KotlinParser
from ..parsers import routes as routes_mod
from ..parsers import service_calls as service_calls_mod
from . import scanner as scanner_mod
from . import resolver as resolver_mod
from .schema import init_db, get_build_db, get_db, backup_to, build_lock, note_contention
from ..paths import resolve_store as _resolve_store

_logger = logging.getLogger(__name__)

# Language -> parser class.
PARSERS: Dict[str, BaseParser] = {}
_parser_instances: Dict[str, BaseParser] = {}


def get_parser(language: str) -> Optional[BaseParser]:
    if language not in _parser_instances:
        cls = {
            "kotlin": KotlinParser,
            "java": None,  # filled below to avoid import cycle risk
            "swift": None,
            "python": None,
            "typescript": None,
            "javascript": None,
            "dart": None,
            "objc": None,
            "go": None,
            "php": None,
            "ruby": None,
            "csharp": None,
            "c": None,
            "cpp": None,
        }.get(language)
        # Lazy imports to avoid loading all parsers if only one language is used.
        if language == "java":
            from ..parsers.java import JavaParser

            _parser_instances["java"] = JavaParser()
        elif language == "swift":
            from ..parsers.swift import SwiftParser

            _parser_instances["swift"] = SwiftParser()
        elif language == "python":
            from ..parsers.python_parser import PythonParser

            _parser_instances["python"] = PythonParser()
        elif language == "typescript":
            from ..parsers.typescript import TypeScriptParser

            _parser_instances["typescript"] = TypeScriptParser()
        elif language == "javascript":
            from ..parsers.typescript import JavaScriptParser

            _parser_instances["javascript"] = JavaScriptParser()
        elif language == "dart":
            from ..parsers.dart import DartParser

            _parser_instances["dart"] = DartParser()
        elif language == "objc":
            from ..parsers.objc import ObjCParser

            _parser_instances["objc"] = ObjCParser()
        elif language == "go":
            from ..parsers.go import GoParser

            _parser_instances["go"] = GoParser()
        elif language == "php":
            from ..parsers.php import PhpParser

            _parser_instances["php"] = PhpParser()
        elif language == "ruby":
            from ..parsers.ruby import RubyParser

            _parser_instances["ruby"] = RubyParser()
        elif language == "csharp":
            from ..parsers.csharp import CSharpParser

            _parser_instances["csharp"] = CSharpParser()
        elif language == "c":
            from ..parsers.c_family import CParser

            _parser_instances["c"] = CParser()
        elif language == "cpp":
            from ..parsers.c_family import CppParser

            _parser_instances["cpp"] = CppParser()
        elif cls is not None:
            _parser_instances[language] = cls()
        else:
            return None
    return _parser_instances.get(language)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _scan_workspace_with_skips(
    workspace: str, repo_filter: Optional[str] = None
) -> tuple[list, list]:
    """Scan the workspace, returning (files_to_index, skips).

    Records skips in the skipped_files table; falls back to scan_workspace if
    a repo has no source files.
    """

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
    """Insert SkipInfo rows into skipped_files. Returns count recorded.

    Called after _clear_repo so a rebuild doesn't leave stale skip rows.
    Best-effort: a skip insert failure must not abort the build.
    """
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
    """Insert parsed results into the database.

    Returns (file_count, symbol_count, edge_count, import_count, repo_edges_by_file).
    """
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
        # No periodic commit for in-memory builds (nothing to fsync mid-build;
        # the single backup_to() at the end is the durability boundary). The
        # on-disk path commits every 500 files to bound WAL lock hold time and
        # let concurrent readers make progress.
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
    """Resolve all edge targets per repo.

    Returns resolution_stats dict with keys: exact, ambiguous, unresolved.
    """
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


def _build_graph_impl(
    conn,
    workspace: str = scanner_mod.DEFAULT_WORKSPACE,
    repo_filter: Optional[str] = None,
    db_path: Optional[str] = None,
    verbose: bool = False,
    progress=None,
) -> dict:
    """Build (or rebuild) the graph into ``conn`` (opened by the caller).

    See ``build_graph`` for the full contract (repo_filter, verbose, progress).
    """
    resolved_db = db_path or str(_resolve_store().db)
    in_memory = repo_filter is None
    cur = conn.cursor()
    log = _log if verbose else lambda *a: None
    emit = progress or (lambda *a, **k: None)

    files, skips = _scan_workspace_with_skips(workspace, repo_filter=repo_filter)
    if not files:
        log("No source files found.")
        return {"repos": 0, "files": 0, "symbols": 0, "edges": 0, "imports": 0}

    emit("scan", files=len(files), skips=len(skips))

    # Bucket files by repo once so the per-repo language inference below is
    # O(files) total rather than O(repos x files).
    repos_seen: Dict[str, scanner_mod.FileInfo] = {}
    files_by_repo: Dict[str, List[scanner_mod.FileInfo]] = {}
    for f in files:
        if f.repo not in repos_seen:
            repos_seen[f.repo] = f
        files_by_repo.setdefault(f.repo, []).append(f)

    # Insert repo records (or update indexed_at).
    # repos.path is stored WORKSPACE-relative (e.g. "." for single-repo, or the
    # repo dir name for multi-repo) so the .kg file is portable across machines.
    # The absolute root is reconstructed at read time via resolve_repo_path()
    # (see scanner.resolve_file_path).
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

    # Clear existing data before (re)building. For a single-repo build, only
    # that repo is cleared; for a full-workspace build, each discovered repo is
    # cleared so file rows don't collide on the UNIQUE(repo_id, path) constraint.
    # A fresh in-memory DB starts empty, so the full-rebuild path can skip
    # clearing entirely -- there is nothing to clear.
    if repo_filter:
        # Crash-window marker: durable BEFORE _clear_repo commits, so a crash
        # at any later commit boundary (clear, periodic 500-file commits,
        # resolve, imports materialization) leaves a detectable 'building'
        # row instead of a silently partial repo. Cleared after the build's
        # last write; `cairn doctor` surfaces a stale marker as an
        # interrupted rebuild.
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

    # Fourth pass: materialize module->module imports edges from the imports
    # table (needs every file's module symbol present, so it runs after the
    # insert+resolve passes; kind='imports' stays outside
    # STRUCTURAL_EDGE_KINDS, so traversal semantics are unchanged).
    try:
        import_edges = materialize_import_edges(conn)
        log(f"  materialized {import_edges} module imports edges")
    except Exception as e:
        log(f"  imports-edge materialization failed: {e}")

    if repo_filter:
        # Single-repo rebuild complete and committed (insert final commit +
        # per-repo resolve commits + imports materialization above): out of
        # the crash window, clear the marker. An exception anywhere above
        # leaves it in place -- the repo really is partial. The clear is the
        # build path's last write so a crash during any earlier write stays
        # detectable.
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
    log(f"Done: {summary}")
    return summary


def build_graph(
    workspace: str = scanner_mod.DEFAULT_WORKSPACE,
    repo_filter: Optional[str] = None,
    db_path: Optional[str] = None,
    verbose: bool = False,
    progress=None,
) -> dict:
    """Build (or rebuild) the graph. Returns summary stats.

    A full-workspace rebuild (repo_filter is None) builds in an in-memory
    SQLite database with bulk-load pragmas, then persists to disk once at the
    end via backup_to(). A single-repo rebuild (repo_filter set) keeps the
    on-disk path so it doesn't clobber the other repos already in the DB.

    ``verbose``: when True, per-file detail (parse errors, route-detection
    failures, per-batch insert counts) is logged. Default False -- most
    callers want the high-level progress, not per-file noise.

    ``progress``: optional callable receiving phase events the caller can
    render as a progress bar or themed log. Event shapes (first arg is the
    phase name, the rest are kwargs/values specific to that phase):

        progress("scan", files=N, skips=M)
        progress("parse_progress", done=k, total=N)
        progress("parse_done", parsed=P, errors=E)
        progress("insert_progress", done=k, total=N, symbols=S, edges=E)
        progress("resolve_start", repo=R)
        progress("resolve_done", repo=R, stats={...})
        progress("persist")

    A no-op default (None) preserves the silent contract for library callers.
    """
    resolved_db = db_path or str(_resolve_store().db)
    in_memory = repo_filter is None

    # Capture phase timings from the progress callbacks (spec observability-
    # telemetry 6.2). First-seen timestamp for phase-start markers, last-seen
    # for done markers, so a multi-repo resolve span covers the whole window.
    # The caller's own progress callback still receives every event unchanged
    # (the golden progress-event test continues to pass).
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
                )
            finally:
                conn.close()

    # Persist a build_runs row on the resolved (on-disk) DB. For an in-memory
    # build backup_to() has already swapped the graph to disk by now, so the
    # row lands in the same DB as the rest of the graph. Best-effort: a
    # telemetry write must never fail a build (record_build_run swallows all
    # errors and logs at DEBUG -- spec 5.4/5.6, analytics not correctness).
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
    """Extract count/resolution columns from a build summary and persist them.

    Thin adapter so ``build_graph`` stays readable; the other entry points
    (embed/sync/incremental) call :func:`record_build_run` directly with the
    fewer columns they have.
    """
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
    """Best-available per-phase durations in seconds, keyed by phase name.

    Returns only the phases whose boundary markers actually fired (a single-repo
    build emits no ``persist``; an empty workspace emits nothing). Each value is
    rounded to milliseconds -- good enough for trending, avoids float noise.
    """
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
    """Resolved worker count from CAIRN_WORKERS, or None when unset/invalid.

    Mirrors the clamping in ``_parse_all`` so the recorded value reflects the
    parse fan-out that actually ran.
    """
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
    """Persist one ``build_runs`` row. Best-effort: never raises.

    ``build_runs`` is a structured per-run record (not a low-cardinality
    event), so this writes a direct INSERT on a short-lived connection rather
    than routing through the buffered telemetry sink. Telemetry is analytics,
    not correctness: every failure is swallowed and logged at DEBUG so a
    metrics write can never fail a build/sync/embed/incremental pass (spec
    observability-telemetry 5.4/5.6).

    ``db_path`` None resolves to the central store for the workspace (mirrors
    ``schema.get_db``). Count columns are all optional -- each entry point
    populates what it cheaply has and leaves the rest NULL.

    ``workers`` and ``session_id`` default from the environment
    (``CAIRN_WORKERS`` / ``CAIRN_SESSION``) so callers don't repeat that logic.
    """
    # CAIRN_TELEMETRY=off stops build-run recording too ("Set off to stop all
    # event and build-run recording", docs/configuration.md / spec 5.1). Lazy
    # import mirrors schema.note_contention's gating so the telemetry package
    # stays out of builder's import graph; a gating failure must not fail the
    # write (analytics, not correctness).
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
    """Worker: parse a single file in a separate process.

    ``args`` is (file_path, file_rel_path, file_language, file_repo); returns
    those plus (parsed_file, error_msg, stack_trace).
    """
    import traceback
    path, rel_path, language, repo = args
    from cairn.graph.builder import get_parser
    parser = get_parser(language)
    if parser is None:
        return path, rel_path, language, repo, None, f"No parser for {language}", None
    try:
        pf = parser.parse(path)
        return path, rel_path, language, repo, pf, None, None
    except Exception as e:
        err_msg = str(e)
        st = traceback.format_exc()
        return path, rel_path, language, repo, None, err_msg, st


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
    """Insert a single parsed file's symbols, imports, and raw edges.

    ``rel_path`` is the repo-relative path stored in ``files.path`` (portable);
    ``abs_path`` is the absolute path used only to stat for size/mtime.
    Returns (symbol_count, edge_count, import_count).
    """
    file_id = _new_id()
    # Populate size and mtime for catch-up reconciliation.
    try:
        st = Path(abs_path).stat()
        file_size = st.st_size
        file_mtime = st.st_mtime
    except OSError:
        file_size, file_mtime = 0, 0.0
    cur.execute(
        """INSERT INTO files (id, repo_id, path, language, hash, line_count, indexed_at, size, mtime)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (file_id, repo, rel_path, language, file_hash, pf.line_count, _now(), file_size, file_mtime),
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

    # --- module symbol: one per file ----------------------------------------
    # Owns module-level code (edges with no enclosing symbol) and the file's
    # import edges materialized post-resolution. Name is the file stem;
    # qualified name is the dotted repo-relative path so import statements
    # can be mapped onto it. Appended AFTER the declared symbols so a code
    # symbol sharing the stem name wins edge ownership (first-wins lookup).
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

    # Same-file symbol-name lookup for edge *source* resolution. Built from the
    # symbols inserted for THIS file only, rather than scanning the global
    # name_to_symbol_ids accumulator (which would make this O(total_symbols)
    # per file -> O(N^2) overall). The keys are exactly the names this file
    # declared, and the values are their symbol ids in this file.
    # (zip stops at pf.symbols, so the module row appended above is excluded.)
    in_file: Dict[str, List[str]] = {}
    for sym, row in zip(pf.symbols, sym_rows):
        in_file.setdefault(sym.name, []).append(row[0])
    # The module symbol's entry lands last: a code symbol with the same name
    # keeps first-wins ownership of that name's edges.
    in_file.setdefault(module_name, []).append(module_id)

    # --- contains edges: parent -> nested, module -> top-level --------------
    # Targets are pinned in-file (qualified-name keyed, bare-name fallback),
    # so these rows skip the resolver round-trip entirely (resolution='exact')
    # and never enter repo_edges_by_file.
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
        # Carry the parser's in-memory-only signals on the tuple for the
        # resolver: receiver_type (6th element, type-aware tier) and
        # call_arity (7th, the call's argument count for the within-tier
        # arity tiebreak); None when the parser didn't/couldn't infer them
        # (abstain-safe).
        file_edges.append((
            edge_id, source_id, edge.target_name, edge.line, edge.column,
            getattr(edge, "receiver_type", None),
            getattr(edge, "call_arity", None),
        ))

    if sym_rows:
        cur.executemany(
            """INSERT INTO symbols
               (id, file_id, name, qualified_name, kind, line_start, line_end,
                column_start, column_end, docstring, modifiers, metadata,
                parameters, return_type, parent_scope, imports_summary, body,
                arity, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'tree_sitter')""",
            sym_rows,
        )
    if imp_rows:
        cur.executemany(
            """INSERT INTO imports (id, file_id, imported_path, resolved_symbol_id, line, local_alias)
               VALUES (?,?,?,?,?,?)""",
            imp_rows,
        )
    if edge_rows:
        cur.executemany(
            """INSERT INTO edges
               (id, source_id, target_id, target_name, kind, line, column, resolution)
               VALUES (?,?,?,?,?,?,?,?)""",
            edge_rows,
        )

    return len(sym_rows), len(edge_rows), len(imp_rows)


def _module_dotted(rel_path: str) -> str:
    """Dotted form of a repo-relative file path ("src/a/b.py" -> "src.a.b").

    Package initializer files ("__init__.py") collapse to their directory
    ("pkg/__init__.py" -> "pkg") so package imports match the module.
    """
    parts = list(Path(rel_path).with_suffix("").parts)
    if len(parts) > 1 and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _norm_module_token(token: str) -> str:
    """Normalize one import path token to a dotted module path.

    Separators become dots and relative markers ("./", "../") drop out:
    "../utils/heap" -> "utils.heap", "cairn/graph/queries" ->
    "cairn.graph.queries".
    """
    t = token.strip().strip("'\"").replace("\\", ".")
    segs = [s for s in t.replace("/", ".").split(".") if s and set(s) != {"."}]
    return ".".join(segs)


def _dotted_suffixes(dotted: str) -> List[str]:
    """All dotted suffixes, longest first ("a.b.c" -> ["a.b.c", "b.c", "c"])."""
    segs = [s for s in dotted.split(".") if s]
    return [".".join(segs[i:]) for i in range(len(segs))]


def _import_module_bases(raw: str) -> List[str]:
    """Base module paths derived from one raw import statement/path.

    Handles the shapes the parsers store verbatim: Python
    "from X import a, b" (bases X.a, X.b, X), "import X.Y" (base X.Y);
    TypeScript/JS "import {a} from './m'" and "import x from './m'"
    (bases m.a/m.x, m); Java/Kotlin "import x.y.Z" / "import static x.y.Z"
    (base x.y.Z); bare paths for Go and C-family includes. Alias suffixes
    ("a as b") and brace noise are stripped.
    """
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
    """(Re)build ``kind='imports'`` module-to-module edges from the imports table.

    Every indexed file carrying a module symbol (kind='module') indexes its
    dotted path suffixes; each import row's normalized module bases match
    longest-suffix-first, and the first candidate matching exactly one indexed
    file wins. Ambiguous matches and external/unmatched imports are skipped
    (single-segment candidates can false-positive onto same-named files; they
    stay because a unique same-named file is usually the right target).
    Existing imports edges sourced from the affected module symbols are
    deleted first, so the pass is idempotent per rebuild. Scope with ``repo``
    or an explicit ``file_ids`` list; neither recomputes the whole store.

    Returns the number of edges inserted.
    """
    scope = ""
    params: List[str] = []
    if repo is not None:
        scope = " AND f.repo_id = ?"
        params.append(repo)
    elif file_ids is not None:
        ph = ",".join("?" for _ in file_ids)
        scope = f" AND f.id IN ({ph})"
        params.extend(file_ids)

    module_rows = conn.execute(
        f"""SELECT s.id AS mid, f.id AS fid, f.path
            FROM symbols s JOIN files f ON s.file_id = f.id
            WHERE s.kind = 'module'{scope}""",
        params,
    ).fetchall()

    source_ids = [r["mid"] for r in module_rows]
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
        conn.executemany(
            """INSERT INTO edges
               (id, source_id, target_id, target_name, kind, line, column, resolution)
               VALUES (?,?,?,?,?,?,?,?)""",
            edge_rows,
        )
    return len(edge_rows)


def insert_parse_error(cur, repo: str, path: str, error_message: str, stack_trace: str | None = None):
    # Ensure a repos row exists so the parse_errors.repo_id FK holds even when
    # the error fires before the repo was registered (e.g. incremental reindex
    # of a file whose repo_id is empty or inferred differently than build
    # stored). Idempotent — ON CONFLICT is a no-op if the row already exists.
    cur.execute(
        """INSERT INTO repos (id, name, path, language, git_remote, indexed_at)
           VALUES (?, ?, ?, '', NULL, ?)
           ON CONFLICT(id) DO NOTHING""",
        (repo, repo, ".", _now()),
    )
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
    """True when ``repo`` carries a marker from an interrupted on-disk rebuild.

    Such a repo is cleared-but-partial: the on-disk path commits every 500
    files (a deliberate WAL-lock trade-off), so a crash mid-rebuild leaves
    committed partial state with no error on later opens. Recovery contract:
    re-run ``cairn build --repo <repo>``. False on DBs predating the
    ``repo_build_state`` table -- no marker can exist there.
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM repo_build_state WHERE repo_id = ? AND state = 'building'",
            (repo,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False  # table missing on a pre-marker DB
    return row is not None


def _clear_repo(conn, repo_name: str):
    """Delete all files/symbols/edges/imports/errors for a repo (for rebuild).

    Must null out cross-repo edges that point at this repo's symbols BEFORE
    deleting the symbols, or the FK constraint on edges.target_id fails.
    """
    cur = conn.cursor()
    repo_symbol_ids_subquery = (
        "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id WHERE f.repo_id = ?"
    )
    # 0. Delete parse errors
    cur.execute("DELETE FROM parse_errors WHERE repo_id = ?", (repo_name,))
    # 0b. Delete recorded skips for this repo so a rebuild doesn't accumulate
    #     stale skip rows.
    cur.execute("DELETE FROM skipped_files WHERE repo_id = ?", (repo_name,))
    # 1. Null target_id on any edge (from any repo) pointing at this repo's symbols.
    #    Preserve the target name so callers() by name still works. Reset
    #    resolution to 'unresolved' — the orphaned edge no longer has a pinned
    #    target, so precise-mode queries (get_callers, impact_analysis) must
    #    not treat it as resolved. Mirrors graph/incremental.py's equivalent
    #    UPDATE (without this, dangling edges keep resolution='exact' and
    #    silently pollute blast-radius results after a single-repo rebuild).
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
    # 3b. Delete embeddings for this repo's symbols BEFORE the symbols go, so
    # the FK (embeddings.symbol_id -> symbols.id) doesn't leave orphans. The
    # incremental path deletes embeddings explicitly; a full repo rebuild must
    # too or it leaves dangling embedding rows pointing at deleted symbols.
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

