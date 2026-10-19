//! Build path: scanner, parallel builder, resolver, incremental graft, streaming closure.

pub mod builder;
pub mod config;
pub mod gitignore;
pub mod parse_dispatch;
pub mod resolver;
pub mod routes;
pub mod scanner;
pub mod service_calls;
pub mod timefmt;

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
