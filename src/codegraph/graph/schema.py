"""SQLite schema and database lifecycle for the Layer 1 code graph.

Additional tables (repo_deps, memory_refs, etc.) are added by later migrations.
"""
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
-- (cg stats reports counts by reason) rather than silent. A skip is NOT an
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
-- file watcher inserts on event; reindex_paths deletes on completion. MCP
-- tools check this table to prepend staleness banners.
CREATE TABLE IF NOT EXISTS pending_sync (
    path TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    changed_at TIMESTAMP NOT NULL
);

-- semantic embeddings keyed to symbols. Vector stored as a BLOB (float32
-- little-endian array). semantic_search() in queries.py decodes the BLOB +
-- cosine-compares against the embedded query. Additive only -- the table
-- stays empty when the [semantic] extra isn't installed; nothing in the
-- builder hot path touches it. The batch `cg embed` command populates it on
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
-- `cg knowledge embed` command populates it on demand.
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
-- MCP tool call. Populated by build_dataflow_index() during `cg build`/`cg sync`.
CREATE TABLE IF NOT EXISTS dataflow (
    symbol TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    within_repo TEXT,      -- JSON list of impacted symbol names
    cross_repo TEXT,       -- JSON list of consumer repo names
    updated TIMESTAMP
);

-- MCP tool invocation metrics. Each tool call is recorded with timing, session
-- provenance, and status (ok/error). The `cg metrics` CLI command aggregates
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
# Added via ALTER rather than CREATE TABLE so existing DBs migrate in place.
EDGE_RESOLUTION_MIGRATION = "ALTER TABLE edges ADD COLUMN resolution TEXT"

# Store file size and mtime for fast catch-up reconciliation. Catch-up compares
# stat() against these columns; only re-hashes on mismatch.
FILES_SIZE_MIGRATION = "ALTER TABLE files ADD COLUMN size INTEGER"
FILES_MTIME_MIGRATION = "ALTER TABLE files ADD COLUMN mtime REAL"

# JSON metadata column for symbols that need structured data beyond
# name/kind/modifiers -- currently routes (kind='route'), which store
# {"http_method", "path", "framework", "handler"}. Reuses the `symbols` table
# rather than a new one, so every existing index/query/FTS5 trigger already
# covers routes with no further change.
SYMBOL_METADATA_MIGRATION = "ALTER TABLE symbols ADD COLUMN metadata TEXT"

# content-hash column on embeddings, so embed_all can detect a changed
# docstring/signature even when the model name hasn't changed. Without it the
# only invalidation trigger is "no row exists for this symbol under the current
# model" -- an edit that doesn't rename the model would silently leave a stale
# vector in place forever. NULL on rows written before this migration; embed_all
# treats NULL as "needs re-embedding" so old rows self-heal on the next
# `cg embed` rather than requiring a one-off backfill script.
EMBEDDINGS_CONTENT_HASH_MIGRATION = "ALTER TABLE embeddings ADD COLUMN content_hash TEXT"
SYMBOL_PARAMETERS_MIGRATION = "ALTER TABLE symbols ADD COLUMN parameters TEXT"
SYMBOL_RETURN_TYPE_MIGRATION = "ALTER TABLE symbols ADD COLUMN return_type TEXT"
# Variant-C embedding context: the three columns chunk_for_symbol reads
# behind `if "X" in row.keys()` guards (parent_scope / imports_summary /
# body). Additive ALTERs, mirroring parameters/return_type. Without these,
# the variant-C "Enclosing Scope:"/"Imports:"/"Body:" sections are always
# empty, silently degrading embedding quality.
SYMBOL_PARENT_SCOPE_MIGRATION = "ALTER TABLE symbols ADD COLUMN parent_scope TEXT"
SYMBOL_IMPORTS_SUMMARY_MIGRATION = "ALTER TABLE symbols ADD COLUMN imports_summary TEXT"
SYMBOL_BODY_MIGRATION = "ALTER TABLE symbols ADD COLUMN body TEXT"
TRANSITIVE_EDGES_TARGET_ID_MIGRATION = "ALTER TABLE transitive_edges ADD COLUMN target_id TEXT"

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
]

# Default DB location: resolved from the central store for the current workspace
# (see src/paths.py). Resolved lazily so tests that set CODEGRAPH_HOME before
# importing still get the right path. CLI decorators read DEFAULT_DB_PATH once
# at import; resolve_store() reflects the cwd the user invoked `cg` from.
from codegraph.paths import resolve_store  # noqa: E402

DEFAULT_DB_PATH = resolve_store().db


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Create tables/indexes/FTS (idempotent) and run additive migrations.

    Shared by get_db and the in-memory build path (get_build_db) so both use
    the identical schema -- including the FTS5 shadow tables and triggers.
    """
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

    # Deferred until after MIGRATIONS: on a pre-existing DB, transitive_edges
    # may predate the target_id column, so this index can only be created
    # once TRANSITIVE_EDGES_TARGET_ID_MIGRATION has backfilled it.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transitive_target_id ON transitive_edges(target_id)"
    )


def _extract_migration_name(migration_sql: str) -> str:
    """Extract a unique migration name from the ALTER TABLE SQL.

    Example: "ALTER TABLE edges ADD COLUMN resolution TEXT" -> "edges.resolution"
    """
    parts = migration_sql.split()
    if "ADD COLUMN" in parts:
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
    """Rebuild the FTS index from symbols when the shadow store is empty.

    A DB whose FTS rows exist but never got tokenized needs a 'rebuild' to
    populate the search index. 'rebuild' repopulates from the external content
    table (symbols). We guard on the token-store table being empty rather than
    the FTS row count: the FTS shadow rows can exist with zero tokens if the
    table was created/populated before triggers were wired, and COUNT(*) on
    symbols_fts would wrongly report "already indexed". The check is cheap
    (~2ms) because the shadow table is small.
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
# A long-lived `cg serve` process calls get_db() once per tool call; without
# this cache it re-runs the full CREATE-TABLE/migration script and an FTS
# backfill check on every call, each briefly taking SQLite's write lock. On
# a busy server that churn is frequent enough that external writers (e.g.
# `cg update`/`cg build` running in a separate process) can starve for the
# whole 5s busy_timeout and fail with "database is locked". Schema/backfill
# only need to happen once per process per db path -- guarded by a lock since
# the metric flusher thread (server.py) can call get_db() concurrently.
_INITIALIZED_PATHS: set[str] = set()
_INIT_LOCK = threading.Lock()


