// F1
//! `cairn update --file` contracts: zero-change warning, build-lock refusal, build_runs rows.

mod common;

use common::Fixture;
use std::path::Path;

#[test]
fn build_and_update_record_build_runs() {
    let fx = Fixture::new("build-runs");
    fx.build();
    assert_eq!(fx.build_runs(), [("build".to_string(), Some(1))]);

    std::fs::write(
        fx.repo.join("mod.py"),
        "def one():\n    return 1\n\n\ndef two():\n    return 2\n",
    )
    .expect("edit source");
    let (ok, out) = fx.update_file(&fx.repo.join("mod.py"));
    assert!(ok, "{out}");
    assert_eq!(
        fx.build_runs(),
        [
            ("incremental".to_string(), Some(1)),
            ("build".to_string(), Some(1)),
        ]
    );
}

#[test]
fn update_file_zero_change_warns_without_a_success_line() {
    let fx = Fixture::new("update-file");
    std::fs::write(fx.repo.join("NOTES.md"), "# notes\n").expect("non-source");
    fx.build();

    // A non-source edit is the hook's zero-change path: warning only.
    let (ok, out) = fx.update_file(&fx.repo.join("NOTES.md"));
    assert!(ok, "{out}");
    assert!(out.contains("no reindexable change for NOTES.md"), "{out}");
    assert!(!out.contains("Reindexed"), "{out}");

    std::fs::write(
        fx.repo.join("mod.py"),
        "def one():\n    return 1\n\n\ndef two():\n    return 2\n",
    )
    .expect("edit source");
    let (ok, out) = fx.update_file(&fx.repo.join("mod.py"));
    assert!(ok, "{out}");
    assert!(out.contains("Reindexed mod.py"), "{out}");
}

#[test]
fn update_file_is_refused_while_the_build_lock_is_held() {
    let fx = Fixture::new("update-file-lock");
    fx.build();
    std::fs::write(
        fx.repo.join("mod.py"),
        "def one():\n    return 1\n\n\ndef two():\n    return 2\n",
    )
    .expect("edit source");

    let lock = cairn_store::swap::build_lock(Path::new(&fx.db)).expect("hold build lock");
    let (ok, out) = fx.update_file(&fx.repo.join("mod.py"));
    assert!(
        !ok,
        "update --file must be refused under the build lock: {out}"
    );
    assert!(out.contains("Cannot acquire build lock"), "{out}");
    assert!(!out.contains("Reindexed"), "{out}");
    drop(lock);

    let (ok, out) = fx.update_file(&fx.repo.join("mod.py"));
    assert!(ok, "{out}");
    assert!(out.contains("Reindexed mod.py"), "{out}");
}
