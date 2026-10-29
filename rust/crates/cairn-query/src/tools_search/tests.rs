//! Unit tests for the search-family tool port.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::thread;
use std::time::{Duration, Instant};

#[cfg(feature = "local-embed")]
use super::semantic_env;
use super::{
    EffectiveBackend, HASH_MODEL, NATIVE_MODEL, SemanticEnv, SemanticMatch, ann_backend_enabled,
    cosine_scan, embed_query, exact_name_hit, floats_to_blob, fused_margin, hash_vec, install_hint,
    is_fts_prefix_pattern, netloc_of, parse_http_response, pattern_to_fts, render_search_symbols,
    render_semantic_search, resolve_backend, round4, rrf_fuse, vec_table_name,
};

fn env_hash() -> SemanticEnv {
    SemanticEnv {
        fusion_enabled: true,
        backend: EffectiveBackend::Hash,
        hash_fallback: false,
        native_cold: false,
        model: HASH_MODEL.to_string(),
        rerank_on: false,
        rerank_override: None,
        reranker_available: false,
        ann_enabled: true,
        min_margin: 0.45,
        server_base_url: None,
        server_api_key: None,
    }
}

fn f64_close(a: f64, b: f64) -> bool {
    (a - b).abs() < 1e-9
}

fn canned_reply(body: &str) -> String {
    format!(
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
        body.len()
    )
}

// --- FTS pattern conversion --------------------------------------------------

#[test]
fn prefix_patterns_pass_through() {
    assert_eq!(pattern_to_fts("Acti*").as_deref(), Some("Acti*"));
    assert!(is_fts_prefix_pattern("Acti*"));
}

#[test]
fn underscored_names_take_the_phrase_path() {
    assert_eq!(
        pattern_to_fts("_render_active").as_deref(),
        Some("\"render active\"*")
    );
    assert!(!is_fts_prefix_pattern("_render_active"));
}

#[test]
fn non_ascii_and_empty_patterns_select_like() {
    assert_eq!(pattern_to_fts(""), None);
    assert_eq!(pattern_to_fts("  "), None);
    assert!(pattern_to_fts("***").is_none());
    // The FTS tokenizer splits on ASCII-only classes, so é breaks the token.
    assert_eq!(pattern_to_fts("café").as_deref(), Some("\"caf\"*"));
}

// --- hash embedder -----------------------------------------------------------

#[test]
fn hash_vector_matches_the_reference() {
    // Reference values from src/cairn/graph/embeddings.py `_hash_vec`.
    let vec = hash_vec("hello world");
    assert_eq!(vec.len(), 256);
    let nonzero: Vec<(usize, f64)> = vec
        .iter()
        .enumerate()
        .filter(|(_, v)| **v != 0.0)
        .map(|(i, v)| (i, *v))
        .collect();
    assert_eq!(nonzero.len(), 2);
    assert_eq!(nonzero[0].0, 44);
    assert!(
        f64_close(nonzero[0].1, -0.6698596209144062),
        "{:?}",
        nonzero[0]
    );
    assert_eq!(nonzero[1].0, 72);
    assert!(
        f64_close(nonzero[1].1, 0.7424877697769897),
        "{:?}",
        nonzero[1]
    );
}

#[test]
fn single_token_hash_vector_is_unit_norm_at_its_dimension() {
    // sha256("build") derives idx=68, sign=+, magnitude contribution 1.057.
    let vec = hash_vec("build");
    let nonzero: Vec<(usize, f64)> = vec
        .iter()
        .enumerate()
        .filter(|(_, v)| **v != 0.0)
        .map(|(i, v)| (i, *v))
        .collect();
    assert_eq!(nonzero, vec![(68, 1.0)]);
}

#[test]
fn blob_encoding_is_little_endian_f32() {
    let blob = floats_to_blob(&[1.0, -0.5]);
    assert_eq!(blob, vec![0, 0, 0x80, 0x3f, 0, 0, 0, 0xbf]);
}

// --- cosine scan -------------------------------------------------------------

