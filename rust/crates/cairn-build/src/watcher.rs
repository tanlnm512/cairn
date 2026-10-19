//! Live file watching on the `notify` crate: debounced event intake feeding
//! the incremental graft.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use cairn_store::schema::open_initialized;
use notify::{Event, RecursiveMode, Watcher};
use rusqlite::params;

use crate::incremental;
use crate::scanner;
use crate::timefmt::now_iso;

/// Events within one quiet window coalesce into a single update pass; the
/// window opens on the FIRST event and is never extended.
pub const DEBOUNCE: Duration = Duration::from_secs(2);

struct Shared {
    pending: HashSet<PathBuf>,
    deadline: Option<Instant>,
    busy: bool,
    running: bool,
}

/// Monitors repository files and schedules incremental graph reindexing.
pub struct FileWatcherService {
    workspace: String,
    db_path: String,
    debounce: Duration,
    shared: Arc<(Mutex<Shared>, Condvar)>,
    failure_latched: Arc<AtomicBool>,
    watcher: Option<notify::RecommendedWatcher>,
    worker: Option<JoinHandle<()>>,
    started: bool,
}

/// CAIRN_WATCH hard kill switch (default on).
fn watch_env_enabled() -> bool {
    match std::env::var("CAIRN_WATCH") {
        Ok(v) => !matches!(
            v.trim().to_lowercase().as_str(),
            "0" | "false" | "no" | "off"
        ),
        Err(_) => true,
    }
}

/// True under CAIRN_READ_ONLY — the watcher must never write.
fn read_only_env() -> bool {
    match std::env::var("CAIRN_READ_ONLY") {
        Ok(v) => matches!(v.to_lowercase().as_str(), "1" | "true" | "yes"),
        Err(_) => false,
    }
}

impl FileWatcherService {
    pub fn new(workspace: &str, db_path: &str) -> Self {
        Self {
            workspace: workspace.to_string(),
            db_path: db_path.to_string(),
            debounce: DEBOUNCE,
            shared: Arc::new((
                Mutex::new(Shared {
                    pending: HashSet::new(),
                    deadline: None,
                    busy: false,
                    running: false,
                }),
                Condvar::new(),
            )),
            failure_latched: Arc::new(AtomicBool::new(false)),
            watcher: None,
            worker: None,
            started: false,
        }
    }

    /// Override the debounce window (tests use a short one).
    pub fn with_debounce(mut self, debounce: Duration) -> Self {
        self.debounce = debounce;
        self
    }

    /// Whether environment switches leave the watcher startable.
    pub fn env_enabled(&self) -> bool {
        watch_env_enabled() && !read_only_env()
    }

    /// Start watching when enabled and return whether this call did.
    pub fn start(&mut self) -> bool {
        if self.started {
            return true;
        }
        if !self.env_enabled() {
            return false;
        }
        let Ok(repos) = scanner::discover_repo_records(&self.workspace) else {
            return false;
        };
        if repos.is_empty() {
            return false;
        }

        let shared = Arc::clone(&self.shared);
        let debounce = self.debounce;
        let Ok(mut watcher) =
            notify::recommended_watcher(move |res: Result<Event, notify::Error>| {
                if let Ok(event) = res {
                    on_event(&shared, debounce, &event);
                }
            })
        else {
            return false;
        };

        let mut scheduled = 0usize;
        for repo in &repos {
            match watcher.watch(&repo.path, RecursiveMode::Recursive) {
                Ok(()) => scheduled += 1,
                Err(e) => eprintln!(
                    "cairn: watcher cannot observe {} ({e})",
                    repo.path.display()
                ),
            }
        }
        if scheduled == 0 {
            return false;
        }
        self.watcher = Some(watcher);
        self.started = true;
        {
            let (lock, _) = &*self.shared;
            lock.lock().expect("watcher lock").running = true;
        }
        self.worker = Some(self.spawn_worker());
        eprintln!(
            "cairn: watching {scheduled} repo(s) under {} for live edits",
            self.workspace
        );
        true
    }

