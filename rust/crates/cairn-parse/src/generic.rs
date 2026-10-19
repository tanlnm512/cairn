//! Generic definition and call extraction for grammar-only language tiers.

use std::path::Path;

use tree_sitter::Node;

use crate::model::{Edge, ParsedFile, Symbol, sha256_hex};
use crate::rationale;
use crate::registry;

/// Node-kind configuration a generic tier declares.
#[derive(Debug, Clone, Copy, Default)]
pub struct GenericConfig {
    /// Node kind -> symbol kind for declarations.
    pub definition_kinds: &'static [(&'static str, &'static str)],
    /// Declaration kinds named as implementations (`impl Foo`).
    pub implementation_kinds: &'static [&'static str],
    /// Declaration kinds that push a qualified-name scope.
    pub scope_kinds: &'static [&'static str],
    /// Scope kinds that also push a type scope (method-vs-function classification).
    pub type_scope_kinds: &'static [&'static str],
    /// Declaration kinds that push a callable scope (edge ownership).
    pub callable_kinds: &'static [&'static str],
    /// Direct child node kinds accepted as declaration names.
    pub name_node_types: &'static [&'static str],
}

/// A language tier driving the generic traversal; hooks override defaults.
pub trait GenericTier {
    /// Registry language name.
    fn language(&self) -> &'static str;
    /// Traversal configuration.
    fn config(&self) -> GenericConfig;
    /// Symbol-kind override; `in_type_scope` reports enclosing type scopes.
    fn symbol_kind(&self, _node: Node<'_>, kind: &str, _in_type_scope: bool) -> String {
        kind.to_string()
    }
    /// Name of an implementation declaration; None skips the declaration.
    fn implementation_name(&self, _node: Node<'_>, _source: &[u8]) -> Option<String> {
        None
    }
}

/// Parse the file at `path` with the generic tier.
pub fn parse_file<T: GenericTier>(tier: &T, path: &Path) -> std::io::Result<ParsedFile> {
    let source = std::fs::read(path)?;
    Ok(parse_source(tier, path, &source))
}

/// Parse `source` with the generic tier.
pub fn parse_source<T: GenericTier>(tier: &T, path: &Path, source: &[u8]) -> ParsedFile {
    let mut parser = registry::parser(tier.language()).expect("tier language is routed");
    let tree = parser.parse(source, None).expect("tree-sitter parse");
    let mut walk = Walk {
        tier,
        config: tier.config(),
        source,
        scope: Vec::new(),
        callable_scope: Vec::new(),
        type_scopes: Vec::new(),
    };
    let mut parsed = ParsedFile {
        path: path.to_string_lossy().into_owned(),
        language: tier.language().to_string(),
        hash: sha256_hex(source),
        line_count: source.iter().filter(|&&b| b == b'\n').count() + 1,
        ..ParsedFile::default()
    };
    walk.visit(tree.root_node(), &mut parsed);
    parsed
}

struct Walk<'a, T: GenericTier> {
    tier: &'a T,
    config: GenericConfig,
    source: &'a [u8],
    scope: Vec<String>,
    callable_scope: Vec<String>,
    type_scopes: Vec<String>,
}

