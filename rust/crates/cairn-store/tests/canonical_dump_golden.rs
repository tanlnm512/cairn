//! Golden parity: the Rust canonical dump reproduces the Python `kg_dump.py` bytes.

use std::collections::BTreeSet;
use std::fs;
use std::path::PathBuf;

use cairn_store::dump::{DumpOptions, TableClass, dump_store};

fn golden_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .join("rust/golden/dump")
}

fn dumped_text(opts: &DumpOptions) -> String {
    let (_, lines) =
        dump_store(&golden_dir().join("corner-store.kg"), opts).expect("dump corner-store");
    let mut text = lines.join("\n");
    text.push('\n');
    text
}

#[test]
fn rust_dump_is_byte_equal_to_the_python_golden() {
    let expected = fs::read_to_string(golden_dir().join("corner-store.canonical.txt"))
        .expect("read canonical golden");
    assert_eq!(dumped_text(&DumpOptions::default()), expected);
}

#[test]
fn rust_dump_with_map_path_is_byte_equal_to_the_python_golden() {
    let expected = fs::read_to_string(golden_dir().join("corner-store.mapped.canonical.txt"))
        .expect("read mapped golden");
    let opts = DumpOptions {
        map_paths: vec![("/Users/x".to_string(), "<HOME>".to_string())],
        only: None,
    };
    assert_eq!(dumped_text(&opts), expected);
}

#[test]
fn table_classification_matches_the_store_contents() {
    let (classes, _) = dump_store(
        &golden_dir().join("corner-store.kg"),
        &DumpOptions::default(),
    )
    .expect("dump corner-store");
    assert_eq!(classes["cells"], TableClass::Content);
    assert_eq!(classes["tricky"], TableClass::Content);
    assert_eq!(classes["weird \"name\""], TableClass::Content);
    assert_eq!(classes["symbols_fts"], TableClass::Fts5);
    assert_eq!(classes["symbols_fts_data"], TableClass::Shadow);
    assert_eq!(classes["vec_corner"], TableClass::Vec0);
    assert_eq!(classes["vec_corner_chunks"], TableClass::Shadow);
    assert_eq!(classes["sqlite_sequence"], TableClass::SqliteInternal);
}

#[test]
fn table_filter_restricts_the_export() {
    let opts = DumpOptions {
        map_paths: vec![],
        only: Some(BTreeSet::from(["tricky".to_string()])),
    };
    let (_, lines) =
        dump_store(&golden_dir().join("corner-store.kg"), &opts).expect("dump corner-store");
    assert_eq!(
        lines,
        vec![
            "# cairn-canonical-dump 1".to_string(),
            "[table tricky]".to_string(),
            "# columns: a".to_string(),
            "\\[table z]".to_string(),
            "plain".to_string(),
        ]
    );
}