#[test]
fn cosine_scan_skips_malformed_and_zero_rows() {
    let q = floats_to_blob(&[1.0, 0.0]);
    let good = floats_to_blob(&[1.0, 0.0]);
    let wrong_dim = floats_to_blob(&[1.0, 0.0, 0.0]);
    let truncated = vec![0u8; 5];
    let zero = floats_to_blob(&[0.0, 0.0]);
    let rows = vec![(wrong_dim, 3), (truncated, 2), (zero, 2), (good, 2)];
    let scored = cosine_scan(&q, 2, &rows, 0.0);
    assert_eq!(scored.len(), 1);
    assert_eq!(scored[0].1, 3);
    assert!(f64_close(scored[0].0, 1.0));
}

#[test]
fn cosine_scan_threshold_and_stable_ties() {
    let q = floats_to_blob(&[1.0, 0.0]);
    let a = floats_to_blob(&[0.5, 0.5]);
    let b = floats_to_blob(&[0.5, 0.5]);
    let c = floats_to_blob(&[0.1, 0.9]);
    let rows = vec![(a, 2), (b, 2), (c, 2)];
    let scored = cosine_scan(&q, 2, &rows, 0.4);
    assert_eq!(
        scored.iter().map(|(_, i)| *i).collect::<Vec<_>>(),
        vec![0, 1]
    );
    assert!(f64_close(scored[0].0, scored[1].0));
}

// --- RRF fusion --------------------------------------------------------------

#[test]
fn rrf_matches_the_reference_values() {
    let fused = rrf_fuse(
        &[
            vec!["a".into(), "b".into(), "c".into()],
            vec!["b".into(), "d".into()],
        ],
        60,
    );
    let expected = [
        ("b", 0.03252247488101534),
        ("a", 0.01639344262295082),
        ("d", 0.016129032258064516),
        ("c", 0.015873015873015872),
    ];
    assert_eq!(fused.len(), expected.len());
    for ((id, score), (want_id, want_score)) in fused.iter().zip(expected.iter()) {
        assert_eq!(id, want_id);
        assert!(f64_close(*score, *want_score), "{score} != {want_score}");
    }
}

#[test]
fn rrf_tie_breaks_by_doc_id() {
    let fused = rrf_fuse(&[vec!["z".into()], vec!["a".into()]], 60);
    assert_eq!(fused[0].0, "a");
    assert_eq!(fused[1].0, "z");
}

#[test]
fn round4_truncates_like_round_half_even() {
    assert!(f64_close(round4(0.01639344262295082), 0.0164));
    assert!(f64_close(round4(0.03252247488101534), 0.0325));
}

// --- rerank availability gate ------------------------------------------------

#[test]
fn margin_gate_scores_the_decisive_top_to_edge_gap() {
    let candidates = vec![
        match_at("a", "fused(bm25+semantic)", 0.032),
        match_at("b", "semantic", 0.016),
    ];
    assert!(f64_close(fused_margin(&candidates, 10), 0.5));
    let sparse = vec![match_at("a", "semantic", 0.02)];
    assert!(f64_close(fused_margin(&sparse, 10), 1.0));
    assert!(f64_close(fused_margin(&[], 10), 1.0));
}

#[test]
fn exact_name_hit_matches_name_and_qualified_tail() {
    let top = match_at("target_is_last", "semantic", 0.02);
    assert!(exact_name_hit("target_is_last", &top));
    assert!(!exact_name_hit("other", &top));
    let mut qualified = match_at("target_is_last", "semantic", 0.02);
    qualified.qualified_name = Some("Class.target_is_last".into());
    assert!(exact_name_hit("Class.target_is_last", &qualified));
    assert!(exact_name_hit("target_is_last", &qualified));
}

// --- rendering ---------------------------------------------------------------

