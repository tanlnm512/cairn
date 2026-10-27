//! `cargo xtask corner-store <db>`: build the corner-case `.kg` fixture for the canonical-dump golden test.

use std::path::Path;
use std::process::ExitCode;

use cairn_store::conn::open_writable;
use rusqlite::{Connection, params};

const SCHEMA: &str = r#"
CREATE TABLE cells (
    id INTEGER PRIMARY KEY,
    n INTEGER,
    r REAL,
    t TEXT,
    b BLOB
);
INSERT INTO cells VALUES
    (1, 0, 0.0, 'zero-int zero-real', x''),
    (2, -7, -0.0, 'tab' || char(9) || 'here' || char(10) || 'newline' || char(13) || 'cr\backslash', x'deadbeef00'),
    (3, 9223372036854775807, 0.1, '/Users/x/repo/src/main.rs', x'00ff10'),
    (4, NULL, 1e308, 'unicode é中 text', NULL),
    (5, 42, 5e-324, 'inf-dot-and-nul: end', x'0a0d09005c');
UPDATE cells SET t = CAST((t || char(0)) AS TEXT) WHERE id = 5;

CREATE TABLE tricky (a TEXT);
INSERT INTO tricky VALUES ('[table z]'), ('plain');

CREATE TABLE empty_t (x TEXT, y INTEGER);

CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, msg TEXT);
INSERT INTO events (msg) VALUES ('first');

CREATE VIRTUAL TABLE symbols_fts USING fts5(
    name, qualified_name, docstring,
    content='cells', content_rowid='id'
);
INSERT INTO symbols_fts(rowid, name) VALUES (1, 'zero');

CREATE VIRTUAL TABLE vec_corner USING vec0(
    embedding float[4] distance_metric=cosine
);

CREATE TABLE "weird ""name""" ("a b" TEXT, "c""d" INTEGER);
INSERT INTO "weird ""name""" VALUES ('quoted', 3);
"#;

fn f32_blob(values: [f32; 4]) -> Vec<u8> {
    values.iter().flat_map(|v| v.to_le_bytes()).collect()
}

fn build(db: &Path) -> Result<(), Box<dyn std::error::Error>> {
    for suffix in ["", "-wal", "-shm"] {
        let mut name = db.as_os_str().to_owned();
        name.push(suffix);
        let _ = std::fs::remove_file(name);
    }
    let conn: Connection = open_writable(db)?;
    conn.execute_batch(SCHEMA)?;
    let mut insert = conn.prepare("INSERT INTO cells (id, n, r, t, b) VALUES (?, ?, ?, ?, ?)")?;
    insert.execute(params![
        6,
        None::<i64>,
        f64::NEG_INFINITY,
        None::<String>,
        vec![1u8]
    ])?;
    insert.execute(params![
        7,
        None::<i64>,
        f64::INFINITY,
        None::<String>,
        None::<Vec<u8>>
    ])?;
    drop(insert);
    let mut vec_insert = conn.prepare("INSERT INTO vec_corner(rowid, embedding) VALUES (?, ?)")?;
    vec_insert.execute(params![1, f32_blob([1.0, 0.0, 0.0, 0.0])])?;
    vec_insert.execute(params![2, f32_blob([0.0, 1.0, 0.0, 0.0])])?;
    drop(vec_insert);
    let nul_text: String = conn.query_row("SELECT t FROM cells WHERE id = 5", [], |r| r.get(0))?;
    if nul_text != "inf-dot-and-nul: end\0" {
        return Err(format!("NUL text did not survive the round trip: {nul_text:?}").into());
    }
    conn.query_row("PRAGMA journal_mode = DELETE", [], |_| Ok(()))?;
    Ok(())
}

pub fn run(args: &[String]) -> ExitCode {
    let [db] = args else {
        eprintln!("usage: cargo xtask corner-store <db>");
        return ExitCode::from(2);
    };
    match build(Path::new(db)) {
        Ok(()) => {
            eprintln!("wrote {db}");
            ExitCode::SUCCESS
        }
        Err(err) => {
            eprintln!("xtask corner-store: {err}");
            ExitCode::FAILURE
        }
    }
}
