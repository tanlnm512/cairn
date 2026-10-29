//! Golden parity for the web parsers against the committed fixtures.

use std::path::{Path, PathBuf};

use cairn_parse::ParsedFile;
use cairn_parse::parser_web::WebParser;
use serde_json::{Value, json};

fn golden_path(lang: &str, file: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(format!("../../../tests/fixtures/golden/{lang}/{file}"))
}

/// Reproduces the expected.json schema exactly.
fn normalized(parsed: &ParsedFile) -> Value {
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
                "metadata": s
                    .metadata
                    .as_ref()
                    .map(|m| Value::Object(m.clone()))
                    .unwrap_or(Value::Null),
                "arity": s.arity,
            })
        })
        .collect();
    symbols.sort_by(|a, b| {
        let key = |v: &Value| {
            (
                v["kind"].as_str().unwrap_or_default().to_string(),
                v["name"].as_str().unwrap_or_default().to_string(),
                v["line_start"].as_i64().unwrap_or_default(),
                v["column_start"].as_i64().unwrap_or_default(),
            )
        };
        key(a).cmp(&key(b))
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
    edges.sort_by(|a, b| {
        let key = |v: &Value| {
            (
                v["source_name"].as_str().unwrap_or_default().to_string(),
                v["kind"].as_str().unwrap_or_default().to_string(),
                v["target_name"].as_str().unwrap_or_default().to_string(),
                v["line"].as_i64().unwrap_or_default(),
                v["column"].as_i64().unwrap_or_default(),
            )
        };
        key(a).cmp(&key(b))
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
    imports.sort_by(|a, b| {
        let key = |v: &Value| {
            (
                v["imported_path"].as_str().unwrap_or_default().to_string(),
                v["line"].as_i64().unwrap_or_default(),
            )
        };
        key(a).cmp(&key(b))
    });

    json!({
        "language": parsed.language,
        "symbols": symbols,
        "edges": edges,
        "imports": imports,
    })
}

fn assert_golden(lang: &str, sample: &str, parser: WebParser) {
    let expected: Value = serde_json::from_str(
        &std::fs::read_to_string(golden_path(lang, "expected.json"))
            .unwrap_or_else(|e| panic!("read {lang} golden: {e}")),
    )
    .expect("valid golden JSON");
    let parsed = parser
        .parse_file(&golden_path(lang, sample))
        .unwrap_or_else(|e| panic!("parse {lang} sample: {e}"));
    assert_eq!(
        normalized(&parsed),
        expected,
        "{lang} parse diverges from the committed golden"
    );
}

#[test]
fn typescript_sample_matches_golden() {
    assert_golden("typescript", "sample.ts", WebParser::typescript());
}

#[test]
fn javascript_sample_matches_golden() {
    assert_golden("javascript", "sample.js", WebParser::javascript());
}
