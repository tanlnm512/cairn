//! Worker connection pool: per-flavor SQLite connections parked between tool
//! calls and reopened when the store file's identity changes (build swap).

use std::ops::Deref;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use rusqlite::Connection;

/// Idle connections parked per flavor before extras are dropped.
const MAX_IDLE: usize = 8;

/// Store identity: (dev, inode) on Unix; (len, mtime_ns) elsewhere.
#[derive(Clone, Copy, PartialEq, Eq)]
struct StoreId(u64, u64);

fn store_id(path: &Path) -> Option<StoreId> {
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

/// A parked connection and the store identity it was opened against.
struct Parked {
    id: StoreId,
    conn: Connection,
}

/// One connection flavor's idle slot.
#[derive(Default)]
struct Slot {
    idle: Mutex<Vec<Parked>>,
}

impl Slot {
    fn acquire(
        self: &Arc<Self>,
        db: &Path,
        open: impl FnOnce(&Path) -> Result<Connection, rusqlite::Error>,
    ) -> Result<ConnLease, String> {
        let current = store_id(db);
        let mut idle = idle_lock(&self.idle);
        while let Some(parked) = idle.pop() {
            if Some(parked.id) == current {
                return Ok(ConnLease::new(parked.conn, self, current));
            }
        }
        let conn = open(db).map_err(|e| format!("store open failed: {e}"))?;
        Ok(ConnLease::new(conn, self, store_id(db)))
    }
}

fn idle_lock(mutex: &Mutex<Vec<Parked>>) -> std::sync::MutexGuard<'_, Vec<Parked>> {
    mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

/// A checked-out connection; parks back into its slot on drop.
pub struct ConnLease {
    conn: Option<Connection>,
    slot: Option<Arc<Slot>>,
    id: Option<StoreId>,
}

impl ConnLease {
    fn new(conn: Connection, slot: &Arc<Slot>, id: Option<StoreId>) -> Self {
        Self {
            conn: Some(conn),
            slot: Some(Arc::clone(slot)),
            id,
        }
    }
}

impl Deref for ConnLease {
    type Target = Connection;
    fn deref(&self) -> &Connection {
        self.conn
            .as_ref()
            .expect("lease holds its connection until drop")
    }
}

impl Drop for ConnLease {
    fn drop(&mut self) {
        let (Some(conn), Some(slot), Some(id)) = (self.conn.take(), self.slot.take(), self.id)
        else {
            return;
        };
        if !conn.is_autocommit() {
            return;
        }
        let mut idle = idle_lock(&slot.idle);
        if idle.len() < MAX_IDLE {
            idle.push(Parked { id, conn });
        }
    }
}

/// Read + primary connection parking shared by every worker on one store.
pub struct ConnPool {
    db_path: PathBuf,
    read_only: bool,
    read: Arc<Slot>,
    write: Arc<Slot>,
}

impl ConnPool {
    /// Pool over `db_path`; a read-only daemon's primary flavor is its read slot.
    pub fn new(db_path: &Path, read_only: bool) -> Self {
        Self {
            db_path: db_path.to_path_buf(),
            read_only,
            read: Arc::default(),
            write: Arc::default(),
        }
    }

    /// A read-only connection lease.
    pub fn read(&self) -> Result<ConnLease, String> {
        self.read
            .acquire(&self.db_path, cairn_store::conn::open_read_only)
    }

    /// The primary flavor: writable unless the daemon serves read-only.
    pub fn primary(&self) -> Result<ConnLease, String> {
        if self.read_only {
            self.read()
        } else {
            self.write
                .acquire(&self.db_path, cairn_store::conn::open_writable)
        }
    }
}
