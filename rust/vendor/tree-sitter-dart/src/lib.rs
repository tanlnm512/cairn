//! Vendored dart grammar exposing `LANGUAGE` for the cairn-parse registry.

use tree_sitter_language::LanguageFn;

unsafe extern "C" {
    fn tree_sitter_dart() -> *const ();
}

/// Tree-sitter language for the vendored dart grammar.
pub const LANGUAGE: LanguageFn = unsafe { LanguageFn::from_raw(tree_sitter_dart) };

#[cfg(test)]
mod tests {
    #[test]
    fn exposes_language() {
        let language = unsafe { (super::LANGUAGE.into_raw())() };
        assert!(!language.is_null());
    }
}
