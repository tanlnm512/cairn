//! Systems-family parsers: go, rust, c, cpp, csharp.

use std::collections::VecDeque;
use std::path::Path;

use tree_sitter::Node;

use crate::generic::{self, GenericConfig, GenericTier};
use crate::model::{Edge, Import, ParsedFile, Symbol, sha256_hex};
use crate::rationale;
use crate::registry;

// --- Shared helpers ---

/// Scope-ordered variable to type tracker for receiver-type inference.
#[derive(Default)]
pub struct ScopeTypeTracker {
    /// Innermost scope last; None marks a name with conflicting types ("ambiguous").
    scopes: Vec<std::collections::HashMap<String, Option<String>>>,
}

impl ScopeTypeTracker {
    /// Tracker with an empty root scope.
    pub fn new() -> Self {
        Self {
            scopes: vec![std::collections::HashMap::new()],
        }
    }

    /// Drop every scope and recorded type.
    pub fn reset(&mut self) {
        *self = Self::new();
    }

    /// Enter a nested block scope.
    pub fn push(&mut self) {
        self.scopes.push(std::collections::HashMap::new());
    }

    /// Exit the innermost scope; the root scope is never popped.
    pub fn pop(&mut self) {
        if self.scopes.len() > 1 {
            self.scopes.pop();
        }
    }

    /// Record a scope type, making conflicting visible types ambiguous.
    pub fn record(&mut self, name: &str, type_name: Option<&str>) {
        let Some(type_name) = type_name.filter(|t| !t.is_empty()) else {
            return;
        };
        if name.is_empty() {
            return;
        }
        let mut visible: Option<Option<String>> = None;
        for scope in self.scopes.iter().rev() {
            if let Some(entry) = scope.get(name) {
                visible = Some(entry.clone());
                break;
            }
        }
        match visible {
            None => {
                self.scopes
                    .last_mut()
                    .expect("root scope is never popped")
                    .insert(name.to_string(), Some(type_name.to_string()));
            }
            Some(None) => {}
            Some(Some(existing)) => {
                if existing != type_name {
                    self.scopes
                        .last_mut()
                        .expect("root scope is never popped")
                        .insert(name.to_string(), None);
                }
            }
        }
    }

    /// Innermost visible type for `name`; None = unknown or ambiguous.
    pub fn resolve(&self, name: &str) -> Option<String> {
        for scope in self.scopes.iter().rev() {
            if let Some(entry) = scope.get(name) {
                return entry.clone();
            }
        }
        None
    }
}

/// Receiver text only when its capitalized shape looks like a type.
pub fn infer_receiver_type(receiver_text: Option<&str>) -> Option<String> {
    let text = receiver_text?;
    text.chars()
        .next()
        .filter(|c| c.is_uppercase())
        .map(|_| text.to_string())
}

/// ParsedFile header for `source` parsed at `path`.
fn parsed_file(path: &Path, language: &str, source: &[u8]) -> ParsedFile {
    ParsedFile {
        path: path.to_string_lossy().into_owned(),
        language: language.to_string(),
        hash: sha256_hex(source),
        line_count: source.iter().filter(|&&b| b == b'\n').count() + 1,
        ..ParsedFile::default()
    }
}

/// Dot-qualified name under the current scope.
fn qualified_name(scope: &[String], name: &str) -> String {
    if scope.is_empty() {
        return name.to_string();
    }
    let mut qualified = scope.join(".");
    qualified.push('.');
    qualified.push_str(name);
    qualified
}

/// Edge owner: innermost callable, else innermost type scope.
fn current_edge_owner(callable_scope: &[String], scope: &[String]) -> String {
    callable_scope
        .last()
        .or_else(|| scope.last())
        .cloned()
        .unwrap_or_default()
}

/// Collect a node's direct children; the cursor borrow must outlive them.
fn children(node: Node<'_>) -> Vec<Node<'_>> {
    let mut cursor = node.walk();
    node.children(&mut cursor).collect()
}

/// Raw source text of `node`.
fn text(node: Node<'_>, source: &[u8]) -> String {
    String::from_utf8_lossy(&source[node.start_byte()..node.end_byte()]).into_owned()
}

/// Whitespace-trimmed source text of `node`.
fn text_trimmed(node: Node<'_>, source: &[u8]) -> String {
    text(node, source).trim().to_string()
}

/// First direct child whose kind is in `types`.
fn child_of_type<'a>(node: Node<'a>, types: &[&str]) -> Option<Node<'a>> {
    children(node)
        .into_iter()
        .find(|child| types.contains(&child.kind()))
}

/// Direct children whose kind is in `types`.
fn children_of_type<'a>(node: Node<'a>, types: &[&str]) -> Vec<Node<'a>> {
    children(node)
        .into_iter()
        .filter(|child| types.contains(&child.kind()))
        .collect()
}

/// Text of the first descendant of `type_` (breadth-first), if any.
fn first_descendant_text(node: Node<'_>, type_: &str, source: &[u8]) -> Option<String> {
    let mut queue: VecDeque<Node<'_>> = children(node).into_iter().collect();
    while let Some(current) = queue.pop_front() {
        if current.kind() == type_ {
            return Some(text_trimmed(current, source));
        }
        queue.extend(children(current));
    }
    None
}

/// Per-parser traversal step; shared walk consumes rationale comments.
trait SysVisit {
    fn visit(&mut self, node: Node<'_>, source: &[u8], parsed: &mut ParsedFile);
}

/// Visit `node`'s children, consuming mapped comment nodes into rationale.
fn walk<V: SysVisit>(visitor: &mut V, node: Node<'_>, source: &[u8], parsed: &mut ParsedFile) {
    let language = parsed.language.clone();
    for child in children(node) {
        if rationale_from_comment(&child, &language, source, parsed) {
            continue;
        }
        visitor.visit(child, source, parsed);
    }
}

/// Consume a mapped comment node into rationale; True when handled.
fn rationale_from_comment(
    node: &Node<'_>,
    language: &str,
    source: &[u8],
    parsed: &mut ParsedFile,
) -> bool {
    if !rationale::comment_node_types(language).contains(&node.kind()) {
        return false;
    }
    if let Some(record) = rationale::extract_rationale(*node, source) {
        parsed.rationale.push(record);
    }
    true
}

// --- Go ---

/// Go type-reference node kinds used by signature and receiver extraction.
const GO_TYPE_NODE_KINDS: &[&str] = &[
    "type_identifier",
    "qualified_type",
    "pointer_type",
    "slice_type",
    "array_type",
    "map_type",
    "channel_type",
    "function_type",
    "struct_type",
    "interface_type",
    "type_alias",
];

/// Tree-sitter Go parser.
pub struct GoParser;

impl GoParser {
    /// Parse the Go file at `path`.
    pub fn parse_file(path: &Path) -> std::io::Result<ParsedFile> {
        let source = std::fs::read(path)?;
        Ok(Self::parse_source(path, &source))
    }

