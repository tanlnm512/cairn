//! The `cairn` binary crate.

pub mod build_graph;
pub mod canonical_dump;
pub mod closure;

/// Returns the crate name; skeleton smoke contract.
pub fn crate_name() -> &'static str {
    env!("CARGO_PKG_NAME")
}

#[cfg(test)]
mod tests {
    #[test]
    fn links_workspace_members() {
        assert_eq!(super::crate_name(), "cairn-cli");
        assert_eq!(cairn_store::crate_name(), "cairn-store");
        assert_eq!(cairn_query::crate_name(), "cairn-query");
        assert_eq!(cairn_parse::crate_name(), "cairn-parse");
        assert_eq!(cairn_build::crate_name(), "cairn-build");
        assert_eq!(cairn_daemon::crate_name(), "cairn-daemon");
    }
}
