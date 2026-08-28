"""SQLite schema and database lifecycle for the Layer 1 code graph."""
from __future__ import annotations

import errno
import fcntl
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import quote

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS repos (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT,
    git_remote TEXT,
    indexed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL REFERENCES repos(id),
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    hash TEXT,
    line_count INTEGER,
    indexed_at TIMESTAMP,
    UNIQUE(repo_id, path)
);

CREATE TABLE IF NOT EXISTS symbols (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES files(id),
    name TEXT NOT NULL,
    qualified_name TEXT,
    kind TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    column_start INTEGER,
    column_end INTEGER,
    docstring TEXT,
    modifiers TEXT
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES symbols(id),
    target_id TEXT REFERENCES symbols(id),
    target_name TEXT,
    kind TEXT NOT NULL,
    line INTEGER,
    column INTEGER
);

CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES files(id),
    imported_path TEXT NOT NULL,
    resolved_symbol_id TEXT REFERENCES symbols(id),
    line INTEGER
);

CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_files_repo ON files(repo_id);

-- FTS5 external-content full-text index over symbols. Lets search_symbols use
-- MATCH + bm25() ranking instead of LIKE on the name only. External-content
-- ('content=symbols') avoids duplicating the text; the three triggers below
-- keep the index in sync with the symbols table automatically, so the
-- builder's INSERT INTO symbols populates FTS with no extra code.
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    name,
    qualified_name,
    docstring,
    content='symbols',
    content_rowid='rowid',
    tokenize='unicode61'   -- splits on non-alphanumeric, so ApiFactory -> Api+Factory
);
CREATE TRIGGER IF NOT EXISTS symbols_ai AFTER INSERT ON symbols BEGIN
    INSERT INTO symbols_fts(rowid, name, qualified_name, docstring)
    VALUES (new.rowid, new.name, new.qualified_name, new.docstring);
END;
CREATE TRIGGER IF NOT EXISTS symbols_ad AFTER DELETE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, qualified_name, docstring)
    VALUES ('delete', old.rowid, old.name, old.qualified_name, old.docstring);
END;
CREATE TRIGGER IF NOT EXISTS symbols_au AFTER UPDATE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, qualified_name, docstring)
    VALUES ('delete', old.rowid, old.name, old.qualified_name, old.docstring);
    INSERT INTO symbols_fts(rowid, name, qualified_name, docstring)
    VALUES (new.rowid, new.name, new.qualified_name, new.docstring);
END;

-- Persisted per-corpus document-frequency table for IDF-aware query
-- enrichment (spec retrieval-quality-v2 FR-003/D-005). One row per indexed
-- token: symbol_df = symbols whose symbols_fts-indexed text contains the
-- token, n_symbols = total symbol count at build time. Rebuilt from the FTS5
-- vocabulary by rebuild_term_df(); refresh rides the embed pass, query time
-- only does per-token indexed SELECTs. Additive-only: plain CREATE TABLE IF
-- NOT EXISTS rides the idempotent executescript in _apply_schema with NO
-- MIGRATIONS entry, so existing DBs gain the table on next connect -- the
-- same pattern build_runs/tool_metrics used.
CREATE TABLE IF NOT EXISTS term_df (
    token TEXT PRIMARY KEY,      -- case-folded FTS5 unicode61 token
    symbol_df INTEGER,
    n_symbols INTEGER
);

