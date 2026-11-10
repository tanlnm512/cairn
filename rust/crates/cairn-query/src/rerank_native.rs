//! In-process cross-encoder reranking behind the local-embed feature.

#[cfg(feature = "local-embed")]
mod native {
    use std::sync::Mutex;
    use std::sync::OnceLock;

    use fastembed::{RerankInitOptions, RerankerModel, TextRerank};

    use crate::embed_native::cache_root;

    static RERANKER: OnceLock<Mutex<Option<TextRerank>>> = OnceLock::new();

    fn reranker() -> Option<&'static Mutex<Option<TextRerank>>> {
        RERANKER.get_or_init(|| {
            let opts =
                RerankInitOptions::new(RerankerModel::BGERerankerV2M3).with_cache_dir(cache_root());
            Mutex::new(TextRerank::try_new(opts).ok())
        });
        RERANKER.get()
    }

    /// Sigmoid-normalized cross-encoder scores per passage (0..1, original
    /// order); None degrades to the caller's fused order (model missing,
    /// offline cold cache, or init failure).
    pub fn rerank_scores(query: &str, passages: &[String]) -> Option<Vec<f64>> {
        let lock = reranker()?;
        let mut guard = lock.lock().ok()?;
        let model = guard.as_mut()?;
        let docs: Vec<&str> = passages.iter().map(String::as_str).collect();
        let results = model.rerank(query, &docs, false, None).ok()?;
        let mut scores = vec![0.0f64; passages.len()];
        for result in results {
            if let Some(slot) = scores.get_mut(result.index) {
                *slot = 1.0 / (1.0 + (-result.score as f64).exp());
            }
        }
        Some(scores)
    }
}

#[cfg(feature = "local-embed")]
pub(crate) use native::rerank_scores;

#[cfg(not(feature = "local-embed"))]
/// No native cross-encoder compiled in; callers keep the fused order.
pub(crate) fn rerank_scores(_query: &str, _passages: &[String]) -> Option<Vec<f64>> {
    None
}
