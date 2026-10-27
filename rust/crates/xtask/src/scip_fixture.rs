//! `scip-fixture`: regenerates the synthetic SCIP index consumed by cairn-build's scip_import tests.

use std::fs;
use std::path::PathBuf;
use std::process::ExitCode;

use cairn_build::scip::proto::{
    Document, Index, Metadata, Occurrence, SingleLineRange, SymbolInformation, ToolInfo,
    occurrence::TypedRange,
};
use prost::Message;
use prost::encoding::{bytes, int32, message, string};

use crate::check_doc_links::repo_root;

const FIXTURE: &str = "rust/crates/cairn-build/tests/fixtures/scip/sample.scip";
// The tests assert this tool name; changing it changes the fixture bytes.
const TOOL_NAME: &str = "make_fixture.py";

const ROLE_DEFINITION: i32 = 1;
const KIND_FUNCTION: i32 = 17;
const KIND_METHOD: i32 = 26;
const KIND_CLASS: i32 = 7;

const ADD: &str = "scip-python pypi util 1.0.0/pkg/util.py/add( f).";
const HELPER: &str = "scip-python pypi util 1.0.0/pkg/util.py/helper( f).";
const MAIN: &str = "scip-python pypi app 1.0.0/pkg/app.py/main( f).";
const KLASS: &str = "scip-python pypi app 1.0.0/pkg/app.py/Klass.";
const METHOD: &str = "scip-python pypi app 1.0.0/pkg/app.py/Klass/method( self).";
const GHOST: &str = "scip-python pypi app 1.0.0/pkg/app.py/ghost( f).";
const LOCAL: &str = "local pkg/app.py:4:9-4:14";
const EXTERNAL: &str = "scip-npm @scope/ext 2.0.0/ext/external( f).";
const EXTRA_ONLY: &str = "scip-python pypi gen 1.0.0/gen/extra.py/only( f).";

fn occurrence(symbol: &str, roles: i32, line: i32, start: i32, end: i32) -> Occurrence {
    Occurrence {
        symbol: symbol.into(),
        symbol_roles: roles,
        typed_range: Some(TypedRange::SingleLineRange(SingleLineRange {
            line,
            start_character: start,
            end_character: end,
        })),
        ..Occurrence::default()
    }
}

fn def_occ(symbol: &str, line: i32, start: i32, end: i32) -> Occurrence {
    occurrence(symbol, ROLE_DEFINITION, line, start, end)
}

fn ref_occ(symbol: &str, line: i32, start: i32, end: i32) -> Occurrence {
    occurrence(symbol, 0, line, start, end)
}

#[allow(deprecated)]
fn legacy_range_occ(symbol: &str, range: Vec<i32>) -> Occurrence {
    Occurrence {
        symbol: symbol.into(),
        range,
        ..Occurrence::default()
    }
}

fn symbol_info(symbol: &str, kind: i32) -> SymbolInformation {
    SymbolInformation {
        symbol: symbol.into(),
        kind,
        ..SymbolInformation::default()
    }
}

fn document(path: &str, symbols: Vec<SymbolInformation>, occurrences: Vec<Occurrence>) -> Document {
    Document {
        language: "python".into(),
        relative_path: path.into(),
        symbols,
        occurrences,
        ..Document::default()
    }
}

