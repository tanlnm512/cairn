//! Connection factory: read-only `.kg` opens with the Python `get_db` pragma parity.

use std::path::Path;
use std::sync::Once;
use std::time::Duration;

use rusqlite::{Connection, OpenFlags};

/// Default `busy_timeout` matching `get_db(busy_timeout_ms=5000)`.
pub const DEFAULT_BUSY_TIMEOUT_MS: u64 = 5000;

/// Register sqlite-vec once per process so stores carrying `vec0` tables open.
pub(crate) fn register_vec0() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        // Safety: `sqlite3_vec_init` is statically compiled with SQLITE_CORE and
        // has the `sqlite3_auto_extension` entry-point signature.
        type VecInit = unsafe extern "C" fn(
            *mut rusqlite::ffi::sqlite3,
            *mut *mut std::os::raw::c_char,
            *const rusqlite::ffi::sqlite3_api_routines,
        ) -> std::os::raw::c_int;
        unsafe {
            rusqlite::ffi::sqlite3_auto_extension(Some(std::mem::transmute::<
                unsafe extern "C" fn(),
                VecInit,
            >(sqlite_vec::sqlite3_vec_init)));
        }
    });
}

/// Open a `.kg` store read-only with pragma parity (`foreign_keys`, `mmap_size`,
/// `busy_timeout`); `journal_mode = WAL` is writable-only in the Python source
/// and is inherited from the file header here.
pub fn open_read_only(path: &Path) -> Result<Connection, rusqlite::Error> {
    register_vec0();
    let conn = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_NO_MUTEX
            | OpenFlags::SQLITE_OPEN_URI,
    )?;
    conn.execute_batch("PRAGMA foreign_keys = ON; PRAGMA mmap_size = 268435456;")?;
    conn.busy_timeout(Duration::from_millis(DEFAULT_BUSY_TIMEOUT_MS))?;
    Ok(conn)
}

/// Open a `.kg` store read-write with the writable `get_db` pragma parity
/// (`foreign_keys`, `journal_mode = WAL`, `mmap_size`, `busy_timeout`).
pub fn open_writable(path: &Path) -> Result<Connection, rusqlite::Error> {
    register_vec0();
    let conn = Connection::open(path)?;
    conn.execute_batch(
        "PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL; PRAGMA mmap_size = 268435456;",
    )?;
    conn.busy_timeout(Duration::from_millis(DEFAULT_BUSY_TIMEOUT_MS))?;
    Ok(conn)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temp_db(tag: &str) -> std::path::PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "cairn-store-conn-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).expect("temp dir");
        dir.join("t.kg")
    }

    #[test]
    fn read_only_open_carries_python_pragma_parity() {
        let path = temp_db("pragmas");
        {
            let writer = Connection::open(&path).expect("writable create");
            writer
                .execute_batch("CREATE TABLE t (x TEXT); INSERT INTO t VALUES ('v');")
                .expect("schema");
        }
        let conn = open_read_only(&path).expect("read-only open");
        let fk: i64 = conn
            .query_row("PRAGMA foreign_keys", [], |r| r.get(0))
            .expect("foreign_keys pragma");
        let mmap: i64 = conn
            .query_row("PRAGMA mmap_size", [], |r| r.get(0))
            .expect("mmap_size pragma");
        let busy: i64 = conn
            .query_row("PRAGMA busy_timeout", [], |r| r.get(0))
            .expect("busy_timeout pragma");
        assert_eq!((fk, mmap, busy), (1, 268435456, 5000));
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM t", [], |r| r.get(0))
            .expect("count");
        assert_eq!(count, 1);
        let _ = fs::remove_file(&path);
        let _ = fs::remove_dir(path.parent().expect("parent"));
    }

    #[test]
    fn opens_stores_carrying_vec0_tables() {
        let path = temp_db("vec0");
        {
            register_vec0();
            let writer = Connection::open(&path).expect("writable create");
            writer
                .execute_batch(
                    "CREATE VIRTUAL TABLE vec_demo USING vec0(embedding float[4] \
                     distance_metric=cosine); INSERT INTO vec_demo(rowid, embedding) \
                     VALUES (1, x'0000803f0000803f0000803f0000803f');",
                )
                .expect("vec0 table");
        }
        let conn = open_read_only(&path).expect("read-only open");
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM vec_demo", [], |r| r.get(0))
            .expect("vec0 read");
        assert_eq!(count, 1);
        let _ = fs::remove_file(&path);
        let _ = fs::remove_dir(path.parent().expect("parent"));
    }
}
