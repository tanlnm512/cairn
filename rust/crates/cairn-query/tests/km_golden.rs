//! km-tool parity: golden-equal fixtures for the deterministic km tools, plus
//! contract tests for `recall_memory`/`ask_compass` semantics whose capture
//! corpus (the 490-file live memory bundle + full graph) is not checked in.
//!
//! Golden inputs are reconstructed exactly as the golden recorder
//! (`cargo xtask golden-record`) builds them: a fresh store + the recorder's own
//! `seed_concepts` bytes (checked in under `rust/golden/km/bundle-seed/`).

use std::fs;
use std::path::{Path, PathBuf};

use regex::Regex;
use rusqlite::Connection;
use serde_json::{Value, json};

use cairn_query::tools_km as km;

fn repo_root() -> PathBuf {
    // CARGO_MANIFEST_DIR is rust/crates/cairn-query in the workspace; the
    // scratch-check harness overrides it via CAIRN_KM_REPO_ROOT.
    let root = std::env::var("CAIRN_KM_REPO_ROOT")
        .unwrap_or_else(|_| env!("CARGO_MANIFEST_DIR").to_string());
    PathBuf::from(root)
        .join("../../..")
        .canonicalize()
        .expect("repo root")
}

// --- fixture harness ---------------------------------------------------------

struct Fixture {
    dir: PathBuf,
    store_home: PathBuf,
    conn: Connection,
    bundle: km::Bundle,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}

const DDL: &str = r#"
CREATE TABLE repos (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL, language TEXT, git_remote TEXT, indexed_at TIMESTAMP);
CREATE TABLE files (id TEXT PRIMARY KEY, repo_id TEXT NOT NULL REFERENCES repos(id), path TEXT NOT NULL, language TEXT NOT NULL, hash TEXT, line_count INTEGER, indexed_at TIMESTAMP, UNIQUE(repo_id, path));
CREATE TABLE symbols (id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES files(id), name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL, line_start INTEGER, line_end INTEGER, column_start INTEGER, column_end INTEGER, docstring TEXT, modifiers TEXT);
CREATE VIRTUAL TABLE symbols_fts USING fts5(name, qualified_name, docstring, content='symbols', content_rowid='rowid', tokenize='unicode61');
CREATE TRIGGER symbols_ai AFTER INSERT ON symbols BEGIN
    INSERT INTO symbols_fts(rowid, name, qualified_name, docstring)
    VALUES (new.rowid, new.name, new.qualified_name, new.docstring);
END;
CREATE TRIGGER symbols_ad AFTER DELETE ON symbols BEGIN
    INSERT INTO symbols_fts(symbols_fts, rowid, name, qualified_name, docstring)
    VALUES ('delete', old.rowid, old.name, old.qualified_name, old.docstring);
END;
CREATE TABLE edges (id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES symbols(id), target_id TEXT REFERENCES symbols(id), target_name TEXT, kind TEXT NOT NULL, line INTEGER, "column" INTEGER, resolution TEXT);
CREATE TABLE imports (id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES files(id), imported_path TEXT NOT NULL, kind TEXT);
CREATE TABLE memory_refs (id TEXT PRIMARY KEY, memory_path TEXT NOT NULL, session_id TEXT NOT NULL, referenced_at TIMESTAMP NOT NULL, context TEXT);
CREATE TABLE memory_validity (concept_id TEXT PRIMARY KEY, valid_from TEXT NOT NULL, valid_until TEXT, successor_symbol TEXT);
CREATE TABLE memory_embeddings (doc_id TEXT NOT NULL, chunk_index INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL, chunk TEXT NOT NULL, embedded_at TIMESTAMP, PRIMARY KEY (doc_id, chunk_index, model));
CREATE TABLE knowledge_embeddings (doc_id TEXT NOT NULL, chunk_index INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL, chunk TEXT NOT NULL, embedded_at TIMESTAMP, PRIMARY KEY (doc_id, chunk_index, model));
CREATE TABLE knowledge_edges (doc_id TEXT NOT NULL, related_id TEXT NOT NULL, relation TEXT NOT NULL, kind TEXT NOT NULL, provenance TEXT, created_at REAL, PRIMARY KEY (doc_id, related_id, relation, kind));
CREATE TABLE agent_symbols (agent_id TEXT NOT NULL, symbol TEXT NOT NULL, memory_id TEXT, kind TEXT NOT NULL, ts TEXT NOT NULL);
"#;