/// Spans are 0-based SCIP ranges; the paired test seeds 1-based symbol spans that join onto them.
fn build_index() -> Index {
    let util = document(
        "pkg/util.py",
        vec![
            symbol_info(ADD, KIND_FUNCTION),
            symbol_info(HELPER, KIND_FUNCTION),
        ],
        vec![
            def_occ(ADD, 1, 4, 7),
            def_occ(HELPER, 5, 4, 10),
            def_occ(LOCAL, 1, 0, 4),
        ],
    );
    let app = document(
        "pkg/app.py",
        vec![
            symbol_info(MAIN, KIND_FUNCTION),
            symbol_info(KLASS, KIND_CLASS),
            symbol_info(METHOD, KIND_METHOD),
        ],
        vec![
            def_occ(MAIN, 3, 4, 8),
            def_occ(KLASS, 7, 0, 5),
            def_occ(METHOD, 8, 4, 10),
            def_occ(LOCAL, 4, 24, 29),
            ref_occ(ADD, 4, 11, 14),
            ref_occ(HELPER, 10, 4, 10),
            ref_occ(METHOD, 8, 0, 6),
            ref_occ(GHOST, 4, 17, 22),
            ref_occ(LOCAL, 4, 24, 29),
            legacy_range_occ(ADD, vec![4, 8, 4, 30]),
            Occurrence {
                symbol: GHOST.into(),
                ..Occurrence::default()
            },
            ref_occ(HELPER, 20, 0, 6),
            ref_occ(EXTERNAL, 11, 0, 8),
        ],
    );
    let extra = document("gen/extra.py", vec![], vec![def_occ(EXTRA_ONLY, 0, 0, 4)]);
    Index {
        metadata: Some(Metadata {
            tool_info: Some(ToolInfo {
                name: TOOL_NAME.into(),
                ..ToolInfo::default()
            }),
            ..Metadata::default()
        }),
        documents: vec![util, app, extra],
        external_symbols: vec![symbol_info(EXTERNAL, KIND_FUNCTION)],
    }
}

// prost's generated `range` is unpacked; the canonical proto3 encoding packs it.
#[allow(deprecated)]
fn encode_occurrence(occ: &Occurrence) -> Vec<u8> {
    let mut buf = Vec::new();
    int32::encode_packed(1, &occ.range, &mut buf);
    Occurrence {
        range: Vec::new(),
        ..occ.clone()
    }
    .encode(&mut buf)
    .expect("vec buffer grows");
    buf
}

fn encode_document(doc: &Document) -> Vec<u8> {
    let mut buf = Vec::new();
    if !doc.relative_path.is_empty() {
        string::encode(1, &doc.relative_path, &mut buf);
    }
    for occ in &doc.occurrences {
        bytes::encode(2, &encode_occurrence(occ), &mut buf);
    }
    Document {
        relative_path: String::new(),
        occurrences: Vec::new(),
        ..doc.clone()
    }
    .encode(&mut buf)
    .expect("vec buffer grows");
    buf
}

fn encode_index(index: &Index) -> Vec<u8> {
    let mut buf = Vec::new();
    if let Some(metadata) = &index.metadata {
        message::encode(1, metadata, &mut buf);
    }
    for doc in &index.documents {
        bytes::encode(2, &encode_document(doc), &mut buf);
    }
    for symbol in &index.external_symbols {
        message::encode(3, symbol, &mut buf);
    }
    buf
}

fn execute(args: &[String]) -> Result<(String, bool), String> {
    let mut check = false;
    let mut out = repo_root().join(FIXTURE);
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--check" => check = true,
            "--out" => out = PathBuf::from(iter.next().ok_or("--out needs a path")?),
            other => return Err(format!("unknown argument `{other}`")),
        }
    }
    let bytes = encode_index(&build_index());
    if check {
        let committed = fs::read(&out).map_err(|e| format!("{}: {e}", out.display()))?;
        if committed != bytes {
            return Ok((
                format!(
                    "{} is stale; regenerate with `cargo xtask scip-fixture`",
                    out.display()
                ),
                false,
            ));
        }
        return Ok((format!("scip fixture fresh ({} bytes)", bytes.len()), true));
    }
    fs::write(&out, &bytes).map_err(|e| format!("{}: {e}", out.display()))?;
    Ok((
        format!("wrote {} ({} bytes)", out.display(), bytes.len()),
        true,
    ))
}

pub fn run(args: &[String]) -> ExitCode {
    match execute(args) {
        Ok((message, ok)) => {
            println!("{message}");
            if ok {
                ExitCode::SUCCESS
            } else {
                ExitCode::FAILURE
            }
        }
        Err(e) => {
            eprintln!("xtask scip-fixture: {e}");
            ExitCode::FAILURE
        }
    }
}
