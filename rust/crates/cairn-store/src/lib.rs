//! Store layer: `.kg` connection factory, vec0 helpers, canonical dump, atomic swap.

pub mod conn;
pub mod dump;
pub mod schema;
pub mod swap;
pub mod vec0;

/// Returns the crate name; skeleton smoke contract.
pub fn crate_name() -> &'static str {
    env!("CARGO_PKG_NAME")
}

#[cfg(test)]
mod tests {
    #[test]
    fn exposes_crate_name() {
        assert_eq!(super::crate_name(), "cairn-store");
    }
}
