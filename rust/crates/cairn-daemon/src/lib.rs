//! Daemon layer: streamable-HTTP MCP server, bearer auth, healthz, reuse lifecycle.

pub mod auth;
pub mod conn_pool;
pub mod healthz;
pub mod lifecycle;
pub mod search;
pub mod server;
pub mod store;
pub mod tools;

/// Returns the crate name; skeleton smoke contract.
pub fn crate_name() -> &'static str {
    env!("CARGO_PKG_NAME")
}

#[cfg(test)]
mod tests {
    #[test]
    fn exposes_crate_name() {
        assert_eq!(super::crate_name(), "cairn-daemon");
    }
}
