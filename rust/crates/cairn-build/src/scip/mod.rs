//! SCIP ingestion: the prost-decoded proto schema, the edges-only importer,
//! and the run-once auto-gen indexer registry. The protobuf runtime is a
//! compile-time dependency; there is no unavailable-runtime branch.

// Generated code; exempt from rustfmt and clippy.
#[rustfmt::skip]
#[allow(clippy::all)]
pub mod proto;

pub mod importer;
pub mod indexers;
