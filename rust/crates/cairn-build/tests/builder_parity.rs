//! Builder contract: parse-order-independent output and python-shaped counts.

use std::fs;
use std::path::{Path, PathBuf};

use cairn_build::builder::build_graph;
use cairn_store::dump::{DumpOptions, dump_connection};

fn temp_dir(tag: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir =
        std::env::temp_dir().join(format!("cairn-build-{tag}-{}-{nanos}", std::process::id()));
    fs::create_dir_all(dir.join("src").join("deep")).expect("temp dir");
    fs::create_dir_all(dir.join(".git")).expect("git marker");
    dir
}

fn write(dir: &Path, rel: &str, content: &str) {
    fs::write(dir.join(rel), content).expect("write fixture");
}

/// Build a deterministic multi-language corpus fixture; returns its root.
fn corpus(tag: &str) -> PathBuf {
    let dir = temp_dir(tag);
    write(
        &dir,
        "src/util.py",
        "\"\"\"Helpers.\"\"\"\n\ndef add(a, b):\n    return a + b\n\n\ndef spread(a, b, c):\n    return add(add(a, b), c)\n",
    );
    write(
        &dir,
        "src/deep/app.py",
        "from util import add\n\n\ndef main():\n    return add(1, 2)\n",
    );
    write(
        &dir,
        "src/worker.ts",
        "export function run(name: string): number {\n  return name.length;\n}\n",
    );
    write(
        &dir,
        "src/lib.rs",
        "pub fn compute(x: u64) -> u64 {\n    x + 1\n}\n",
    );
    // Skipped by the size cap, never indexed.
    write(
        &dir,
        "src/huge.py",
        &format!("x = 1\n{}", " ".repeat(1_000_001)),
    );
    dir
}

fn dump_tables(db: &Path, only: &[&str]) -> String {
    let conn = cairn_store::conn::open_read_only(db).expect("open store");
    let opts = DumpOptions {
        map_paths: vec![],
        only: Some(only.iter().map(|t| t.to_string()).collect()),
    };
    let (_, lines) = dump_connection(&conn, &opts).expect("dump");
    conn.close().expect("close");
    lines.join("\n")
}

#[test]
fn output_is_identical_across_parse_pool_sizes() {
    let dir = corpus("order");
    let db_a = dir.join("a.kg");
    let db_b = dir.join("b.kg");

    // 1 worker parses serially; 8 scrambles parse completion order on the
    // rayon pool. Insert order is restored by the stable sort, so the
    // structural tables must be byte-equal across the two runs.
    let (summary_a, _) = build_graph(
        dir.to_str().expect("ws"),
        db_a.to_str().expect("db"),
        None,
        Some(1),
    )
    .expect("serial build");
    let (summary_b, _) = build_graph(
        dir.to_str().expect("ws"),
        db_b.to_str().expect("db"),
        None,
        Some(8),
    )
    .expect("parallel build");

    assert_eq!(summary_a.files, 4); // src/huge.py is size-capped, not indexed
    assert_eq!(summary_a.skipped, 1);
    assert_eq!(summary_a.files, summary_b.files);
    assert_eq!(summary_a.symbols, summary_b.symbols);
    assert_eq!(summary_a.edges, summary_b.edges);
    assert_eq!(summary_a.resolution, summary_b.resolution);
    assert_eq!(summary_a.parse_errors, 0);

    let tables = ["symbols", "edges", "imports", "rationale"];
    for table in tables {
        let a = dump_tables(&db_a, &[table]);
        let b = dump_tables(&db_b, &[table]);
        assert_eq!(a, b, "table {table} diverged across parse orders");
    }

    // The imports edge from app.py's `from util import add` resolves to
    // util.py's module symbol.
    let edges = dump_tables(&db_a, &["edges"]);
    assert!(
        edges.contains("\timports\t"),
        "expected materialized imports edges:\n{edges}"
    );
    // add/spread/main resolved; the ts/rust files contribute their own edges.
    assert!(edges.contains("exact"), "expected resolved edges:\n{edges}");

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn all_skipped_scan_refuses_to_replace_a_populated_store() {
    let dir = temp_dir("empty");
    // One source file, filtered out by gitignore: skip_count > 0, files == 0.
    fs::write(dir.join(".gitignore"), "skipme.py\n").expect("gitignore");
    fs::write(dir.join("skipme.py"), "x = 1\n").expect("skipped file");
    let db = dir.join("store.kg");
    // Seed the store so it holds a graph, then scan a workspace with no
    // indexable sources.
    {
        let conn = cairn_store::schema::open_initialized(&db).expect("open");
        conn.execute_batch(
            "INSERT INTO repos (id, name, path) VALUES ('r', 'r', '.');
             INSERT INTO files (id, repo_id, path, language) VALUES ('f', 'r', 'x.py', 'python');
             INSERT INTO symbols (id, file_id, name, kind) VALUES ('s', 'f', 'x', 'function');",
        )
        .expect("seed");
    }
    let err = build_graph(
        dir.to_str().expect("ws"),
        db.to_str().expect("db"),
        None,
        None,
    )
    .expect_err("must refuse");
    assert!(err.contains("refusing to replace"), "{err}");
    let _ = fs::remove_dir_all(&dir);
}