    /// Parse Go `source` as the file at `path`.
    pub fn parse_source(path: &Path, source: &[u8]) -> ParsedFile {
        let mut parsed = parsed_file(path, "go", source);
        let mut parser = registry::parser("go").expect("go grammar is routed");
        let tree = parser.parse(source, None).expect("tree-sitter parse");
        let mut state = GoWalk::default();
        state.visit(tree.root_node(), source, &mut parsed);
        parsed
    }
}

#[derive(Default)]
struct GoWalk {
    scope: Vec<String>,
    callable_scope: Vec<String>,
}

impl SysVisit for GoWalk {
    fn visit(&mut self, node: Node<'_>, source: &[u8], parsed: &mut ParsedFile) {
        match node.kind() {
            "import_declaration" => {
                parsed.imports.extend(self.parse_imports(node, source));
            }
            "type_declaration" => {
                for spec in children_of_type(node, &["type_spec"]) {
                    let Some(sym) = self.parse_type_spec(spec, source) else {
                        continue;
                    };
                    parsed
                        .edges
                        .extend(self.parse_embeds(spec, source, &sym.name));
                    self.scope.push(sym.name.clone());
                    parsed.symbols.push(sym);
                    walk(self, spec, source, parsed);
                    self.scope.pop();
                }
            }
            "function_declaration" => {
                if let Some(sym) = self.parse_function(node, source) {
                    parsed.symbols.push(sym.clone());
                    self.callable_scope.push(sym.name);
                    walk(self, node, source, parsed);
                    self.callable_scope.pop();
                }
            }
            "method_declaration" => {
                if let Some(sym) = self.parse_method(node, source) {
                    parsed.symbols.push(sym.clone());
                    self.callable_scope.push(sym.name);
                    walk(self, node, source, parsed);
                    self.callable_scope.pop();
                }
            }
            "call_expression" => {
                if let Some(edge) = self.parse_call(node, source) {
                    parsed.edges.push(edge);
                }
                walk(self, node, source, parsed);
            }
            _ => walk(self, node, source, parsed),
        }
    }
}

impl GoWalk {
    fn parse_type_spec(&self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = children(node)
            .into_iter()
            .find(|child| child.kind() == "type_identifier")
            .map(|child| text_trimmed(child, source))?;
        let kind = if child_of_type(node, &["interface_type"]).is_some() {
            "interface"
        } else {
            "class"
        };
        Some(Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name,
            kind: kind.to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            ..Symbol::default()
        })
    }

    /// Embeds edges for anonymous struct fields and interface type elements.
    fn parse_embeds(&self, node: Node<'_>, source: &[u8], owner: &str) -> Vec<Edge> {
        let mut edges = Vec::new();
        let candidates: Vec<Node<'_>> = if let Some(struct_node) =
            child_of_type(node, &["struct_type"])
        {
            let Some(declarations) = child_of_type(struct_node, &["field_declaration_list"]) else {
                return edges;
            };
            children(declarations)
                .into_iter()
                .filter(|f| {
                    f.kind() == "field_declaration"
                        && child_of_type(*f, &["field_identifier"]).is_none()
                })
                .collect()
        } else if let Some(iface) = child_of_type(node, &["interface_type"]) {
            children(iface)
                .into_iter()
                .filter(|c| matches!(c.kind(), "type_elem" | "type_identifier" | "qualified_type"))
                .collect()
        } else {
            return edges;
        };
        for elem in candidates {
            if let Some(target) = first_descendant_text(elem, "type_identifier", source) {
                edges.push(Edge {
                    source_name: owner.to_string(),
                    kind: "embeds".to_string(),
                    target_name: target,
                    line: elem.start_position().row + 1,
                    ..Edge::default()
                });
            }
        }
        edges
    }

    fn parse_function(&self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = children(node)
            .into_iter()
            .find(|child| child.kind() == "identifier")
            .map(|child| text_trimmed(child, source))?;
        let (parameters, return_type, arity) = self.parse_signature(node, source);
        Some(Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name,
            kind: "function".to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            parameters,
            return_type,
            arity,
            ..Symbol::default()
        })
    }

    fn parse_method(&self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let mut name = None;
        let mut param_lists = Vec::new();
        for child in children(node) {
            match child.kind() {
                "field_identifier" if name.is_none() => name = Some(text_trimmed(child, source)),
                "parameter_list" => param_lists.push(child),
                _ => {}
            }
        }
        let name = name?;
        let receiver_type = param_lists
            .first()
            .and_then(|recv| self.receiver_type_from_params(*recv, source));
        let (parameters, return_type, arity) = if param_lists.len() >= 2 {
            self.parse_params_and_return(param_lists[1], node, source)
        } else {
            (None, None, None)
        };
        Some(Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name,
            kind: "method".to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            parameters,
            return_type,
            parent_scope: receiver_type,
            arity,
            ..Symbol::default()
        })
    }

    fn parse_signature(
        &self,
        node: Node<'_>,
        source: &[u8],
    ) -> (Option<String>, Option<String>, Option<usize>) {
        let Some(params) = child_of_type(node, &["parameter_list"]) else {
            return (None, None, None);
        };
        self.parse_params_and_return(params, node, source)
    }

    fn parse_params_and_return(
        &self,
        params_node: Node<'_>,
        parent: Node<'_>,
        source: &[u8],
    ) -> (Option<String>, Option<String>, Option<usize>) {
        let params = self.summarize_params(params_node, source);
        let mut return_type = None;
        let mut seen_params = false;
        for child in children(parent) {
            if child == params_node {
                seen_params = true;
                continue;
            }
            if !seen_params {
                continue;
            }
            if child.kind() == "block" {
                break;
            }
            if GO_TYPE_NODE_KINDS.contains(&child.kind()) {
                return_type = Some(text_trimmed(child, source));
                break;
            }
        }
        (params, return_type, self.param_arity(params_node))
    }

    /// Parameter count, or None for a variadic parameter list.
    fn param_arity(&self, params_node: Node<'_>) -> Option<usize> {
        let mut arity = 0;
        for child in children(params_node) {
            match child.kind() {
                "variadic_parameter_declaration" => return None,
                "parameter_declaration" => {
                    let names = children(child)
                        .into_iter()
                        .filter(|pc| pc.kind() == "identifier")
                        .count();
                    arity += if names == 0 { 1 } else { names };
                }
                _ => {}
            }
        }
        Some(arity)
    }

    /// Render a parameter_list as `name1 type1, name2 type2`.
    fn summarize_params(&self, params_node: Node<'_>, source: &[u8]) -> Option<String> {
        let mut parts = Vec::new();
        for child in children(params_node) {
            if child.kind() != "parameter_declaration" {
                continue;
            }
            let mut names = Vec::new();
            let mut ptype = None;
            for pc in children(child) {
                if pc.kind() == "identifier" {
                    names.push(text_trimmed(pc, source));
                } else if GO_TYPE_NODE_KINDS.contains(&pc.kind()) {
                    ptype = Some(text_trimmed(pc, source));
                }
            }
            let mut label = names.join(", ");
            if let Some(ptype) = ptype {
                label = if label.is_empty() {
                    ptype
                } else {
                    format!("{label} {ptype}")
                };
            }
            if !label.is_empty() {
                parts.push(label);
            }
        }
        if parts.is_empty() {
            None
        } else {
            Some(parts.join(", "))
        }
    }

    /// Bare receiver type, unwrapping pointers and qualifiers.
    fn receiver_type_from_params(&self, recv_node: Node<'_>, source: &[u8]) -> Option<String> {
        for child in children(recv_node) {
            if child.kind() != "parameter_declaration" {
                continue;
            }
            for pc in children(child) {
                if pc.kind() == "pointer_type" {
                    for gc in children(pc) {
                        if GO_TYPE_NODE_KINDS.contains(&gc.kind()) {
                            return Some(text_trimmed(gc, source));
                        }
                    }
                } else if GO_TYPE_NODE_KINDS.contains(&pc.kind()) {
                    return Some(text_trimmed(pc, source));
                }
            }
        }
        None
    }

    fn parse_call(&self, node: Node<'_>, source: &[u8]) -> Option<Edge> {
        let mut callee = None;
        let mut receiver_text = None;
        for child in children(node) {
            match child.kind() {
                "identifier" | "field_identifier" => callee = Some(text_trimmed(child, source)),
                "selector_expression" => {
                    let (field, operand) = self.split_selector(child, source);
                    callee = field;
                    receiver_text = operand;
                }
                _ => {}
            }
        }
        Some(Edge {
            source_name: current_edge_owner(&self.callable_scope, &self.scope),
            kind: "calls".to_string(),
            target_name: callee?,
            line: node.start_position().row + 1,
            receiver_type: infer_receiver_type(receiver_text.as_deref()),
            call_arity: self.argument_arity(node),
            ..Edge::default()
        })
    }

    /// Named-argument count; None when a spread argument makes it unknowable.
    fn argument_arity(&self, call_node: Node<'_>) -> Option<usize> {
        let arg_list = child_of_type(call_node, &["argument_list"])?;
        let mut count = 0;
        for child in children(arg_list) {
            if !child.is_named() {
                continue;
            }
            if child.kind() == "variadic_argument" {
                return None;
            }
            count += 1;
        }
        Some(count)
    }

    /// Split a selector_expression into (field_name, receiver_text).
    fn split_selector(&self, node: Node<'_>, source: &[u8]) -> (Option<String>, Option<String>) {
        let mut field_name = None;
        let mut operand_text = None;
        for child in children(node) {
            if child.kind() == "field_identifier" && field_name.is_none() {
                field_name = Some(text_trimmed(child, source));
            } else if matches!(
                child.kind(),
                "identifier" | "call_expression" | "selector_expression"
            ) && operand_text.is_none()
            {
                operand_text = Some(text_trimmed(child, source));
            }
        }
        (field_name, operand_text)
    }

    /// Imports for single and grouped specs, preserving only binding aliases.
    fn parse_imports(&self, node: Node<'_>, source: &[u8]) -> Vec<Import> {
        let mut imports = Vec::new();
        for spec in self.all_import_specs(node) {
            let mut path = None;
            let mut alias = None;
            for child in children(spec) {
                if child.kind() == "package_identifier" {
                    alias = Some(text_trimmed(child, source));
                } else if child.kind() == "interpreted_string_literal" {
                    path = Some(text_trimmed(child, source).trim_matches('"').to_string());
                    break;
                }
            }
            if let Some(path) = path.filter(|p| !p.is_empty()) {
                imports.push(Import {
                    imported_path: path,
                    line: spec.start_position().row + 1,
                    local_alias: alias,
                });
            }
        }
        imports
    }

    /// Every import_spec under an import_declaration (any depth, BFS).
    fn all_import_specs<'a>(&self, node: Node<'a>) -> Vec<Node<'a>> {
        let mut found = Vec::new();
        let mut queue: VecDeque<Node<'_>> = children(node).into_iter().collect();
        while let Some(current) = queue.pop_front() {
            if current.kind() == "import_spec" {
                found.push(current);
            } else {
                queue.extend(children(current));
            }
        }
        found
    }
}

