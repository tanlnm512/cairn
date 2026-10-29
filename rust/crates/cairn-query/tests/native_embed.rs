//! Env-gated native-inference tests: set CAIRN_EMBED_TEST_NATIVE=1 with a
//! pre-warmed model cache to run; otherwise they skip.
#![cfg(feature = "local-embed")]

use cairn_query::tools_search::{self, EffectiveBackend, NATIVE_MODEL, embed_query, semantic_env};
use cairn_store::vec0::{self, VecSource};

fn enabled() -> bool {
    std::env::var("CAIRN_EMBED_TEST_NATIVE").ok().as_deref() == Some("1")
}

#[test]
fn native_embed_produces_1024_dim_dense_vectors_under_one_stamp() {
    if !enabled() {
        return;
    }
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let saved = std::env::var("CAIRN_EMBED_BACKEND").ok();
    unsafe { std::env::set_var("CAIRN_EMBED_BACKEND", "native") };
    let env = semantic_env(None);
    unsafe {
        match saved {
            Some(previous) => std::env::set_var("CAIRN_EMBED_BACKEND", previous),
            None => std::env::remove_var("CAIRN_EMBED_BACKEND"),
        }
    }
    assert_eq!(
        env.backend,
        EffectiveBackend::Native,
        "warm the cache first"
    );
    assert_eq!(env.model, NATIVE_MODEL);

    // Query leg.
    let (blob, dim) = embed_query(&env, "rust trait object dispatch").expect("query embed");
    assert_eq!(dim, 1024, "bge-m3 dense dimension drift");
    assert_eq!(blob.len(), 1024 * 4);

    // Corpus leg: same stamp, same dimension.
    let (blobs, dim) = tools_search::native_embed_texts(
        &[
            "function that sorts edges".to_string(),
            "struct definition".to_string(),
        ],
        2,
    )
    .expect("corpus embed");
    assert_eq!(dim, 1024);
    assert_eq!(blobs.len(), 2);
    assert!(blobs.iter().all(|blob| blob.len() == 1024 * 4));

    // The vec0 table the stamp scopes.
    assert_eq!(
        vec0::table_name(NATIVE_MODEL, VecSource::Embeddings),
        "vec_native_BAAI_bge_m3"
    );
}
