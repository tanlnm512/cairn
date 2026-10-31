//! SCIP ingestion: the prost-decoded proto schema, the edges-only importer,
//! and the run-once auto-gen indexer registry. The protobuf runtime is a
//! compile-time dependency; there is no unavailable-runtime branch.

// Generated code; exempt from rustfmt and clippy.
#[rustfmt::skip]
#[allow(clippy::all)]
pub mod proto;

pub mod importer;
pub mod indexers;

use std::path::{Path, PathBuf};

/// Generated-index cache dir `<cairn home>/<store key>/scip`, resolved via
/// the `workspaces.json` registry; None when the home or the workspace's
/// registry entry is missing.
pub fn store_scip_dir(workspace: &str) -> Option<PathBuf> {
    let home = cairn_store::paths::cairn_home()?;
    let registry = cairn_store::paths::load_registry();
    let resolved = cairn_store::paths::resolve_path(Path::new(workspace));
    // Registry keys are stored as `init` received them; try the resolved
    // form first, then the path as given.
    let key = registry
        .get(resolved.to_string_lossy().as_ref())
        .or_else(|| registry.get(workspace))?;
    Some(home.join(key).join("scip"))
}