// --- Rust ---

/// Generic-tier configuration mirroring the Rust adapter.
pub struct RustTier;

impl GenericTier for RustTier {
    fn language(&self) -> &'static str {
        "rust"
    }

    fn config(&self) -> GenericConfig {
        GenericConfig {
            definition_kinds: &[
                ("function_item", "function"),
                ("function_signature_item", "function"),
                ("struct_item", "class"),
                ("enum_item", "enum"),
                ("trait_item", "interface"),
                ("impl_item", "implementation"),
                ("mod_item", "module"),
                ("type_item", "class"),
                ("const_item", "constant"),
                ("static_item", "constant"),
                ("macro_definition", "macro"),
            ],
            implementation_kinds: &["impl_item"],
            scope_kinds: &[
                "struct_item",
                "enum_item",
                "trait_item",
                "impl_item",
                "mod_item",
            ],
            type_scope_kinds: &["struct_item", "enum_item", "trait_item", "impl_item"],
            callable_kinds: &["function_item", "function_signature_item"],
            name_node_types: &["identifier", "type_identifier", "field_identifier"],
        }
    }

    fn symbol_kind(&self, node: Node<'_>, kind: &str, in_type_scope: bool) -> String {
        if matches!(node.kind(), "function_item" | "function_signature_item") && in_type_scope {
            return "method".to_string();
        }
        kind.to_string()
    }

    fn implementation_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        children(node)
            .into_iter()
            .rev()
            .filter(|child| child.kind() != "declaration_list")
            .find_map(|child| simple_type_name(child, source))
    }
}

/// Parse the Rust file at `path` with the generic tier.
pub fn parse_rust_file(path: &Path) -> std::io::Result<ParsedFile> {
    generic::parse_file(&RustTier, path)
}

/// Parse Rust `source` with the generic tier.
pub fn parse_rust_source(path: &Path, source: &[u8]) -> ParsedFile {
    generic::parse_source(&RustTier, path, source)
}

/// First type name under a type-shaped node.
fn simple_type_name(node: Node<'_>, source: &[u8]) -> Option<String> {
    if matches!(
        node.kind(),
        "type_identifier" | "generic_type" | "scoped_type_identifier"
    ) {
        return first_type_identifier(node, source);
    }
    None
}

fn first_type_identifier(node: Node<'_>, source: &[u8]) -> Option<String> {
    if node.kind() == "type_identifier" {
        return Some(text_trimmed(node, source));
    }
    children(node)
        .into_iter()
        .find_map(|child| first_type_identifier(child, source))
}

// --- C / C++ ---

/// Shared traversal for the C and C++ grammars.
pub struct CFamilyParser {
    language: &'static str,
}

impl CFamilyParser {
    /// Parser for `.c` sources.
    pub fn c() -> Self {
        Self { language: "c" }
    }

    /// Parser for C++ sources.
    pub fn cpp() -> Self {
        Self { language: "cpp" }
    }

    /// Parse the file at `path`.
    pub fn parse_file(&self, path: &Path) -> std::io::Result<ParsedFile> {
        let source = std::fs::read(path)?;
        Ok(self.parse_source(path, &source))
    }

    /// Parse `source` as the file at `path`.
    pub fn parse_source(&self, path: &Path, source: &[u8]) -> ParsedFile {
        let mut parsed = parsed_file(path, self.language, source);
        let mut parser = registry::parser(self.language).expect("c-family grammar is routed");
        let tree = parser.parse(source, None).expect("tree-sitter parse");
        let mut state = CFamilyWalk {
            language: self.language,
            scope: Vec::new(),
            callable_scope: Vec::new(),
            pending_edges: Vec::new(),
            class_depth: 0,
            types: ScopeTypeTracker::new(),
        };
        state.visit(tree.root_node(), source, &mut parsed);
        parsed.edges.extend(state.pending_edges);
        parsed
    }
}

struct CFamilyWalk {
    language: &'static str,
    scope: Vec<String>,
    callable_scope: Vec<String>,
    pending_edges: Vec<Edge>,
    /// > 0 when inside a class/struct body.
    class_depth: usize,
    types: ScopeTypeTracker,
}