#[test]
fn search_render_truncates_past_fifty() {
    use super::SymbolRow;
    let rows: Vec<SymbolRow> = (0..60)
        .map(|i| SymbolRow {
            id: format!("s{i}"),
            kind: "function".into(),
            name: format!("sym{i}"),
            qualified_name: None,
            file_path: "src/a.py".into(),
            line_start: Some(i),
            repo: "cairn".into(),
        })
        .collect();
    let text = render_search_symbols("sym*", &rows);
    assert!(text.starts_with("60 symbols matching 'sym*':"));
    assert!(text.contains("  ... and 10 more"));
    assert_eq!(text.matches("\n  function").count(), 50);
}

#[test]
fn search_render_shows_none_for_missing_line() {
    use super::SymbolRow;
    let rows = vec![SymbolRow {
        id: "s".into(),
        kind: "module".into(),
        name: "m".into(),
        qualified_name: None,
        file_path: "a.py".into(),
        line_start: None,
        repo: "r".into(),
    }];
    assert!(render_search_symbols("m", &rows).contains("a.py:None  (r)"));
}

#[test]
fn semantic_render_labels_provenance_and_chunk() {
    let mut row = match_at("x", "fused(bm25+semantic)", 0.03252247488101534);
    row.chunk = "File: a/b.py\nBody: x".into();
    let text = render_semantic_search("q", &[row], true);
    assert!(text.starts_with("=== semantic_search: \"q\" (1 match(es)) ==="));
    assert!(text.contains("  [fused(bm25+semantic) 0.03] function x  (b.py)  [cairn]"));
    assert!(text.contains("\n    File: a/b.py"));
    assert!(text.ends_with(
        "Note: these are similarity-scored fuzzy matches, not structural edges. \
         Use get_callers/impact_analysis to verify precise relationships."
    ));
}

#[test]
fn semantic_render_empty_message_tracks_fusion() {
    let off = render_semantic_search("q", &[], false);
    assert!(off.contains("found anything above the cosine threshold."));
    let on = render_semantic_search("q", &[], true);
    assert!(on.contains("found anything. The corpus"));
    assert!(!on.contains("above the cosine threshold"));
}

fn match_at(id: &str, provenance: &str, score: f64) -> SemanticMatch {
    SemanticMatch {
        id: id.into(),
        kind: "function".into(),
        name: id.into(),
        qualified_name: None,
        file_path: "a/b.py".into(),
        repo: "cairn".into(),
        score,
        provenance: provenance.into(),
        reranked: false,
        chunk: String::new(),
    }
}

// --- knobs and transports ----------------------------------------------------

#[test]
fn vec_table_sanitizes_model_stamps() {
    assert_eq!(vec_table_name("hash-256-v1"), "vec_hash_256_v1");
    assert_eq!(
        vec_table_name("server/127.0.0.1:8000/bge-m3"),
        "vec_server_127_0_0_1_8000_bge_m3"
    );
    assert_eq!(vec_table_name(NATIVE_MODEL), "vec_native_BAAI_bge_m3");
}

#[test]
fn netloc_strips_ipv6_brackets() {
    assert_eq!(netloc_of("http://127.0.0.1:8000/v1"), "127.0.0.1:8000");
    assert_eq!(netloc_of("http://[::1]:8000/v1"), "::1:8000");
}

#[test]
fn unknown_backend_names_resolve_to_local_semantics() {
    let (backend, base) = resolve_backend("hash");
    assert_eq!(backend, EffectiveBackend::Hash);
    assert!(base.is_none());
    let (backend, base) = resolve_backend("not-a-backend");
    assert_eq!(backend, EffectiveBackend::Hash);
    assert!(base.is_none());
    // Feature-off builds reject the name instead of silently hashing.
    #[cfg(not(feature = "local-embed"))]
    {
        let (backend, base) = resolve_backend("native");
        assert_eq!(backend, EffectiveBackend::Unavailable);
        assert!(base.is_none());
    }
}

// --- native backend resolution --------------------------------------------------

