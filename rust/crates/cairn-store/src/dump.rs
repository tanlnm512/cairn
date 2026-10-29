//! Canonical per-table dump of a `.kg` store for cross-build content diffing.
//!
//! Byte-compatible with the frozen references under `rust/golden/dump/`: same format line,
//! cell encoding (`\N` NULL, C99-hex floats, `x'<hex>'` blobs, escaped text),
//! shadow/virtual-table skip rules, and sorted row order.

use std::collections::{BTreeSet, HashMap};
use std::fmt::Write as _;
use std::path::Path;

use rusqlite::Connection;
use rusqlite::types::ValueRef;

use crate::conn::open_read_only;

/// Format header line of every canonical dump.
pub const FORMAT_LINE: &str = "# cairn-canonical-dump 1";

/// Which tables a dump exports and which it skips.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TableClass {
    /// Ordinary row-content table (the only exported class).
    Content,
    /// FTS5 virtual table.
    Fts5,
    /// vec0 virtual table.
    Vec0,
    /// Shadow table of a virtual table.
    Shadow,
    /// `sqlite_*` internal table.
    SqliteInternal,
}

impl TableClass {
    /// Classification label as printed by `cairn canonical-dump --list-tables`.
    pub fn as_str(self) -> &'static str {
        match self {
            TableClass::Content => "content",
            TableClass::Fts5 => "fts5",
            TableClass::Vec0 => "vec0",
            TableClass::Shadow => "shadow",
            TableClass::SqliteInternal => "sqlite-internal",
        }
    }
}

/// Dump options behind `cairn canonical-dump --map-path` / `--tables`.
#[derive(Debug, Default)]
pub struct DumpOptions {
    /// (from, to) rewrites applied inside text cells, in the given order.
    pub map_paths: Vec<(String, String)>,
    /// Restrict the export to these content tables; all when `None`.
    pub only: Option<BTreeSet<String>>,
}

/// Dump failure: SQLite error or a non-UTF-8 text cell.
#[derive(Debug)]
pub enum DumpError {
    /// Underlying SQLite failure.
    Sqlite(rusqlite::Error),
    /// A TEXT cell that is not valid UTF-8 (rendering fails the same way).
    InvalidText(String),
}

impl From<rusqlite::Error> for DumpError {
    fn from(e: rusqlite::Error) -> Self {
        DumpError::Sqlite(e)
    }
}

impl std::fmt::Display for DumpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DumpError::Sqlite(e) => write!(f, "sqlite error: {e}"),
            DumpError::InvalidText(table) => write!(f, "non-utf8 text cell in {table}"),
        }
    }
}

impl std::error::Error for DumpError {}

/// Map table name -> class for every table in the store.
pub fn classify_tables(conn: &Connection) -> rusqlite::Result<HashMap<String, TableClass>> {
    let mut stmt = conn.prepare("SELECT name, sql FROM sqlite_master WHERE type = 'table'")?;
    let rows = stmt.query_map([], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?))
    })?;
    let mut virtual_tables: Vec<String> = Vec::new();
    let mut classes: HashMap<String, TableClass> = HashMap::new();
    for row in rows {
        let (name, sql) = row?;
        if name.starts_with("sqlite_") {
            classes.insert(name, TableClass::SqliteInternal);
        } else if sql
            .as_deref()
            .is_some_and(|s| s.to_lowercase().contains("using fts5"))
        {
            classes.insert(name.clone(), TableClass::Fts5);
            virtual_tables.push(name);
        } else if sql
            .as_deref()
            .is_some_and(|s| s.to_lowercase().contains("using vec0"))
        {
            classes.insert(name.clone(), TableClass::Vec0);
            virtual_tables.push(name);
        } else {
            classes.insert(name, TableClass::Content);
        }
    }
    // Shadow tables are named "<virtual_table>_<suffix>" by both fts5 and vec0.
    for (name, class) in classes.iter_mut() {
        if *class != TableClass::Content {
            continue;
        }
        if virtual_tables
            .iter()
            .any(|v| name == v || name.starts_with(format!("{v}_").as_str()))
        {
            *class = TableClass::Shadow;
        }
    }
    Ok(classes)
}

