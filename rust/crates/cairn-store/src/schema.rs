//! Schema DDL mirror: base tables plus the additive-migration column order of the Python store.

use std::path::Path;
use std::time::Duration;

use rusqlite::Connection;

/// Base DDL mirroring `src/cairn/graph/schema.py` SCHEMA_SQL (column definitions
/// and order are the C-1 contract; prose comments are not carried over).
pub const SCHEMA_SQL: &str = r#"
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

CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    name,
    qualified_name,
    docstring,
    content='symbols',
    content_rowid='rowid',
    tokenize='unicode61'
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

CREATE TABLE IF NOT EXISTS term_df (
    token TEXT PRIMARY KEY,
    symbol_df INTEGER,
    n_symbols INTEGER
);

CREATE TABLE IF NOT EXISTS memory_refs (
    id TEXT PRIMARY KEY,
    memory_path TEXT NOT NULL,
    session_id TEXT NOT NULL,
    referenced_at TIMESTAMP NOT NULL,
    context TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_refs_path ON memory_refs(memory_path);
CREATE INDEX IF NOT EXISTS idx_memory_refs_session ON memory_refs(session_id);

CREATE TABLE IF NOT EXISTS memory_failure_signatures (
    sig         TEXT PRIMARY KEY,
    tool_name   TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    first_seen  TIMESTAMP NOT NULL,
    last_seen   TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS parse_errors (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    repo_id TEXT NOT NULL REFERENCES repos(id),
    error_message TEXT,
    stack_trace TEXT,
    timestamp TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_parse_errors_repo ON parse_errors(repo_id);

CREATE TABLE IF NOT EXISTS skipped_files (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    path TEXT NOT NULL,
    reason TEXT NOT NULL,
    size_bytes INTEGER,
    recorded_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_skipped_repo ON skipped_files(repo_id);
CREATE INDEX IF NOT EXISTS idx_skipped_reason ON skipped_files(reason);
CREATE INDEX IF NOT EXISTS idx_skipped_path ON skipped_files(path);

CREATE INDEX IF NOT EXISTS idx_imports_file ON imports(file_id);
CREATE INDEX IF NOT EXISTS idx_imports_path ON imports(imported_path);

CREATE TABLE IF NOT EXISTS pending_sync (
    path TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    changed_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    symbol_id TEXT NOT NULL REFERENCES symbols(id),
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,
    chunk TEXT NOT NULL,
    embedded_at TIMESTAMP,
    PRIMARY KEY (symbol_id, model)
);
CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model);

CREATE TABLE IF NOT EXISTS embeddings_mv (
    symbol_id TEXT NOT NULL REFERENCES symbols(id),
    model TEXT NOT NULL,
    vector_kind TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,
    chunk TEXT NOT NULL,
    content_hash TEXT,
    embedded_at TIMESTAMP,
    PRIMARY KEY (symbol_id, model, vector_kind)
);
CREATE INDEX IF NOT EXISTS idx_embeddings_mv_model ON embeddings_mv(model);

CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    doc_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,
    chunk TEXT NOT NULL,
    embedded_at TIMESTAMP,
    PRIMARY KEY (doc_id, chunk_index, model)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_embeddings_model ON knowledge_embeddings(model);

CREATE TABLE IF NOT EXISTS knowledge_edges (
    doc_id TEXT NOT NULL,
    related_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    kind TEXT NOT NULL,
    provenance TEXT,
    created_at REAL,
    PRIMARY KEY (doc_id, related_id, relation, kind)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_doc ON knowledge_edges(doc_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_related ON knowledge_edges(related_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_relation ON knowledge_edges(relation);

CREATE TABLE IF NOT EXISTS knowledge_doc_refs (
    doc_id TEXT NOT NULL,
    ref TEXT NOT NULL,
    ref_kind TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (doc_id, ref, ref_kind)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_doc_refs_doc ON knowledge_doc_refs(doc_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_doc_refs_ref ON knowledge_doc_refs(ref);

CREATE TABLE IF NOT EXISTS memory_embeddings (
    doc_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vec BLOB NOT NULL,
    chunk TEXT NOT NULL,
    embedded_at TIMESTAMP,
    PRIMARY KEY (doc_id, chunk_index, model)
);
CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model ON memory_embeddings(model);

CREATE TABLE IF NOT EXISTS memory_validity (
    concept_id TEXT PRIMARY KEY,
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    successor_symbol TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_validity_from ON memory_validity(valid_from);
CREATE INDEX IF NOT EXISTS idx_memory_validity_until ON memory_validity(valid_until);

CREATE TABLE IF NOT EXISTS agent_symbols (
    agent_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    memory_id TEXT,
    kind TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_symbols_symbol_ts ON agent_symbols(symbol, ts);

CREATE TABLE IF NOT EXISTS dataflow (
    symbol TEXT NOT NULL,
    repo TEXT NOT NULL,
    within_repo TEXT,
    cross_repo TEXT,
    analysis_errors TEXT,
    updated TIMESTAMP,
    PRIMARY KEY (symbol, repo)
);

CREATE TABLE IF NOT EXISTS tool_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT 'unknown',
    invoked_at TIMESTAMP NOT NULL,
    duration_ms REAL,
    status TEXT NOT NULL DEFAULT 'ok',
    error_message TEXT,
    req_chars INTEGER,
    resp_chars INTEGER,
    args_summary TEXT,
    source TEXT NOT NULL DEFAULT 'mcp',
    truncated_from_chars INTEGER,
    truncated_to_chars INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tool_metrics_tool ON tool_metrics(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_metrics_session ON tool_metrics(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_metrics_invoked ON tool_metrics(invoked_at, id);

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

CREATE TABLE IF NOT EXISTS repo_build_state (
    repo_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TIMESTAMP NOT NULL,
    name TEXT NOT NULL,
    session_id TEXT,
    attrs TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_name ON events(name);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

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

CREATE TABLE IF NOT EXISTS communities (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    size INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS symbol_communities (
    community_id INTEGER NOT NULL,
    symbol_id TEXT NOT NULL,
    structural_degree INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS rationale (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES files(id),
    symbol_id TEXT REFERENCES symbols(id),
    line INTEGER NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rationale_file ON rationale(file_id);
CREATE INDEX IF NOT EXISTS idx_rationale_symbol ON rationale(symbol_id);

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"#;

/// Additive migrations mirroring `schema.py` MIGRATIONS order, `(name, ALTER SQL)`.
pub const MIGRATIONS: &[(&str, &str)] = &[
    (
        "edges.resolution",
        "ALTER TABLE edges ADD COLUMN resolution TEXT",
    ),
    ("files.size", "ALTER TABLE files ADD COLUMN size INTEGER"),
    ("files.mtime", "ALTER TABLE files ADD COLUMN mtime REAL"),
    (
        "symbols.metadata",
        "ALTER TABLE symbols ADD COLUMN metadata TEXT",
    ),
    (
        "embeddings.content_hash",
        "ALTER TABLE embeddings ADD COLUMN content_hash TEXT",
    ),
    (
        "symbols.parameters",
        "ALTER TABLE symbols ADD COLUMN parameters TEXT",
    ),
    (
        "symbols.return_type",
        "ALTER TABLE symbols ADD COLUMN return_type TEXT",
    ),
    (
        "symbols.parent_scope",
        "ALTER TABLE symbols ADD COLUMN parent_scope TEXT",
    ),
    (
        "symbols.imports_summary",
        "ALTER TABLE symbols ADD COLUMN imports_summary TEXT",
    ),
    ("symbols.body", "ALTER TABLE symbols ADD COLUMN body TEXT"),
    (
        "transitive_edges.target_id",
        "ALTER TABLE transitive_edges ADD COLUMN target_id TEXT",
    ),
    (
        "symbols.source",
        "ALTER TABLE symbols ADD COLUMN source TEXT",
    ),
    (
        "tool_metrics.req_chars",
        "ALTER TABLE tool_metrics ADD COLUMN req_chars INTEGER",
    ),
    (
        "tool_metrics.resp_chars",
        "ALTER TABLE tool_metrics ADD COLUMN resp_chars INTEGER",
    ),
    (
        "tool_metrics.args_summary",
        "ALTER TABLE tool_metrics ADD COLUMN args_summary TEXT",
    ),
    (
        "tool_metrics.source",
        "ALTER TABLE tool_metrics ADD COLUMN source TEXT NOT NULL DEFAULT 'mcp'",
    ),
    (
        "tool_metrics.truncated_from_chars",
        "ALTER TABLE tool_metrics ADD COLUMN truncated_from_chars INTEGER",
    ),
    (
        "tool_metrics.truncated_to_chars",
        "ALTER TABLE tool_metrics ADD COLUMN truncated_to_chars INTEGER",
    ),
    (
        "imports.local_alias",
        "ALTER TABLE imports ADD COLUMN local_alias TEXT",
    ),
    (
        "symbols.arity",
        "ALTER TABLE symbols ADD COLUMN arity INTEGER",
    ),
    ("edges.source", "ALTER TABLE edges ADD COLUMN source TEXT"),
];

const DATAFLOW_PK_MIGRATION: &str = "dataflow.pk_symbol_repo";
const MEMORY_VALIDITY_BACKFILL_KEY: &str = "memory_validity.backfill";

fn migration_applied(conn: &Connection, key: &str) -> rusqlite::Result<bool> {
    conn.query_row("SELECT 1 FROM schema_meta WHERE key = ?", [key], |row| {
        row.get::<_, i64>(0)
    })
    .map(|_| true)
    .or_else(|e| match e {
        rusqlite::Error::QueryReturnedNoRows => Ok(false),
        other => Err(other),
    })
}

fn record_migration(conn: &Connection, key: &str) -> rusqlite::Result<()> {
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, 'applied')",
        [key],
    )?;
    Ok(())
}

fn dataflow_pk(conn: &Connection) -> rusqlite::Result<Vec<String>> {
    let mut stmt =
        conn.prepare("SELECT name FROM pragma_table_info('dataflow') WHERE pk > 0 ORDER BY pk")?;
    stmt.query_map([], |r| r.get(0))?.collect()
}

fn dataflow_needs_rebuild(conn: &Connection) -> rusqlite::Result<bool> {
    let has_errors_column: bool = conn.query_row(
        "SELECT EXISTS (SELECT 1 FROM pragma_table_info('dataflow') WHERE name = 'analysis_errors')",
        [],
        |r| r.get(0),
    )?;
    Ok(!has_errors_column || dataflow_pk(conn)? != ["symbol", "repo"])
}

fn migrate_dataflow_pk(conn: &Connection) -> rusqlite::Result<()> {
    // Shape decides, not the marker: a stamped store may still hold a legacy table.
    let rebuild = dataflow_needs_rebuild(conn)?;
    if !rebuild && migration_applied(conn, DATAFLOW_PK_MIGRATION)? {
        return Ok(());
    }
    let tx = conn.unchecked_transaction()?;
    if rebuild {
        // ALTER cannot change a PK: rebuild and carry the rows over.
        tx.execute_batch(
            "DROP TABLE IF EXISTS dataflow_new;
            CREATE TABLE dataflow_new (
                symbol TEXT NOT NULL,
                repo TEXT NOT NULL,
                within_repo TEXT,
                cross_repo TEXT,
                analysis_errors TEXT,
                updated TIMESTAMP,
                PRIMARY KEY (symbol, repo)
            );
            INSERT INTO dataflow_new (symbol, repo, within_repo, cross_repo, updated)
                SELECT symbol, repo, within_repo, cross_repo, updated FROM dataflow;
            DROP TABLE dataflow;
            ALTER TABLE dataflow_new RENAME TO dataflow;",
        )?;
    }
    record_migration(&tx, DATAFLOW_PK_MIGRATION)?;
    tx.commit()
}

/// Create tables/indexes/FTS (idempotent) and apply additive migrations,
/// mirroring `schema.py::_apply_schema`.
pub fn apply_schema(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(SCHEMA_SQL)?;
    for (name, sql) in MIGRATIONS {
        if migration_applied(conn, name)? {
            continue;
        }
        // Duplicate-column on an already-migrated DB records the marker too.
        match conn.execute_batch(sql) {
            Ok(()) => record_migration(conn, name)?,
            Err(rusqlite::Error::SqliteFailure(e, msg))
                if e.code == rusqlite::ErrorCode::Unknown
                    && msg
                        .as_deref()
                        .is_some_and(|m| m.contains("duplicate column")) =>
            {
                record_migration(conn, name)?;
            }
            Err(e) => return Err(e),
        }
    }
    migrate_dataflow_pk(conn)?;
    conn.execute_batch(
        "CREATE INDEX IF NOT EXISTS idx_transitive_target_id ON transitive_edges(target_id)",
    )?;
    Ok(())
}

/// Record the memory-validity backfill marker when no sibling `.knowledge`
/// bundle exists; a present bundle is Python-side backfill work (not mirrored).
pub fn note_memory_validity_backfill(conn: &Connection, db_path: &Path) -> rusqlite::Result<()> {
    if migration_applied(conn, MEMORY_VALIDITY_BACKFILL_KEY)? {
        return Ok(());
    }
    let root = db_path.parent().map(|p| p.join(".knowledge"));
    let has_bundle = root.is_some_and(|p| p.is_dir());
    if !has_bundle {
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES (?, 'applied')",
            [MEMORY_VALIDITY_BACKFILL_KEY],
        )?;
    }
    Ok(())
}

/// Open a store with the Python `get_db` parity: writable pragmas, schema
/// application, and the memory-validity backfill marker (the writable
/// counterpart of `conn::open_read_only`; plain pragma-only opens use
/// `conn::open_writable`).
pub fn open_initialized(path: &Path) -> Result<Connection, rusqlite::Error> {
    crate::conn::register_vec0();
    let conn = Connection::open(path)?;
    conn.busy_timeout(Duration::from_millis(crate::conn::DEFAULT_BUSY_TIMEOUT_MS))?;
    conn.execute_batch(
        "PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL; PRAGMA mmap_size = 268435456;",
    )?;
    apply_schema(&conn)?;
    note_memory_validity_backfill(&conn, path)?;
    Ok(conn)
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::*;

    fn temp_db(tag: &str) -> std::path::PathBuf {
        static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
        let unique = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "cairn-store-schema-{tag}-{}-{nanos}-{unique}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).expect("temp dir");
        dir.join("t.kg")
    }

    #[test]
    fn fresh_store_column_order_matches_the_migrated_python_layout() {
        let path = temp_db("columns");
        let conn = open_initialized(&path).expect("open writable");
        let cols = |table: &str| -> Vec<String> {
            let mut stmt = conn
                .prepare(&format!("PRAGMA table_info({table})"))
                .expect("table_info");
            let rows = stmt.query_map([], |r| r.get::<_, String>(1)).expect("cols");
            rows.map(|c| c.expect("col")).collect()
        };
        assert_eq!(
            cols("edges"),
            vec![
                "id",
                "source_id",
                "target_id",
                "target_name",
                "kind",
                "line",
                "column",
                "resolution",
                "source",
            ]
        );
        assert_eq!(
            cols("symbols"),
            vec![
                "id",
                "file_id",
                "name",
                "qualified_name",
                "kind",
                "line_start",
                "line_end",
                "column_start",
                "column_end",
                "docstring",
                "modifiers",
                "metadata",
                "parameters",
                "return_type",
                "parent_scope",
                "imports_summary",
                "body",
                "source",
                "arity",
            ]
        );
        assert_eq!(
            cols("files"),
            vec![
                "id",
                "repo_id",
                "path",
                "language",
                "hash",
                "line_count",
                "indexed_at",
                "size",
                "mtime",
            ]
        );
        assert_eq!(
            cols("imports"),
            vec![
                "id",
                "file_id",
                "imported_path",
                "resolved_symbol_id",
                "line",
                "local_alias"
            ]
        );
        conn.close().expect("close");

        // The store matches the python-built probe store's content-table set
        // (the live parity check lives in the G2 gate script).
        let conn = crate::conn::open_read_only(&path).expect("read-only reopen");
        let classes = crate::dump::classify_tables(&conn).expect("classify");
        let content: Vec<&String> = classes
            .iter()
            .filter(|(_, c)| **c == crate::dump::TableClass::Content)
            .map(|(n, _)| n)
            .collect();
        for expected in [
            "repos",
            "files",
            "symbols",
            "edges",
            "imports",
            "parse_errors",
            "skipped_files",
            "rationale",
            "schema_meta",
            "build_runs",
            "dataflow",
        ] {
            assert!(
                content.iter().any(|n| n.as_str() == expected),
                "missing {expected}"
            );
        }
        let _ = fs::remove_file(&path);
        let _ = fs::remove_dir(path.parent().expect("parent"));
    }

    fn stamped_store_with_legacy_dataflow(tag: &str) -> (std::path::PathBuf, Connection) {
        let path = temp_db(tag);
        let conn = Connection::open(&path).expect("open");
        apply_schema(&conn).expect("fresh schema");
        conn.execute_batch(
            "DROP TABLE dataflow;
            CREATE TABLE dataflow (
                symbol TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                within_repo TEXT,
                cross_repo TEXT,
                updated TIMESTAMP
            );
            INSERT INTO dataflow (symbol, repo, within_repo, cross_repo, updated)
                VALUES ('Shared', 'ra', '[\"x\"]', '[]', 0);",
        )
        .expect("legacy table");
        (path, conn)
    }

    fn remove_store(path: &Path, conn: Connection) {
        conn.close().expect("close");
        let _ = fs::remove_file(path);
        let _ = fs::remove_dir(path.parent().expect("parent"));
    }

    #[test]
    fn legacy_dataflow_table_is_rebuilt_even_when_already_stamped() {
        let (path, conn) = stamped_store_with_legacy_dataflow("dataflow-pk");

        apply_schema(&conn).expect("apply");

        assert_eq!(dataflow_pk(&conn).expect("pk"), ["symbol", "repo"]);
        let (repo, within, errors): (String, String, Option<String>) = conn
            .query_row(
                "SELECT repo, within_repo, analysis_errors FROM dataflow WHERE symbol = 'Shared'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .expect("carried row");
        assert_eq!(
            (repo.as_str(), within.as_str(), errors),
            ("ra", "[\"x\"]", None)
        );
        assert!(migration_applied(&conn, DATAFLOW_PK_MIGRATION).expect("marker"));
        remove_store(&path, conn);
    }

    #[test]
    fn failed_dataflow_rebuild_leaves_the_legacy_table_intact() {
        let (path, conn) = stamped_store_with_legacy_dataflow("dataflow-atomic");
        // A view on dataflow makes the final RENAME fail after the DROP.
        conn.execute_batch(
            "DELETE FROM schema_meta WHERE key = 'dataflow.pk_symbol_repo';
            CREATE VIEW dataflow_symbols AS SELECT symbol FROM dataflow;",
        )
        .expect("failure setup");

        assert!(apply_schema(&conn).is_err());

        assert_eq!(dataflow_pk(&conn).expect("pk"), ["symbol"]);
        let rows: i64 = conn
            .query_row("SELECT COUNT(*) FROM dataflow", [], |r| r.get(0))
            .expect("rows");
        assert_eq!(rows, 1);
        assert!(!migration_applied(&conn, DATAFLOW_PK_MIGRATION).expect("marker"));
        remove_store(&path, conn);
    }

    #[test]
    fn memory_validity_marker_lands_without_a_sibling_bundle() {
        let path = temp_db("validity");
        let conn = open_initialized(&path).expect("open writable");
        let marked: String = conn
            .query_row(
                "SELECT value FROM schema_meta WHERE key = 'memory_validity.backfill'",
                [],
                |r| r.get(0),
            )
            .expect("marker");
        assert_eq!(marked, "applied");
        conn.close().expect("close");
        let _ = fs::remove_file(&path);
        let _ = fs::remove_dir(path.parent().expect("parent"));
    }
}
