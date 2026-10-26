//! Build path: scanner, parallel builder, resolver, incremental graft, streaming closure.

pub mod builder;
pub mod closure;
pub mod communities;
pub mod config;
pub mod dataflow;
pub mod embeddings;
pub mod gitignore;
pub mod incremental;
pub mod parse_dispatch;
pub mod resolver;
pub mod routes;
pub mod scanner;
pub mod scip;
pub mod service_calls;
pub mod timefmt;
pub mod watcher;

pub use builder::{BuildSummary, ResolutionCounts, StageTimings, build_graph};

/// Returns the crate name; skeleton smoke contract.
pub fn crate_name() -> &'static str {
    env!("CARGO_PKG_NAME")
}

#[cfg(test)]
mod tests {
    #[test]
    fn exposes_crate_name() {
        assert_eq!(super::crate_name(), "cairn-build");
    }
}
