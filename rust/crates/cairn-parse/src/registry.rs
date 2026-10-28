//! Language-to-grammar routes for the parse registry.

use tree_sitter::{Language, Parser};

/// Every routed language name, sorted.
pub const LANGUAGE_NAMES: &[&str] = &[
    "c",
    "cpp",
    "csharp",
    "dart",
    "go",
    "java",
    "javascript",
    "kotlin",
    "objc",
    "php",
    "python",
    "ruby",
    "rust",
    "swift",
    "tsx",
    "typescript",
];

/// Grammar for `name`; None when unsupported.
pub fn grammar(name: &str) -> Option<Language> {
    Some(match name {
        "c" => tree_sitter_c::LANGUAGE.into(),
        "cpp" => tree_sitter_cpp::LANGUAGE.into(),
        "csharp" => tree_sitter_c_sharp::LANGUAGE.into(),
        "dart" => cairn_tree_sitter_dart::LANGUAGE.into(),
        "go" => tree_sitter_go::LANGUAGE.into(),
        "java" => tree_sitter_java::LANGUAGE.into(),
        "javascript" => tree_sitter_javascript::LANGUAGE.into(),
        "kotlin" => tree_sitter_kotlin::LANGUAGE.into(),
        "objc" => tree_sitter_objc::LANGUAGE.into(),
        // php_only yields a pure PHP AST instead of an embedded-HTML tree.
        "php" => tree_sitter_php::LANGUAGE_PHP_ONLY.into(),
        "python" => tree_sitter_python::LANGUAGE.into(),
        "ruby" => tree_sitter_ruby::LANGUAGE.into(),
        "rust" => tree_sitter_rust::LANGUAGE.into(),
        "swift" => tree_sitter_swift::LANGUAGE.into(),
        "tsx" => tree_sitter_typescript::LANGUAGE_TSX.into(),
        "typescript" => tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into(),
        _ => return None,
    })
}

/// Whether `name` has a grammar route.
pub fn is_language_available(name: &str) -> bool {
    grammar(name).is_some()
}

/// Parser configured for `name`; None when unsupported.
pub fn parser(name: &str) -> Option<Parser> {
    let mut parser = Parser::new();
    parser.set_language(&grammar(name)?).ok()?;
    Some(parser)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn routes_every_language_name() {
        assert_eq!(LANGUAGE_NAMES.len(), 16);
        for name in LANGUAGE_NAMES {
            assert!(grammar(name).is_some(), "{name} has no grammar route");
            assert!(is_language_available(name), "{name} reports unavailable");
        }
    }

    #[test]
    fn unknown_language_is_unavailable() {
        assert!(!is_language_available("cobol"));
        assert!(grammar("cobol").is_none());
        assert!(parser("cobol").is_none());
    }

    #[test]
    fn every_routed_grammar_parses_a_snippet() {
        let snippets: &[(&str, &str)] = &[
            ("c", "int main(void) { return 0; }"),
            ("cpp", "int main() { return 0; }"),
            ("csharp", "class A { void M() {} }"),
            ("dart", "void main() {}"),
            ("go", "func main() {}"),
            ("java", "class A { void m() {} }"),
            ("javascript", "function f() {}"),
            ("kotlin", "fun main() {}"),
            ("objc", "@implementation A @end"),
            ("php", "<?php function f() {}"),
            ("python", "def f(): pass"),
            ("ruby", "def f; end"),
            ("rust", "fn f() {}"),
            ("swift", "func f() {}"),
            ("tsx", "const x = 1;"),
            ("typescript", "const x: number = 1;"),
        ];
        for (name, source) in snippets {
            let mut parser = parser(name).unwrap_or_else(|| panic!("{name} unavailable"));
            let tree = parser
                .parse(source.as_bytes(), None)
                .unwrap_or_else(|| panic!("{name} produced no tree"));
            assert!(
                !tree.root_node().has_error(),
                "{name} failed to parse its smoke snippet"
            );
        }
    }
}