impl SysVisit for CFamilyWalk {
    fn visit(&mut self, node: Node<'_>, source: &[u8], parsed: &mut ParsedFile) {
        match node.kind() {
            "preproc_include" => {
                if let Some(import) = self.parse_include(node, source) {
                    parsed.imports.push(import);
                }
            }
            "namespace_definition" => {
                let ns_name = self.struct_name(node, source);
                if let Some(ns) = &ns_name {
                    self.scope.push(ns.clone());
                }
                walk(self, node, source, parsed);
                if ns_name.is_some() {
                    self.scope.pop();
                }
            }
            "class_specifier" | "struct_specifier" => match self.parse_type_decl(node, source) {
                Some(sym) => {
                    self.scope.push(sym.name.clone());
                    parsed.symbols.push(sym);
                    self.class_depth += 1;
                    walk(self, node, source, parsed);
                    self.class_depth -= 1;
                    self.scope.pop();
                }
                None => walk(self, node, source, parsed),
            },
            "function_definition" => {
                // Unparsed definitions skip their bodies entirely.
                if let Some(sym) = self.parse_function(node, source) {
                    parsed.symbols.push(sym.clone());
                    self.callable_scope.push(sym.name);
                    self.types.push();
                    self.record_params(node, source);
                    walk(self, node, source, parsed);
                    self.types.pop();
                    self.callable_scope.pop();
                }
            }
            "compound_statement" => {
                self.types.push();
                walk(self, node, source, parsed);
                self.types.pop();
            }
            "call_expression" => {
                if let Some(edge) = self.parse_call(node, source) {
                    parsed.edges.push(edge);
                }
                walk(self, node, source, parsed);
            }
            "declaration" => {
                self.record_declaration(node, source);
                walk(self, node, source, parsed);
            }
            _ => walk(self, node, source, parsed),
        }
    }
}

impl CFamilyWalk {
    /// class_specifier / struct_specifier -> Symbol(class) plus extends edges.
    fn parse_type_decl(&mut self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = self.struct_name(node, source)?;
        for child in children(node) {
            if child.kind() == "base_class_clause" {
                for bc in children(child) {
                    if bc.kind() == "type_identifier" {
                        self.pending_edges.push(Edge {
                            source_name: name.clone(),
                            kind: "extends".to_string(),
                            target_name: text_trimmed(bc, source),
                            line: node.start_position().row + 1,
                            ..Edge::default()
                        });
                    }
                }
            }
        }
        Some(Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name,
            kind: "class".to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            ..Symbol::default()
        })
    }

    /// Function symbol, classified as a method inside a class scope.
    fn parse_function(&self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = child_of_type(node, &["function_declarator"])
            .and_then(|declarator| self.decl_name(declarator, source))
            .or_else(|| self.decl_name(node, source))?;
        let kind = if self.class_depth > 0 {
            "method"
        } else {
            "function"
        };
        Some(Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name,
            kind: kind.to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            arity: self.definition_arity(node, source),
            ..Symbol::default()
        })
    }

    /// Calls edge for identifier, member, scoped, or template callees.
    fn parse_call(&self, node: Node<'_>, source: &[u8]) -> Option<Edge> {
        let callee = node.child_by_field_name("function")?;
        let target = self.extract_callee(callee, source)?;
        Some(Edge {
            source_name: current_edge_owner(&self.callable_scope, &self.scope),
            kind: "calls".to_string(),
            target_name: target,
            line: node.start_position().row + 1,
            receiver_type: self.call_receiver_type(callee, source),
            call_arity: self.call_arity(node),
            ..Edge::default()
        })
    }

    /// Named-child count of the argument_list (parens/commas are anonymous).
    fn call_arity(&self, node: Node<'_>) -> Option<usize> {
        let args = node.child_by_field_name("arguments")?;
        Some(children(args).into_iter().filter(|c| c.is_named()).count())
    }

    fn call_receiver_type(&self, callee: Node<'_>, source: &[u8]) -> Option<String> {
        if callee.kind() == "field_expression" {
            return self.expr_receiver_type(callee.child_by_field_name("argument"), source);
        }
        if callee.kind() == "qualified_identifier"
            && let Some(scope) = callee.child_by_field_name("scope")
        {
            // Lowercase namespaces miss the capitalized-type heuristic.
            return infer_receiver_type(Some(&text_trimmed(scope, source)));
        }
        None
    }

    /// Type of a receiver expression, or None when not inferable.
    fn expr_receiver_type(&self, expr: Option<Node<'_>>, source: &[u8]) -> Option<String> {
        let expr = expr?;
        if expr.kind() == "this" {
            return if self.class_depth > 0 {
                self.scope.last().cloned()
            } else {
                None
            };
        }
        if expr.kind() == "identifier" {
            let name = text_trimmed(expr, source);
            if let Some(tracked) = self.types.resolve(&name) {
                return Some(tracked);
            }
            return infer_receiver_type(Some(&name));
        }
        None
    }

    fn extract_callee(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        if node.kind() == "field_expression" {
            let field = node.child_by_field_name("field")?;
            return Some(text_trimmed(field, source));
        }
        self.callee_name(node, source)
    }

    fn callee_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        match node.kind() {
            "identifier" | "field_identifier" => Some(text_trimmed(node, source)),
            "template_function" | "qualified_identifier" => {
                let name = node.child_by_field_name("name")?;
                self.callee_name(name, source)
            }
            _ => None,
        }
    }

    /// Parameter count of a function_definition; None when not exact.
    fn definition_arity(&self, node: Node<'_>, source: &[u8]) -> Option<usize> {
        let declarator = child_of_type(node, &["function_declarator"])?;
        let params = declarator.child_by_field_name("parameters")?;
        if children(params).iter().any(|c| text(*c, source) == "...") {
            return None;
        }
        let named: Vec<Node<'_>> = children(params)
            .into_iter()
            .filter(|c| c.is_named())
            .collect();
        if named.is_empty() {
            // `()` means zero parameters in C++ but is unspecified in C.
            return if self.language == "cpp" {
                Some(0)
            } else {
                None
            };
        }
        if named.iter().any(|c| c.kind() != "parameter_declaration") {
            // Default arguments or other shapes leave the count inexact.
            return None;
        }
        if named.len() == 1 {
            let type_node = named[0].child_by_field_name("type");
            if named[0].child_by_field_name("declarator").is_none()
                && type_node
                    .map(|t| text_trimmed(t, source) == "void")
                    .unwrap_or(false)
            {
                return Some(0); // `(void)` is zero parameters
            }
        }
        Some(named.len())
    }

    /// Record each typed parameter into the tracker's function scope.
    fn record_params(&mut self, fn_node: Node<'_>, source: &[u8]) {
        let Some(declarator) = child_of_type(fn_node, &["function_declarator"]) else {
            return;
        };
        let Some(params) = declarator.child_by_field_name("parameters") else {
            return;
        };
        for child in children(params) {
            if child.kind() != "parameter_declaration" {
                continue;
            }
            let Some(type_name) = self.type_name_of(child.child_by_field_name("type"), source)
            else {
                continue;
            };
            if let Some(name) =
                self.declared_var_name(child.child_by_field_name("declarator"), source)
            {
                self.types.record(&name, Some(&type_name));
            }
        }
    }

    /// Record each typed local declarator (`Engine a, *b = x;`).
    fn record_declaration(&mut self, node: Node<'_>, source: &[u8]) {
        let type_node = node.child_by_field_name("type");
        let Some(type_name) = self.type_name_of(type_node, source) else {
            return;
        };
        for child in children(node) {
            if child.is_named()
                && Some(child) != type_node
                && let Some(name) = self.declared_var_name(Some(child), source)
            {
                self.types.record(&name, Some(&type_name));
            }
        }
    }

    /// Bare class-like type name; primitives and qualified types stay None.
    fn type_name_of(&self, type_node: Option<Node<'_>>, source: &[u8]) -> Option<String> {
        let node = type_node?;
        match node.kind() {
            "type_identifier" => Some(text_trimmed(node, source)),
            "struct_specifier" | "union_specifier" | "enum_specifier" | "class_specifier" => node
                .child_by_field_name("name")
                .map(|n| text_trimmed(n, source)),
            _ => None,
        }
    }

    /// Identifier bound by a declarator chain (init/pointer/reference/array).
    fn declared_var_name(&self, node: Option<Node<'_>>, source: &[u8]) -> Option<String> {
        let mut current = node;
        while let Some(node) = current {
            match node.kind() {
                "identifier" => return Some(text_trimmed(node, source)),
                "function_declarator" | "function_pointer_declarator" => return None,
                _ => {}
            }
            current = node
                .child_by_field_name("declarator")
                .or_else(|| child_of_type(node, &["identifier"]));
        }
        None
    }

    fn parse_include(&self, node: Node<'_>, source: &[u8]) -> Option<Import> {
        for child in children(node) {
            if matches!(child.kind(), "system_lib_string" | "string_literal") {
                return Some(Import {
                    imported_path: text_trimmed(child, source),
                    line: node.start_position().row + 1,
                    local_alias: None,
                });
            }
        }
        None
    }

    /// First identifier / field_identifier child text.
    fn decl_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        children(node)
            .into_iter()
            .find(|child| matches!(child.kind(), "identifier" | "field_identifier"))
            .map(|child| text_trimmed(child, source))
    }

    /// The type_identifier / namespace_identifier name of a class/struct/namespace.
    fn struct_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        for child in children(node) {
            if matches!(child.kind(), "type_identifier" | "namespace_identifier") {
                return Some(text_trimmed(child, source));
            }
        }
        None
    }
}

