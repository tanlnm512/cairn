//! `cairn import-scip` reports disagreements and join anomalies as distinct counters.

use std::path::{Path, PathBuf};
use std::process::Command;

use cairn_build::scip::proto;
use prost::Message as _;

fn cairn(home: &Path, workspace: Option<&Path>, args: &[&str]) -> (bool, String) {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_cairn"));
    cmd.args(args).env("HOME", home).env("CAIRN_HOME", home);
    match workspace {
        Some(ws) => {
            cmd.env("CAIRN_WORKSPACE", ws);
        }
        None => {
            cmd.env_remove("CAIRN_WORKSPACE");
        }
    }
    let out = cmd.output().expect("spawn cairn");
    (
        out.status.success(),
        String::from_utf8_lossy(&out.stdout).into_owned(),
    )
}

/// A document whose occurrences all sit past the file's symbols: zero join
/// yield, so the importer keeps the document on its tree-sitter edges.
fn ghost_document(rel: &str) -> proto::Document {
    let mut doc = proto::Document {
        relative_path: rel.to_string(),
        ..proto::Document::default()
    };
    for line in [50, 51] {
        doc.occurrences.push(proto::Occurrence {
            symbol: format!("scip-x ghost a 1.0.0/f{line}( f)."),
            symbol_roles: 0,
            typed_range: Some(proto::occurrence::TypedRange::SingleLineRange(
                proto::SingleLineRange {
                    line,
                    start_character: 0,
                    end_character: 4,
                },
            )),
            ..proto::Occurrence::default()
        });
    }
    doc
}

#[test]
fn import_scip_labels_join_anomalies_separately_from_disagreements() {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let home: PathBuf =
        std::env::temp_dir().join(format!("cairn-scip-labels-{}-{nanos}", std::process::id()));
    let ws = home.join("ws");
    let repo = ws.join("repo");
    std::fs::create_dir_all(repo.join("pkg")).expect("pkg dir");
    std::fs::create_dir_all(repo.join(".git")).expect("repo marker");
    std::fs::write(
        repo.join("pkg").join("util.py"),
        "def add(a, b):\n    return a + b\n",
    )
    .expect("util");
    std::fs::write(
        repo.join("pkg").join("app.py"),
        "def main():\n    return add(1, 2)\n",
    )
    .expect("app");
    let db = ws.join("store.kg");
    let db = db.to_str().expect("utf8 db path").to_string();
    let ws_arg = ws.to_str().expect("utf8 ws").to_string();
    let (built, out) = cairn(&home, None, &["build", "--workspace", &ws_arg, "--db", &db]);
    assert!(built, "build failed: {out}");

    let index_path = ws.join("ghost.scip");
    let bytes = proto::Index {
        documents: vec![ghost_document("pkg/util.py"), ghost_document("pkg/app.py")],
        ..proto::Index::default()
    }
    .encode_to_vec();
    std::fs::write(&index_path, bytes).expect("write index");

    let (ok, out) = cairn(
        &home,
        Some(&ws),
        &[
            "import-scip",
            index_path.to_str().expect("utf8 index"),
            "--workspace",
            &ws_arg,
            "--db",
            &db,
        ],
    );
    assert!(ok, "{out}");
    assert!(out.contains("0 disagreements, 2 join anomalies"), "{out}");
    assert!(!out.contains("(2 disagreements)"), "{out}");

    let _ = std::fs::remove_dir_all(&home);
}