fn copy_tree(src: &Path, dst: &Path) {
    fs::create_dir_all(dst).expect("dst dir");
    for entry in fs::read_dir(src).expect("src dir") {
        let entry = entry.expect("entry");
        let target = dst.join(entry.file_name());
        if entry.path().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            if entry.file_name() == ".okf.lock" {
                continue;
            }
            fs::copy(entry.path(), &target).expect("copy file");
        }
    }
}

fn fixture(tag: &str) -> Fixture {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!(
        "cairn-km-golden-{}-{nanos}-{tag}",
        std::process::id()
    ));
    let store_home = dir.join("store");
    let knowledge = store_home.join(".knowledge");
    copy_tree(&repo_root().join("rust/golden/km/bundle-seed"), &knowledge);
    let db_path = store_home.join("store.kg");
    let conn = Connection::open(&db_path).expect("open store");
    conn.execute_batch(DDL).expect("km ddl");
    let bundle = km::Bundle::new(knowledge);
    Fixture {
        dir,
        store_home,
        conn,
        bundle,
    }
}

impl Fixture {
    fn seed_graph(&self) {
        self.conn
            .execute_batch(
                "INSERT INTO repos VALUES ('r1', 'demo', '/tmp/demo', 'py', NULL, NULL);
                 INSERT INTO files VALUES ('f1', 'r1', 'src/main.py', 'py', NULL, 1, NULL);
                 INSERT INTO files VALUES ('f2', 'r1', 'scripts/rust_migration/golden_record.py', 'py', NULL, 1, NULL);
                 INSERT INTO symbols VALUES ('s1', 'f1', 'main', NULL, 'function', 1, 2, NULL, NULL, NULL, NULL);
                 INSERT INTO symbols VALUES ('s2', 'f1', 'edge_builder', NULL, 'function', 4, 9, NULL, NULL, NULL, NULL);
                 INSERT INTO symbols VALUES ('s3', 'f1', 'caller_fn', NULL, 'function', 12, 15, NULL, NULL, NULL, NULL);
                 INSERT INTO edges VALUES ('e1', 's3', 's2', NULL, 'calls', 13, NULL, 'exact');",
            )
            .expect("seed graph");
    }

    fn seed_memory(&self, rel_id: &str, frontmatter: &str, body: &str) {
        let path = self.bundle.root.join(format!("{rel_id}.md"));
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).expect("tier dir");
        }
        fs::write(path, format!("---\n{frontmatter}---\n\n{body}")).expect("seed memory");
    }
}

// --- normalization (port of the recorder's scrub rules) -----------------------

fn re_replace(pattern: &str, replacement: &str, text: &str) -> String {
    Regex::new(pattern)
        .expect("rule regex")
        .replace_all(text, replacement)
        .to_string()
}

fn normalize_text(text: &str, store_home: &Path) -> String {
    // Slash-normalize first so one set of path spellings covers every platform;
    // scrub the canonical (longer, resolved) form before the given store home.
    let norm = if cfg!(windows) {
        text.replace('\\', "/")
    } else {
        text.to_string()
    };
    let canonical = store_home
        .canonicalize()
        .unwrap_or_else(|_| store_home.to_path_buf());
    // canonicalize on Windows yields a `\\?\` verbatim prefix the tool output
    // (already slash-normalized above) never carries; strip it both ways.
    let canon = canonical
        .to_string_lossy()
        .replace('\\', "/")
        .trim_start_matches("//?/")
        .trim_start_matches("UNC/")
        .to_string();
    let home = store_home.to_string_lossy().replace('\\', "/");
    let mut out = norm.replace(&canon, "<STORE_HOME>");
    out = out.replace(&home, "<STORE_HOME>");
    out = re_replace(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
        "<ISO>",
        &out,
    );
    out = re_replace(r"\b1[0-9]{9}\.\d+\b", "<EPOCH>", &out);
    out = re_replace(r"\d{4}-\d{2}-\d{2}", "<DATE>", &out);
    out = re_replace(r"\b\d+\.\d+ ?m?s\b|\b\d+ ?ms\b", "<DUR>", &out);
    out = re_replace(r"\bjust now\b|\b\d+[smhd] old\b", "<AGE>", &out);
    out = re_replace(r"\b[0-9a-f]{12}\b", "<ID>", &out);
    out = re_replace(r"([-_.])[0-9a-f]{6}\b", "${1}<ID6>", &out);
    Regex::new(r"\b\d+\.\d{5,}\b")
        .expect("float regex")
        .replace_all(&out, |caps: &regex::Captures| {
            format!("{:.4}", caps[0].parse::<f64>().unwrap_or(0.0))
        })
        .to_string()
}

