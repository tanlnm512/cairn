//! Golden parity for the script-family parsers against the Python fixtures
//! under tests/fixtures/golden: normalized output must equal expected.json.

use std::path::PathBuf;

use cairn_parse::model::ParsedFile;
use cairn_parse::parser_script::{DartParser, PhpParser, PythonParser, RubyParser};
use serde_json::{Value, json};

fn golden_dir(lang: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/fixtures/golden")
        .join(lang)
}

fn expected(lang: &str) -> Value {
    let path = golden_dir(lang).join("expected.json");
    serde_json::from_str(&std::fs::read_to_string(&path).expect("read expected.json"))
        .expect("parse expected.json")
}

/// Mirrors tests/fixtures/golden/regenerate.py::normalise.
fn normalise(parsed: &ParsedFile) -> Value {
    let mut symbols: Vec<Value> = parsed
        .symbols
        .iter()
        .map(|s| {
            let mut modifiers: Vec<String> = s.modifiers.clone();
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
    symbols.sort_by_key(|v| {
        (
            v["kind"].as_str().expect("kind").to_string(),
            v["name"].as_str().expect("name").to_string(),
            v["line_start"].as_u64().expect("line_start"),
            v["column_start"].as_u64().expect("column_start"),
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
    edges.sort_by_key(|v| {
        (
            v["source_name"].as_str().expect("source_name").to_string(),
            v["kind"].as_str().expect("kind").to_string(),
            v["target_name"].as_str().expect("target_name").to_string(),
            v["line"].as_u64().expect("line"),
            v["column"].as_u64().expect("column"),
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
    imports.sort_by_key(|v| {
        (
            v["imported_path"]
                .as_str()
                .expect("imported_path")
                .to_string(),
            v["line"].as_u64().expect("line"),
        )
    });

    json!({
        "language": parsed.language,
        "symbols": symbols,
        "edges": edges,
        "imports": imports,
    })
}

fn sample_path(lang: &str, filename: &str) -> PathBuf {
    let full = golden_dir(lang).join(filename);
    assert!(full.exists(), "sample fixture missing for {lang}");
    full
}

#[test]
fn python_matches_golden() {
    let parsed = PythonParser::new()
        .parse(&sample_path("python", "sample.py"))
        .expect("parse python");
    assert_eq!(
        normalise(&parsed),
        expected("python"),
        "python diverged from golden"
    );
}

#[test]
fn php_matches_golden() {
    let parsed = PhpParser::new()
        .parse(&sample_path("php", "sample.php"))
        .expect("parse php");
    assert_eq!(
        normalise(&parsed),
        expected("php"),
        "php diverged from golden"
    );
}

#[test]
fn ruby_matches_golden() {
    let parsed = RubyParser::new()
        .parse(&sample_path("ruby", "sample.rb"))
        .expect("parse ruby");
    assert_eq!(
        normalise(&parsed),
        expected("ruby"),
        "ruby diverged from golden"
    );
}

#[test]
fn dart_matches_golden() {
    let parsed = DartParser::new()
        .parse(&sample_path("dart", "sample.dart"))
        .expect("parse dart");
    assert_eq!(
        normalise(&parsed),
        expected("dart"),
        "dart diverged from golden"
    );
}