/// Dump the store at `db_path`: returns (classification, dump lines).
pub fn dump_store(
    db_path: &Path,
    opts: &DumpOptions,
) -> Result<(HashMap<String, TableClass>, Vec<String>), DumpError> {
    let conn = open_read_only(db_path)?;
    let result = dump_connection(&conn, opts);
    conn.close().map_err(|(_, e)| DumpError::Sqlite(e))?;
    result
}

/// Dump an already-open connection (same output as [`dump_store`]).
pub fn dump_connection(
    conn: &Connection,
    opts: &DumpOptions,
) -> Result<(HashMap<String, TableClass>, Vec<String>), DumpError> {
    let classes = classify_tables(conn)?;
    let mut lines = vec![FORMAT_LINE.to_string()];
    let mut map_sorted: Vec<&(String, String)> = opts.map_paths.iter().collect();
    map_sorted.sort();
    for (src, dst) in map_sorted {
        lines.push(format!("# map-path: {src} => {dst}"));
    }
    let mut tables: Vec<&String> = classes
        .iter()
        .filter(|(_, class)| **class == TableClass::Content)
        .map(|(name, _)| name)
        .collect();
    tables.sort();
    for table in tables {
        if opts.only.as_ref().is_some_and(|only| !only.contains(table)) {
            continue;
        }
        let cols = table_columns(conn, table)?;
        lines.push(format!("[table {table}]"));
        lines.push(format!(
            "# columns: {}",
            cols.iter()
                .map(|c| escape(c))
                .collect::<Vec<_>>()
                .join("\t")
        ));
        lines.extend(dump_table_rows(conn, table, &opts.map_paths)?);
    }
    Ok((classes, lines))
}

fn quote_ident(name: &str) -> String {
    format!("\"{}\"", name.replace('"', "\"\""))
}

fn escape(text: &str) -> String {
    text.replace('\\', "\\\\")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('\t', "\\t")
        .replace('\0', "\\0")
}

/// Render a double as a `float.hex()`-format string.
fn float_hex(x: f64) -> String {
    let bits = x.to_bits();
    let sign = if bits >> 63 == 1 { "-" } else { "" };
    let exp_bits = (bits >> 52) & 0x7ff;
    let mant = bits & 0x000f_ffff_ffff_ffff;
    match (exp_bits, mant) {
        (0x7ff, 0) => format!("{sign}inf"),
        (0x7ff, _) => "nan".to_string(),
        (0, 0) => format!("{sign}0x0.0p+0"),
        (0, _) => format!("{sign}0x0.{mant:013x}p-1022"),
        _ => format!("{sign}0x1.{mant:013x}p{:+}", exp_bits as i64 - 1023),
    }
}

fn hex_encode(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        let _ = write!(out, "{b:02x}");
    }
    out
}

fn normalize_cell(
    value: ValueRef<'_>,
    map_paths: &[(String, String)],
    table: &str,
) -> Result<String, DumpError> {
    match value {
        ValueRef::Null => Ok("\\N".to_string()),
        ValueRef::Real(x) => Ok(escape(&float_hex(x))),
        ValueRef::Integer(i) => Ok(escape(&i.to_string())),
        ValueRef::Blob(b) => Ok(format!("x'{}'", hex_encode(b))),
        ValueRef::Text(t) => {
            let mut text = std::str::from_utf8(t)
                .map_err(|_| DumpError::InvalidText(table.to_string()))?
                .to_string();
            for (src, dst) in map_paths {
                if !src.is_empty() && text.contains(src.as_str()) {
                    text = text.replace(src.as_str(), dst.as_str());
                }
            }
            Ok(escape(&text))
        }
    }
}

fn table_columns(conn: &Connection, table: &str) -> Result<Vec<String>, DumpError> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({})", quote_ident(table)))?;
    let mut rows = stmt.query([])?;
    let mut cols = Vec::new();
    while let Some(row) = rows.next()? {
        cols.push(row.get(1)?);
    }
    Ok(cols)
}