fn normalize_value(value: &Value, store_home: &Path) -> Value {
    match value {
        Value::String(text) => Value::String(normalize_text(text, store_home)),
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|v| normalize_value(v, store_home))
                .collect(),
        ),
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(k, v)| (k.clone(), normalize_value(v, store_home)))
                .collect(),
        ),
        other => other.clone(),
    }
}

fn assert_golden(tool: &str, actual_text: &str, store_home: &Path) {
    let fixture_path = repo_root().join(format!("rust/golden/mcp/{tool}.json"));
    let fixture: Value = serde_json::from_str(
        &fs::read_to_string(&fixture_path)
            .unwrap_or_else(|e| panic!("read fixture {}: {e}", fixture_path.display())),
    )
    .expect("fixture json");
    let expected = normalize_value(&fixture["response"], store_home);
    let actual = json!({
        "isError": false,
        "content": [{"type": "text", "text": actual_text}],
        "structuredContent": {"result": actual_text},
    });
    let actual = normalize_value(&actual, store_home);
    assert_eq!(
        actual,
        expected,
        "{tool} response differs from {}",
        fixture_path.display()
    );
}

fn add_probe(fx: &Fixture, kind: &str) -> String {
    let msg = km::knowledge_add(
        &fx.bundle,
        &format!("golden mcp capture {kind} probe"),
        "golden capture probe document",
        "decision",
        "",
        "",
        "",
        "",
        "",
        false,
    )
    .expect("setup knowledge_add");
    Regex::new(r"Stored knowledge document: (\S+)")
        .expect("id regex")
        .captures(&msg)
        .expect("concept id in message")
        .get(1)
        .expect("group")
        .as_str()
        .to_string()
}

// --- golden tests (fixture-equal on recorder-reconstructed inputs) -------------

#[test]
fn knowledge_add_golden() {
    let fx = fixture("add");
    let out = km::knowledge_add(
        &fx.bundle,
        "golden mcp capture add probe",
        "golden capture probe document",
        "decision",
        "",
        "",
        "",
        "",
        "",
        false,
    )
    .expect("knowledge_add");
    assert_golden("knowledge_add", &out, &fx.store_home);
}

#[test]
fn knowledge_delete_golden() {
    let fx = fixture("delete");
    let doc_id = add_probe(&fx, "delete");
    let out = km::knowledge_delete(&fx.conn, &fx.bundle, &doc_id, false).expect("knowledge_delete");
    assert_eq!(doc_id, "knowledge/decision/golden-mcp-capture-delete-probe");
    assert_golden("knowledge_delete", &out, &fx.store_home);
}

#[test]
fn knowledge_status_golden() {
    let fx = fixture("status");
    let doc_id = add_probe(&fx, "status");
    let out =
        km::knowledge_status(&fx.bundle, &doc_id, "superseded", false).expect("knowledge_status");
    assert_golden("knowledge_status", &out, &fx.store_home);
}

#[test]
fn trace_workflow_golden() {
    let fx = fixture("workflow");
    let out = km::trace_workflow_tool(&fx.bundle, "knowledge/workflow/golden-capture-workflow")
        .expect("trace_workflow");
    assert_golden("trace_workflow", &out, &fx.store_home);
}

#[test]
fn get_compass_golden() {
    let fx = fixture("compass");
    let out = km::get_compass(&fx.bundle, "golden-capture").expect("get_compass");
    assert_golden("get_compass", &out, &fx.store_home);
}

#[test]
fn search_knowledge_golden() {
    let fx = fixture("search-knowledge");
    let out = km::search_knowledge(&fx.bundle, "golden capture rollback", "", 10, false)
        .expect("search_knowledge");
    assert_golden("search_knowledge", &out, &fx.store_home);
}

#[test]
fn knowledge_search_golden() {
    let fx = fixture("knowledge-search");
    let out = km::knowledge_search(&fx.conn, &fx.bundle, "golden capture rollback", 20)
        .expect("knowledge_search");
    assert_golden("knowledge_search", &out, &fx.store_home);
}

