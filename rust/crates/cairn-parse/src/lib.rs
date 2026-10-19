//! Parsing layer: per-language symbol/edge extraction over tree-sitter grammars.

pub mod generic;
pub mod model;
pub mod parser_apple;
pub mod parser_jvm;
pub mod parser_script;
pub mod parser_sys;
pub mod parser_web;
pub mod rationale;
pub mod registry;

pub use generic::{GenericConfig, GenericTier, parse_file, parse_source};
pub use model::{Edge, Import, Metadata, ParsedFile, RationaleRecord, Symbol};
pub use parser_web::{WebParser, language_for_suffix, resolve_relative_import};
pub use rationale::comment_node_types;
pub use registry::{LANGUAGE_NAMES, is_language_available, parser};

/// Returns the crate name; skeleton smoke contract.
pub fn crate_name() -> &'static str {
    env!("CARGO_PKG_NAME")
}

#[cfg(test)]
mod tests {
    #[test]
    fn exposes_crate_name() {
        assert_eq!(super::crate_name(), "cairn-parse");
    }
}