impl<T: GenericTier> Walk<'_, T> {
    fn visit(&mut self, node: Node<'_>, parsed: &mut ParsedFile) {
        if let Some(&(_, kind)) = self
            .config
            .definition_kinds
            .iter()
            .find(|(k, _)| *k == node.kind())
            && let Some(name) = self.definition_name(node).filter(|n| !n.is_empty())
        {
            parsed.symbols.push(Symbol {
                name: name.clone(),
                kind: self
                    .tier
                    .symbol_kind(node, kind, !self.type_scopes.is_empty()),
                qualified_name: Some(self.qualified_name(&name)),
                line_start: node.start_position().row + 1,
                line_end: node.end_position().row + 1,
                column_start: node.start_position().column,
                column_end: node.end_position().column,
                ..Symbol::default()
            });
            self.visit_declaration_body(node, &name, parsed);
            return;
        }

        if node.kind() == "call_expression"
            && let Some(edge) = self.parse_call(node)
        {
            parsed.edges.push(edge);
        }

        self.visit_children(node, parsed);
    }

    fn visit_declaration_body(&mut self, node: Node<'_>, name: &str, parsed: &mut ParsedFile) {
        let scope_name = self.scope_name(node, name);
        if let Some(scope_name) = scope_name.clone() {
            self.scope.push(scope_name.clone());
            if self.config.type_scope_kinds.contains(&node.kind()) {
                self.type_scopes.push(scope_name);
            }
        }

        if self.config.callable_kinds.contains(&node.kind()) {
            self.callable_scope.push(name.to_string());
        }

        self.visit_children(node, parsed);

        if self.config.callable_kinds.contains(&node.kind()) {
            self.callable_scope.pop();
        }
        if scope_name.is_some() {
            if self.config.type_scope_kinds.contains(&node.kind()) {
                self.type_scopes.pop();
            }
            self.scope.pop();
        }
    }

    fn visit_children(&mut self, node: Node<'_>, parsed: &mut ParsedFile) {
        for child in children(node) {
            if self.rationale_from_comment(child, parsed) {
                continue;
            }
            self.visit(child, parsed);
        }
    }

    fn rationale_from_comment(&self, node: Node<'_>, parsed: &mut ParsedFile) -> bool {
        let comment_types = rationale::comment_node_types(self.tier.language());
        if !comment_types.contains(&node.kind()) {
            return false;
        }
        if let Some(record) = rationale::extract_rationale(node, self.source) {
            parsed.rationale.push(record);
        }
        true
    }

    fn definition_name(&self, node: Node<'_>) -> Option<String> {
        if self.config.implementation_kinds.contains(&node.kind()) {
            return self
                .tier
                .implementation_name(node, self.source)
                .map(|implemented| format!("impl {implemented}"));
        }
        self.direct_name(node, self.config.name_node_types)
    }

    fn direct_name(&self, node: Node<'_>, node_types: &[&str]) -> Option<String> {
        children(node)
            .into_iter()
            .find(|child| node_types.contains(&child.kind()))
            .map(|child| self.node_text(child).trim().to_string())
    }

    fn scope_name(&self, node: Node<'_>, name: &str) -> Option<String> {
        if !self.config.scope_kinds.contains(&node.kind()) {
            return None;
        }
        if self.config.implementation_kinds.contains(&node.kind()) {
            return self.tier.implementation_name(node, self.source);
        }
        Some(name.to_string())
    }

    fn parse_call(&self, node: Node<'_>) -> Option<Edge> {
        let function_node = children(node)
            .into_iter()
            .find(|child| child.is_named() && child.kind() != "arguments")?;
        let callee = self.callee_name(function_node)?;
        Some(Edge {
            source_name: self.current_edge_owner(),
            kind: "calls".to_string(),
            target_name: callee,
            line: node.start_position().row + 1,
            column: node.start_position().column,
            call_arity: self.argument_count(node),
            generic_tier: true,
            ..Edge::default()
        })
    }

    fn callee_name(&self, node: Node<'_>) -> Option<String> {
        if self.config.name_node_types.contains(&node.kind()) {
            return Some(self.node_text(node).trim().to_string());
        }
        if node.kind() == "generic_function" {
            return self.direct_name(node, &["identifier"]);
        }
        if node.kind() == "field_expression" {
            return self
                .last_descendant_of_type(node, "field_identifier")
                .map(|field| self.node_text(field).trim().to_string());
        }
        if node.kind() == "scoped_identifier" {
            return self
                .last_descendant_of_type(node, "identifier")
                .map(|identifier| self.node_text(identifier).trim().to_string());
        }
        None
    }

    fn last_descendant_of_type<'a>(&self, node: Node<'a>, node_type: &str) -> Option<Node<'a>> {
        let mut found = None;
        for child in children(node) {
            if let Some(descendant) = self.last_descendant_of_type(child, node_type) {
                found = Some(descendant);
            }
        }
        if node.kind() == node_type {
            found = Some(node);
        }
        found
    }

    fn argument_count(&self, node: Node<'_>) -> Option<usize> {
        let arguments = self.child_of_type(node, &["arguments"])?;
        Some(
            children(arguments)
                .into_iter()
                .filter(|c| c.is_named())
                .count(),
        )
    }

    fn child_of_type<'a>(&self, node: Node<'a>, types: &[&str]) -> Option<Node<'a>> {
        children(node)
            .into_iter()
            .find(|child| types.contains(&child.kind()))
    }

    fn node_text(&self, node: Node<'_>) -> String {
        String::from_utf8_lossy(&self.source[node.start_byte()..node.end_byte()]).into_owned()
    }

    fn qualified_name(&self, name: &str) -> String {
        if self.scope.is_empty() {
            return name.to_string();
        }
        let mut qualified = self.scope.join(".");
        qualified.push('.');
        qualified.push_str(name);
        qualified
    }

    fn current_edge_owner(&self) -> String {
        self.callable_scope
            .last()
            .or_else(|| self.scope.last())
            .cloned()
            .unwrap_or_default()
    }
}