#[test]
fn record_memory_golden() {
    let fx = fixture("record");
    let out = km::record_memory(
        &fx.conn,
        &fx.bundle,
        "pattern",
        "golden mcp capture parity probe",
        "Why: exercise the record path during golden capture.\nHow to apply: regenerated by the golden recorder.",
        "",
        0.7,
        None,
    )
    .expect("record_memory");
    assert_golden("record_memory", &out, &fx.store_home);
}

// --- recall_memory contract tests (refs-verified fraction semantics) ------------

const MEMORY_FRONTMATTER: &str = "\
type: Tribal-{mtype}
title: '{title}'
description: '{title}'
tags:
- {mtype}
generated:
  by: cairn/0.22.0
  at: '2026-10-01T00:00:00Z'
okf_version: '0.2'
memory_status: {tier}
memory_score: {score}
memory_tier: {tier}
tier: asserted
memory_type: {mtype}
memory_is_latest: {latest}
memory_supersedes: []
memory_superseded_by: {superseded_by}
{extra}
";

fn recall_fixture() -> Fixture {
    let fx = fixture("recall");
    fx.seed_graph();
    fx.seed_memory(
        "memory/tribal/alpha-probe",
        &MEMORY_FRONTMATTER
            .replace("{mtype}", "pattern")
            .replace("{title}", "probe alpha memory")
            .replace("{tier}", "tribal")
            .replace("{score}", "0.632")
            .replace("{latest}", "true")
            .replace("{superseded_by}", "null")
            .replace("{extra}", ""),
        "probe alpha body",
    );
    fx.seed_memory(
        "memory/tribal/beta-probe",
        &MEMORY_FRONTMATTER
            .replace("{mtype}", "mistake")
            .replace("{title}", "probe beta memory")
            .replace("{tier}", "tribal")
            .replace("{score}", "0.550")
            .replace("{latest}", "true")
            .replace("{superseded_by}", "null")
            .replace("{extra}", ""),
        "cites `src/main.py` and `ghost_fn`",
    );
    fx.seed_memory(
        "memory/tribal/gamma-probe",
        &MEMORY_FRONTMATTER
            .replace("{mtype}", "decision")
            .replace("{title}", "probe gamma memory")
            .replace("{tier}", "tribal")
            .replace("{score}", "0.600")
            .replace("{latest}", "false")
            .replace("{superseded_by}", "memory/tribal/alpha-probe")
            .replace("{extra}", ""),
        "superseded body",
    );
    fx.seed_memory(
        "memory/tribal/delta-probe",
        &MEMORY_FRONTMATTER
            .replace("{mtype}", "decision")
            .replace("{title}", "probe delta memory")
            .replace("{tier}", "tribal")
            .replace("{score}", "0.590")
            .replace("{latest}", "true")
            .replace("{superseded_by}", "null")
            .replace(
                "{extra}",
                "valid_from: '2025-01-01T00:00:00Z'\nvalid_until: '2026-01-01T00:00:00Z'\n",
            ),
        "expired window body",
    );
    fx
}

#[test]
fn recall_memory_refs_verified_semantics() {
    let fx = recall_fixture();
    let out = km::recall_memory(&fx.conn, &fx.bundle, "probe", "", false, None, None, None)
        .expect("recall");
    // gamma is superseded and delta's validity window closed: 2 visible hits.
    assert!(out.starts_with("2 memories matching 'probe':"), "{out}");
    assert!(
        out.contains("[tribal 0.632, refs-verified=n/a (0 refs)] probe alpha memory"),
        "{out}"
    );
    assert!(
        out.contains("[tribal 0.550, refs-verified=0.5] probe beta memory [STALE]"),
        "{out}"
    );
    assert!(
        out.contains(
            "    ^ a cited file/symbol no longer exists in the graph -- verify before relying on this memory"
        ),
        "{out}"
    );
    assert!(!out.contains("probe gamma"), "{out}");
    assert!(!out.contains("probe delta"), "{out}");
    assert!(
        out.ends_with(
            "(4 of 4 memories not yet embedded -- run `cairn memory embed` for semantic recall)"
        ),
        "{out}"
    );
    // Description line: memories carry description == title.
    assert!(out.contains("    probe alpha memory"), "{out}");
}

