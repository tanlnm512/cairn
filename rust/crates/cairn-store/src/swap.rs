//! Store replacement: advisory build lock, WAL-safe atomic swap, telemetry carry-over.

use std::cell::Cell;
use std::fs::{File, OpenOptions, TryLockError};
use std::path::{Path, PathBuf};
use std::time::Duration;

use rusqlite::Connection;

/// Durable analytics carried across a swap; operational tables are rebuilt.
const TELEMETRY_TABLE_COLUMNS: &[(&str, &str)] = &[
    (
        "build_runs",
        "kind, started_at, duration_s, phase_timings, repos, files, symbols, \
         edges, resolution_exact, resolution_ambiguous, resolution_unresolved, \
         parse_errors, skipped, workers, session_id",
    ),
    ("events", "ts, name, session_id, attrs"),
    (
        "tool_metrics",
        "tool_name, session_id, invoked_at, duration_ms, status, error_message, \
         req_chars, resp_chars, args_summary, source, \
         truncated_from_chars, truncated_to_chars",
    ),
];

/// Held `<db>.build.lock` flock; released on drop.
pub struct BuildLock(File);

impl Drop for BuildLock {
    fn drop(&mut self) {
        let _ = self.0.unlock();
    }
}

fn with_suffix(path: &Path, suffix: &str) -> PathBuf {
    let mut raw = path.as_os_str().to_owned();
    raw.push(suffix);
    PathBuf::from(raw)
}

/// The staging path `<db>.tmp` a full build is written to before the swap.
pub fn staging_path(db: &Path) -> PathBuf {
    with_suffix(db, ".tmp")
}

/// Acquire the non-blocking advisory writer lock for `db`; Err on contention.
pub fn build_lock(db: &Path) -> Result<BuildLock, String> {
    if let Some(parent) = db.parent().filter(|p| !p.as_os_str().is_empty()) {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(with_suffix(db, ".build.lock"))
        .map_err(|e| e.to_string())?;
    match file.try_lock() {
        Ok(()) => Ok(BuildLock(file)),
        Err(TryLockError::WouldBlock) => Err(format!(
            "Cannot acquire build lock: another build or update is already in \
             progress on {}. Wait for it to finish and retry.",
            db.display()
        )),
        Err(TryLockError::Error(e)) => Err(e.to_string()),
    }
}

/// Unlink `<db>-wal` and `<db>-shm` if present.
pub fn remove_sidecars(db: &Path) {
    for suffix in ["-wal", "-shm"] {
        let _ = std::fs::remove_file(with_suffix(db, suffix));
    }
}

/// Store file identity: (dev, inode) on Unix; (len, mtime_ns) elsewhere.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct StoreId(u64, u64);

/// The identity of the store file at `path`; None when it cannot be read.
pub fn store_id(path: &Path) -> Option<StoreId> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let meta = std::fs::metadata(path).ok()?;
        Some(StoreId(meta.dev(), meta.ino()))
    }
    #[cfg(not(unix))]
    {
        use std::time::UNIX_EPOCH;
        let meta = std::fs::metadata(path).ok()?;
        let mtime = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?;
        Some(StoreId(meta.len(), mtime.as_nanos() as u64))
    }
}

/// Acquire the build lock, then open the store: a connection opened before a
/// full build's swap keeps writing to the replaced (unlinked) file.
pub fn open_locked(db: &Path) -> Result<(BuildLock, Connection), String> {
    let lock = build_lock(db)?;
    let conn = crate::schema::open_initialized(db)
        .map_err(|e| format!("open store {}: {e}", db.display()))?;
    Ok((lock, conn))
}

/// Pinned store identity for a write session that re-takes the build lock per
/// write and aborts when a concurrent build replaced the store file.
pub struct StorePin {
    db: PathBuf,
    id: Cell<Option<StoreId>>,
}

/// The build lock held for one write against a pinned store.
pub struct PinnedLock<'a> {
    pin: &'a StorePin,
    _lock: BuildLock,
}

impl Drop for PinnedLock<'_> {
    fn drop(&mut self) {
        // Absorb this session's own writes so only a replacement trips the pin.
        self.pin.id.set(store_id(&self.pin.db));
    }
}