// --- C# ---

/// Tree-sitter C# parser.
pub struct CSharpParser;

impl CSharpParser {
    /// Parse the C# file at `path`.
    pub fn parse_file(path: &Path) -> std::io::Result<ParsedFile> {
        let source = std::fs::read(path)?;
        Ok(Self::parse_source(path, &source))
    }

    /// Parse C# `source` as the file at `path`.
    pub fn parse_source(path: &Path, source: &[u8]) -> ParsedFile {
        let mut parsed = parsed_file(path, "csharp", source);
        let mut parser = registry::parser("csharp").expect("csharp grammar is routed");
        let tree = parser.parse(source, None).expect("tree-sitter parse");
        let mut state = CSharpWalk::default();
        state.visit(tree.root_node(), source, &mut parsed);
        parsed.edges.extend(state.pending_edges);
        parsed
    }
}

#[derive(Default)]
struct CSharpWalk {
    scope: Vec<String>,
    callable_scope: Vec<String>,
    pending_edges: Vec<Edge>,
    types: ScopeTypeTracker,
}

impl SysVisit for CSharpWalk {
    fn visit(&mut self, node: Node<'_>, source: &[u8], parsed: &mut ParsedFile) {
        match node.kind() {
            "using_directive" => {
                if let Some(import) = self.parse_using(node, source) {
                    parsed.imports.push(import);
                }
            }
            "namespace_declaration" => {
                // Namespace scopes its body but is not itself a Symbol.
                let ns_name = self.namespace_name(node, source);
                if let Some(ns) = &ns_name {
                    self.scope.push(ns.clone());
                }
                walk(self, node, source, parsed);
                if ns_name.is_some() {
                    self.scope.pop();
                }
            }
            "class_declaration"
            | "interface_declaration"
            | "struct_declaration"
            | "record_declaration" => {
                if let Some(sym) = self.parse_type_decl(node, source) {
                    self.scope.push(sym.name.clone());
                    parsed.symbols.push(sym);
                    self.types.push();
                    walk(self, node, source, parsed);
                    self.types.pop();
                    self.scope.pop();
                }
            }
            "enum_declaration" => {
                if let Some(sym) = self.parse_enum(node, source) {
                    self.scope.push(sym.name.clone());
                    parsed.symbols.push(sym);
                    self.parse_enum_members(node, source, parsed);
                    walk(self, node, source, parsed);
                    self.scope.pop();
                }
            }
            "method_declaration" | "constructor_declaration" => {
                if let Some(sym) = self.parse_method(node, source) {
                    parsed.symbols.push(sym.clone());
                    self.callable_scope.push(sym.name);
                    self.types.push();
                    self.record_parameters(node, source);
                    walk(self, node, source, parsed);
                    self.types.pop();
                    self.callable_scope.pop();
                }
            }
            "property_declaration" => {
                if let Some(sym) = self.parse_property(node, source) {
                    parsed.symbols.push(sym);
                }
                walk(self, node, source, parsed);
            }
            "field_declaration" => {
                self.record_statement_declarations(node, source);
                parsed.symbols.extend(self.parse_fields(node, source));
                walk(self, node, source, parsed);
            }
            "invocation_expression" | "object_creation_expression" => {
                if let Some(edge) = self.parse_call(node, source) {
                    parsed.edges.push(edge);
                }
                walk(self, node, source, parsed);
            }
            "block" => {
                self.types.push();
                walk(self, node, source, parsed);
                self.types.pop();
            }
            "local_declaration_statement" => {
                self.record_statement_declarations(node, source);
                walk(self, node, source, parsed);
            }
            "using_statement" | "for_statement" => {
                // The declared variable is scoped to the statement, body included.
                self.types.push();
                self.record_statement_declarations(node, source);
                walk(self, node, source, parsed);
                self.types.pop();
            }
            "foreach_statement" => {
                self.types.push();
                self.record_foreach(node, source);
                walk(self, node, source, parsed);
                self.types.pop();
            }
            "assignment_expression" => {
                self.record_assignment(node, source);
                walk(self, node, source, parsed);
            }
            _ => walk(self, node, source, parsed),
        }
    }
}