#[test]
fn recall_memory_superseded_and_as_of_filters() {
    let fx = recall_fixture();
    let out = km::recall_memory(&fx.conn, &fx.bundle, "probe", "", true, None, None, None)
        .expect("recall include_superseded");
    assert!(
        out.contains("[tribal 0.600, refs-verified=n/a (0 refs)] probe gamma memory [SUPERSEDED]"),
        "{out}"
    );
    assert!(out.starts_with("3 memories matching 'probe':"), "{out}");

    let past = km::recall_memory(
        &fx.conn,
        &fx.bundle,
        "probe",
        "",
        false,
        Some("2025-06-01T00:00:00Z"),
        None,
        None,
    )
    .expect("recall as_of");
    assert!(past.contains("probe delta memory"), "{past}");
    assert!(!past.contains("probe gamma"), "{past}");

    let tiered = km::recall_memory(
        &fx.conn, &fx.bundle, "probe", "drafts", false, None, None, None,
    )
    .expect("recall tier filter");
    assert!(
        tiered.starts_with("No memories matching 'probe'."),
        "{tiered}"
    );
}

#[test]
fn recall_memory_empty_message() {
    let fx = recall_fixture();
    let out = km::recall_memory(
        &fx.conn,
        &fx.bundle,
        "zzzunfindable",
        "",
        false,
        None,
        None,
        None,
    )
    .expect("recall empty");
    assert_eq!(
        out,
        "No memories matching 'zzzunfindable'. Nothing was recorded under those \
         tokens. Try broader/fewer keywords, a symbol name instead of prose, \
         or `cairn memory digest` (via the CLI, no query) to see top tribal \
         memories for orientation. If you expected a memory here, it may not \
         have been captured -- see the Memory Capture Workflow in the skill."
    );
}

// --- record + lifecycle contract tests ------------------------------------------

#[test]
fn record_recall_roundtrip_and_supersession() {
    let fx = fixture("roundtrip");
    fx.seed_graph();
    let first = km::record_memory(
        &fx.conn,
        &fx.bundle,
        "decision",
        "probe epsilon capture",
        "body citing `src/main.py`",
        "",
        0.7,
        None,
    )
    .expect("record 1");
    assert!(
        first.starts_with("Recorded decision 'probe epsilon capture' -> memory/tribal/"),
        "{first}"
    );
    assert!(first.contains("(score=0.578, tier=tribal)"), "{first}");

    let second = km::record_memory(
        &fx.conn,
        &fx.bundle,
        "decision",
        "probe epsilon capture",
        "revised body",
        "",
        0.7,
        None,
    )
    .expect("record 2");
    let old_path = first
        .split("-> ")
        .nth(1)
        .and_then(|rest| rest.split(" (score").next())
        .expect("path in message")
        .to_string();
    let old_base = old_path.rsplit('/').next().expect("basename");
    // Python echoes the candidate's read-back concept_id, which from_file makes
    // absolute; the memory identity is the basename either way.
    assert!(
        second.contains(&format!("[superseded {old_path}]")) || second.contains(old_base),
        "{second}"
    );
    let old_file = fx.bundle.root.join(format!("{old_path}.md"));
    assert!(old_file.exists(), "superseded memory stays in place");
    let superseded = km::Bundle::new(&fx.bundle.root)
        .read_concept(&old_path)
        .expect("old concept readable");
    assert_eq!(superseded.ext_bool("memory_is_latest"), Some(false));

    let recall = km::recall_memory(&fx.conn, &fx.bundle, "epsilon", "", false, None, None, None)
        .expect("recall after supersession");
    // Only the latest revision surfaces; the superseded one is hidden.
    assert!(
        recall.contains("1 memories matching 'epsilon':"),
        "{recall}"
    );
}

