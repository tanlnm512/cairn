//! In-process bge-m3 embedding behind the local-embed cargo feature.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use fastembed::{EmbeddingModel, TextEmbedding, TextInitOptions};

use crate::tools_search::NATIVE_HF_MODEL_ID;

/// The warm model; one instance process-wide, every batch serialized.
static MODEL: OnceLock<Result<Mutex<TextEmbedding>, String>> = OnceLock::new();

/// Cache root mirroring the embedder's own resolution: HF_HOME wins, then
/// FASTEMBED_CACHE_DIR, then the ./.fastembed_cache default.
pub fn cache_root() -> PathBuf {
    if let Ok(home) = std::env::var("HF_HOME") {
        return PathBuf::from(home);
    }
    PathBuf::from(
        std::env::var("FASTEMBED_CACHE_DIR").unwrap_or_else(|_| ".fastembed_cache".to_string()),
    )
}

fn repo_dir(root: &Path) -> PathBuf {
    root.join(format!("models--{}", NATIVE_HF_MODEL_ID.replace('/', "--")))
}

/// True when a model snapshot carries the ONNX files; never constructs
/// the model, so probing cannot download.
pub fn model_cached() -> bool {
    let snapshots = repo_dir(&cache_root()).join("snapshots");
    let Ok(entries) = std::fs::read_dir(snapshots) else {
        return false;
    };
    entries.flatten().any(|entry| {
        let onnx = entry.path().join("onnx");
        ["model.onnx", "model.onnx_data"]
            .iter()
            .all(|file| onnx.join(file).is_file())
    })
}

fn init_options() -> TextInitOptions {
    TextInitOptions::new(EmbeddingModel::BGEM3).with_show_download_progress(true)
}

/// Download the model weights on a cold cache by constructing the model
/// once and dropping it; never embeds.
pub fn warm_model() -> Result<(), String> {
    TextEmbedding::try_new(init_options())
        .map(|_| ())
        .map_err(|e| e.to_string())
}

fn model() -> Result<&'static Mutex<TextEmbedding>, String> {
    let cell = MODEL.get_or_init(|| {
        TextEmbedding::try_new(init_options())
            .map(Mutex::new)
            .map_err(|e| e.to_string())
    });
    cell.as_ref().map_err(Clone::clone)
}

/// Dense vectors for `texts`; `batch_size` sub-batches inference.
pub fn embed(texts: &[String], batch_size: usize) -> Result<Vec<Vec<f32>>, String> {
    let mut model = model()?
        .lock()
        .map_err(|_| "native model lock poisoned".to_string())?;
    model
        .embed(texts, Some(batch_size.max(1)))
        .map_err(|e| e.to_string())
}