impl CSharpWalk {
    fn parse_type_decl(&mut self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let kind = if node.kind() == "interface_declaration" {
            "interface"
        } else {
            "class"
        };
        let name = self.decl_name(node, source)?;
        for child in children(node) {
            if child.kind() != "base_list" {
                continue;
            }
            // First name is the base class (extends); the rest are interfaces.
            let bases: Vec<Node<'_>> = children(child)
                .into_iter()
                .filter(|c| matches!(c.kind(), "identifier" | "generic_name" | "qualified_name"))
                .collect();
            for (i, base) in bases.iter().enumerate() {
                let edge_kind = if i == 0 && kind == "class" {
                    "extends"
                } else {
                    "implements"
                };
                if let Some(target) = self.type_name(*base, source) {
                    self.pending_edges.push(Edge {
                        source_name: name.clone(),
                        kind: edge_kind.to_string(),
                        target_name: target,
                        line: node.start_position().row + 1,
                        ..Edge::default()
                    });
                }
            }
            break;
        }
        Some(Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name,
            kind: kind.to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: collect_modifiers(node, source),
            ..Symbol::default()
        })
    }

    fn parse_enum(&self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = self.decl_name(node, source)?;
        Some(Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name,
            kind: "enum".to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: collect_modifiers(node, source),
            ..Symbol::default()
        })
    }

    /// Capture enum members as enum_case symbols.
    fn parse_enum_members(&self, node: Node<'_>, source: &[u8], parsed: &mut ParsedFile) {
        for child in children(node) {
            if child.kind() != "enum_member_declaration_list" {
                continue;
            }
            for member in children(child) {
                if member.kind() != "enum_member_declaration" {
                    continue;
                }
                if let Some(mname) = self.decl_name(member, source) {
                    parsed.symbols.push(Symbol {
                        qualified_name: Some(qualified_name(&self.scope, &mname)),
                        name: mname,
                        kind: "enum_case".to_string(),
                        line_start: member.start_position().row + 1,
                        line_end: member.end_position().row + 1,
                        column_start: member.start_position().column,
                        column_end: member.end_position().column,
                        ..Symbol::default()
                    });
                }
            }
        }
    }

    fn parse_method(&self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = self.decl_name(node, source)?;
        Some(Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name,
            kind: "method".to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: collect_modifiers(node, source),
            arity: self.parameter_arity(node, source),
            ..Symbol::default()
        })
    }

    fn parse_property(&self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = self.decl_name(node, source)?;
        Some(Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name,
            kind: "property".to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: collect_modifiers(node, source),
            ..Symbol::default()
        })
    }

    /// field_declaration -> one Symbol per variable_declarator.
    fn parse_fields(&self, node: Node<'_>, source: &[u8]) -> Vec<Symbol> {
        let mut out = Vec::new();
        for var_decl in children(node) {
            if var_decl.kind() != "variable_declaration" {
                continue;
            }
            for vd in children(var_decl) {
                if vd.kind() != "variable_declarator" {
                    continue;
                }
                if let Some(fname) = self.decl_name(vd, source) {
                    out.push(Symbol {
                        qualified_name: Some(qualified_name(&self.scope, &fname)),
                        name: fname,
                        kind: "property".to_string(),
                        line_start: node.start_position().row + 1,
                        line_end: node.end_position().row + 1,
                        column_start: node.start_position().column,
                        column_end: node.end_position().column,
                        modifiers: collect_modifiers(node, source),
                        ..Symbol::default()
                    });
                }
            }
        }
        out
    }

    /// invocation_expression / object_creation_expression -> Edge(calls).
    fn parse_call(&mut self, node: Node<'_>, source: &[u8]) -> Option<Edge> {
        let owner = current_edge_owner(&self.callable_scope, &self.scope);
        let line = node.start_position().row + 1;
        let call_arity = self.argument_arity(node);
        if node.kind() == "object_creation_expression" {
            // new T() -> target is the type name.
            for child in children(node) {
                let target = match child.kind() {
                    "identifier" => Some(text_trimmed(child, source)),
                    "generic_name" => self.extract_generic_name(child, source),
                    _ => None,
                };
                if let Some(target) = target {
                    return Some(Edge {
                        source_name: owner,
                        kind: "calls".to_string(),
                        target_name: target,
                        line,
                        call_arity,
                        ..Edge::default()
                    });
                }
            }
            return None;
        }
        // invocation_expression: callee is the first child.
        let callee_node = children(node).into_iter().next()?;
        let target = self.extract_callee(callee_node, source)?;
        Some(Edge {
            source_name: owner,
            kind: "calls".to_string(),
            target_name: target,
            line,
            receiver_type: self.member_receiver_type(Some(callee_node), source),
            call_arity,
            ..Edge::default()
        })
    }

    fn extract_callee(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        match node.kind() {
            "identifier" => Some(text_trimmed(node, source)),
            // obj.Method -> take the trailing identifier (the method name).
            "member_access_expression" => children(node)
                .into_iter()
                .rev()
                .find(|child| child.kind() == "identifier")
                .map(|child| text_trimmed(child, source)),
            "generic_name" => self.extract_generic_name(node, source),
            _ => None,
        }
    }

    /// generic_name (`List<T>`) -> the leading identifier (`List`).
    fn extract_generic_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        children(node)
            .into_iter()
            .find(|child| child.kind() == "identifier")
            .map(|child| text_trimmed(child, source))
    }

    /// Import for plain, static, and grammar-flattened alias using directives.
    fn parse_using(&self, node: Node<'_>, source: &[u8]) -> Option<Import> {
        let line = node.start_position().row + 1;
        let mut idents = Vec::new();
        let mut qualified = Vec::new();
        let is_alias = children(node).iter().any(|c| c.kind() == "=");
        for child in children(node) {
            match child.kind() {
                "identifier" => idents.push(child),
                "qualified_name" => qualified.push(child),
                _ => {}
            }
        }
        if !is_alias {
            let name_node = if !qualified.is_empty() {
                &qualified
            } else {
                &idents
            };
            let first = *name_node.first()?;
            return Some(Import {
                imported_path: text_trimmed(first, source),
                line,
                local_alias: None,
            });
        }
        if idents.is_empty() {
            return None;
        }
        let target = if !qualified.is_empty() {
            text_trimmed(qualified[0], source)
        } else if idents.len() > 1 {
            text_trimmed(idents[1], source)
        } else {
            return None;
        };
        Some(Import {
            imported_path: target,
            line,
            local_alias: Some(text_trimmed(idents[0], source)),
        })
    }

    /// First identifier child text.
    fn decl_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        children(node)
            .into_iter()
            .find(|child| child.kind() == "identifier")
            .map(|child| text_trimmed(child, source))
    }

    /// Namespace name from a namespace_declaration.
    fn namespace_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        for child in children(node) {
            if matches!(child.kind(), "qualified_name" | "identifier") {
                return Some(text_trimmed(child, source));
            }
        }
        None
    }

    fn record_statement_declarations(&mut self, node: Node<'_>, source: &[u8]) {
        for child in children(node) {
            if child.kind() == "variable_declaration" {
                self.record_variable_declaration(child, source);
            }
        }
    }

    fn record_variable_declaration(&mut self, var_decl: Node<'_>, source: &[u8]) {
        let kids = children(var_decl);
        let Some(&type_node) = kids.first() else {
            return;
        };
        for child in kids.iter().skip(1) {
            if child.kind() != "variable_declarator" {
                continue;
            }
            if let Some(name) = self.decl_name(*child, source) {
                let declared = self.declared_type(Some(type_node), *child, source);
                self.types.record(&name, declared.as_deref());
            }
        }
    }

    fn record_parameters(&mut self, node: Node<'_>, source: &[u8]) {
        for child in children(node) {
            if child.kind() != "parameter_list" {
                continue;
            }
            for p in children(child) {
                if p.kind() != "parameter" {
                    continue;
                }
                let ids: Vec<Node<'_>> = children(p)
                    .into_iter()
                    .filter(|c| c.kind() == "identifier")
                    .collect();
                let Some(&last) = ids.last() else {
                    continue;
                };
                let type_node = children(p)
                    .into_iter()
                    .find(|c| c.is_named() && c.kind() != "modifier" && *c != last);
                if let Some(t) = type_node {
                    let name = text_trimmed(last, source);
                    let type_name = self.type_name(t, source);
                    self.types.record(&name, type_name.as_deref());
                }
            }
        }
    }

    fn record_foreach(&mut self, node: Node<'_>, source: &[u8]) {
        let named: Vec<Node<'_>> = children(node)
            .into_iter()
            .filter(|c| c.is_named() && c.kind() != "block")
            .collect();
        if named.len() < 2 || named[1].kind() != "identifier" {
            return;
        }
        if named[0].kind() == "implicit_type" {
            return;
        }
        let name = text_trimmed(named[1], source);
        let type_name = self.type_name(named[0], source);
        self.types.record(&name, type_name.as_deref());
    }

    fn record_assignment(&mut self, node: Node<'_>, source: &[u8]) {
        let Some(lhs) = children(node).into_iter().next() else {
            return;
        };
        if lhs.kind() != "identifier" {
            return;
        }
        let Some(value) = self.initializer_node(node) else {
            return;
        };
        let name = text_trimmed(lhs, source);
        match value.kind() {
            "object_creation_expression" => {
                let type_name = self.creation_type_name(value, source);
                self.types.record(&name, type_name.as_deref());
            }
            "identifier" => {
                let resolved = self.types.resolve(&text_trimmed(value, source));
                self.types.record(&name, resolved.as_deref());
            }
            _ => {}
        }
    }

    /// First named child after a plain `=` token, if any.
    fn initializer_node<'a>(&self, node: Node<'a>) -> Option<Node<'a>> {
        let mut seen_eq = false;
        for child in children(node) {
            if child.kind() == "=" {
                seen_eq = true;
            } else if seen_eq && child.is_named() {
                return Some(child);
            }
        }
        None
    }

    fn declared_type(
        &self,
        type_node: Option<Node<'_>>,
        declarator: Node<'_>,
        source: &[u8],
    ) -> Option<String> {
        if let Some(t) = type_node
            && t.kind() != "implicit_type"
        {
            return self.type_name(t, source);
        }
        // `var` -- the initializer decides; anything else is unknown.
        let value = self.initializer_node(declarator)?;
        match value.kind() {
            "object_creation_expression" => self.creation_type_name(value, source),
            "identifier" => self.types.resolve(&text_trimmed(value, source)),
            _ => None,
        }
    }

    fn creation_type_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        children(node)
            .into_iter()
            .find(|c| matches!(c.kind(), "identifier" | "generic_name" | "qualified_name"))
            .and_then(|c| self.type_name(c, source))
    }

    /// Bare type name for a type node (`List<T>`/`A.B`/`T?`/`T[]`).
    fn type_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        match node.kind() {
            "identifier" | "predefined_type" => Some(text_trimmed(node, source)),
            "generic_name" => self.decl_name(node, source),
            "qualified_name" => children(node)
                .into_iter()
                .rfind(|c| c.is_named())
                .and_then(|c| self.type_name(c, source)),
            "array_type" | "nullable_type" | "pointer_type" => children(node)
                .into_iter()
                .find(|c| c.is_named())
                .and_then(|c| self.type_name(c, source)),
            _ => None,
        }
    }

    /// Receiver type for `recv.Member(...)` calls, else None.
    fn member_receiver_type(&self, callee_node: Option<Node<'_>>, source: &[u8]) -> Option<String> {
        let callee = callee_node?;
        if callee.kind() != "member_access_expression" {
            return None;
        }
        let expr = callee.child_by_field_name("expression")?;
        if expr.kind() != "identifier" {
            return None;
        }
        let name = text_trimmed(expr, source);
        if let Some(resolved) = self.types.resolve(&name) {
            return Some(resolved);
        }
        infer_receiver_type(Some(&name))
    }

    /// Parameter count, or None when a fixed count cannot match calls.
    fn parameter_arity(&self, node: Node<'_>, source: &[u8]) -> Option<usize> {
        for child in children(node) {
            if child.kind() != "parameter_list" {
                continue;
            }
            let kids = children(child);
            let params: Vec<&Node<'_>> = kids.iter().filter(|c| c.kind() == "parameter").collect();
            // An inlined `params` array parameter has no `parameter` wrapper.
            if kids.iter().any(|c| c.is_named() && c.kind() != "parameter") {
                return None;
            }
            for p in &params {
                for m in children(**p) {
                    if m.kind() == "="
                        || m.kind() == "params"
                        || (m.kind() == "modifier" && text_trimmed(m, source) == "this")
                    {
                        return None;
                    }
                }
            }
            return Some(params.len());
        }
        None
    }

    fn argument_arity(&self, node: Node<'_>) -> Option<usize> {
        for child in children(node) {
            if child.kind() == "argument_list" {
                return Some(
                    children(child)
                        .into_iter()
                        .filter(|c| c.kind() == "argument")
                        .count(),
                );
            }
        }
        None
    }
}