#[test]
fn memory_demote_delete_decay_promote_lifecycle() {
    let fx = fixture("lifecycle");
    fx.seed_graph();
    let msg = km::record_memory(
        &fx.conn,
        &fx.bundle,
        "pattern",
        "lifecycle probe memory",
        "body",
        "",
        0.7,
        None,
    )
    .expect("record");
    let path = msg
        .split("-> ")
        .nth(1)
        .and_then(|rest| rest.split(" (score").next())
        .expect("path")
        .to_string();

    // Demotion strictly downward: tribal -> raw moves the file; tribal -> tribal rejected.
    assert_eq!(
        km::demote_memory(&fx.bundle, &path, "tribal", None).expect("demote same tier"),
        None
    );
    assert_eq!(
        km::demote_memory(&fx.bundle, &path, "nonsense", None).expect("demote bad tier"),
        None
    );
    let new_path = km::demote_memory(&fx.bundle, &path, "raw", None)
        .expect("demote")
        .expect("demoted path");
    assert!(new_path.starts_with("memory/raw/"), "{new_path}");
    assert!(
        !fx.bundle.root.join(format!("{path}.md")).exists(),
        "old tier file removed"
    );
    assert!(
        fx.bundle.root.join(format!("{new_path}.md")).exists(),
        "new tier file written"
    );

    // Decay archives expired raw memories by timestamp age.
    let (expired, archived) = km::decay(&fx.bundle, 7, 90, None).expect("decay");
    assert_eq!((expired, archived), (0, 0), "fresh memory stays");
    let old_id = "memory/raw/2025-old-entry";
    fx.seed_memory(
        old_id,
        &MEMORY_FRONTMATTER
            .replace("{mtype}", "pattern")
            .replace("{title}", "old raw entry")
            .replace("{tier}", "raw")
            .replace("{score}", "0.100")
            .replace("{latest}", "true")
            .replace("{superseded_by}", "null")
            .replace("{extra}", ""),
        "stale raw body",
    );
    // The seeded timestamp is 2026-10-01; rewrite it old to trip the decay window.
    let old_file = fx.bundle.root.join("memory/raw/2025-old-entry.md");
    let text = fs::read_to_string(&old_file).expect("read old entry");
    fs::write(
        &old_file,
        text.replace("2026-10-01T00:00:00Z", "2025-01-01T00:00:00Z"),
    )
    .expect("backdate entry");
    let (expired, _archived) = km::decay(&fx.bundle, 7, 90, None).expect("decay expired");
    assert_eq!(expired, 1, "old raw entry expired to archived");

    // Promote moves the memory into the compass namespace as canonical.
    let promoted = km::promote_memory(&fx.bundle, &new_path, None)
        .expect("promote")
        .expect("promoted id");
    assert!(
        promoted.starts_with("compass/promoted-lifecycle-probe-memory-"),
        "{promoted}"
    );
    assert!(
        fx.bundle.root.join(format!("{promoted}.md")).exists(),
        "promoted file written"
    );
    assert!(
        !fx.bundle.root.join(format!("{new_path}.md")).exists(),
        "source file removed"
    );
    let canonical = fx.bundle.read_concept(&promoted).expect("promoted concept");
    assert_eq!(canonical.type_, "Compass");
    assert_eq!(canonical.ext_str("memory_status"), Some("canonical"));

    // delete_memory only manages memory/-namespace concepts: a promoted memory
    // now lives under compass/ and is refused (Python parity).
    assert!(
        !fx.bundle
            .read_concept(&promoted)
            .expect("promoted survives")
            .body
            .is_empty()
            && fx.bundle.root.join(format!("{promoted}.md")).exists(),
        "promoted doc untouched by delete_memory"
    );
    let msg2 = km::record_memory(
        &fx.conn,
        &fx.bundle,
        "mistake",
        "deletable probe memory",
        "body",
        "",
        0.7,
        None,
    )
    .expect("record deletable");
    let deletable = msg2
        .split("-> ")
        .nth(1)
        .and_then(|rest| rest.split(" (score").next())
        .expect("path")
        .to_string();
    assert!(km::delete_memory(&fx.bundle, &deletable, None).expect("delete"));
    assert!(
        !fx.bundle.root.join(format!("{deletable}.md")).exists(),
        "deleted"
    );
}

// --- ask_compass contract tests (cross-layer routing) ----------------------------

#[test]
fn classify_intent_matches_python_table() {
    let cases: &[(&str, &str, &str)] = &[
        ("what is the refund policy", "knowledge_lookup", "L5"),
        ("epic requirement scope", "knowledge_lookup", "L5"),
        ("where is ApiFactory defined", "definition_lookup", "L1"),
        ("who calls submit_order", "call_graph", "L1"),
        ("what breaks if i change pricing", "impact_analysis", "L1"),
        (
            "how does the build resolve call edges",
            "feature_understanding",
            "L2",
        ),
        ("what design pattern is used here", "architecture", "L2"),
        ("guide for onboarding", "module_navigation", "L3"),
        ("why did we chose sqlite", "past_decisions", "L4"),
        ("mistake we keep making", "common_mistakes", "L4"),
        ("xyzzy gibberish", "complex", "ALL"),
    ];
    for (query, intent, layer) in cases {
        assert_eq!(
            km::classify_intent(query),
            (*intent, *layer),
            "intent mismatch for {query:?}"
        );
    }
}