/// Collect a node's direct children; the cursor borrow must outlive them.
pub(crate) fn children(node: Node<'_>) -> Vec<Node<'_>> {
    let mut cursor = node.walk();
    node.children(&mut cursor).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Mirrors the Python reference tier (`parsers/rust.py`) config.
    struct RustTier;

    impl GenericTier for RustTier {
        fn language(&self) -> &'static str {
            "rust"
        }

        fn config(&self) -> GenericConfig {
            GenericConfig {
                definition_kinds: &[
                    ("function_item", "function"),
                    ("struct_item", "class"),
                    ("impl_item", "implementation"),
                ],
                implementation_kinds: &["impl_item"],
                scope_kinds: &["struct_item", "impl_item"],
                type_scope_kinds: &["struct_item", "impl_item"],
                callable_kinds: &["function_item"],
                name_node_types: &["identifier", "type_identifier"],
            }
        }

        fn symbol_kind(&self, node: Node<'_>, kind: &str, in_type_scope: bool) -> String {
            if node.kind() == "function_item" && in_type_scope {
                return "method".to_string();
            }
            kind.to_string()
        }

        fn implementation_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
            children(node)
                .into_iter()
                .rev()
                .find(|child| child.kind() != "declaration_list")
                .and_then(|child| self.first_type_identifier(child, source))
        }
    }

    impl RustTier {
        fn first_type_identifier(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
            if node.kind() == "type_identifier" {
                return Some(
                    String::from_utf8_lossy(&source[node.start_byte()..node.end_byte()])
                        .trim()
                        .to_string(),
                );
            }
            children(node)
                .into_iter()
                .find_map(|child| self.first_type_identifier(child, source))
        }
    }

    #[test]
    fn extracts_symbols_edges_and_rationale() {
        let source = r#"// NOTE: single counter per store
struct Counter {
    n: u32,
}

impl Counter {
    fn inc(&self) -> u32 {
        helper();
        self.n + 1
    }
}

fn helper() -> u32 {
    1
}
"#;
        let parsed = parse_source(&RustTier, Path::new("lib.rs"), source.as_bytes());

        assert_eq!(parsed.language, "rust");
        assert_eq!(parsed.line_count, source.matches('\n').count() + 1);
        assert_eq!(parsed.hash, sha256_hex(source.as_bytes()));

        let symbols: Vec<(String, String, String)> = parsed
            .symbols
            .iter()
            .map(|s| {
                (
                    s.name.clone(),
                    s.kind.clone(),
                    s.qualified_name.clone().expect("qualified"),
                )
            })
            .collect();
        assert_eq!(
            symbols,
            vec![
                ("Counter".into(), "class".into(), "Counter".into()),
                (
                    "impl Counter".into(),
                    "implementation".into(),
                    "impl Counter".into()
                ),
                ("inc".into(), "method".into(), "Counter.inc".into()),
                ("helper".into(), "function".into(), "helper".into()),
            ]
        );

        let edges: Vec<(String, String, usize, bool)> = parsed
            .edges
            .iter()
            .map(|e| {
                (
                    e.source_name.clone(),
                    e.target_name.clone(),
                    e.call_arity.expect("arity"),
                    e.generic_tier,
                )
            })
            .collect();
        assert_eq!(edges, vec![("inc".into(), "helper".into(), 0, true)]);

        assert_eq!(parsed.rationale.len(), 1);
        assert_eq!(parsed.rationale[0].kind, "note");
        assert_eq!(parsed.rationale[0].text, "single counter per store");
        assert_eq!(parsed.rationale[0].line, 1);
    }

    #[test]
    fn skips_unnameable_declarations() {
        let source = "fn () {}\n";
        let parsed = parse_source(&RustTier, Path::new("bad.rs"), source.as_bytes());
        assert!(parsed.symbols.is_empty());
        assert!(parsed.edges.is_empty());
    }
}
