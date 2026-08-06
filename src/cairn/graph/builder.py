"""Graph builder: scan -> parse -> store -> resolve edges.

Orchestrates the indexing pipeline. For each repo:
  1. Scan source files (scanner)
  2. Parse each file (parser for its language)
  3. Insert symbols + imports + edges (edges initially unresolved)
  4. Resolve edge targets via the import-aware resolver (src/graph/resolver.py)

Resolution is deferred to a second pass per repo, after that repo's symbols
and imports are committed, so the resolver sees a complete symbol/import index.
An edge is resolved only when exactly one candidate exists in a tier
(same-file -> import-aware -> same-repo -> global); otherwise it is marked
``ambiguous`` and left unresolved by design -- precise-by-default queries then
trust only ``resolution='exact'`` rows, while ``--fuzzy`` re-enables matching
by the preserved ``target_name``.
"""
from __future__ import annotations

import json
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
from .schema import init_db, get_build_db, backup_to
from ..paths import resolve_store as _resolve_store

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

    Uses scanner.iter_files_and_skips per repo so the builder can record skips
    in the skipped_files table. Falls back to scan_workspace if a repo has no
    source files.
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
    """Parse all files and return parsed results.

    Returns:
        (parsed_results, parse_errors) where parsed_results is a list of tuples
        and parse_errors is the count of files that failed to parse.
    """
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

    parse_errors = sum(1 for r in parsed_results if r[5] is not None)
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

    Returns:
        (file_count, symbol_count, edge_count, import_count, repo_edges_by_file)
    """
    cur = conn.cursor()
    log = _log if verbose else lambda *a: None
    emit = progress or (lambda *a, **k: None)

    symbol_count = 0
    edge_count = 0
    import_count = 0
    file_count = 0

    # repo_edges_by_file: {repo -> {source_file_id -> [(edge_id, source_sid,
    #   target_name, line, column), ...]}} -- the resolver consumes this.
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

        # Framework-aware route detection: merged into the parsed file's own
        # symbols/edges *before* insertion -- routes are ordinary kind='route'
        # symbols and kind='references' edges to the rest of the pipeline
        # (normal insert, normal same-file source lookup, normal resolver
        # pass). No special-casing needed beyond this merge.
        try:
            route_extraction = routes_mod.detect_routes(pf, language)
            if route_extraction:
                pf.symbols.extend(route_extraction.routes)
                pf.edges.extend(route_extraction.references)
        except Exception as e:
            # Route detection is best-effort sugar on top of the real parse;
            # never let it fail the whole file's indexing.
            log(f"  route detection failed for {rel_path}: {e}")

        # Service-topology edge detection: merged into the parsed file's edges
        # before insertion -- http_call/service_call are ordinary kinds to the
        # rest of the pipeline (free-text `edges.kind`, indexed). By default
        # impact_analysis/trace_flow exclude these from blast radius; they are
        # queryable via get_callees/get_callers(kind=...).
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

    Returns:
        resolution_stats dict with keys: exact, ambiguous, unresolved
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

    ``conn``: database connection to use. Must be opened by the caller.
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

    # Group files by repo for repo-record insertion. Bucket files by repo once
    # (O(files)) so the per-repo language inference below is O(files) total
    # rather than O(repos x files) -- the list comprehension re-scan of all
    # files was 2.5M comparisons on a 50-repo/50k-file workspace.
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
    conn = get_build_db() if in_memory else init_db(resolved_db)
    try:
        return _build_graph_impl(
            conn=conn,
            workspace=workspace,
            repo_filter=repo_filter,
            db_path=db_path,
            verbose=verbose,
            progress=progress,
        )
    finally:
        conn.close()


def _parse_file_worker(args: tuple[str, str, str, str]) -> tuple[str, str, str, str, Optional[ParsedFile], Optional[str], Optional[str]]:
    """Worker function to parse a single file in a separate process.

    Args:
        args: (file_path, file_rel_path, file_language, file_repo)
    Returns:
        (file_path, file_rel_path, file_language, file_repo, parsed_file, error_msg, stack_trace)
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

    ``rel_path`` is the repo-relative path stored in ``files.path`` (portable
    across machines); ``abs_path`` is the absolute path used only to stat the
    file for size/mtime. See scanner.resolve_file_path for the read-side
    reconstruction of rel_path -> absolute.

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
    # Variant-C embedding context: derive parent_scope and a file-level
    # imports_summary at build time when the parser didn't supply them, so
    # variant-C chunks get meaningful "Enclosing Scope:"/"Imports:" sections
    # without every parser having to populate them. `body` stays None unless
    # the parser set it (per-symbol source extraction is out of scope here;
    # parent_scope + imports_summary already materially improve variant C, and
    # the body section degrades gracefully to omitted).
    file_imports_summary = ", ".join(
        imp.imported_path for imp in pf.imports[:20]
    ) or None  # file-level summary, identical for every symbol in this file
    for sym in pf.symbols:
        sym_id = _new_id()
        # Enclosing scope is everything before the last "." of the qualified
        # name (e.g. "com.foo.Bar.baz" -> "com.foo.Bar"). None when there is
        # no qualifier (top-level symbol) or the parser already set it.
        if sym.parent_scope is None and sym.qualified_name and "." in sym.qualified_name:
            sym.parent_scope = sym.qualified_name.rsplit(".", 1)[0]
        if sym.imports_summary is None:
            sym.imports_summary = file_imports_summary
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
        ))
        name_to_symbol_ids.setdefault(sym.name, []).append((sym_id, repo, file_id))

    # --- imports ------------------------------------------------------------
    for imp in pf.imports:
        imp_rows.append((_new_id(), file_id, imp.imported_path, None, imp.line))

    # Same-file symbol-name lookup for edge *source* resolution. Built from the
    # symbols inserted for THIS file only, rather than scanning the global
    # name_to_symbol_ids accumulator (which would make this O(total_symbols)
    # per file -> O(N^2) overall). The keys are exactly the names this file
    # declared, and the values are their symbol ids in this file.
    in_file: Dict[str, List[str]] = {}
    for sym, row in zip(pf.symbols, sym_rows):
        in_file.setdefault(sym.name, []).append(row[0])

    # --- edges: source resolved now; target left NULL for the resolver -----
    file_edges = repo_edges_by_file.setdefault(repo, {}).setdefault(file_id, [])
    for edge in pf.edges:
        source_ids = in_file.get(edge.source_name, [])
        source_id = source_ids[0] if source_ids else None
        if source_id is None:
            continue  # file-level call with no owning symbol; cannot attach
        edge_id = _new_id()
        edge_rows.append((
            edge_id, source_id, None, edge.target_name, edge.kind,
            edge.line, edge.column, None,
        ))
        # Carry receiver_type as the 6th (in-memory only) tuple element so the
        # resolver's type-aware tier can use it; None when the parser didn't/
        # couldn't infer a receiver type (abstain-safe).
        file_edges.append((
            edge_id, source_id, edge.target_name, edge.line, edge.column,
            getattr(edge, "receiver_type", None),
        ))

    if sym_rows:
        cur.executemany(
            """INSERT INTO symbols
               (id, file_id, name, qualified_name, kind, line_start, line_end,
                column_start, column_end, docstring, modifiers, metadata,
                parameters, return_type, parent_scope, imports_summary, body)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            sym_rows,
        )
    if imp_rows:
        cur.executemany(
            """INSERT INTO imports (id, file_id, imported_path, resolved_symbol_id, line)
               VALUES (?,?,?,?,?)""",
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
    #    Preserve the target name so callers() by name still works.
    cur.execute(
        f"UPDATE edges SET target_name = "
        f"COALESCE(target_name, (SELECT name FROM symbols WHERE id = edges.target_id)), "
        f"target_id = NULL "
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

