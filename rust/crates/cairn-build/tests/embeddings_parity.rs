//! Embed pass parity against the committed golden.

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

use cairn_build::embeddings::{current_model, embed_all, embed_symbols};
use cairn_query::tools_search::{self, floats_to_blob, hash_vec};
use cairn_store::schema::open_initialized;
use cairn_store::vec0::{self, VecSource};
use serde_json::Value;

fn golden_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .join("rust/golden/embed")
}

fn load_golden() -> Value {
    serde_json::from_str(
        &fs::read_to_string(golden_dir().join("embed-golden.json")).expect("read golden"),
    )
    .expect("parse golden")
}

/// Copy the checked-in fixture (pre-embed store + corpus) to a scratch dir.
fn stage(tag: &str) -> (PathBuf, PathBuf, PathBuf) {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!(
        "cairn-embed-parity-{tag}-{}-{nanos}",
        std::process::id()
    ));
    fs::create_dir_all(&dir).expect("temp dir");
    let db = dir.join("rust.kg");
    fs::copy(golden_dir().join("embed-corpus.kg"), &db).expect("copy store");
    let corpus = dir.join("corpus");
    copy_tree(&golden_dir().join("corpus"), &corpus);
    // The workspace marker is not checked in (a nested .git is a repo
    // boundary); discovery only needs it to exist.
    fs::create_dir_all(corpus.join(".git")).expect("git marker");
    (dir, db, corpus)
}

fn copy_tree(src: &PathBuf, dst: &PathBuf) {
    fs::create_dir_all(dst).expect("mkdir");
    for entry in fs::read_dir(src).expect("read_dir") {
        let entry = entry.expect("entry");
        let target = dst.join(entry.file_name());
        if entry.file_type().expect("type").is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).expect("copy file");
        }
    }
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn rows_of(conn: &rusqlite::Connection, sql: &str) -> Vec<Vec<String>> {
    let mut stmt = conn.prepare(sql).expect("prepare");
    let cols = stmt.column_count();
    let mapped = stmt
        .query_map([], |r| {
            let mut row = Vec::with_capacity(cols);
            for i in 0..cols {
                let value: rusqlite::types::Value = r.get(i)?;
                row.push(match value {
                    rusqlite::types::Value::Blob(b) => hex(&b),
                    rusqlite::types::Value::Integer(n) => n.to_string(),
                    rusqlite::types::Value::Text(s) => s,
                    rusqlite::types::Value::Null => String::new(),
                    other => format!("{other:?}"),
                });
            }
            Ok(row)
        })
        .expect("query");
    mapped.map(|r| r.expect("row")).collect()
}

fn assert_rows_match_golden(golden_rows: &[Value], actual: &[Vec<String>], label: &str) {
    assert_eq!(
        actual.len(),
        golden_rows.len(),
        "{label} row count diverged"
    );
    for (expected, got) in golden_rows.iter().zip(actual) {
        let expected: Vec<String> = expected
            .as_array()
            .expect("golden row array")
            .iter()
            .map(|v| {
                if let Some(s) = v.as_str() {
                    s.to_string()
                } else if let Some(n) = v.as_i64() {
                    n.to_string()
                } else {
                    panic!("{label}: unexpected golden cell {v}")
                }
            })
            .collect();
        assert_eq!(&expected, got, "{label} row diverged");
    }
}