    /// Stop watching and join the worker. Idempotent, never panics.
    pub fn stop(&mut self) {
        {
            let (lock, cv) = &*self.shared;
            let mut g = lock.lock().expect("watcher lock");
            g.running = false;
            g.pending.clear();
            g.deadline = None;
            cv.notify_all();
        }
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
        self.watcher = None; // dropping the watcher drains platform emitters
        self.started = false;
    }

    fn spawn_worker(&self) -> JoinHandle<()> {
        let shared = Arc::clone(&self.shared);
        let workspace = self.workspace.clone();
        let db_path = self.db_path.clone();
        let debounce = self.debounce;
        let latch = Arc::clone(&self.failure_latched);
        std::thread::Builder::new()
            .name("cairn-watcher-flush".to_string())
            .spawn(move || {
                loop {
                    let (lock, cv) = &*shared;
                    let mut g = lock.lock().expect("watcher lock");
                    loop {
                        if !g.running {
                            return;
                        }
                        let Some(deadline) = g.deadline else {
                            g = cv.wait(g).expect("watcher lock");
                            continue;
                        };
                        let now = Instant::now();
                        if now >= deadline {
                            break;
                        }
                        let (next, _) = cv.wait_timeout(g, deadline - now).expect("watcher lock");
                        g = next;
                    }
                    if !g.running {
                        return;
                    }
                    if g.pending.is_empty() {
                        g.deadline = None;
                        continue;
                    }
                    if g.busy {
                        // Re-arm so the wait stays bounded even if the running
                        // pass's wake-up window is missed.
                        g.deadline = Some(Instant::now() + debounce);
                        continue;
                    }
                    g.busy = true;
                    g.deadline = None;
                    let paths: Vec<PathBuf> = g.pending.drain().collect();
                    drop(g);

                    let ok = update_pass(&workspace, &db_path, &paths);

                    let mut g = lock.lock().expect("watcher lock");
                    g.busy = false;
                    if g.running && !g.pending.is_empty() && g.deadline.is_none() {
                        g.deadline = Some(Instant::now() + debounce);
                    }
                    cv.notify_all();
                    note_failure(&latch, ok);
                }
            })
            .expect("spawn watcher worker")
    }
}

impl Drop for FileWatcherService {
    fn drop(&mut self) {
        self.stop();
    }
}

/// Record one filesystem event without performing IO beyond a stat.
fn on_event(shared: &Arc<(Mutex<Shared>, Condvar)>, debounce: Duration, event: &Event) {
    for path in &event.paths {
        let is_dir = path.symlink_metadata().map(|m| m.is_dir()).unwrap_or(false);
        if is_dir {
            continue;
        }
        offer(shared, path, debounce);
    }
}

/// Add a candidate path and arm the debounce window if idle.
fn offer(shared: &Arc<(Mutex<Shared>, Condvar)>, path: &Path, debounce: Duration) {
    let is_source = path
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| format!(".{e}"))
        .and_then(|s| scanner::extension_language(&s))
        .is_some();
    // .gitignore is not a source extension but must be seen; everything else
    // non-source is dropped here. (The Rust scanner re-reads gitignore files
    // on every walk, so a later .gitignore event needs no cache invalidation.)
    let is_gitignore = path.file_name().and_then(|n| n.to_str()) == Some(".gitignore");
    if !is_source && !is_gitignore {
        return;
    }
    let (lock, cv) = &**shared;
    let mut g = lock.lock().expect("watcher lock");
    if !g.running {
        return;
    }
    g.pending.insert(path.to_path_buf());
    if g.deadline.is_none() {
        g.deadline = Some(Instant::now() + debounce);
        cv.notify_all();
    }
}

/// Normalize an event path to `(repo, repo_relative, abs_form)`.
fn path_forms(workspace: &str, abs_path: &Path) -> Option<(String, String, String)> {
    let resolved = scanner::resolve_lenient(abs_path);
    let repo = scanner::infer_repo_for_path(workspace, &resolved)?;
    let repo_path = scanner::resolve_repo_path(workspace, &repo).ok()?;
    let root = std::fs::canonicalize(&repo_path).unwrap_or_else(|_| repo_path.clone());
    let rel = resolved
        .strip_prefix(&root)
        .ok()?
        .to_string_lossy()
        .replace(std::path::MAIN_SEPARATOR, "/");
    let abs_form = repo_path.join(&rel);
    Some((repo, rel, abs_form.to_string_lossy().into_owned()))
}