/// Env-mutating native tests serialize here; values restore after the body.
#[cfg(feature = "local-embed")]
fn with_embed_env(backend: &str, cache_root: &std::path::Path, body: impl FnOnce()) {
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let keys = ["CAIRN_EMBED_BACKEND", "FASTEMBED_CACHE_DIR", "HF_HOME"];
    let saved: Vec<(&str, Option<String>)> = keys
        .iter()
        .map(|key| (*key, std::env::var(key).ok()))
        .collect();
    unsafe {
        std::env::set_var("CAIRN_EMBED_BACKEND", backend);
        std::env::set_var("FASTEMBED_CACHE_DIR", cache_root);
        std::env::remove_var("HF_HOME");
    }
    body();
    unsafe {
        for (key, value) in saved {
            match value {
                Some(previous) => std::env::set_var(key, previous),
                None => std::env::remove_var(key),
            }
        }
    }
}

#[cfg(feature = "local-embed")]
fn unique_temp_dir(tag: &str) -> std::path::PathBuf {
    std::env::temp_dir().join(format!(
        "cairn-native-{tag}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos()
    ))
}

#[cfg(feature = "local-embed")]
#[test]
fn native_cold_cache_falls_back_to_hash_with_marker() {
    let dir = unique_temp_dir("cold");
    with_embed_env("native", &dir, || {
        let env = semantic_env(None);
        assert_eq!(env.backend, EffectiveBackend::Hash);
        assert!(env.hash_fallback);
        assert!(env.native_cold);
        assert_eq!(env.model, HASH_MODEL);
    });
    let _ = std::fs::remove_dir_all(dir);
}

#[cfg(feature = "local-embed")]
#[test]
fn native_warm_cache_resolves_native_stamp() {
    let dir = unique_temp_dir("warm");
    let commit = "0123456789abcdef";
    let repo = dir.join("models--BAAI--bge-m3");
    std::fs::create_dir_all(repo.join("refs")).expect("refs dir");
    std::fs::write(repo.join("refs").join("main"), commit).expect("ref");
    let onnx = repo.join("snapshots").join(commit).join("onnx");
    std::fs::create_dir_all(&onnx).expect("snapshot dir");
    std::fs::write(onnx.join("model.onnx"), b"x").expect("model");
    std::fs::write(onnx.join("model.onnx_data"), b"x").expect("weights");
    with_embed_env("native", &dir, || {
        let (backend, base) = resolve_backend("native");
        assert_eq!(backend, EffectiveBackend::Native);
        assert!(base.is_none());
        let env = semantic_env(None);
        assert_eq!(env.backend, EffectiveBackend::Native);
        assert!(!env.hash_fallback);
        assert!(!env.native_cold);
        assert_eq!(env.model, NATIVE_MODEL);
    });
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn ann_backend_reads_process_config_without_mutation() {
    // The default resolves sqlite-vec; asserting only that the read never panics.
    let _ = ann_backend_enabled();
    let _ = super::rerank_enabled();
}

#[test]
fn install_hint_names_only_shipped_backends() {
    let hint = install_hint();
    assert!(!hint.contains("[semantic]"), "{hint}");
    assert!(hint.contains("CAIRN_EMBED_BACKEND=omlx"), "{hint}");
    assert!(hint.contains("CAIRN_EMBED_BASE_URL"), "{hint}");
    assert!(hint.contains("CAIRN_EMBED_BACKEND=hash"), "{hint}");
    assert!(hint.contains("local-embed"), "{hint}");
}

#[test]
fn embed_query_hash_is_deterministic() {
    let env = env_hash();
    let (blob_a, dim_a) = embed_query(&env, "hello world").expect("hash embed");
    let (blob_b, dim_b) = embed_query(&env, "hello world").expect("hash embed");
    assert_eq!((dim_a, dim_b), (256, 256));
    assert_eq!(blob_a, blob_b);
    assert_eq!(blob_a.len(), 256 * 4);
}

// --- http response parsing ---------------------------------------------------

#[test]
fn http_response_parses_content_length_and_chunked() {
    let raw =
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 5\r\n\r\nhello";
    let reply = parse_http_response(raw).expect("parse");
    assert_eq!(reply.status, 200);
    assert_eq!(reply.body, b"hello");
    let chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n";
    let reply = parse_http_response(chunked).expect("parse chunked");
    assert_eq!(reply.status, 200);
    assert_eq!(reply.body, b"hello");
}

#[test]
fn http_response_reports_bad_status_lines() {
    assert!(parse_http_response(b"garbage\r\n\r\n").is_err());
}

fn serve_once(response: String) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let addr = listener.local_addr().expect("addr");
    thread::spawn(move || {
        if let Ok((mut stream, _)) = listener.accept() {
            drain_request(&mut stream);
            let _ = stream.write_all(response.as_bytes());
        }
    });
    format!("http://{addr}")
}

