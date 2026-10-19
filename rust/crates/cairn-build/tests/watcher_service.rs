//! Watcher contract: a live edit under a watched repo reaches the store
//! through the debounced update pass, and the env kill switch holds.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use cairn_build::builder::build_graph;
use cairn_build::watcher::FileWatcherService;
use cairn_store::conn::open_read_only;

fn temp_dir(tag: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir =
        std::env::temp_dir().join(format!("cairn-watch-{tag}-{}-{nanos}", std::process::id()));
    fs::create_dir_all(dir.join(".git")).expect("temp dir");
    dir
}

fn write(dir: &Path, rel: &str, content: &str) {
    fs::write(dir.join(rel), content).expect("write fixture");
}

fn symbol_count(db: &Path, name: &str) -> i64 {
    let conn = open_read_only(db).expect("open store");
    let count: i64 = conn
        .query_row("SELECT COUNT(*) FROM symbols WHERE name = ?", [name], |r| {
            r.get(0)
        })
        .expect("count");
    conn.close().expect("close");
    count
}

#[test]
fn live_edit_lands_in_the_store_through_the_debounce_window() {
    let dir = temp_dir("live");
    let ws = dir.to_str().expect("ws");
    let db = dir.join("w.kg");
    write(&dir, "util.py", "def alpha_fn():\n    return 1\n");

    let (summary, _) =
        build_graph(ws, db.to_str().expect("db"), None, Some(1)).expect("base build");
    assert_eq!(summary.files, 1);
    assert_eq!(symbol_count(&db, "alpha_fn"), 1);

    let mut service = FileWatcherService::new(ws, db.to_str().expect("db"))
        .with_debounce(Duration::from_millis(300));
    assert!(service.start(), "watcher must start on a repo workspace");

    // The edit changes the byte count, so the stat fallback detects it
    // regardless of the 0.5s mtime tolerance.
    write(
        &dir,
        "util.py",
        "def alpha_fn():\n    return 1\n\n\ndef beta_fn():\n    return 2\n",
    );

    let deadline = Instant::now() + Duration::from_secs(20);
    let mut landed = false;
    while Instant::now() < deadline {
        if symbol_count(&db, "beta_fn") >= 1 {
            landed = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    assert!(landed, "edited file was not reindexed by the watcher");
    assert_eq!(symbol_count(&db, "alpha_fn"), 1, "existing symbol lost");

    service.stop();
    // Restart on the same service must re-arm cleanly.
    assert!(service.start(), "watcher restart must succeed");
    service.stop();

    // CAIRN_WATCH=0 disables starting; the check is sequential to this test
    // so the env flip cannot race the live pass above.
    // SAFETY: single-threaded test process at this point; restored below.
    unsafe { std::env::set_var("CAIRN_WATCH", "0") };
    let disabled = FileWatcherService::new(ws, db.to_str().expect("db"));
    assert!(
        !disabled.env_enabled(),
        "CAIRN_WATCH=0 must disable the watcher"
    );
    let mut disabled = disabled;
    assert!(!disabled.start(), "start() must refuse under CAIRN_WATCH=0");
    // SAFETY: restoring the process default.
    unsafe { std::env::remove_var("CAIRN_WATCH") };

    let _ = fs::remove_dir_all(&dir);
}