/// One debounced batch: mark pending_sync, reindex, clear leftovers.
/// Returns whether the pass completed without absorbing a failure.
fn update_pass(workspace: &str, db_path: &str, raw_paths: &[PathBuf]) -> bool {
    let mut sorted: Vec<&PathBuf> = raw_paths.iter().collect();
    sorted.sort();
    let mut source_paths: Vec<(PathBuf, String, String, String)> = Vec::new();
    for p in sorted {
        if p.file_name().and_then(|n| n.to_str()) == Some(".gitignore") {
            continue;
        }
        let Some((repo, rel, abs_form)) = path_forms(workspace, p) else {
            continue; // outside the workspace / not under a repo
        };
        // Layer A (skip dirs) applies even to deletions: a file under
        // node_modules/ was never indexed, so its deletion is a no-op.
        if scanner::path_under_skip_dir(Path::new(&rel)) {
            continue;
        }
        let repo_root = scanner::resolve_repo_path(workspace, &repo)
            .map(|root| scanner::resolve_lenient(&root))
            .unwrap_or_else(|_| PathBuf::from(workspace));
        let resolved = scanner::resolve_lenient(p);
        if resolved.exists() {
            match scanner::is_skipped(&resolved, &repo_root) {
                Ok(true) => continue,
                Ok(false) => {}
                Err(e) => {
                    eprintln!("cairn: watcher update failed: {e}");
                    return false;
                }
            }
        }
        source_paths.push((p.clone(), repo, rel, abs_form));
    }
    if source_paths.is_empty() {
        return true;
    }

    // Pending rows use exactly the two path forms deletion consumes.
    let Ok(conn) = open_initialized(Path::new(db_path)) else {
        // Banner is best-effort (e.g. unmigrated DB); the reindex below
        // is the load-bearing half.
        eprintln!("cairn: watcher could not mark pending_sync");
        return false;
    };
    {
        let now = now_iso();
        for (_, repo, rel, abs_form) in &source_paths {
            let _ = conn.execute(
                "INSERT OR IGNORE INTO pending_sync (path, repo_id, changed_at) VALUES (?, ?, ?)",
                params![rel, repo, now],
            );
            let _ = conn.execute(
                "INSERT OR IGNORE INTO pending_sync (path, repo_id, changed_at) VALUES (?, ?, ?)",
                params![abs_form, repo, now],
            );
        }
    }

    match incremental::incremental_update(workspace, db_path, None, None) {
        Ok(_) => {}
        Err(e) => {
            eprintln!("cairn: watcher update deferred (writer lock busy): {e}");
            return false;
        }
    }

    // Git redetection never lists a deleted untracked file, so sweep this
    // pass's own deletion events through the path-targeted repair.
    let missing: Vec<String> = source_paths
        .iter()
        .filter(|(p, ..)| !p.exists())
        .map(|(p, ..)| p.to_string_lossy().into_owned())
        .collect();
    if !missing.is_empty() {
        let Ok(mut conn) = open_initialized(Path::new(db_path)) else {
            eprintln!("cairn: watcher update failed: store unreachable");
            return false;
        };
        let result = incremental::reindex_paths(&mut conn, workspace, &missing);
        if !result.errors.is_empty() {
            eprintln!("cairn: watcher update failed: {}", result.errors.join("; "));
            return false;
        }
    }

    // Clear pending rows even when restored content produces no git diff.
    if let Ok(conn) = open_initialized(Path::new(db_path)) {
        for (p, ..) in &source_paths {
            let path_str = p.to_string_lossy().into_owned();
            let _ = conn.execute(
                "DELETE FROM pending_sync WHERE path = ?",
                [path_str.as_str()],
            );
        }
    }
    true
}

/// Warn once per contiguous failure run; reset on the next success.
fn note_failure(latch: &AtomicBool, ok: bool) {
    if ok {
        latch.store(false, Ordering::Relaxed);
    } else if !latch.swap(true, Ordering::Relaxed) {
        eprintln!("cairn: watcher update failed");
    }
}