/// Modifier keywords of a declaration node.
fn collect_modifiers(node: Node<'_>, source: &[u8]) -> Vec<String> {
    let mut mods = Vec::new();
    for child in children(node) {
        if child.kind() != "modifier" {
            continue;
        }
        for m in children(child) {
            let txt = text_trimmed(m, source);
            if !txt.is_empty() && txt != "modifier" {
                mods.push(txt);
            }
        }
    }
    mods
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use serde_json::json;

    use super::*;

    fn golden_dir() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../tests/fixtures/golden")
    }

    fn go(source: &str) -> ParsedFile {
        GoParser::parse_source(Path::new("sample.go"), source.as_bytes())
    }

    fn c(source: &str) -> ParsedFile {
        CFamilyParser::c().parse_source(Path::new("sample.c"), source.as_bytes())
    }

    fn cpp(source: &str) -> ParsedFile {
        CFamilyParser::cpp().parse_source(Path::new("sample.cpp"), source.as_bytes())
    }

    fn cs(source: &str) -> ParsedFile {
        CSharpParser::parse_source(Path::new("sample.cs"), source.as_bytes())
    }

    fn parse_for(lang: &str, path: &Path, source: &[u8]) -> ParsedFile {
        match lang {
            "go" => GoParser::parse_source(path, source),
            "rust" => parse_rust_source(path, source),
            "c" => CFamilyParser::c().parse_source(path, source),
            "cpp" => CFamilyParser::cpp().parse_source(path, source),
            "csharp" => CSharpParser::parse_source(path, source),
            other => unreachable!("unsupported golden language {other}"),
        }
    }

    /// Same shape as the parser-golden expected.json schema.
    fn normalise(parsed: &ParsedFile) -> serde_json::Value {
        let mut symbols: Vec<_> = parsed
            .symbols
            .iter()
            .map(|s| {
                let mut modifiers = s.modifiers.clone();
                modifiers.sort();
                (
                    (s.kind.clone(), s.name.clone(), s.line_start, s.column_start),
                    json!({
                        "name": s.name,
                        "kind": s.kind,
                        "qualified_name": s.qualified_name,
                        "line_start": s.line_start,
                        "line_end": s.line_end,
                        "column_start": s.column_start,
                        "column_end": s.column_end,
                        "docstring": s.docstring,
                        "modifiers": modifiers,
                        "metadata": s.metadata,
                        "arity": s.arity,
                    }),
                )
            })
            .collect();
        symbols.sort_by(|a, b| a.0.cmp(&b.0));

        let mut edges: Vec<_> = parsed
            .edges
            .iter()
            .map(|e| {
                (
                    (
                        e.source_name.clone(),
                        e.kind.clone(),
                        e.target_name.clone(),
                        e.line,
                        e.column,
                    ),
                    json!({
                        "source_name": e.source_name,
                        "kind": e.kind,
                        "target_name": e.target_name,
                        "line": e.line,
                        "column": e.column,
                        "call_arity": e.call_arity,
                    }),
                )
            })
            .collect();
        edges.sort_by(|a, b| a.0.cmp(&b.0));

        let mut imports: Vec<_> = parsed
            .imports
            .iter()
            .map(|i| {
                (
                    (i.imported_path.clone(), i.line),
                    json!({
                        "imported_path": i.imported_path,
                        "line": i.line,
                        "local_alias": i.local_alias,
                    }),
                )
            })
            .collect();
        imports.sort_by(|a, b| a.0.cmp(&b.0));

        json!({
            "language": parsed.language,
            "symbols": symbols.into_iter().map(|(_, v)| v).collect::<Vec<_>>(),
            "edges": edges.into_iter().map(|(_, v)| v).collect::<Vec<_>>(),
            "imports": imports.into_iter().map(|(_, v)| v).collect::<Vec<_>>(),
        })
    }

    #[test]
    fn golden_output_matches_python_fixtures() {
        let cases = [
            ("go", "sample.go"),
            ("rust", "sample.rs"),
            ("c", "sample.c"),
            ("cpp", "sample.cpp"),
            ("csharp", "sample.cs"),
        ];
        for (lang, file) in cases {
            let path = golden_dir().join(lang).join(file);
            let source = std::fs::read(&path).unwrap_or_else(|e| panic!("{lang}: {e}"));
            let parsed = parse_for(lang, &path, &source);
            let expected = std::fs::read_to_string(golden_dir().join(lang).join("expected.json"))
                .unwrap_or_else(|e| panic!("{lang}: {e}"));
            let expected: serde_json::Value = serde_json::from_str(&expected).unwrap();
            assert_eq!(
                normalise(&parsed),
                expected,
                "{lang} parser output diverged from the committed golden"
            );
        }
    }

    fn call_edge<'a>(parsed: &'a ParsedFile, target: &str) -> &'a Edge {
        let matches: Vec<&Edge> = parsed
            .edges
            .iter()
            .filter(|e| e.kind == "calls" && e.target_name == target)
            .collect();
        assert_eq!(matches.len(), 1, "expected one '{target}' calls edge");
        matches[0]
    }

    fn symbol<'a>(parsed: &'a ParsedFile, name: &str) -> &'a Symbol {
        let matches: Vec<&Symbol> = parsed.symbols.iter().filter(|s| s.name == name).collect();
        assert_eq!(matches.len(), 1, "expected one '{name}' symbol");
        matches[0]
    }

    #[test]
    fn scope_type_tracker_poisons_conflicts_until_scope_pops() {
        let mut tracker = ScopeTypeTracker::new();
        tracker.record("p", Some("CB"));
        assert_eq!(tracker.resolve("p"), Some("CB".into()));
        tracker.record("p", Some("CB"));
        assert_eq!(tracker.resolve("p"), Some("CB".into()));
        tracker.push();
        tracker.record("p", Some("Other"));
        assert_eq!(tracker.resolve("p"), None);
        tracker.pop();
        assert_eq!(tracker.resolve("p"), Some("CB".into()));
        tracker.record("p", Some("Third"));
        assert_eq!(tracker.resolve("p"), None);
        tracker.record("", Some("X"));
        tracker.record("n", Some(""));
        assert_eq!(tracker.resolve("n"), None);
        assert_eq!(tracker.resolve("never"), None);
    }

    #[test]
    fn go_method_carries_receiver_parent_scope_and_arity() {
        let parsed = go(
            "package m\n\ntype Server struct{}\n\nfunc (s *Server) Handle(req string, n int) {}\n",
        );
        let handle = symbol(&parsed, "Handle");
        assert_eq!(handle.kind, "method");
        assert_eq!(handle.parent_scope.as_deref(), Some("Server"));
        assert_eq!(handle.arity, Some(2));

        let variadic =
            go("package m\n\ntype Server struct{}\n\nfunc (s *Server) V(rest ...int) {}\n");
        assert_eq!(symbol(&variadic, "V").arity, None);
    }

    #[test]
    fn go_call_receivers_follow_capitalization_and_spread_abstains() {
        let parsed = go("package m\n\nfunc use() { Server.New() }\n");
        assert_eq!(
            call_edge(&parsed, "New").receiver_type.as_deref(),
            Some("Server")
        );

        let lower =
            go("package m\n\ntype Server struct{}\n\nfunc use() { s := &Server{}; s.Handle() }\n");
        assert_eq!(call_edge(&lower, "Handle").receiver_type, None);

        let pkg = go("package m\n\nfunc use() { fmt.Println(\"a\", \"b\") }\n");
        let edge = call_edge(&pkg, "Println");
        assert_eq!(edge.receiver_type, None);
        assert_eq!(edge.call_arity, Some(2));

        let spread = go("package m\n\nfunc use() {\n\txs := []int{1, 2}\n\tf(xs...)\n}\n");
        assert_eq!(call_edge(&spread, "f").call_arity, None);
    }

    #[test]
    fn go_imports_record_only_binding_aliases() {
        let parsed = go(
            "package m\n\nimport (\n\t\"context\"\n\tx \"example.com/x\"\n\t_ \"example.com/blank\"\n)\n",
        );
        let imports: Vec<(String, Option<String>)> = parsed
            .imports
            .iter()
            .map(|i| (i.imported_path.clone(), i.local_alias.clone()))
            .collect();
        assert_eq!(
            imports,
            vec![
                ("context".into(), None),
                ("example.com/x".into(), Some("x".into())),
                ("example.com/blank".into(), None),
            ]
        );
    }

    #[test]
    fn c_definition_arity_table() {
        let parsed = c(
            "int add(int a, int b) { return a + b; }\nint h(int a, ...) {}\nint k(void) {}\nint m() {}\n",
        );
        assert_eq!(symbol(&parsed, "add").arity, Some(2));
        assert_eq!(symbol(&parsed, "h").arity, None);
        assert_eq!(symbol(&parsed, "k").arity, Some(0));
        assert_eq!(symbol(&parsed, "m").arity, None);
    }

    #[test]
    fn c_member_receiver_tracks_params_and_recovers_after_shadow() {
        let parsed = c("void run(struct CB *p) {\n  p->cb(1);\n}\n");
        let edge = call_edge(&parsed, "cb");
        assert_eq!(edge.receiver_type.as_deref(), Some("CB"));
        assert_eq!(edge.call_arity, Some(1));

        let shadowed = c(
            "void run(struct CB *p) {\n  if (1) { struct Other p; p->shadowed(1); }\n  p->outer(2);\n}\n",
        );
        assert_eq!(call_edge(&shadowed, "shadowed").receiver_type, None);
        assert_eq!(
            call_edge(&shadowed, "outer").receiver_type.as_deref(),
            Some("CB")
        );
    }

    #[test]
    fn cpp_this_and_qualified_call_receivers() {
        let parsed = cpp("class Bot {\npublic:\n    void go() { this->step(1); }\n};\n");
        let edge = call_edge(&parsed, "step");
        assert_eq!(edge.receiver_type.as_deref(), Some("Bot"));
        assert_eq!(edge.call_arity, Some(1));

        let qualified = cpp("void run() {\n  Math::abs(-1);\n  app::helper(1, 2);\n}\n");
        assert_eq!(
            call_edge(&qualified, "abs").receiver_type.as_deref(),
            Some("Math")
        );
        let helper = call_edge(&qualified, "helper");
        assert_eq!(helper.receiver_type, None);
        assert_eq!(helper.call_arity, Some(2));
    }

    #[test]
    fn cpp_definition_arity_table() {
        let parsed = cpp(
            "int add(int a, int b) {}\nint g(int a, int b = 0) {}\nint h(int a, ...) {}\nint k(void) {}\nint m() {}\n",
        );
        assert_eq!(symbol(&parsed, "add").arity, Some(2));
        assert_eq!(symbol(&parsed, "g").arity, None);
        assert_eq!(symbol(&parsed, "h").arity, None);
        assert_eq!(symbol(&parsed, "k").arity, Some(0));
        assert_eq!(symbol(&parsed, "m").arity, Some(0));
    }

    #[test]
    fn csharp_var_local_receiver_comes_from_constructor() {
        let parsed = cs(
            "class C {\n    void Go() {\n        var c = new Client();\n        c.Send(1);\n    }\n}\n",
        );
        assert_eq!(
            call_edge(&parsed, "Send").receiver_type.as_deref(),
            Some("Client")
        );
    }

    #[test]
    fn csharp_reassignment_to_different_type_poisons() {
        let parsed = cs(
            "class C {\n    void Go() {\n        var c = new Client();\n        c = new Other();\n        c.Send();\n    }\n}\n",
        );
        assert_eq!(call_edge(&parsed, "Send").receiver_type, None);
    }
}