-- memory cross-session reference tracking
CREATE TABLE IF NOT EXISTS memory_refs (
    id TEXT PRIMARY KEY,
    memory_path TEXT NOT NULL,
    session_id TEXT NOT NULL,
    referenced_at TIMESTAMP NOT NULL,
    context TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_refs_path ON memory_refs(memory_path);
CREATE INDEX IF NOT EXISTS idx_memory_refs_session ON memory_refs(session_id);

-- cross-repo dependency records (namespace/import based)
CREATE TABLE IF NOT EXISTS repo_deps (
    id TEXT PRIMARY KEY,
    source_repo TEXT NOT NULL,
    target_repo TEXT NOT NULL,
    dep_type TEXT NOT NULL,
    evidence TEXT,
    symbol_count INTEGER DEFAULT 0
);

-- Audit/logging table for parse failures
CREATE TABLE IF NOT EXISTS parse_errors (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    repo_id TEXT NOT NULL REFERENCES repos(id),
    error_message TEXT,
    stack_trace TEXT,
    timestamp TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_parse_errors_repo ON parse_errors(repo_id);

-- files the scanner chose not to index, with the reason. Makes skips auditable
-- (cairn stats reports counts by reason) rather than silent. A skip is NOT an
-- error -- generated/vendored/gitignored/large files are deliberately excluded
-- so the graph reflects hand-written source only.
CREATE TABLE IF NOT EXISTS skipped_files (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    path TEXT NOT NULL,
    reason TEXT NOT NULL,      -- 'default_skip' | 'gitignored' | 'config_exclude' | 'size_cap'
    size_bytes INTEGER,
    recorded_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_skipped_repo ON skipped_files(repo_id);
CREATE INDEX IF NOT EXISTS idx_skipped_reason ON skipped_files(reason);
CREATE INDEX IF NOT EXISTS idx_skipped_path ON skipped_files(path);

-- import-aware resolution support. Cover the imports lookup that the resolver
-- (src/graph/resolver.py) performs per source file. Existing indexes cover
-- edges.target_id (idx_edges_target) and symbols.qualified_name.
CREATE INDEX IF NOT EXISTS idx_imports_file ON imports(file_id);
CREATE INDEX IF NOT EXISTS idx_imports_path ON imports(imported_path);

-- pending_sync tracks files with unindexed edits (the debounce window). The
-- live watcher (watcher.FileWatcherService, active when the [watch] extra's
-- watchdog is importable) inserts on debounced file events; reindex_paths
-- deletes on completion (and the watcher cleans up leftovers after identical-
-- content saves). MCP tools check this table to prepend staleness banners.
CREATE TABLE IF NOT EXISTS pending_sync (
    path TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    changed_at TIMESTAMP NOT NULL
);

-- semantic embeddings keyed to symbols. Vector stored as a BLOB (float32
-- little-endian array). semantic_search() in queries.py decodes the BLOB +
-- cosine-compares against the embedded query. Additive only -- the table
-- stays empty when the [semantic] extra isn't installed; nothing in the
-- builder hot path touches it. The batch `cairn embed` command populates it on
-- demand.
CREATE TABLE IF NOT EXISTS embeddings (
    symbol_id TEXT NOT NULL REFERENCES symbols(id),
    model TEXT NOT NULL,          -- e.g. 'all-MiniLM-L6-v2', for invalidation
    dim INTEGER NOT NULL,         -- vector dimensionality
    vec BLOB NOT NULL,            -- float32 little-endian
    chunk TEXT NOT NULL,          -- the text that was embedded (for display)
    embedded_at TIMESTAMP,
    PRIMARY KEY (symbol_id, model)
);
CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model);

-- Parallel multi-vector embeddings table (spec retrieval-quality-v2 FR-005).
-- Holds ONLY the extra vector kinds ('name', 'docstring') as one row per
-- (symbol, model, kind); the base embeddings table above -- PK (symbol_id,
-- model), whose rowids key the per-model vec0 ANN tables -- is NEVER
-- repurposed or re-PK'd (D-006). Populated solely by the opt-in
-- `cairn embed --multivector` pass; stays EMPTY on default builds, so
-- single-vector storage and query behavior are byte-identical (TC-020).
-- Additive-only: plain CREATE TABLE IF NOT EXISTS rides the idempotent
-- executescript in _apply_schema with NO MIGRATIONS entry, so existing DBs
-- gain the table on next connect -- the same pattern term_df used.
CREATE TABLE IF NOT EXISTS embeddings_mv (
    symbol_id TEXT NOT NULL REFERENCES symbols(id),
    model TEXT NOT NULL,          -- same model stamp as the embeddings row
    vector_kind TEXT NOT NULL,    -- 'name' | 'docstring' (MV_KINDS)
    dim INTEGER NOT NULL,         -- vector dimensionality
    vec BLOB NOT NULL,            -- float32 little-endian
    chunk TEXT NOT NULL,          -- the kind-specific text that was embedded
    content_hash TEXT,            -- _chunk_hash of the kind text (staleness)
    embedded_at TIMESTAMP,
    PRIMARY KEY (symbol_id, model, vector_kind)
);
CREATE INDEX IF NOT EXISTS idx_embeddings_mv_model ON embeddings_mv(model);

-- semantic embeddings for knowledge documents. doc_id is a concept_id path on
-- disk (NOT a DB row), so there is NO foreign key constraint. The batch
-- `cairn knowledge embed` command populates it on demand.
CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    doc_id TEXT NOT NULL,          -- concept_id path (e.g. "knowledge/business-rule/refund-policy")
    chunk_index INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL,           -- for invalidation on model swap
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,             -- float32 little-endian
    chunk TEXT NOT NULL,           -- the text that was embedded
    embedded_at TIMESTAMP,
    PRIMARY KEY (doc_id, chunk_index, model)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_embeddings_model ON knowledge_embeddings(model);

-- semantic embeddings for memory concepts (decisions/patterns/mistakes under
-- memory/). doc_id is a concept_id path on disk (NOT a DB row, and NOT
-- stable -- promote/demote/decay move a memory to a new concept_id), so
-- there is no foreign key constraint and a row can go stale on a tier move
-- (skipped at read time when doc_id no longer resolves in the bundle).
-- chunk_index lets a long body be split into multiple embedded pieces
-- (see chunk_memory_body) instead of one embedding per whole concept.
CREATE TABLE IF NOT EXISTS memory_embeddings (
    doc_id TEXT NOT NULL,          -- concept_id path (e.g. "memory/tribal/foo-a1b2c3")
    chunk_index INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL,           -- for invalidation on model swap
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,             -- float32 little-endian
    chunk TEXT NOT NULL,           -- the text that was embedded
    embedded_at TIMESTAMP,
    PRIMARY KEY (doc_id, chunk_index, model)
);
CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model ON memory_embeddings(model);

-- precomputed dataflow index for public/exported symbols. Within-repo
-- impacted symbols and cross-repo consumer repos are materialised so lookups
-- are O(1) instead of re-running impact_analysis + cross_repo_deps on each
-- MCP tool call. Populated by build_dataflow_index() during `cairn build`/`cairn sync`.
CREATE TABLE IF NOT EXISTS dataflow (
    symbol TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    within_repo TEXT,      -- JSON list of impacted symbol names
    cross_repo TEXT,       -- JSON list of consumer repo names
    updated TIMESTAMP
);

-- MCP tool invocation metrics. Each tool call is recorded with timing, session
-- provenance, and status (ok/error). The `cairn metrics` CLI command aggregates
-- this for observability.
CREATE TABLE IF NOT EXISTS tool_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT 'unknown',
    invoked_at TIMESTAMP NOT NULL,
    duration_ms REAL,
    status TEXT NOT NULL DEFAULT 'ok',    -- 'ok' | 'error'
    error_message TEXT,
    req_chars INTEGER,          -- request payload size in chars; NULL on pre-migration rows
    resp_chars INTEGER,         -- response payload size in chars; NULL on pre-migration rows
    args_summary TEXT,          -- redacted, truncated JSON summary of the call's kwargs
    source TEXT NOT NULL DEFAULT 'mcp',  -- 'mcp' | 'cli' (spec cli-usage-recording FR-002)
    truncated_from_chars INTEGER,  -- original chars, set only when the result was capped; NULL = not truncated
    truncated_to_chars INTEGER     -- delivered chars (capped, incl. the notice); NULL = not truncated
);
CREATE INDEX IF NOT EXISTS idx_tool_metrics_tool ON tool_metrics(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_metrics_session ON tool_metrics(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_metrics_invoked ON tool_metrics(invoked_at, id);

-- One row per indexing pass (full build / incremental sync / embed) so build
-- history, phase timings, and resolution quality survive the process that
-- produced them (spec observability-telemetry 6.2). Additive-only: plain
-- CREATE TABLE IF NOT EXISTS rides the idempotent executescript in
-- _apply_schema with NO MIGRATIONS entry, so existing DBs gain the table on
-- next connect -- the same pattern tool_metrics used.
CREATE TABLE IF NOT EXISTS build_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    duration_s REAL,
    phase_timings TEXT,
    repos INTEGER, files INTEGER, symbols INTEGER, edges INTEGER,
    resolution_exact INTEGER, resolution_ambiguous INTEGER, resolution_unresolved INTEGER,
    parse_errors INTEGER, skipped INTEGER,
    workers INTEGER,
    session_id TEXT
);

-- Crash-recovery marker for the single-repo on-disk rebuild path. That path
-- commits mid-rebuild (every 500 files, to bound WAL lock hold time), so a
-- crash leaves the repo cleared-but-partial. builder sets one 'building' row
-- before clearing the repo and deletes it after the final commit;
-- builder.repo_build_in_progress() reads it so a later change can surface
-- "re-run cairn build --repo X". Additive-only, rides the idempotent
-- executescript like build_runs.
CREATE TABLE IF NOT EXISTS repo_build_state (
    repo_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,          -- 'building'
    started_at TIMESTAMP NOT NULL
);

-- Generic low-cardinality event log (ann_fallback, lock_contention, ...).
-- attrs is JSON of enums/short tags/bucketed values only -- no paths or free
-- text -- so the distinct-value space stays bounded. Written by the telemetry
-- sink, which also prunes retention opportunistically.
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TIMESTAMP NOT NULL,
    name TEXT NOT NULL,
    session_id TEXT,
    attrs TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_name ON events(name);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

-- Transitive closure graph matrix for O(1) multi-hop call graph lookups
CREATE TABLE IF NOT EXISTS transitive_edges (
    source_id TEXT NOT NULL,
    target_name TEXT NOT NULL,
    target_id TEXT,
    distance INTEGER NOT NULL,
    PRIMARY KEY (source_id, target_name, distance)
);
CREATE INDEX IF NOT EXISTS idx_transitive_source ON transitive_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_transitive_target ON transitive_edges(target_name);
-- Ancestor lookups (impact_analysis index mode) filter on target_id; the
-- target_name index above does not cover them.
CREATE INDEX IF NOT EXISTS idx_transitive_target_id ON transitive_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_transitive_distance ON transitive_edges(distance);

-- Migration tracking: records which schema migrations have been applied.
-- Used to ensure migrations are only run once and to detect partial/failed
-- migrations. key is the migration name (e.g., "edges.resolution"), value
-- stores metadata (e.g., timestamp, status).
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

# edges.resolution column tracks HOW an edge target was resolved, so queries
# can distinguish trusted resolutions from name-only matches. Values:
#   'exact'      — resolved to exactly one candidate (trusted)
#   'ambiguous'  — multiple candidates existed; left unresolved on purpose
#   'unresolved' — no candidate found (e.g. stdlib/external call)
EDGE_RESOLUTION_MIGRATION = "ALTER TABLE edges ADD COLUMN resolution TEXT"

# File size and mtime columns for fast catch-up reconciliation; only re-hashes
# on mismatch.
FILES_SIZE_MIGRATION = "ALTER TABLE files ADD COLUMN size INTEGER"
FILES_MTIME_MIGRATION = "ALTER TABLE files ADD COLUMN mtime REAL"

# JSON metadata column for symbols that need structured data beyond
# name/kind/modifiers (e.g. routes store {"http_method","path","framework","handler"}).
SYMBOL_METADATA_MIGRATION = "ALTER TABLE symbols ADD COLUMN metadata TEXT"

# content-hash column on embeddings, so embed_all can detect a changed
# docstring/signature even when the model name hasn't changed. NULL on rows
# written before this migration; embed_all treats NULL as "needs re-embedding".
EMBEDDINGS_CONTENT_HASH_MIGRATION = "ALTER TABLE embeddings ADD COLUMN content_hash TEXT"
SYMBOL_PARAMETERS_MIGRATION = "ALTER TABLE symbols ADD COLUMN parameters TEXT"
SYMBOL_RETURN_TYPE_MIGRATION = "ALTER TABLE symbols ADD COLUMN return_type TEXT"
# Variant-C embedding context columns chunk_for_symbol reads behind
# `if "X" in row.keys()` guards (parent_scope / imports_summary / body).
SYMBOL_PARENT_SCOPE_MIGRATION = "ALTER TABLE symbols ADD COLUMN parent_scope TEXT"
SYMBOL_IMPORTS_SUMMARY_MIGRATION = "ALTER TABLE symbols ADD COLUMN imports_summary TEXT"
SYMBOL_BODY_MIGRATION = "ALTER TABLE symbols ADD COLUMN body TEXT"
TRANSITIVE_EDGES_TARGET_ID_MIGRATION = "ALTER TABLE transitive_edges ADD COLUMN target_id TEXT"

# Provenance column on symbols: 'tree_sitter' or 'scip'. NULL on legacy rows
# (pre-SCIP builds) is treated as 'tree_sitter'. Additive ALTER is invisible to
# the FTS5 triggers (schema.py CREATE TRIGGER only references rowid, name,
# qualified_name, docstring), so it composes with existing migrations cleanly.
SYMBOL_SOURCE_MIGRATION = "ALTER TABLE symbols ADD COLUMN source TEXT"

# Payload-size and arg-summary columns on tool_metrics: request/response
# payload sizes in chars plus a redacted, truncated summary of the call's
# kwargs. NULL on rows recorded before these migrations ran.
TOOL_METRICS_REQ_CHARS_MIGRATION = "ALTER TABLE tool_metrics ADD COLUMN req_chars INTEGER"
TOOL_METRICS_RESP_CHARS_MIGRATION = "ALTER TABLE tool_metrics ADD COLUMN resp_chars INTEGER"
TOOL_METRICS_ARGS_SUMMARY_MIGRATION = "ALTER TABLE tool_metrics ADD COLUMN args_summary TEXT"

# Origin stamp on tool_metrics rows (spec cli-usage-recording FR-002/D-002):
# 'mcp' (the default -- the MCP INSERT in mcp_server/metric_buffering.py names
# no source column and rides this default, byte-identical per FR-005) or 'cli'
# (stated explicitly by telemetry/cli_metrics). NOT NULL + DEFAULT makes the
# ALTER legal on old DBs and backfills pre-migration rows as 'mcp' -- honest
# for this table's history, so NULL never appears in the views.
TOOL_METRICS_SOURCE_MIGRATION = "ALTER TABLE tool_metrics ADD COLUMN source TEXT NOT NULL DEFAULT 'mcp'"

# Truncation-magnitude columns on tool_metrics (spec ui-dashboard-polish
# FR-003/D-002): original vs delivered chars, set only on calls whose result
# was actually capped. Nullable by design -- NULL means no-evidence (a
# non-truncated call or a pre-migration row), never zero, and the CLI writer
# (which truncates nothing) needs no change.
TOOL_METRICS_TRUNCATED_FROM_CHARS_MIGRATION = (
    "ALTER TABLE tool_metrics ADD COLUMN truncated_from_chars INTEGER"
)
TOOL_METRICS_TRUNCATED_TO_CHARS_MIGRATION = (
    "ALTER TABLE tool_metrics ADD COLUMN truncated_to_chars INTEGER"
)

# All additive migrations applied at connect time. Each is attempted in a
# try/except so re-running on an already-migrated DB is a no-op.
MIGRATIONS = [
    EDGE_RESOLUTION_MIGRATION,
    FILES_SIZE_MIGRATION,
    FILES_MTIME_MIGRATION,
    SYMBOL_METADATA_MIGRATION,
    EMBEDDINGS_CONTENT_HASH_MIGRATION,
    SYMBOL_PARAMETERS_MIGRATION,
    SYMBOL_RETURN_TYPE_MIGRATION,
    SYMBOL_PARENT_SCOPE_MIGRATION,
    SYMBOL_IMPORTS_SUMMARY_MIGRATION,
    SYMBOL_BODY_MIGRATION,
    TRANSITIVE_EDGES_TARGET_ID_MIGRATION,
    SYMBOL_SOURCE_MIGRATION,
    TOOL_METRICS_REQ_CHARS_MIGRATION,
    TOOL_METRICS_RESP_CHARS_MIGRATION,
    TOOL_METRICS_ARGS_SUMMARY_MIGRATION,
    TOOL_METRICS_SOURCE_MIGRATION,
    TOOL_METRICS_TRUNCATED_FROM_CHARS_MIGRATION,
    TOOL_METRICS_TRUNCATED_TO_CHARS_MIGRATION,
]

# Default DB location: resolved from the central store for the current workspace.
from cairn.paths import render_env_resolution_chain, resolve_store  # noqa: E402

DEFAULT_DB_PATH = resolve_store().db

_logger = logging.getLogger(__name__)

# Process-global guard so each contention site warns at most once per process.
# Keyed by ``site`` so distinct call points warn independently. Guarded by a
# lock because swallow sites are reachable from flusher daemon threads
# (metric_buffering / embed_buffering) concurrently with the main thread.
_CONTENTION_WARNED: dict[str, bool] = {}
_CONTENTION_LOCK = threading.Lock()


def _is_lock_contention(error: Exception) -> bool:
    """True when an ``OperationalError`` is genuinely lock contention/busy.

    "database is locked"/"database is busy" mean another writer holds the DB;
    "no such table", "no such module", "duplicate column", ... are schema- or
    availability-shaped failures that must not pollute the ``lock_contention``
    signal doctor and ``metrics --contention`` aggregate on.
    """
    msg = str(error).lower()
    return "locked" in msg or "busy" in msg


def note_contention(site: str, error: Exception | None = None) -> None:
    """Emit one lock-contention warning per (process, site).

    Called at ``except sqlite3.OperationalError`` swallow sites so a
    silently-absorbed "database is locked" surfaces at least once instead of
    vanishing. Another cairn process holds the DB; ``busy_timeout`` retried and
    absorbed the contention (the operation completed or degraded gracefully).
    Distinct ``site`` tags warn independently; the same site warns at most once
    per process -- mirrors ``warn_hash_fallback_once`` (graph/embeddings.py) and
    ``warn_ann_fallback_once`` (graph/ann_index.py).

    ``error``: pass the caught exception whenever the ``except`` clause is
    broader than pure lock contention. A non-lock-shaped OperationalError
    ("no such table", "no such module: FTS5", "duplicate column") is a schema/
    availability failure, not contention -- it is skipped (debug-logged) so
    phantom contention events don't dilute the doctor/metrics signal.

    ``site`` is a stable, low-cardinality ``module.function`` tag (NO line
    numbers -- they drift). Also emits a durable ``lock_contention`` telemetry
    event (spec §6.4) so ``cairn doctor`` / ``cairn metrics --contention`` can
    aggregate contention trends, not just log a one-time line. The event is
    gated by ``CAIRN_TELEMETRY`` internally; the WARNING stays unconditional
    (an operational signal, not telemetry data) -- turning telemetry off stops
    recording but does not silence the operational warning.

    Thread-safe: the guard dict is mutated only under ``_CONTENTION_LOCK``; the
    emit + log calls happen after the lock is released so they can't serialize
    concurrent swallow sites.
    """
    if error is not None and not _is_lock_contention(error):
        _logger.debug(
            "note_contention(%s) skipped: %r is not lock-shaped", site, error
        )
        return
    with _CONTENTION_LOCK:
        if _CONTENTION_WARNED.get(site):
            return
        _CONTENTION_WARNED[site] = True
    # Durable event for doctor/metrics aggregation (best-effort; gated by
    # CAIRN_TELEMETRY). Lazy import: schema is imported very early, so this
    # keeps the telemetry package out of the import graph until first contention.
    try:
        from cairn.telemetry import LOCK_CONTENTION, emit as _emit

        _emit(LOCK_CONTENTION, site=site)
    except Exception:
        pass
    _logger.warning(
        "SQLite lock contention absorbed at %s -- another cairn process holds "
        "the DB; busy_timeout retried/absorbed this so the operation completed "
        "(or degraded gracefully). Repeated contention is diagnosable via "
        "`cairn serve start` (single-daemon SSE mode serializes access and "
        "avoids cross-process lock waits).",
        site,
    )


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Create tables/indexes/FTS (idempotent) and run additive migrations."""
    conn.executescript(SCHEMA_SQL)
    for migration in MIGRATIONS:
        # Check if migration has already been applied
        migration_name = _extract_migration_name(migration)
        already_applied = conn.execute(
            "SELECT 1 FROM schema_meta WHERE key = ?",
            (migration_name,)
        ).fetchone() is not None

        if already_applied:
            continue  # Skip already-applied migration

        try:
            conn.execute(migration)
            # Record successful migration
            conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES (?, ?)",
                (migration_name, "applied")
            )
        except sqlite3.OperationalError as e:
            error_msg = str(e).lower()
            if "duplicate column" in error_msg:
                # Idempotent: column already exists. This is the expected path on
                # a fresh DB whose CREATE TABLE already declares the column (e.g.
                # transitive_edges.target_id) -- the migration is retained only to
                # upgrade pre-existing DBs. It is NOT lock contention, so it must
                # stay silent; warning here would fire on every first-run DB init
                # and dilute the note_contention signal with a false positive.
                conn.execute(
                    "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                    (migration_name, "applied")
                )
            else:
                # Genuine error. Surface lock contention once before
                # propagating so the failure isn't silent; other shapes
                # ("no such table" on a corrupt DB) are not contention and
                # are skipped by note_contention's discrimination.
                note_contention("schema.migration", error=e)
                raise

    # Deferred until after MIGRATIONS: the target_id column may not exist on a
    # pre-existing DB until TRANSITIVE_EDGES_TARGET_ID_MIGRATION backfills it.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transitive_target_id ON transitive_edges(target_id)"
    )


def _extract_migration_name(migration_sql: str) -> str:
    """Extract a unique migration name from ALTER TABLE SQL (e.g. "edges.resolution")."""
    parts = migration_sql.split()
    # ``parts`` holds whitespace-split tokens, so check for the ADD and COLUMN
    # keywords separately (``"ADD COLUMN" in parts`` would look for a single
    # two-word element that never exists).
    if "ADD" in parts and "COLUMN" in parts:
        table_idx = parts.index("TABLE") + 1
        col_idx = parts.index("COLUMN") + 1
        table = parts[table_idx]
        column = parts[col_idx]
        return f"{table}.{column}"
    else:
        # For non-ADD COLUMN migrations, use a hash or truncated SQL as key
        import hashlib
        # usedforsecurity=False: this is a content key (migration fingerprint),
        # not a cryptographic hash -- also silences bandit B324.
        return hashlib.md5(migration_sql.encode(), usedforsecurity=False).hexdigest()[:16]


def _maybe_backfill_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the FTS index from symbols when the token store is empty.

    Guards on symbols_fts_data being empty (the FTS shadow rows can exist with
    zero tokens if populated before triggers were wired), not the FTS row count.
    """
    try:
        token_rows = conn.execute(
            "SELECT COUNT(*) FROM symbols_fts_data"
        ).fetchone()[0]
        sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        if token_rows == 0 and sym_count > 0:
            conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    except sqlite3.OperationalError as e:
        # FTS5 unavailable, table missing, or no shadow table -- LIKE fallback.
        # None of those is contention; note_contention's discrimination skips
        # them so only a genuine "database is locked" emits the signal.
        note_contention("schema.backfill_fts", error=e)


def _unicode61_tokens(text: str):
    """Yield the FTS5 unicode61 tokenization of ``text`` (approximate).

    Case-folds and splits on non-alphanumeric runs, matching the
    ``tokenize='unicode61'`` declaration on ``symbols_fts`` for the ASCII
    identifiers and English docstrings this table serves (full unicode
    diacritic folding differs only for exotic input). Used by the
    ``rebuild_term_df`` fallback scan.
    """
    cur: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            yield "".join(cur)
            cur = []
    if cur:
        yield "".join(cur)


def _rebuild_term_df_vocab(conn: sqlite3.Connection, n_symbols: int) -> Optional[int]:
    """term_df rebuild via the FTS5 vocabulary (primary path).

    Returns the number of tokens written, or ``None`` when the fts5vocab
    virtual table cannot be created or queried (caller falls back to the
    aggregate scan). The vocab table lives in temp and targets main's
    symbols_fts via the three-argument form (a temp fts5vocab may reference
    an FTS5 table in any attached database). Row mode yields exactly one
    row per distinct term with ``doc`` = number of FTS rows containing it,
    which IS symbol_df -- no GROUP BY or COUNT needed.
    """
    try:
        conn.execute("DROP TABLE IF EXISTS temp.term_df_vocab")
        conn.execute(
            "CREATE VIRTUAL TABLE temp.term_df_vocab "
            "USING fts5vocab(main, symbols_fts, row)"
        )
        try:
            conn.execute("DELETE FROM term_df")
            cur = conn.execute(
                "INSERT INTO term_df (token, symbol_df, n_symbols) "
                "SELECT term, doc, ? FROM temp.term_df_vocab",
                (n_symbols,),
            )
            return cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0
        finally:
            conn.execute("DROP TABLE temp.term_df_vocab")
    except sqlite3.OperationalError as e:
        _logger.debug(
            "term_df fts5vocab path unavailable (%s); falling back to scan", e
        )
        return None


def _rebuild_term_df_scan(conn: sqlite3.Connection, n_symbols: int) -> int:
    """term_df rebuild via one aggregate scan of symbols (fallback path).

    Tokenizes each symbol's indexed text (name, qualified_name, docstring)
    with the unicode61 approximation in :func:`_unicode61_tokens` and counts
    the distinct symbols per token. Deterministic: a pure function of the
    symbols table's contents.
    """
    df: dict[str, set[int]] = {}
    for rowid, name, qname, doc in conn.execute(
        "SELECT rowid, name, qualified_name, docstring FROM symbols"
    ):
        text = " ".join(t for t in (name, qname, doc) if t)
        for token in set(_unicode61_tokens(text)):
            df.setdefault(token, set()).add(rowid)
    conn.execute("DELETE FROM term_df")
    conn.executemany(
        "INSERT INTO term_df (token, symbol_df, n_symbols) VALUES (?, ?, ?)",
        [(token, len(rows), n_symbols) for token, rows in sorted(df.items())],
    )
    return len(df)


def rebuild_term_df(conn: sqlite3.Connection) -> int:
    """Rebuild the persisted per-corpus DF table from ``symbols_fts``.

    Populates one row per indexed token: ``symbol_df`` = number of distinct
    symbols whose indexed text contains the token, ``n_symbols`` = total
    symbol count. Reads the FTS5 vocabulary in row mode via an fts5vocab
    virtual table; when that is unusable, falls back to one aggregate scan
    of the symbols table. A pure function of the DB contents -- no env,
    network, or time dependence -- so repeated runs on the same DB produce
    identical table contents (spec retrieval-quality-v2 TC-014). Commits;
    returns the number of tokens written.
    """
    n_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    written = _rebuild_term_df_vocab(conn, n_symbols)
    if written is None or (written == 0 and n_symbols > 0):
        # No vocab path, or a zero-row vocab on a non-empty corpus -- the
        # latter means the FTS index is stale/empty (e.g. symbols existed
        # before symbols_fts did). Fall back to the aggregate scan.
        written = _rebuild_term_df_scan(conn, n_symbols)
    conn.commit()
    return written


# Paths whose schema has already been applied+backfilled in this process.
# Schema/backfill only need to happen once per process per db path -- guarded
# by a lock since the metric flusher thread (server.py) can call get_db()
# concurrently.
_INITIALIZED_PATHS: set[str] = set()
_INIT_LOCK = threading.Lock()


def get_db(
    db_path: Optional[str] = None,
    busy_timeout_ms: int = 5000,
    read_only: bool = False,
) -> sqlite3.Connection:
    """Open a SQLite connection to the graph DB, creating it if missing.

    Runs idempotent schema migrations and returns a connection with Row factory
    and foreign keys ON. When db_path is None, resolves the store for the
    current workspace context (CAIRN_DB env > central store keyed by workspace).

    read_only=True opens via the SQLite URI (`file:<path>?mode=ro`); such a
    connection cannot contend with writers and skips schema apply / FTS backfill
    (migrations are the writable CLI process's responsibility).
    """
    path = Path(db_path) if db_path else resolve_store().db
    key = str(path.resolve())  # resolve() works on non-existent paths too (strict=False default)
    # FR-004 (D-008): a missing store PARENT DIRECTORY yields sqlite's bare
    # "unable to open database file", which names neither the path nor the env
    # that resolved it. Raise the same exception type (doctor's catch formats
    # its own "cannot open database: " prefix around it) with the resolved
    # path, env chain, and remediation. Choke point before any connect, so
    # read-only and writable opens both enrich. A directory that exists with
    # the db file merely absent keeps today's behavior (sqlite creates/opens).
    if not path.parent.exists():
        raise sqlite3.OperationalError(
            f"store parent directory does not exist (db path: {path}). "
            f"Env resolution chain: {render_env_resolution_chain()}. "
            f"Fix: set CAIRN_HOME to the parent of the populated store "
            f"(default ~/.cairn), then run 'cairn init && cairn build' first."
        )
    if read_only:
        # URI form: a read-only connection. must exist -- a read-only open of
        # a missing file is an error a writer must fix via `cairn init && cairn build`.
        # quote(): '?', '#' and spaces in the path would otherwise parse as
        # URI query/fragment separators and silently truncate mode=ro.
        uri = f"file:{quote(str(path.resolve()))}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA mmap_size = 268435456")
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    # The schema work + commit and marking the path initialized must be atomic
    # under _INIT_LOCK so a second thread calling get_db() for the same path
    # cannot observe the key as initialized on a connection whose migrations
    # have not yet been applied. The flag is set AFTER the migration+commit
    # succeed — if _apply_schema raises mid-migration (disk full, locked file),
    # the path must NOT be marked initialized, or every later get_db(path) in
    # this process will skip schema application permanently.
    with _INIT_LOCK:
        already_initialized = key in _INITIALIZED_PATHS
        # Only a writable connection can apply the schema (it writes). A
        # read-only connection must NOT mark the path initialized.
        if not already_initialized and not read_only:
            _apply_schema(conn)
            _maybe_backfill_fts(conn)
            conn.commit()
            # Only flag initialized once the migration is durably committed.
            _INITIALIZED_PATHS.add(key)
    return conn


def init_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Create the schema (idempotent) and return an open connection."""
    conn = get_db(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def get_build_db() -> sqlite3.Connection:
    """In-memory connection tuned for bulk load. Persist with backup_to()."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")   # re-enabled on the persisted DB
    conn.execute("PRAGMA journal_mode = MEMORY")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -262144")  # 256 MB page cache (KB, negative)
    _apply_schema(conn)
    return conn