/// Read one full request (headers + content-length body) before replying.
fn drain_request(stream: &mut std::net::TcpStream) {
    let mut raw: Vec<u8> = Vec::new();
    let mut buf = [0u8; 4096];
    let header_end = loop {
        if let Some(end) = super::find_subsequence(&raw, b"\r\n\r\n") {
            break end;
        }
        let n = stream.read(&mut buf).unwrap_or(0);
        if n == 0 {
            return;
        }
        raw.extend_from_slice(&buf[..n]);
    };
    let head = String::from_utf8_lossy(&raw[..header_end]).to_string();
    let length = head
        .lines()
        .find_map(|l| {
            let (name, value) = l.split_once(':')?;
            name.eq_ignore_ascii_case("content-length")
                .then(|| value.trim().parse::<usize>().ok())?
        })
        .unwrap_or(0);
    let total = header_end + 4 + length;
    while raw.len() < total {
        let n = stream.read(&mut buf).unwrap_or(0);
        if n == 0 {
            return;
        }
        raw.extend_from_slice(&buf[..n.min(total - raw.len())]);
    }
}

#[test]
fn probe_accepts_matching_model_and_rejects_missing() {
    let base = serve_once(canned_reply("{\"data\":[{\"id\":\"bge-m3\"}]}"));
    assert!(super::probe_server(&base, "bge-m3", None));
    assert!(!super::probe_server(&base, "other-model", None));
    assert!(!super::probe_server("http://127.0.0.1:1", "bge-m3", None));
}

#[test]
fn server_embed_parses_indexed_vectors() {
    let addr = serve_once(canned_reply(
        "{\"data\":[{\"index\":1,\"embedding\":[0.0,1.0]},{\"index\":0,\"embedding\":[1.0,0.0]}]}",
    ));
    let (blobs, dim) = super::server_embed(&["a".into(), "b".into()], &addr, "bge-m3", None)
        .expect("server embed");
    assert_eq!(dim, 2);
    assert_eq!(blobs.len(), 2);
    // Index order restored: input[0] carries embedding index 0.
    assert_eq!(floats_to_blob(&[1.0, 0.0]), blobs[0]);
    assert_eq!(floats_to_blob(&[0.0, 1.0]), blobs[1]);
}

#[test]
fn server_embed_surfaces_rejections_without_retrying_4xx() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let addr = listener.local_addr().expect("addr");
    thread::spawn(move || {
        if let Ok((mut stream, _)) = listener.accept() {
            drain_request(&mut stream);
            let body = "{\"error\":{\"message\":\"model not found\"}}";
            let reply = format!(
                "HTTP/1.1 404 Not Found\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(reply.as_bytes());
        }
    });
    let err = super::server_embed(&["a".into()], &format!("http://{addr}"), "nope", None)
        .expect_err("rejected");
    assert!(err.contains("HTTP 404"), "{err}");
    assert!(err.contains("model not found"), "{err}");
}

#[test]
fn unreachable_server_fails_after_bounded_retries() {
    let started = Instant::now();
    let err = super::server_embed(&["a".into()], "http://127.0.0.1:1", "m", None)
        .expect_err("unreachable");
    assert!(err.contains("unreachable after 3 retries"), "{err}");
    // Backoff schedule 0.5 + 1.0 + 2.0 elapses between the four attempts.
    assert!(started.elapsed() >= Duration::from_secs_f64(3.4));
}
