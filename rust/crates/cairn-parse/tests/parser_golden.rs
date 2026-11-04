//! Golden parity: per-language parsers reproduce the committed expected.json.

use std::path::{Path, PathBuf};

use cairn_parse::ParsedFile;
use cairn_parse::parser_apple::{ObjCParser, SwiftParser};
use cairn_parse::parser_jvm::{JavaParser, KotlinParser};
use cairn_parse::parser_script::{DartParser, PhpParser, PythonParser, RubyParser};
use cairn_parse::parser_web::WebParser;
use serde_json::{Value, json};

fn golden_dir(lang: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!("../../../tests/fixtures/golden/{lang}"))
}

fn expected(lang: &str) -> Value {
    let path = golden_dir(lang).join("expected.json");
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {lang} golden: {e}"));
    serde_json::from_str(&text).expect("parse expected.json")
}

/// Normalizes to the expected.json schema (sorted symbols, edges, imports).
fn normalize(parsed: &ParsedFile) -> Value {
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
    symbols.sort_by_key(|v| {
        (
            v["kind"].as_str().unwrap_or_default().to_string(),
            v["name"].as_str().unwrap_or_default().to_string(),
            v["line_start"].as_i64().unwrap_or_default(),
            v["column_start"].as_i64().unwrap_or_default(),
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
            v["source_name"].as_str().unwrap_or_default().to_string(),
            v["kind"].as_str().unwrap_or_default().to_string(),
            v["target_name"].as_str().unwrap_or_default().to_string(),
            v["line"].as_i64().unwrap_or_default(),
            v["column"].as_i64().unwrap_or_default(),
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
            v["imported_path"].as_str().unwrap_or_default().to_string(),
            v["line"].as_i64().unwrap_or_default(),
        )
    });

    json!({
        "language": parsed.language,
        "symbols": symbols,
        "edges": edges,
        "imports": imports,
    })
}

type SampleParser = fn(&Path) -> ParsedFile;

const CASES: &[(&str, &str, SampleParser)] = &[
    ("python", "sample.py", |p| {
        PythonParser::new().parse(p).expect("parse python")
    }),
    ("php", "sample.php", |p| {
        PhpParser::new().parse(p).expect("parse php")
    }),
    ("ruby", "sample.rb", |p| {
        RubyParser::new().parse(p).expect("parse ruby")
    }),
    ("dart", "sample.dart", |p| {
        DartParser::new().parse(p).expect("parse dart")
    }),
    ("java", "sample.java", |p| {
        JavaParser::new().parse(p).expect("parse java")
    }),
    ("kotlin", "sample.kt", |p| {
        KotlinParser::new().parse(p).expect("parse kotlin")
    }),
    ("swift", "sample.swift", |p| {
        SwiftParser.parse_file(p).expect("parse swift")
    }),
    ("objc", "sample.m", |p| {
        ObjCParser.parse_file(p).expect("parse objc")
    }),
    ("typescript", "sample.ts", |p| {
        WebParser::typescript()
            .parse_file(p)
            .expect("parse typescript")
    }),
    ("javascript", "sample.js", |p| {
        WebParser::javascript()
            .parse_file(p)
            .expect("parse javascript")
    }),
];

#[test]
fn samples_match_the_committed_golden() {
    for (lang, sample, parse) in CASES {
        let parsed = parse(&golden_dir(lang).join(sample));
        assert_eq!(
            normalize(&parsed),
            expected(lang),
            "{lang} diverges from the committed golden"
        );
    }
}