def _remove_db_sidecars(db_path: str) -> None:
    """Unlink "<db_path>-wal" and "<db_path>-shm" if present (missing = ok)."""
    for suffix in ("-wal", "-shm"):
        try:
            os.unlink(db_path + suffix)
        except OSError:
            pass


def swap_db_file(tmp_path: str, db_path: str) -> None:
    """Atomically replace ``db_path`` with ``tmp_path``, WAL-safe.

    os.replace alone is NOT enough when the OLD db ran in WAL mode: its
    "<db_path>-wal"/"-shm" survive the swap, and the next open replays the old
    committed WAL frames over the NEW main file -- silently serving the
    pre-build graph (or SQLITE_CORRUPT). The caller must hold ``build_lock``
    on the real db path. Steps: checkpoint the old WAL into the old main file
    (best-effort), unlink the old sidecars, THEN replace. Open old
    connections keep their fds on the unlinked inodes, so they are unaffected.
    """
    if os.path.exists(db_path):
        # Checkpoint so committed frames land in the old main file before the
        # sidecars go -- any straggler that still reads the old inode finds a
        # self-consistent DB. Best-effort: a busy TRUNCATE leaves frames in a
        # wal that is unlinked below regardless.
        try:
            old = sqlite3.connect(db_path)
            try:
                old.execute("PRAGMA busy_timeout = 5000")
                old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                old.close()
        except sqlite3.Error:
            pass
    _remove_db_sidecars(db_path)
    os.replace(tmp_path, db_path)
    # The tmp DB was opened in WAL mode (serving mode for readers); a clean
    # close removes its sidecars, but a crash path can leave "<tmp>-wal"
    # litter behind -- it has the wrong name to be replayed, drop it anyway.
    _remove_db_sidecars(tmp_path)


