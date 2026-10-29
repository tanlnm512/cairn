//! Golden parity: the apple parsers reproduce the committed golden JSON.

use std::fs;
use std::path::PathBuf;

use cairn_parse::model::ParsedFile;
use cairn_parse::parser_apple::{ObjCParser, SwiftParser};
use serde_json::{Value, json};

fn golden_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .join("tests/fixtures/golden")
}

/// Same shape and sort keys as the expected.json schema.
fn normalise(parsed: &ParsedFile) -> Value {
    let mut symbols: Vec<Value> = parsed
        .symbols
        .iter()
        .map(|s| {
            let mut modifiers = s.modifiers.clone();
            modifiers.sort();
            json!({
                "name": s.name,
                "kind": s.kind,
                "qualified_name": s.qualified_name,
                "line_start": s.line_start,
                "line_end": s.line_end,
                "column_start": s.column_start,
                "column_end": s.column_end,
                "docstring": s.docstring,
                "modifiers": modifiers,
                "metadata": s.metadata,
                "arity": s.arity,
            })
        })
        .collect();
    symbols.sort_by_key(|s| {
        (
            s["kind"].as_str().unwrap_or_default().to_string(),
            s["name"].as_str().unwrap_or_default().to_string(),
            s["line_start"].as_i64().unwrap_or_default(),
            s["column_start"].as_i64().unwrap_or_default(),
        )
    });

    let mut edges: Vec<Value> = parsed
        .edges
        .iter()
        .map(|e| {
            json!({
                "source_name": e.source_name,
                "kind": e.kind,
                "target_name": e.target_name,
                "line": e.line,
                "column": e.column,
                "call_arity": e.call_arity,
            })
        })
        .collect();
    edges.sort_by_key(|e| {
        (
            e["source_name"].as_str().unwrap_or_default().to_string(),
            e["kind"].as_str().unwrap_or_default().to_string(),
            e["target_name"].as_str().unwrap_or_default().to_string(),
            e["line"].as_i64().unwrap_or_default(),
            e["column"].as_i64().unwrap_or_default(),
        )
    });

    let mut imports: Vec<Value> = parsed
        .imports
        .iter()
        .map(|i| {
            json!({
                "imported_path": i.imported_path,
                "line": i.line,
                "local_alias": i.local_alias,
            })
        })
        .collect();
    imports.sort_by_key(|i| {
        (
            i["imported_path"].as_str().unwrap_or_default().to_string(),
            i["line"].as_i64().unwrap_or_default(),
        )
    });

    json!({
        "language": parsed.language,
        "symbols": symbols,
        "edges": edges,
        "imports": imports,
    })
}

fn expected(lang: &str) -> Value {
    let text = fs::read_to_string(golden_dir().join(lang).join("expected.json"))
        .unwrap_or_else(|e| panic!("read {lang} golden: {e}"));
    serde_json::from_str(&text).expect("parse golden JSON")
}

#[test]
fn swift_output_matches_the_golden() {
    let parsed = SwiftParser
        .parse_file(&golden_dir().join("swift/sample.swift"))
        .expect("parse swift sample");
    assert_eq!(normalise(&parsed), expected("swift"));
}

#[test]
fn objc_output_matches_the_golden() {
    let parsed = ObjCParser
        .parse_file(&golden_dir().join("objc/sample.m"))
        .expect("parse objc sample");
    assert_eq!(normalise(&parsed), expected("objc"));
}
