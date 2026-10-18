//! Query engine: FTS5 search, definition lookup, callers/callees, impact, explore, trace.

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