impl StorePin {
    /// Open `db` under the build lock and pin its identity; the lock is released.
    pub fn open(db: &Path) -> Result<(Connection, StorePin), String> {
        let (_lock, conn) = open_locked(db)?;
        Ok((
            conn,
            StorePin {
                db: db.to_path_buf(),
                id: Cell::new(store_id(db)),
            },
        ))
    }

    /// Re-acquire the build lock for one write; Err on contention or when the
    /// pinned store file was replaced by a concurrent build.
    pub fn write_lock(&self) -> Result<PinnedLock<'_>, String> {
        let lock = build_lock(&self.db)?;
        if store_id(&self.db) != self.id.get() {
            return Err(format!(
                "store replaced by a concurrent build on {}: re-run the command",
                self.db.display()
            ));
        }
        Ok(PinnedLock {
            pin: self,
            _lock: lock,
        })
    }
}

/// Remove a store file and its sidecars (missing is fine).
pub fn discard(db: &Path) {
    remove_sidecars(db);
    let _ = std::fs::remove_file(db);
}

/// Atomically replace the WAL-mode store at `db` with `staged`.
pub fn swap_db_file(staged: &Path, db: &Path) -> Result<(), String> {
    if db.exists()
        && let Ok(old) = Connection::open(db)
    {
        // Checkpoint before unlinking so no committed frame is lost.
        let _ = old.busy_timeout(Duration::from_millis(5000));
        let _ = old.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |_| Ok(()));
    }
    remove_sidecars(db);
    replace_db_file(staged, db)?;
    remove_sidecars(staged);
    Ok(())
}

/// Atomic staged-to-live rename; on Windows the live file moves aside first
/// because rename-over-open-file is denied while a parked connection holds it.
fn replace_db_file(staged: &Path, db: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        std::fs::rename(staged, db).map_err(|e| format!("swap {}: {e}", db.display()))
    }
    #[cfg(windows)]
    {
        let aside = db.with_extension("kg.replaced");
        let _ = std::fs::remove_file(&aside);
        if db.exists() && std::fs::rename(db, &aside).is_err() {
            return copy_into_place(staged, db);
        }
        if let Err(e) = std::fs::rename(staged, db) {
            let _ = std::fs::rename(&aside, db);
            return Err(format!("swap {}: {e}", db.display()));
        }
        let _ = std::fs::remove_file(&aside);
        Ok(())
    }
}

/// Copy staged over the live file when renaming it aside is denied (an open
/// handle without share-delete); readers detect the swap by identity change.
#[cfg(windows)]
fn copy_into_place(staged: &Path, db: &Path) -> Result<(), String> {
    std::fs::copy(staged, db).map_err(|e| format!("swap {}: {e}", db.display()))?;
    let _ = std::fs::remove_file(staged);
    Ok(())
}

/// Best-effort append of retained analytics history from `old_db` into `dest`.
pub fn copy_telemetry_tables(dest: &Connection, old_db: &Path) {
    if !old_db.exists() {
        return;
    }
    if dest
        .execute(
            "ATTACH DATABASE ? AS srcdb",
            [old_db.to_string_lossy().as_ref()],
        )
        .is_err()
    {
        return;
    }
    for (table, columns) in TELEMETRY_TABLE_COLUMNS {
        let exists = dest
            .query_row(
                "SELECT 1 FROM srcdb.sqlite_master WHERE type = 'table' AND name = ?",
                [table],
                |_| Ok(()),
            )
            .is_ok();
        if exists {
            let _ = dest.execute(
                &format!(
                    "INSERT INTO {table} ({columns}) SELECT {columns} FROM srcdb.{table} ORDER BY id"
                ),
                [],
            );
        }
    }
    let _ = dest.execute("DETACH DATABASE srcdb", []);
}

/// Carry telemetry from `db` into `staged`, then swap `staged` over `db`; caller holds the lock.
pub fn install_staged(staged: &Path, db: &Path) -> Result<(), String> {
    {
        let conn = Connection::open(staged).map_err(|e| e.to_string())?;
        copy_telemetry_tables(&conn, db);
    }
    swap_db_file(staged, db)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_lock_is_exclusive_until_dropped() {
        let dir = std::env::temp_dir().join(format!("cairn-swap-lock-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dir");
        let db = dir.join("store.kg");
        let held = build_lock(&db).expect("first lock");
        let err = build_lock(&db).err().expect("contended lock must fail");
        assert!(err.contains("Cannot acquire build lock"), "{err}");
        drop(held);
        build_lock(&db).expect("lock after release");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