fn dump_table_rows(
    conn: &Connection,
    table: &str,
    map_paths: &[(String, String)],
) -> Result<Vec<String>, DumpError> {
    let cols = table_columns(conn, table)?;
    let collist = cols
        .iter()
        .map(|c| quote_ident(c))
        .collect::<Vec<_>>()
        .join(", ");
    let mut stmt = conn.prepare(&format!("SELECT {collist} FROM {}", quote_ident(table)))?;
    let ncols = stmt.column_count();
    let mut rows_iter = stmt.query([])?;
    let mut rows: Vec<Vec<String>> = Vec::new();
    while let Some(row) = rows_iter.next()? {
        let mut cells: Vec<String> = Vec::with_capacity(ncols);
        for i in 0..ncols {
            cells.push(normalize_cell(row.get_ref(i)?, map_paths, table)?);
        }
        // A row must never be mistakable for a "[table <name>]" section header.
        let line = cells.join("\t");
        if line.starts_with("[table ") && line.ends_with(']') {
            cells[0] = format!("\\{}", cells[0]);
        }
        rows.push(cells);
    }
    rows.sort();
    Ok(rows.into_iter().map(|cells| cells.join("\t")).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn float_hex_matches_reference_vectors() {
        // Expected values generated with CPython 3.12 `float.hex()`.
        let cases: &[(u64, &str)] = &[
            (0x0000_0000_0000_0000, "0x0.0p+0"),
            (0x8000_0000_0000_0000, "-0x0.0p+0"),
            (0x3ff0_0000_0000_0000, "0x1.0000000000000p+0"),
            (0xbff0_0000_0000_0000, "-0x1.0000000000000p+0"),
            (0x3ff8_0000_0000_0000, "0x1.8000000000000p+0"),
            (0x406f_e000_0000_0000, "0x1.fe00000000000p+7"),
            (0x3fb9_9999_9999_999a, "0x1.999999999999ap-4"),
            (0x4009_21fb_5444_2d18, "0x1.921fb54442d18p+1"),
            (0x7fe1_ccf3_85eb_c8a0, "0x1.1ccf385ebc8a0p+1023"),
            (0x419d_6f34_547e_6b75, "0x1.d6f34547e6b75p+26"),
            (0x0000_0000_0000_0001, "0x0.0000000000001p-1022"),
            (0x0000_0000_0000_0005, "0x0.0000000000005p-1022"),
            (0x0008_0000_0000_0000, "0x0.8000000000000p-1022"),
            (0x0000_1000_0000_0000, "0x0.0100000000000p-1022"),
            (0x000f_ffff_ffff_ffff, "0x0.fffffffffffffp-1022"),
            (0x0010_0000_0000_0000, "0x1.0000000000000p-1022"),
            (0x7fef_ffff_ffff_ffff, "0x1.fffffffffffffp+1023"),
            (0x7ff0_0000_0000_0000, "inf"),
            (0xfff0_0000_0000_0000, "-inf"),
            (0x7ff8_0000_0000_0000, "nan"),
            (0xfff8_0000_0000_0001, "nan"),
            (0xc004_0000_0000_0000, "-0x1.4000000000000p+1"),
        ];
        for &(bits, expected) in cases {
            let actual = float_hex(f64::from_bits(bits));
            assert_eq!(actual, expected, "bits {bits:#018x}");
        }
    }

    #[test]
    fn escape_and_quote_ident_match_reference() {
        assert_eq!(escape("a\\b\nc\rd\te\0f"), "a\\\\b\\nc\\rd\\te\\0f");
        assert_eq!(escape("plain"), "plain");
        assert_eq!(quote_ident("simple"), "\"simple\"");
        assert_eq!(quote_ident("has\"quote"), "\"has\"\"quote\"");
    }

    fn fixture_db() -> Connection {
        crate::conn::register_vec0();
        let conn = Connection::open_in_memory().expect("in-memory db");
        conn.execute_batch(
            "CREATE TABLE vals (a TEXT, b INTEGER, c REAL, d BLOB, e TEXT);
             INSERT INTO vals VALUES (NULL, 42, 1.5, x'deadbeef', 'line1\nline2');
             INSERT INTO vals VALUES ('[table x]', -7, 0.1, NULL, 'plain');
             CREATE TABLE empty_t (x TEXT, y INTEGER);
             CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT, msg TEXT);
             INSERT INTO logs (msg) VALUES ('first');
             CREATE TABLE tricky (a TEXT);
             INSERT INTO tricky VALUES ('[table y]');
             CREATE VIRTUAL TABLE sym_fts USING fts5(name, content='vals');
             CREATE VIRTUAL TABLE vec_m USING vec0(embedding float[2] distance_metric=cosine);",
        )
        .expect("fixture schema");
        conn
    }

    #[test]
    fn classify_tables_matches_reference_classes() {
        let conn = fixture_db();
        let classes = classify_tables(&conn).expect("classify");
        assert_eq!(classes["vals"], TableClass::Content);
        assert_eq!(classes["empty_t"], TableClass::Content);
        assert_eq!(classes["logs"], TableClass::Content);
        assert_eq!(classes["sym_fts"], TableClass::Fts5);
        assert_eq!(classes["sym_fts_data"], TableClass::Shadow);
        assert_eq!(classes["sym_fts_idx"], TableClass::Shadow);
        assert_eq!(classes["vec_m"], TableClass::Vec0);
        assert_eq!(classes["vec_m_chunks"], TableClass::Shadow);
        assert_eq!(classes["sqlite_sequence"], TableClass::SqliteInternal);
    }

    #[test]
    fn dump_lines_match_the_format() {
        let conn = fixture_db();
        let (classes, lines) = dump_connection(&conn, &DumpOptions::default()).expect("dump");
        assert_eq!(classes["vals"], TableClass::Content);
        assert_eq!(lines[0], FORMAT_LINE);
        assert!(lines.contains(&"[table vals]".to_string()));
        assert!(lines.contains(&"[table empty_t]".to_string()));
        // Virtual, shadow, and sqlite-internal tables are skipped.
        assert!(!lines.iter().any(|l| l.starts_with("[table sym_fts]")));
        assert!(!lines.iter().any(|l| l.starts_with("[table vec_m]")));
        assert!(!lines.iter().any(|l| l.starts_with("[table sqlite_")));
        assert!(!lines.iter().any(|l| l.contains("sym_fts_data")));
        let vals_pos = lines
            .iter()
            .position(|l| l == "[table vals]")
            .expect("vals");
        assert_eq!(lines[vals_pos + 1], "# columns: a\tb\tc\td\te");
        // Vals sorts last; its two rows close the dump.
        let rows = &lines[vals_pos + 2..];
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0], "[table x]\t-7\t0x1.999999999999ap-4\t\\N\tplain");
        assert_eq!(
            rows[1],
            "\\N\t42\t0x1.8000000000000p+0\tx'deadbeef'\tline1\\nline2"
        );
        // A row whose full joined line would look like a section header is escaped.
        let tricky_pos = lines
            .iter()
            .position(|l| l == "[table tricky]")
            .expect("tricky");
        assert_eq!(lines[tricky_pos + 2], "\\[table y]");
    }

    #[test]
    fn map_paths_rewrite_text_cells_and_header() {
        let conn = fixture_db();
        let opts = DumpOptions {
            map_paths: vec![("first".to_string(), "FIRST".to_string())],
            only: Some(BTreeSet::from(["logs".to_string()])),
        };
        let (_, lines) = dump_connection(&conn, &opts).expect("dump");
        assert_eq!(lines[0], FORMAT_LINE);
        assert_eq!(lines[1], "# map-path: first => FIRST");
        assert_eq!(lines.len(), 5);
        assert_eq!(lines[2], "[table logs]");
        assert_eq!(lines[3], "# columns: id\tmsg");
        assert_eq!(lines[4], "1\tFIRST");
    }
}
