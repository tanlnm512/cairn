//! The `cairn` binary crate.

pub mod agent_install;
pub mod agent_templates;
pub mod build_graph;
pub mod canonical_dump;
pub mod cli;
pub mod cli_rows;
pub mod closure;
pub mod cmds_agents;
pub mod cmds_connect;
pub mod cmds_core;
pub mod cmds_dashboard;
pub mod cmds_doctor;
pub mod cmds_knowledge;
pub mod cmds_serve;
pub mod cmds_system;
pub mod dashboard;
pub mod display;
pub mod pack;
pub mod py_json;
pub mod rail;
pub mod store_paths;

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