def get_db(
    db_path: Optional[str] = None,
    busy_timeout_ms: int = 5000,
    read_only: bool = False,
) -> sqlite3.Connection:
    """Open a SQLite connection to the graph DB. Creates the file if missing.

    Runs schema migrations (idempotent CREATE IF NOT EXISTS) so that DBs created
    in earlier phases gain new tables (memory_refs, repo_deps) automatically.
    Returns a connection with Row factory enabled and foreign keys ON.

    When db_path is None, resolves the store for the current workspace context
    (CODEGRAPH_DB env > central store keyed by workspace).

    Schema application and FTS backfill are skipped after the first call for
    a given path in this process (see _INITIALIZED_PATHS above) -- callers
    that need to force a re-check (e.g. right after an external process may
    have created the file) should use init_db() instead.

    busy_timeout_ms defaults to 5s, tuned for interactive MCP tool calls that
    should fail fast rather than hang. CLI write commands (`cg update`, single
    -file reindex) pass a longer value: multiple MCP clients (SSE daemon,
    per-editor stdio `cg serve` processes) can legitimately hold the writer
    lock for longer than 5s under concurrent load, and a background CLI
    command can afford to wait.

    read_only=True opens the connection read-only via the SQLite URI
    (`file:<path>?mode=ro`). Such a connection CANNOT acquire SQLite's writer
    lock, so it is structurally incapable of contending with writers -- this
    is the basis for a read-only MCP daemon that coexists with `cg build` /
    `cg embed` / `cg memory` holding the single write lock. Under read_only:
      - journal_mode=WAL is NOT set: it requires a write transaction, so a
        read-only connection would raise "attempt to write a readonly
        database". WAL is a property of the *file*, set by the first writer
        (the CLI), so readers still get WAL's concurrent-reader benefits.
      - schema apply / FTS backfill are skipped: they write. Migrations are
        the responsibility of the writable CLI process (`cg build`/`cg init`),
        not a read-only reader. A read-only open does NOT mark the path as
        initialized in _INITIALIZED_PATHS, so a later writable get_db() in the
        same process still runs its migrations.
    """
    path = Path(db_path) if db_path else resolve_store().db
    key = str(path.resolve())  # resolve() works on non-existent paths too (strict=False default)
    if read_only:
        # URI form: a read-only connection. must exist -- a read-only open of
        # a missing file is an error a writer must fix via `cg init && cg build`.
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
    # TOCTOU invariant: the schema work (_apply_schema + _maybe_backfill_fts +
    # commit) and the marking of the path as initialized must be atomic under
    # _INIT_LOCK, so a second thread calling get_db() for the same path cannot
    # observe the key as initialized (skip its own migrations) on a connection
    # whose migrations have not been applied yet. _apply_schema and
    # _maybe_backfill_fts only execute SQL and never acquire _INIT_LOCK, so
    # nesting them here cannot self-deadlock (no re-entrancy).
    with _INIT_LOCK:
        already_initialized = key in _INITIALIZED_PATHS
        # Only a writable connection can apply the schema (it writes). A
        # read-only connection must NOT mark the path initialized: if it did,
        # a concurrent/racing writable get_db() in the same process would see
        # the path as already-initialized and skip its own _apply_schema,
        # silently losing migrations. Read-only readers always re-check; the
        # check is cheap (a set membership test under the lock).
        if not already_initialized and not read_only:
            _INITIALIZED_PATHS.add(key)
            # Ensure schema is current (idempotent; cheap on already-initialized
            # DBs). Held under the lock so a racing second get_db() for the same
            # path can't return a connection whose migrations haven't committed.
            _apply_schema(conn)
            _maybe_backfill_fts(conn)
            conn.commit()
    return conn


def init_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Create the schema (idempotent) and return an open connection."""
    conn = get_db(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def get_build_db() -> sqlite3.Connection:
    """In-memory connection tuned for bulk load. Persist with backup_to().

    A from-scratch full rebuild pays no disk/transaction overhead until the
    very end. NEVER apply these pragmas to the serving connection (get_db):
    they trade durability/concurrency for throughput, which is only safe for a
    from-scratch build you can always redo.
    """
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
    """Persist an in-memory build DB to disk with atomic swap and build locking.

    Writes to db_path + '.tmp' first, then os.replace() for atomic swap.
    Takes an advisory file lock on db_path + '.build.lock' to prevent
    concurrent rebuilds. Leaves the persisted DB with foreign_keys ON and
    journal_mode WAL, ready to serve.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # Take advisory lock to prevent concurrent rebuilds
    lock_path = db_path + ".build.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        # Try to acquire exclusive lock (non-blocking)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError) as e:
            if e.errno == errno.EWOULDBLOCK:
                raise RuntimeError(
                    f"Cannot rebuild: another build is already in progress. "
                    f"If you believe this is incorrect, remove {lock_path} and retry."
                )
            raise

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

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        # Clean up lock file if it exists
        if os.path.exists(lock_path):
            try:
                os.unlink(lock_path)
            except OSError:
                pass
