//! Query engine: FTS5 search, definition lookup, callers/callees, impact, explore, trace.

pub mod blast;
pub mod graph;
pub mod grep;
pub mod ingest;
pub mod skillgen;
pub mod tasks;
pub mod tools_federation;
pub mod tools_flow;
pub mod tools_km;
pub mod tools_nav;
pub mod tools_repo;
pub mod tools_search;
pub mod tools_wiki;

/// Returns the crate name; skeleton smoke contract.
pub fn crate_name() -> &'static str {
    env!("CARGO_PKG_NAME")
}

#[cfg(test)]
mod tests {
    #[test]
    fn exposes_crate_name() {
        assert_eq!(super::crate_name(), "cairn-query");
    }
}
