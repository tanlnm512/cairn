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


# File extensions per scanner language, used by the per-language SCIP fallback
# to identify tree-sitter rows that should be removed when an indexer's names
# don't match (e.g. scip-swift USRs). Inverse of scanner.EXTENSION_MAP.
_LANGUAGE_EXTENSIONS_CACHE: Dict[str, list] = {}


def _language_extensions(language: str) -> list:
    """Return the file extensions (with leading dot) for a scanner language."""
    if not _LANGUAGE_EXTENSIONS_CACHE:
        try:
            for ext, lang in scanner_mod.EXTENSION_MAP.items():
                _LANGUAGE_EXTENSIONS_CACHE.setdefault(lang, []).append(ext)
        except Exception:
            pass
    return _LANGUAGE_EXTENSIONS_CACHE.get(language, [])


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

    # Capture the scanner's real yield before any SCIP skip so the 'scan' event
    # reflects what was found, not the post-skip tree-sitter subset.
    scan_total = len(files)

    # SCIP coexistence: if cairn.json declares a pre-built SCIP index for a
    # language and that index file exists, tree-sitter STILL parses those files
    # (providing modifiers, body, inheritance edges, parent_scope that SCIP
    # can't emit). The importer then merges SCIP's exact-resolution edges onto
    # the tree-sitter rows post-resolve (below). One row per symbol after merge.
    scip_languages: Dict[str, str] = {}
    try:
        from .config import load_config
        cfg = load_config(workspace)
        if cfg.scip:
            ws_root_cfg = Path(workspace).resolve()
            for lang, rel_path in cfg.scip.items():
                idx_path = (ws_root_cfg / rel_path)
                if not idx_path.exists():
                    # Auto-generation (bounded): if a known indexer is on PATH,
                    # produce the missing index once before the existence gate.
                    # Never raises -- a missing/failing tool falls back to
                    # tree-sitter for this language. An existing index is never
                    # rebuilt (the user/CI owns the regeneration cadence).
                    try:
                        from ..parsers.scip_indexers import try_generate_index
                        try_generate_index(lang, idx_path, workspace, log)
                    except Exception as e:
                        log(f"  scip[{lang}]: index generation skipped ({e})")
                if idx_path.exists():
                    scip_languages[lang] = str(idx_path)
    except Exception:
        # Config loading must never break the build: a malformed cairn.json or
        # an unreadable path falls back to tree-sitter for everything.
        scip_languages = {}
    if scip_languages:
        # Warn when a 'scip' key doesn't correspond to any known scanner
        # language -- the importer won't find matching tree-sitter rows to
        # merge into, so the index contributes standalone rows only.
        known_langs: set = set()
        try:
            known_langs.update(scanner_mod.EXTENSION_MAP.values())
        except Exception:
            pass
        known_langs.update(f.language for f in files)
        unmatched = [k for k in scip_languages if k not in known_langs]
        if unmatched:
            log(f"  warning: cairn.json 'scip' keys not recognized as languages: {unmatched} "
                f"(known: {sorted(known_langs)}). SCIP data for them won't merge with tree-sitter.")

    emit("scan", files=scan_total, skips=len(skips))

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
        # resolve) leaves a detectable 'building' row instead of a silently
        # partial repo. Cleared after the final resolve commit below.
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

    if repo_filter:
        # Single-repo rebuild complete and committed (insert final commit +
        # per-repo resolve commits): out of the crash window, clear the marker.
        # An exception above leaves it in place -- the repo really is partial.
        _clear_repo_build_state(conn, repo_filter)

    # SCIP post-resolve hook: import pre-built indexes for languages whose
    # files were skipped above. SCIP's exact edges aren't re-resolved, so this
    # runs AFTER _resolve_all (tree-sitter's resolver would otherwise try to
    # re-link them) but BEFORE backup_to (so in-memory builds capture the
    # SCIP data too). Runs before the CLI's dataflow/transitive passes so
    # derived indexes cover SCIP symbols.
    scip_import_stats: Dict[str, dict] = {}
    if scip_languages:
        try:
            from ..parsers.scip_importer import scip_available, import_scip_file
            if scip_available():
                # repo_id for the importer: consistent with files.repo_id
                # (repo basename for multi-repo, or the single repo's id).
                for lang, idx_path in scip_languages.items():
                    # Determine the repo id to attribute SCIP symbols to. Use
                    # the first repo seen (typical single-repo case); for
                    # multi-repo the index is still imported under one id.
                    repo_for_scip = next((str(_r) for _r in repos_seen), "default")
                    try:
                        # ws_root lets the importer normalize each document's
                        # path to (repo_id, repo-relative) so SCIP rows share
                        # file identity with the scanner/incremental paths.
                        s = import_scip_file(
                            conn, idx_path, repo_id=repo_for_scip, fmt="proto",
                            ws_root=ws_root,
                        )
                        scip_import_stats[lang] = s
                        log(f"  SCIP[{lang}]: {s.get('symbols_added',0)} symbols, "
                            f"{s.get('edges_added',0)} edges, "
                            f"{s.get('symbols_merged',0)} merged")
                    except Exception as e:
                        # Roll back any partial writes the importer left on the
                        # shared connection before it raised. import_scip_file
                        # commits at the end of a successful import but does not
                        # roll back on failure, so rows inserted before the
                        # exception point would otherwise ride along on the next
                        # unrelated conn.commit() in the build — silently mixing
                        # a half-imported SCIP index into the graph. Rolling
                        # back here scopes the revert to only this import's
                        # pending writes (earlier, committed work in the build
                        # is already durably committed and unaffected).
                        conn.rollback()
                        log(f"  SCIP[{lang}] import failed: {e}; skipping "
                            f"(partial writes rolled back)")
                        # Don't fail the build over a bad SCIP index.
        except ImportError:
            log("  SCIP indexes configured but [scip] extra not installed; "
                "using tree-sitter fallback")

    # Per-language fallback: if an indexer's symbol names don't match
    # tree-sitter's (merge rate ~0, e.g. scip-swift's opaque USRs), the
    # coexistence duplicates are harmful -- two disconnected graphs for the
    # same logical symbol, and get_callers breaks for both name forms. Revert
    # that language to pure-SCIP: delete the tree-sitter rows for its files so
    # only the SCIP data remains (clean, no dupes). Languages whose indexers
    # have human-readable descriptors (scip-java, scip-typescript) keep the
    # coexistence merge (source='merged').
    for lang, s in scip_import_stats.items():
        added = s.get("symbols_added", 0)
        merged = s.get("symbols_merged", 0)
        if added > 0 and merged == 0:
            # Nothing merged -- the two sources don't share a name space.
            # Delete tree-sitter symbols/edges for this language's files so
            # only SCIP remains (reverts to the pre-coexistence skip model).
            exts = _language_extensions(lang)
            if exts:
                like_clause = " OR ".join("path LIKE ?" for _ in exts)
                cur = conn.cursor()
                ts_file_ids = [
                    r[0] for r in cur.execute(
                        f"SELECT id FROM files WHERE ({like_clause}) "
                        f"AND hash != 'scip_imported'",
                        tuple(f"%{e}" for e in exts),
                    ).fetchall()
                ]
                if ts_file_ids:
                    fid_placeholders = ",".join("?" for _ in ts_file_ids)
                    fids = tuple(ts_file_ids)
                    # Delete only TREE-SITTER symbols (source != 'scip'), NOT
                    # SCIP's -- after file_id reconciliation they share the
                    # same file row, so a blanket delete would nuke SCIP too.
                    ts_sym_ids = [
                        r[0] for r in cur.execute(
                            f"SELECT id FROM symbols WHERE file_id IN ({fid_placeholders}) "
                            f"AND source != 'scip'",
                            fids,
                        ).fetchall()
                    ]
                    if ts_sym_ids:
                        sid_placeholders = ",".join("?" for _ in ts_sym_ids)
                        cur.execute(
                            f"DELETE FROM edges WHERE source_id IN ({sid_placeholders})",
                            tuple(ts_sym_ids),
                        )
                        cur.execute(
                            f"DELETE FROM symbols WHERE id IN ({sid_placeholders})",
                            tuple(ts_sym_ids),
                        )
                    log(f"  SCIP[{lang}]: 0/{added} symbols merged (indexer names "
                        f"don't match tree-sitter); reverted to pure-SCIP "
                        f"(removed {len(ts_sym_ids)} tree-sitter symbols)")
                    s["reverted_to_pure_scip"] = True

    if in_memory:
        # Close out the single implicit transaction that's been open across
        # the whole build (no periodic commits for in-memory builds) before
        # handing the connection to the backup API.
        conn.commit()
        emit("persist")
        log("  persisting in-memory graph to disk...")
        backup_to(conn, resolved_db)         # single dump

    # Fold SCIP import stats into the top-level counts so the summary
    # reflects the full build, not just the tree-sitter phase. Without this,
    # an all-SCIP workspace reports repos=0/files=0/symbols=0.
    scip_repo_count = 0
    if scip_import_stats:
        for s in scip_import_stats.values():
            symbol_count += s.get("symbols_added", 0)
            edge_count += s.get("edges_added", 0)
            file_count += s.get("files_added", 0)
        # If tree-sitter found no repos but SCIP imported data, count the SCIP
        # file rows so the summary isn't structurally empty.
        if not repos_seen:
            scip_repo_count = cur.execute(
                "SELECT COUNT(DISTINCT repo_id) FROM files WHERE hash = 'scip_imported'"
            ).fetchone()[0]
            # reflect SCIP repos in the summary without mutating repos_seen

    summary = {
        "repos": scip_repo_count if (not repos_seen and scip_import_stats) else len(repos_seen),
        "files": file_count,
        "symbols": symbol_count,
        "edges": edge_count,
        "imports": import_count,
        "skipped": skip_count_summary,
        "parse_errors": parse_errors,
        "resolution": resolution_stats,
    }
    if scip_import_stats:
        summary["scip"] = scip_import_stats
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
                parameters, return_type, parent_scope, imports_summary, body, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'tree_sitter')""",
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
        cur.execute(
            "DELETE FROM embeddings WHERE symbol_id IN "
            "(SELECT id FROM symbols WHERE file_id IN "
            "(SELECT id FROM files WHERE repo_id = ?))",
            (repo_name,),
        )
    except sqlite3.OperationalError:
        note_contention("builder.delete_repo_embeddings")
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