#[test]
fn ask_compass_routes_to_nonempty_layer() {
    let fx = fixture("ask-routed");
    fx.seed_graph();
    km::knowledge_add(
        &fx.bundle,
        "refund policy",
        "customers may request a refund within 30 days",
        "business-rule",
        "",
        "",
        "",
        "",
        "",
        false,
    )
    .expect("seed policy doc");
    let out = km::ask_compass(&fx.conn, &fx.bundle, "refund policy", "").expect("ask_compass");
    assert!(
        out.starts_with("Intent: knowledge_lookup (routed to L5; queried: knowledge)"),
        "{out}"
    );
    assert!(out.contains("[knowledge]"), "{out}");
    assert!(out.contains("refund policy"), "{out}");
    assert!(!out.contains("(Showing fallback results"), "{out}");
}

#[test]
fn ask_compass_degrades_when_targeted_layer_is_thin() {
    let fx = fixture("ask-degraded");
    fx.seed_graph();
    let out = km::ask_compass(
        &fx.conn,
        &fx.bundle,
        "how does the build resolve call edges",
        "",
    )
    .expect("ask_compass degraded");
    assert!(
        out.contains("Intent: feature_understanding (routed to L2; queried: graph)"),
        "{out}"
    );
    assert!(
        out.contains("(Showing fallback results — targeted layer had no coverage.)"),
        "{out}"
    );
    // The graph fallback is non-empty on the seeded store, so the
    // all-layers-empty note must not fire (same shape as the captured fixture).
    assert!(!out.contains("(No results from any layer"), "{out}");
}

#[test]
fn ask_compass_graph_layer_lists_neighbors() {
    let fx = fixture("ask-graph");
    fx.seed_graph();
    let out =
        km::ask_compass(&fx.conn, &fx.bundle, "who calls edge_builder", "").expect("graph route");
    assert!(
        out.starts_with("Intent: call_graph (routed to L1; queried: graph)"),
        "{out}"
    );
    assert!(out.contains("[graph]"), "{out}");
    assert!(out.contains("edge_builder"), "{out}");
    assert!(out.contains("caller_fn (src/main.py:13)"), "{out}");
}

#[test]
fn ask_compass_file_context_loads_compass_and_critic() {
    let fx = fixture("ask-file");
    fx.seed_graph();
    let out = km::ask_compass(
        &fx.conn,
        &fx.bundle,
        "",
        "scripts/rust_migration/golden_record.py",
    )
    .expect("file context");
    assert!(
        out.contains("Context for scripts/rust_migration/golden_record.py:"),
        "{out}"
    );
    assert!(
        out.contains("  inferred module: scripts/rust_migration"),
        "{out}"
    );
    assert!(out.contains("# Compass: golden capture compass"), "{out}");
    assert!(out.contains("```cairn-critic"), "{out}");
    assert!(out.contains("\"passed\": false"), "{out}");
}

// --- knowledge_search semantics (expansion + graph bridge degradation) -----------

#[test]
fn knowledge_search_bridge_without_imports_is_silent() {
    let fx = fixture("bridge");
    km::knowledge_add(
        &fx.bundle,
        "deploy rollback rule",
        "always `scripts/deploy.sh` after rollback",
        "business-rule",
        "",
        "",
        "cairn",
        "",
        "",
        false,
    )
    .expect("seed doc");
    let out = km::knowledge_search(&fx.conn, &fx.bundle, "deploy rollback", 20).expect("search");
    assert!(out.contains("affects_repos: cairn"), "{out}");
    // No imports rows in the fixture store: the bridge degrades silently.
    assert!(!out.contains("depends on:"), "{out}");
}

#[test]
fn knowledge_search_rejects_out_of_namespace_delete() {
    let fx = fixture("namespace");
    let out = km::knowledge_delete(&fx.conn, &fx.bundle, "compass/golden-capture", false)
        .expect("refused delete");
    assert!(
        out.starts_with(
            "Refused: 'compass/golden-capture' resolves to 'compass/golden-capture', outside"
        ),
        "{out}"
    );
    assert!(
        fx.bundle.root.join("compass/golden-capture.md").exists(),
        "compass doc untouched"
    );
}