#[test]
fn embed_reproduces_the_embed_golden() {
    let golden = load_golden();
    let model = golden["model"].as_str().expect("model").to_string();
    let (dir, db, corpus) = stage("full");

    {
        let conn = open_initialized(&db).expect("open store");
        let summary =
            embed_all(&conn, corpus.to_str().expect("ws"), 64, None, true, true).expect("embed");
        assert_eq!(summary.model, model);
        assert_eq!(
            summary.embedded,
            golden["summary"]["embedded"].as_u64().unwrap() as usize
        );
        assert_eq!(
            summary.attempted,
            golden["summary"]["attempted"].as_u64().unwrap() as usize
        );
        assert_eq!(
            summary.failed_batches,
            golden["summary"]["failed_batches"].as_u64().unwrap() as usize
        );
        assert_eq!(
            summary.skipped,
            golden["summary"]["skipped"].as_u64().unwrap() as usize
        );
        assert_eq!(
            summary.total,
            golden["summary"]["total"].as_u64().unwrap() as usize
        );
        assert_eq!(
            summary.reaped,
            golden["summary"]["reaped"].as_u64().unwrap() as usize
        );
        assert_eq!(
            summary.mv_embedded,
            golden["summary"]["mv_embedded"]
                .as_u64()
                .map(|v| v as usize)
        );

        // vec0 rebuild + per-model row counts.
        let base = vec0::rebuild_index(&conn, &model, VecSource::Embeddings).expect("rebuild");
        let mv = vec0::rebuild_index(&conn, &model, VecSource::EmbeddingsMv).expect("rebuild mv");
        let expected_counts: HashMap<String, i64> = golden["vec_counts"]
            .as_object()
            .expect("vec_counts")
            .iter()
            .map(|(k, v)| (k.clone(), v.as_i64().unwrap()))
            .collect();
        for (table, count) in &expected_counts {
            let outcome = if table.starts_with("vecmv_") {
                &mv
            } else {
                &base
            };
            match outcome {
                vec0::RebuildOutcome::Built { indexed, dim, .. } => {
                    assert_eq!(indexed, count, "{table} row count");
                    assert_eq!(*dim, 256, "{table} dim");
                }
                vec0::RebuildOutcome::Skipped { reason, .. } => {
                    panic!("{table} rebuild skipped: {reason}")
                }
            }
            let live: i64 = conn
                .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| r.get(0))
                .expect("live count");
            assert_eq!(&live, count, "{table} live count");
        }

        // Row content: symbol rows, mv rows, term_df (embedded_at excluded).
        let embeddings = rows_of(
            &conn,
            "SELECT symbol_id, model, dim, vec, chunk, content_hash
             FROM embeddings ORDER BY symbol_id",
        );
        assert_rows_match_golden(
            golden["embeddings"].as_array().expect("golden embeddings"),
            &embeddings,
            "embeddings",
        );
        let mv_rows = rows_of(
            &conn,
            "SELECT symbol_id, model, vector_kind, dim, vec, chunk, content_hash
             FROM embeddings_mv ORDER BY symbol_id, vector_kind",
        );
        assert_rows_match_golden(
            golden["embeddings_mv"].as_array().expect("golden mv"),
            &mv_rows,
            "embeddings_mv",
        );
        let term_df = rows_of(
            &conn,
            "SELECT token, symbol_df, n_symbols FROM term_df ORDER BY token",
        );
        assert_rows_match_golden(
            golden["term_df"].as_array().expect("golden term_df"),
            &term_df,
            "term_df",
        );
    }

    // Sample KNN: the vec0 index answers the probe queries identically.
    {
        let conn = cairn_store::conn::open_read_only(&db).expect("reopen read-only");
        for (query, expected) in golden["knn"].as_object().expect("knn") {
            let vec = hash_vec(query);
            let floats: Vec<f32> = vec.iter().map(|&v| v as f32).collect();
            let hits = vec0::ann_query(
                &conn,
                &model,
                &floats_to_blob(&floats),
                5,
                VecSource::Embeddings,
            )
            .expect("ann query")
            .expect("index present");
            let expected: Vec<(String, f64)> = expected
                .as_array()
                .expect("knn hits")
                .iter()
                .map(|h| {
                    (
                        h[0].as_str().expect("symbol id").to_string(),
                        h[1].as_f64().expect("similarity"),
                    )
                })
                .collect();
            assert_eq!(hits.len(), expected.len(), "knn size for {query:?}");
            for ((got_id, got_sim), (want_id, want_sim)) in hits.iter().zip(&expected) {
                assert_eq!(got_id, want_id, "knn order for {query:?}");
                // The vec0 C source is identical (0.1.9) but compiled per side;
                // f32 distance noise stays within a few float32 ulps.
                assert!(
                    (got_sim - want_sim).abs() < 1e-6,
                    "knn similarity for {query:?}: got {got_sim}, want {want_sim}"
                );
            }
        }
    }

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn embed_limit_bounds_base_and_mv_passes() {
    let golden = load_golden();
    let total = golden["summary"]["total"].as_u64().unwrap() as usize;
    let (dir, db, corpus) = stage("limit");
    let conn = open_initialized(&db).expect("open store");
    let summary = embed_all(
        &conn,
        corpus.to_str().expect("ws"),
        64,
        Some(2),
        false,
        true,
    )
    .expect("embed");
    assert_eq!(summary.embedded, 2);
    assert_eq!(summary.attempted, 2);
    assert_eq!(summary.skipped, total - 2);
    assert_eq!(summary.reaped, 0, "reap skipped with reap_orphans=false");
    assert_eq!(summary.mv_embedded, Some(2));
    drop(conn);
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn embed_symbols_resyncs_the_vec0_row_in_place() {
    let golden = load_golden();
    let model = golden["model"].as_str().expect("model").to_string();
    let target = golden["embeddings"][0][0]
        .as_str()
        .expect("symbol id")
        .to_string();
    let expected_chunk = golden["embeddings"][0][4]
        .as_str()
        .expect("chunk")
        .to_string();
    let expected_hash = golden["embeddings"][0][5]
        .as_str()
        .expect("hash")
        .to_string();
    let (dir, db, corpus) = stage("resync");

    let conn = open_initialized(&db).expect("open store");
    embed_all(&conn, corpus.to_str().expect("ws"), 64, None, true, true).expect("embed");
    vec0::rebuild_index(&conn, &model, VecSource::Embeddings).expect("rebuild");
    let before: i64 = vec0::index_row_count(&conn, &model)
        .expect("count")
        .expect("index");

    // Mark the row stale; the upsert must keep its rowid and re-sync vec0.
    conn.execute(
        "UPDATE embeddings SET content_hash = 'stale' WHERE symbol_id = ?1 AND model = ?2",
        rusqlite::params![target, model],
    )
    .expect("stale mark");
    let summary = embed_symbols(
        &conn,
        corpus.to_str().expect("ws"),
        std::slice::from_ref(&target),
        true,
    )
    .expect("embed symbols");
    assert_eq!(summary.embedded, 1);
    assert_eq!(summary.ann_synced, 1);

    let (chunk, content_hash): (String, String) = conn
        .query_row(
            "SELECT chunk, content_hash FROM embeddings WHERE symbol_id = ?1 AND model = ?2",
            rusqlite::params![target, model],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .expect("row");
    assert_eq!(
        (chunk.as_str(), content_hash.as_str()),
        (expected_chunk.as_str(), expected_hash.as_str())
    );
    let after: i64 = vec0::index_row_count(&conn, &model)
        .expect("count")
        .expect("index");
    assert_eq!(
        before, after,
        "vec0 row count must stay stable across a resync"
    );

    let vec = hash_vec(&expected_chunk);
    let floats: Vec<f32> = vec.iter().map(|&v| v as f32).collect();
    let hits = vec0::ann_query(
        &conn,
        &model,
        &floats_to_blob(&floats),
        1,
        VecSource::Embeddings,
    )
    .expect("ann")
    .expect("index");
    assert_eq!(
        hits[0].0, target,
        "the resynced row is the chunk's own nearest neighbor"
    );
    assert!((hits[0].1 - 1.0).abs() < 1e-6, "self similarity {hits:?}");
    drop(conn);
    let _ = fs::remove_dir_all(&dir);
}

// --- native stamp parity -------------------------------------------------------

/// One configuration yields one stamp on both legs: the native stamp on a
/// warm cache, the hash stamp on a cold one (feature builds); feature-off
/// builds reject the name on both legs.
#[test]
fn native_config_stamps_both_legs_identically() {
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let keys = ["CAIRN_EMBED_BACKEND", "FASTEMBED_CACHE_DIR", "HF_HOME"];
    let saved: Vec<(&str, Option<String>)> = keys
        .iter()
        .map(|key| (*key, std::env::var(key).ok()))
        .collect();
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let root = std::env::temp_dir().join(format!(
        "cairn-native-parity-{}-{nanos}",
        std::process::id()
    ));
    unsafe {
        std::env::set_var("CAIRN_EMBED_BACKEND", "native");
        std::env::set_var("FASTEMBED_CACHE_DIR", &root);
        std::env::remove_var("HF_HOME");
    }

    #[cfg(feature = "local-embed")]
    {
        // Cold cache: both legs resolve the hash fallback under one stamp.
        let query_env = tools_search::semantic_env(None);
        let corpus_model = current_model().expect("corpus env");
        assert_eq!(query_env.model, corpus_model);
        assert_eq!(query_env.model, tools_search::HASH_MODEL);
        assert!(query_env.native_cold);

        // Warm cache (fake snapshot — resolution only, no model construction):
        // both legs carry the native stamp.
        let commit = "0123456789abcdef";
        let repo = root.join("models--BAAI--bge-m3");
        fs::create_dir_all(repo.join("refs")).expect("refs dir");
        fs::write(repo.join("refs").join("main"), commit).expect("ref");
        let onnx = repo.join("snapshots").join(commit).join("onnx");
        fs::create_dir_all(&onnx).expect("snapshot dir");
        fs::write(onnx.join("model.onnx"), b"x").expect("model");
        fs::write(onnx.join("model.onnx_data"), b"x").expect("weights");
        let query_env = tools_search::semantic_env(None);
        let corpus_model = current_model().expect("corpus env");
        assert_eq!(query_env.model, corpus_model);
        assert_eq!(query_env.model, tools_search::NATIVE_MODEL);
        assert!(!query_env.native_cold);
    }

    #[cfg(not(feature = "local-embed"))]
    {
        // Both legs reject the name, naming the cargo feature.
        let query_env = tools_search::semantic_env(None);
        assert_eq!(
            query_env.backend,
            tools_search::EffectiveBackend::Unavailable
        );
        let err = current_model().expect_err("feature-off native must not embed");
        assert!(err.contains("local-embed"), "{err}");
    }

    unsafe {
        for (key, value) in saved {
            match value {
                Some(previous) => std::env::set_var(key, previous),
                None => std::env::remove_var(key),
            }
        }
    }
    let _ = fs::remove_dir_all(root);
}