# Analytics tables carried across a whole-file DB swap (full rebuild /
# staged build). The swap replaces the entire file, so without this list the
# build history, degradation events, and tool health reset to empty on every
# rebuild -- defeating the retention contract that makes build trends,
# contention history, and doctor's freshness/tool-health windows useful
# (spec observability-telemetry §6.2). ``pending_sync`` is deliberately NOT
# carried: it is operational state (files with unindexed edits) tied to the
# pre-swap graph's rows, and a full rebuild has recomputed that graph.
_TELEMETRY_TABLE_COLUMNS = {
    "build_runs": (
        "kind, started_at, duration_s, phase_timings, repos, files, symbols, "
        "edges, resolution_exact, resolution_ambiguous, resolution_unresolved, "
        "parse_errors, skipped, workers, session_id"
    ),
    "events": "ts, name, session_id, attrs",
    "tool_metrics": (
        "tool_name, session_id, invoked_at, duration_ms, status, error_message, "
        "req_chars, resp_chars, args_summary, source, "
        "truncated_from_chars, truncated_to_chars"
    ),
}


def copy_telemetry_tables(dest_conn: sqlite3.Connection, old_db_path: str) -> None:
    """Carry the analytics tables from ``old_db_path`` into ``dest_conn``.

    Called inside ``build_lock`` immediately before a whole-file swap
    (:func:`backup_to` and the staged-build swap in ``cli.core``), with the
    freshly built DB open on ``dest_conn`` and the DB being replaced still at
    ``old_db_path``. Rows are appended with FRESH ids (the id space of the new
    DB is not empty on the staged path, where this build's own ``build_runs``
    row already landed), preserving source order so time-ordered consumers
    stay correct. Best-effort throughout: a missing old DB (first build), a
    pre-telemetry old DB (missing tables), or any copy error degrades to
    starting the analytics history fresh -- analytics must never fail a build.
    """
    if not os.path.exists(old_db_path):
        return
    try:
        dest_conn.execute("ATTACH DATABASE ? AS srcdb", (old_db_path,))
    except sqlite3.Error:
        return
    try:
        for table, columns in _TELEMETRY_TABLE_COLUMNS.items():
            try:
                exists = dest_conn.execute(
                    "SELECT 1 FROM srcdb.sqlite_master "
                    "WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()
                if not exists:
                    continue  # pre-telemetry source DB -- nothing to carry
                dest_conn.execute(
                    f"INSERT INTO {table} ({columns}) "
                    f"SELECT {columns} FROM srcdb.{table} ORDER BY id"
                )
            except sqlite3.Error:
                pass  # best-effort per table (analytics, not correctness)
        dest_conn.commit()
    finally:
        try:
            dest_conn.execute("DETACH DATABASE srcdb")
        except sqlite3.Error:
            pass


def backup_to(mem_conn: sqlite3.Connection, db_path: str) -> None:
    """Persist an in-memory build DB to disk with atomic swap and build locking."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    with build_lock(db_path):
        # Write to temp file in the same directory for atomic swap
        tmp_path = db_path + ".tmp"
        dest = sqlite3.connect(tmp_path)
        try:
            try:
                with dest:
                    mem_conn.backup(dest)          # C-level page copy, no row iteration
                dest.execute("PRAGMA foreign_keys = ON")
                dest.execute("PRAGMA journal_mode = WAL")  # serving mode for readers
                # Carry analytics history from the DB about to be replaced --
                # the swap below discards the whole old file, and without
                # this every full rebuild reset build_runs/events/tool_metrics
                # to empty (see copy_telemetry_tables).
                copy_telemetry_tables(dest, db_path)
                dest.commit()
            except BaseException:
                # A failed backup must not leave a half-written "<db>.tmp" on
                # disk (the next successful build would swap it in). dest may
                # have flipped to WAL mid-failure, so drop sidecars too.
                _remove_db_sidecars(tmp_path)
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        finally:
            dest.close()

        # Atomic swap: readers with the old inode keep seeing the old DB,
        # new connections see the fresh DB (old WAL sidecars removed -- see
        # swap_db_file)
        swap_db_file(tmp_path, db_path)


# Imported here to avoid a circular import at module top (contextlib is stdlib).
import contextlib


@contextlib.contextmanager
def build_lock(db_path: str):
    """Advisory exclusive lock serializing writers of ``db_path``.

    The full-rebuild path (in-memory build -> backup_to), the single-repo
    path (init_db -> direct writes), staged CLI builds, and incremental
    updates all write the live DB. Without this lock two of them could
    interleave writes with only SQLite's busy_timeout as a guard. The lock
    is non-blocking: a second writer raises RuntimeError immediately rather
    than waiting, so the user knows to retry later.

    The lock FILE is deliberately never unlinked, by winner or by loser:
    flock is inode-based, and unlink-on-release races a third process into
    creating a fresh inode while a waiter still blocks on the old one (two
    concurrent holders). A stale zero-byte ``<db>.build.lock`` is harmless --
    only flock state matters, and it dies with the holder's fd.

    Raises ``RuntimeError`` if another build or update holds the lock.
    """
    lock_path = str(db_path) + ".build.lock"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError) as e:
            if e.errno == errno.EWOULDBLOCK:
                raise RuntimeError(
                    f"Cannot acquire build lock: another build or update is "
                    f"already in progress on {db_path}. Wait for it to finish "
                    f"and retry."
                )
            raise
        try:
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)
