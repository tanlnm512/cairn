"""SQLite schema and database lifecycle for the Layer 1 code graph."""
from __future__ import annotations

import errno
import fcntl
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

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

-- pending_sync tracks files with unindexed edits (the debounce window). A live
-- filesystem watcher (the optional [watch] extra) inserts on event;
-- reindex_paths deletes on completion. MCP tools check this table to prepend
-- staleness banners. The default install has no live watcher (watcher.py is
-- boot-time catch-up only), so this table stays empty unless [watch] is wired.
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
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_metrics_tool ON tool_metrics(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_metrics_session ON tool_metrics(session_id);

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
]

# Default DB location: resolved from the central store for the current workspace.
from cairn.paths import resolve_store  # noqa: E402

DEFAULT_DB_PATH = resolve_store().db


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
                # Idempotent: column already exists, mark as applied
                conn.execute(
                    "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
                    (migration_name, "applied")
                )
            else:
                # Real error - raise it
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
        return hashlib.md5(migration_sql.encode()).hexdigest()[:16]


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
    except sqlite3.OperationalError:
        pass  # FTS5 unavailable, table missing, or no shadow table -- LIKE fallback


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
    if read_only:
        # URI form: a read-only connection. must exist -- a read-only open of
        # a missing file is an error a writer must fix via `cairn init && cairn build`.
        uri = f"file:{path.resolve()}?mode=ro"
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


def backup_to(mem_conn: sqlite3.Connection, db_path: str) -> None:
    """Persist an in-memory build DB to disk with atomic swap and build locking."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    with build_lock(db_path):
        # Write to temp file in the same directory for atomic swap
        tmp_path = db_path + ".tmp"
        dest = sqlite3.connect(tmp_path)
        try:
            with dest:
                mem_conn.backup(dest)          # C-level page copy, no row iteration
            dest.execute("PRAGMA foreign_keys = ON")
            dest.execute("PRAGMA journal_mode = WAL")  # serving mode for readers
            dest.commit()
        finally:
            dest.close()

        # Atomic swap: readers with the old inode keep seeing the old DB,
        # new connections see the fresh DB
        os.replace(tmp_path, db_path)


# Imported here to avoid a circular import at module top (contextlib is stdlib).
import contextlib


@contextlib.contextmanager
def build_lock(db_path: str):
    """Advisory exclusive lock preventing concurrent rebuilds of ``db_path``.

    The full-rebuild path (in-memory build -> backup_to) and the single-repo
    path (init_db -> direct writes) both write the live DB. Without this lock
    two ``cairn build --repo X`` runs (or a build racing an update) could
    interleave writes with only SQLite's busy_timeout as a guard. The lock is
    non-blocking: a second build raises RuntimeError immediately rather than
    waiting, so the user knows to retry later.

    Raises ``RuntimeError`` if another build holds the lock.
    """
    lock_path = str(db_path) + ".build.lock"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
    acquired = False
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except (IOError, OSError) as e:
            if e.errno == errno.EWOULDBLOCK:
                raise RuntimeError(
                    f"Cannot rebuild: another build is already in progress. "
                    f"If you believe this is incorrect, remove {lock_path} and retry."
                )
            raise
        yield
    finally:
        if acquired:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        # Clean up the lock file so it doesn't outlive the build.
        if os.path.exists(lock_path):
            try:
                os.unlink(lock_path)
            except OSError:
                pass
