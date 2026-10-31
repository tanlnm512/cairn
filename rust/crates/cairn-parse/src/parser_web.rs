//! Web-family parsers: TypeScript (including the TSX grammar route) and JavaScript.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use tree_sitter::Node;

use crate::generic::children;
use crate::model::{Edge, Import, ParsedFile, Symbol, sha256_hex};
use crate::rationale;
use crate::registry;

const TS_JS_MODIFIERS: &[&str] = &[
    "public",
    "private",
    "protected",
    "static",
    "readonly",
    "abstract",
    "async",
    "override",
    "declare",
];

// Node types that declare a named type containing members (class-like).
const TYPE_DECL_NODES: &[&str] = &[
    "class_declaration",
    "abstract_class_declaration",
    "interface_declaration",
];

// Decorators are children of their target declaration in the TS grammar.
const DECL_NODES_WITH_OWN_DECORATORS: &[&str] = &[
    "class_declaration",
    "abstract_class_declaration",
    "interface_declaration",
    "method_definition",
    "function_declaration",
    "public_field_definition",
];

// Node types treated as top-level-or-nested variable declarations.
const VAR_DECL_NODES: &[&str] = &["lexical_declaration", "variable_declaration"];

// Extensions tried when resolving a relative import to a file on disk.
const RESOLUTION_EXTS: &[&str] = &["", ".ts", ".tsx", ".d.ts", ".js", ".jsx"];

/// Parser language the scanner routes for `suffix`; registers the tsx/jsx
/// variants (.ts/.tsx/.mts/.cts -> typescript, .js/.jsx/.mjs/.cjs -> javascript).
pub fn language_for_suffix(suffix: &str) -> Option<&'static str> {
    match suffix {
        "ts" | "tsx" | "mts" | "cts" => Some("typescript"),
        "js" | "jsx" | "mjs" | "cjs" => Some("javascript"),
        _ => None,
    }
}

/// Existing extension-stripped path for a relative TS import, else None.
pub fn resolve_relative_import(importer: &Path, spec: &str) -> Option<String> {
    if !spec.starts_with('.') {
        return None; // package import (e.g. "react") -> left unresolved/external
    }
    let joined = importer.parent().map(|parent| parent.join(spec))?;
    let base = resolve_existing_prefix(&joined);
    for candidate_base in [base.clone(), base.join("index")] {
        for ext in RESOLUTION_EXTS {
            let candidate = format!("{}{ext}", candidate_base.display());
            if Path::new(&candidate).is_file() {
                return Some(candidate_base.to_string_lossy().into_owned());
            }
        }
    }
    None
}

/// Path.resolve(strict=False) equivalent: absolute with symlinks resolved in
/// the existing prefix and a lexical tail for missing components.
fn resolve_existing_prefix(path: &Path) -> PathBuf {
    if let Ok(resolved) = path.canonicalize() {
        return resolved;
    }
    match (path.parent(), path.file_name()) {
        (Some(parent), Some(name)) => {
            let mut resolved = resolve_existing_prefix(parent);
            resolved.push(name);
            resolved
        }
        _ => path.to_path_buf(),
    }
}

/// Shared traversal for the TypeScript/TSX/JavaScript grammars.
pub struct WebParser {
    language: &'static str,
}

impl WebParser {
    pub fn typescript() -> Self {
        Self {
            language: "typescript",
        }
    }

    pub fn javascript() -> Self {
        Self {
            language: "javascript",
        }
    }

    /// Parser for the file's suffix, or None for non-web files.
    pub fn for_path(path: &Path) -> Option<Self> {
        let suffix = path.extension()?.to_str()?;
        Some(match language_for_suffix(suffix)? {
            "typescript" => Self::typescript(),
            _ => Self::javascript(),
        })
    }

    pub fn language(&self) -> &'static str {
        self.language
    }

    /// Registry language whose grammar parses `path` (.tsx picks the TSX grammar).
    fn grammar_language(&self, path: &Path) -> Option<&'static str> {
        match self.language {
            "typescript" => Some(if path.extension()? == "tsx" {
                "tsx"
            } else {
                "typescript"
            }),
            "javascript" => Some("javascript"),
            _ => None,
        }
    }

    /// Parse the file at `path` with the web-family traversal.
    pub fn parse_file(&self, path: &Path) -> std::io::Result<ParsedFile> {
        let source = std::fs::read(path)?;
        Ok(self.parse_source(path, &source))
    }

    /// Parse `source` with the web-family traversal.
    pub fn parse_source(&self, path: &Path, source: &[u8]) -> ParsedFile {
        let grammar = self
            .grammar_language(path)
            .unwrap_or_else(|| panic!("{} grammar is routed", self.language));
        let mut parser = registry::parser(grammar).expect("web grammar is routed");
        let tree = parser.parse(source, None).expect("tree-sitter parse");
        let mut parsed = ParsedFile {
            path: path.to_string_lossy().into_owned(),
            language: self.language.to_string(),
            hash: sha256_hex(source),
            line_count: source.iter().filter(|&&b| b == b'\n').count() + 1,
            ..ParsedFile::default()
        };
        let mut walk = Walk {
            language: self.language,
            source,
            path,
            file_stem: path
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default(),
            scope: Vec::new(),
            callable_scope: Vec::new(),
            func_depth: 0,
            pending_decorators: Vec::new(),
            pending_edges: Vec::new(),
            types: ScopeTypeTracker::new(),
        };
        walk.visit(tree.root_node(), &mut parsed);
        parsed.edges.append(&mut walk.pending_edges);
        parsed
    }
}

struct Walk<'a> {
    language: &'static str,
    source: &'a [u8],
    path: &'a Path,
    file_stem: String,
    scope: Vec<String>,
    callable_scope: Vec<String>,
    func_depth: usize,
    pending_decorators: Vec<String>,
    pending_edges: Vec<Edge>,
    types: ScopeTypeTracker,
}

impl Walk<'_> {
    fn visit(&mut self, node: Node<'_>, parsed: &mut ParsedFile) {
        let kind = node.kind();

        if kind == "import_statement" {
            if let Some(imp) = self.parse_import(node) {
                parsed.imports.push(imp);
            }
            return; // don't descend; nothing else useful inside
        }

        if kind == "decorator" {
            // Target-owned decorators bypass the pending queue; parameter decorators are dropped.
            let parent_kind = node.parent().map(|p| p.kind().to_string());
            match parent_kind.as_deref() {
                Some("required_parameter" | "optional_parameter") => return,
                Some(parent) if DECL_NODES_WITH_OWN_DECORATORS.contains(&parent) => return,
                _ => {}
            }
            self.pending_decorators
                .push(self.node_text(node).trim().into());
            return;
        }

        if TYPE_DECL_NODES.contains(&kind) {
            if let Some(sym) = self.parse_type_decl(node) {
                let name = sym.name.clone();
                parsed.symbols.push(sym);
                self.scope.push(name);
                self.walk(node, parsed);
                self.scope.pop();
            }
            return;
        }

        if kind == "type_alias_declaration" {
            if let Some(sym) = self.parse_simple_decl(node, "type", &["type_identifier"]) {
                parsed.symbols.push(sym);
            }
            return;
        }

        if kind == "enum_declaration" {
            if let Some(sym) = self.parse_simple_decl(node, "enum", &["identifier"]) {
                parsed.symbols.push(sym);
            }
            return;
        }

        if kind == "internal_module" {
            if let Some(sym) =
                self.parse_simple_decl(node, "namespace", &["identifier", "nested_identifier"])
            {
                let name = sym.name.clone();
                parsed.symbols.push(sym);
                self.scope.push(name);
                self.walk(node, parsed);
                self.scope.pop();
            }
            return;
        }

        if kind == "function_declaration" || kind == "method_definition" {
            let sym_kind = if kind == "function_declaration" {
                "function"
            } else {
                "method"
            };
            if let Some(sym) = self.parse_function(node, sym_kind) {
                let name = sym.name.clone();
                parsed.symbols.push(sym);
                self.callable_scope.push(name);
                self.func_depth += 1;
                self.enter_function_scope(node);
                self.walk(node, parsed);
                self.types.pop();
                self.func_depth -= 1;
                self.callable_scope.pop();
            }
            return;
        }

        if kind == "public_field_definition" {
            if let Some(sym) = self.parse_field(node) {
                parsed.symbols.push(sym);
            }
            // Field initializers can emit calls without opening a function scope.
            self.walk(node, parsed);
            return;
        }

        if VAR_DECL_NODES.contains(&kind) {
            self.handle_var_decl(node, parsed);
            return;
        }

        if kind == "arrow_function" || kind == "function_expression" {
            // Anonymous function (e.g. a callback argument): not a named
            // symbol, but its body is no longer "top-level" for var purposes.
            self.func_depth += 1;
            self.enter_function_scope(node);
            self.walk(node, parsed);
            self.types.pop();
            self.func_depth -= 1;
            return;
        }

        if kind == "statement_block" {
            // Block scope for let/const: recorded var types pop with the block.
            self.types.push();
            self.walk(node, parsed);
            self.types.pop();
            return;
        }

        if kind == "call_expression" || kind == "new_expression" {
            if let Some(edge) = self.parse_call(node) {
                parsed.edges.push(edge);
            }
            self.walk(node, parsed);
            return;
        }

        // Opening JSX elements emit one component reference; closing tags do not.
        if kind == "jsx_opening_element" || kind == "jsx_self_closing_element" {
            if let Some(edge) = self.parse_jsx_ref(node) {
                parsed.edges.push(edge);
            }
            self.walk(node, parsed);
            return;
        }

        if kind == "assignment_expression" {
            // `x = new T()` re-binds x; a conflicting recorded type poisons
            // the name to unknown for receiver inference.
            self.record_assignment(node);
        }

        self.walk(node, parsed);
    }

    fn walk(&mut self, node: Node<'_>, parsed: &mut ParsedFile) {
        for child in children(node) {
            if self.rationale_from_comment(child, parsed) {
                continue;
            }
            self.visit(child, parsed);
        }
    }

    fn rationale_from_comment(&self, node: Node<'_>, parsed: &mut ParsedFile) -> bool {
        let comment_types = rationale::comment_node_types(self.language);
        if !comment_types.contains(&node.kind()) {
            return false;
        }
        if let Some(record) = rationale::extract_rationale(node, self.source) {
            parsed.rationale.push(record);
        }
        true
    }

    // --- name & modifier helpers -----------------------------------------

    fn find_name(&self, node: Node<'_>, types: &[&str]) -> Option<String> {
        children(node)
            .into_iter()
            .find(|child| types.contains(&child.kind()))
            .map(|child| self.node_text(child).trim().to_string())
    }

    fn collect_modifiers(&self, node: Node<'_>) -> Vec<String> {
        let mut mods = Vec::new();
        for child in children(node) {
            if child.kind() == "accessibility_modifier" {
                mods.push(self.node_text(child).trim().to_string());
            } else {
                let txt = self.node_text(child).trim().to_string();
                if TS_JS_MODIFIERS.contains(&txt.as_str()) {
                    mods.push(txt);
                }
            }
        }
        mods
    }

    fn qualified_name(&self, name: &str) -> String {
        let mut parts = vec![self.file_stem.clone()];
        parts.extend(self.scope.iter().cloned());
        parts.push(name.to_string());
        parts.join(".")
    }

    fn take_pending_decorators(&mut self) -> Vec<String> {
        std::mem::take(&mut self.pending_decorators)
    }

    fn own_decorators(&self, node: Node<'_>) -> Vec<String> {
        children(node)
            .into_iter()
            .filter(|c| c.kind() == "decorator")
            .map(|c| self.node_text(c).trim().to_string())
            .collect()
    }

    // --- declarations ------------------------------------------------------

    fn parse_type_decl(&mut self, node: Node<'_>) -> Option<Symbol> {
        let mut decorators = self.take_pending_decorators();
        decorators.extend(self.own_decorators(node));
        let name = self.find_name(node, &["type_identifier", "identifier"])?;
        let kind = if node.kind() == "interface_declaration" {
            "interface"
        } else {
            "class"
        };
        let mut mods = self.collect_modifiers(node);
        mods.extend(decorators);
        let symbol = Symbol {
            name: name.clone(),
            kind: kind.to_string(),
            qualified_name: Some(self.qualified_name(&name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: mods,
            ..Symbol::default()
        };
        self.parse_heritage(node, &name);
        Some(symbol)
    }

    fn parse_simple_decl(&self, node: Node<'_>, kind: &str, name_types: &[&str]) -> Option<Symbol> {
        let name = self.find_name(node, name_types)?;
        Some(Symbol {
            name: name.clone(),
            kind: kind.to_string(),
            qualified_name: Some(self.qualified_name(&name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            ..Symbol::default()
        })
    }

    fn parse_function(&mut self, node: Node<'_>, kind: &str) -> Option<Symbol> {
        let mut decorators = self.take_pending_decorators();
        decorators.extend(self.own_decorators(node));
        let name_types: &[&str] = if node.kind() == "method_definition" {
            &["property_identifier"]
        } else {
            &["identifier"]
        };
        let name = self.find_name(node, name_types)?;
        let mut mods = self.collect_modifiers(node);
        mods.extend(decorators);
        Some(Symbol {
            name: name.clone(),
            kind: kind.to_string(),
            qualified_name: Some(self.qualified_name(&name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: mods,
            arity: self.callable_arity(node),
            ..Symbol::default()
        })
    }

    fn parse_field(&mut self, node: Node<'_>) -> Option<Symbol> {
        let mut decorators = self.take_pending_decorators();
        decorators.extend(self.own_decorators(node));
        let name = self.find_name(node, &["property_identifier"])?;
        let mut mods = self.collect_modifiers(node);
        mods.extend(decorators);
        Some(Symbol {
            name: name.clone(),
            kind: "property".to_string(),
            qualified_name: Some(self.qualified_name(&name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: mods,
            ..Symbol::default()
        })
    }

    fn handle_var_decl(&mut self, node: Node<'_>, parsed: &mut ParsedFile) {
        let is_top = self.func_depth == 0;
        for child in children(node) {
            if child.kind() != "variable_declarator" {
                continue;
            }
            let mut name: Option<String> = None;
            let mut pattern: Option<Node<'_>> = None;
            let mut value: Option<Node<'_>> = None;
            let mut seen_eq = false;
            for c in children(child) {
                if c.kind() == "=" {
                    seen_eq = true;
                } else if seen_eq {
                    if value.is_none() {
                        value = Some(c);
                    }
                } else if c.kind() == "identifier" && name.is_none() {
                    name = Some(self.node_text(c).trim().to_string());
                } else if pattern.is_none() && c.is_named() {
                    pattern = Some(c);
                }
            }
            let is_func_value = value
                .map(|v| v.kind() == "arrow_function" || v.kind() == "function_expression")
                .unwrap_or(false);
            self.record_variable(name.as_deref(), child);
            if is_func_value {
                let value = value.expect("func value");
                if is_top && let Some(n) = name.clone() {
                    parsed.symbols.push(Symbol {
                        name: n.clone(),
                        kind: "function".to_string(),
                        qualified_name: Some(self.qualified_name(&n)),
                        line_start: child.start_position().row + 1,
                        line_end: child.end_position().row + 1,
                        column_start: child.start_position().column,
                        column_end: child.end_position().column,
                        arity: self.callable_arity(value),
                        ..Symbol::default()
                    });
                    self.callable_scope.push(n);
                }
                self.func_depth += 1;
                self.enter_function_scope(value);
                self.walk(value, parsed);
                self.types.pop();
                self.func_depth -= 1;
                if is_top && name.is_some() {
                    self.callable_scope.pop();
                }
            } else {
                if is_top {
                    if let Some(n) = &name {
                        self.push_variable_symbol(n, child, parsed);
                    }
                    if let Some(pattern) = pattern {
                        let mut bindings = Vec::new();
                        self.pattern_bindings(pattern, &mut bindings);
                        for (n, binding) in bindings {
                            self.push_variable_symbol(&n, binding, parsed);
                        }
                    }
                }
                if let Some(v) = value {
                    // Visit initializer nodes directly so their own expression type emits an edge.
                    self.visit(v, parsed);
                }
            }
        }
    }

    /// Push a variable symbol for `name` declared at `node`.
    fn push_variable_symbol(&self, name: &str, node: Node<'_>, parsed: &mut ParsedFile) {
        parsed.symbols.push(Symbol {
            name: name.to_string(),
            kind: "variable".to_string(),
            qualified_name: Some(self.qualified_name(name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            ..Symbol::default()
        });
    }

    /// Bound (name, node) pairs of a destructuring pattern: shorthand,
    /// renamed, rest, defaulted, and nested sub-patterns.
    fn pattern_bindings<'a>(&self, node: Node<'a>, out: &mut Vec<(String, Node<'a>)>) {
        match node.kind() {
            "identifier" | "shorthand_property_identifier_pattern" => {
                out.push((self.node_text(node).trim().to_string(), node));
            }
            "pair_pattern" => {
                if let Some(value) = node.child_by_field_name("value") {
                    self.pattern_bindings(value, out);
                }
            }
            "assignment_pattern" | "object_assignment_pattern" => {
                if let Some(left) = node.child_by_field_name("left") {
                    self.pattern_bindings(left, out);
                }
            }
            "object_pattern" | "array_pattern" | "rest_pattern" => {
                for c in children(node) {
                    if c.is_named() {
                        self.pattern_bindings(c, out);
                    }
                }
            }
            _ => {}
        }
    }

    fn parse_import(&mut self, node: Node<'_>) -> Option<Import> {
        let mut spec: Option<String> = None;
        let mut alias: Option<String> = None;
        for child in children(node) {
            if child.kind() == "string" {
                for gc in children(child) {
                    if gc.kind() == "string_fragment" {
                        spec = Some(self.node_text(gc).trim().to_string());
                    }
                }
                break;
            }
            if child.kind() == "import_clause" {
                alias = self.import_clause_alias(child);
            }
        }
        let spec = spec?;
        let resolved = resolve_relative_import(self.path, &spec);
        Some(Import {
            imported_path: resolved.unwrap_or(spec),
            line: node.start_position().row + 1,
            local_alias: alias,
        })
    }

    // --- edges ---------------------------------------------------------

    fn heritage_target_name(&self, node: Node<'_>) -> Option<String> {
        if node.kind() == "identifier" || node.kind() == "type_identifier" {
            return Some(self.node_text(node).trim().to_string());
        }
        if node.kind() == "generic_type" {
            return node
                .child_by_field_name("name")
                .map(|inner| self.node_text(inner).trim().to_string());
        }
        None
    }

    fn parse_heritage(&mut self, node: Node<'_>, owner: &str) {
        for child in children(node) {
            match child.kind() {
                "class_heritage" => {
                    let mut found_wrapper = false;
                    for hchild in children(child) {
                        match hchild.kind() {
                            "extends_clause" => {
                                found_wrapper = true;
                                self.emit_heritage_edges(hchild, "extends", owner, node);
                            }
                            "implements_clause" => {
                                found_wrapper = true;
                                self.emit_heritage_edges(hchild, "implements", owner, node);
                            }
                            _ => {}
                        }
                    }
                    if !found_wrapper {
                        // JS grammar: class_heritage's own children are directly
                        // 'extends' + identifier, with no wrapper node.
                        self.emit_heritage_edges(child, "extends", owner, node);
                    }
                }
                "extends_type_clause" => {
                    self.emit_heritage_edges(child, "extends", owner, node);
                }
                _ => {}
            }
        }
    }

    fn emit_heritage_edges(&mut self, clause: Node<'_>, kind: &str, owner: &str, decl: Node<'_>) {
        for c in children(clause) {
            if let Some(name) = self.heritage_target_name(c) {
                self.pending_edges.push(Edge {
                    source_name: owner.to_string(),
                    kind: kind.to_string(),
                    target_name: name,
                    line: decl.start_position().row + 1,
                    ..Edge::default()
                });
            }
        }
    }

    fn parse_call(&self, node: Node<'_>) -> Option<Edge> {
        let kids = children(node);
        if kids.is_empty() {
            return None;
        }
        // call_expression: callee is the first child. new_expression: first
        // child is the literal 'new' keyword, callee is the second.
        let callee_idx = usize::from(node.kind() == "new_expression");
        let callee = kids.get(callee_idx)?;
        let target = self.extract_callee(*callee)?;
        let receiver = if node.kind() == "call_expression" && callee.kind() == "member_expression" {
            self.member_receiver_type(*callee)
        } else {
            None
        };
        Some(Edge {
            source_name: self.current_edge_owner(),
            kind: "calls".to_string(),
            target_name: target,
            line: node.start_position().row + 1,
            receiver_type: receiver,
            call_arity: self.call_arity(node),
            ..Edge::default()
        })
    }

    // --- receiver-type & arity signals -----------------------------------

    fn enter_function_scope(&mut self, fn_node: Node<'_>) {
        self.types.push();
        self.record_params(fn_node);
    }

    fn record_params(&mut self, fn_node: Node<'_>) {
        // Record annotated function parameters (TS) into the fresh scope.
        let Some(params) = fn_node.child_by_field_name("parameters") else {
            return;
        };
        if params.kind() != "formal_parameters" {
            return;
        }
        for child in children(params) {
            if child.kind() != "required_parameter" && child.kind() != "optional_parameter" {
                continue;
            }
            let pattern = child.child_by_field_name("pattern");
            let annotation = child.child_by_field_name("type");
            let (Some(pattern), Some(annotation)) = (pattern, annotation) else {
                continue;
            };
            if pattern.kind() != "identifier" {
                continue;
            }
            let name = self.node_text(pattern).trim().to_string();
            let type_name = self.annotation_type_name(annotation);
            self.types.record(&name, type_name);
        }
    }

    fn record_variable(&mut self, name: Option<&str>, declarator: Node<'_>) {
        // Record a declarator's type: annotation first, else the constructed
        // type of a ``new`` initializer.
        let Some(name) = name.filter(|n| !n.is_empty()) else {
            return;
        };
        if let Some(annotation) = declarator.child_by_field_name("type") {
            let type_name = self.annotation_type_name(annotation);
            self.types.record(name, type_name);
            return;
        }
        if let Some(value) = declarator.child_by_field_name("value")
            && value.kind() == "new_expression"
        {
            let type_name = self.new_callee_name(value);
            self.types.record(name, type_name);
        }
    }

    fn record_assignment(&mut self, node: Node<'_>) {
        let left = node.child_by_field_name("left");
        let right = node.child_by_field_name("right");
        let (Some(left), Some(right)) = (left, right) else {
            return;
        };
        if left.kind() != "identifier" {
            return;
        }
        if right.kind() == "new_expression" {
            let name = self.node_text(left).trim().to_string();
            let type_name = self.new_callee_name(right);
            self.types.record(&name, type_name);
        }
    }

    fn new_callee_name(&self, new_node: Node<'_>) -> Option<String> {
        let ctor = new_node.child_by_field_name("constructor")?;
        self.extract_callee(ctor)
    }

    fn annotation_type_name(&self, annotation: Node<'_>) -> Option<String> {
        // Bare type name of a type_annotation, or None for non-simple shapes
        // (arrays, unions, ...) whose members would not resolve to the named type.
        let mut type_node = None;
        for child in children(annotation) {
            if child.is_named() {
                type_node = Some(child);
                break;
            }
        }
        let mut type_node = type_node?;
        if type_node.kind() == "nested_type_identifier" || type_node.kind() == "generic_type" {
            type_node = type_node.child_by_field_name("name")?;
        }
        if type_node.kind() == "type_identifier" {
            return Some(self.node_text(type_node).trim().to_string());
        }
        None
    }

    fn member_receiver_type(&self, member: Node<'_>) -> Option<String> {
        // Return a member-call receiver type for tracked, this, or static objects.
        let obj = member.child_by_field_name("object")?;
        if obj.kind() == "identifier" {
            let name = self.node_text(obj).trim().to_string();
            return match self.types.resolve(&name) {
                Some(t) => Some(t),
                None => infer_receiver_type(&name),
            };
        }
        if obj.kind() == "this" {
            return self.scope.last().cloned();
        }
        None
    }

    fn formal_arity(&self, params: Node<'_>) -> Option<usize> {
        // Formal parameter count; None when any parameter is optional,
        // defaulted, or rest (no single arity to match a call against).
        let mut count = 0usize;
        for child in children(params) {
            if !child.is_named() {
                continue;
            }
            match child.kind() {
                "optional_parameter" | "rest_pattern" | "assignment_pattern" => return None,
                "required_parameter" => {
                    if child.child_by_field_name("value").is_some() {
                        return None;
                    }
                    if let Some(pattern) = child.child_by_field_name("pattern")
                        && pattern.kind() == "rest_pattern"
                    {
                        return None;
                    }
                }
                _ => {}
            }
            count += 1;
        }
        Some(count)
    }

    fn callable_arity(&self, fn_node: Node<'_>) -> Option<usize> {
        // Arity of a function/method/function-expression definition node.
        if let Some(params) = fn_node.child_by_field_name("parameters") {
            return self.formal_arity(params);
        }
        if fn_node.child_by_field_name("parameter").is_some() {
            return Some(1); // arrow shorthand: `x => ...`
        }
        None
    }

    fn call_arity(&self, node: Node<'_>) -> Option<usize> {
        // Argument count at a call/new site; None when the argument list is
        // absent or contains a spread.
        let args = node.child_by_field_name("arguments")?;
        let mut count = 0usize;
        for child in children(args) {
            if !child.is_named() {
                continue;
            }
            if child.kind() == "spread_element" {
                return None;
            }
            count += 1;
        }
        Some(count)
    }

    fn import_clause_alias(&self, clause: Node<'_>) -> Option<String> {
        // Return the sole local alias when an import has exactly one binding.
        let mut aliases: Vec<String> = Vec::new();
        for child in children(clause) {
            match child.kind() {
                "namespace_import" => {
                    for c in children(child) {
                        if c.kind() == "identifier" {
                            aliases.push(self.node_text(c).trim().to_string());
                        }
                    }
                }
                "named_imports" => {
                    for spec in children(child) {
                        if spec.kind() != "import_specifier" {
                            continue;
                        }
                        if let Some(alias) = spec.child_by_field_name("alias") {
                            aliases.push(self.node_text(alias).trim().to_string());
                        }
                    }
                }
                _ => {}
            }
        }
        if aliases.len() == 1 {
            aliases.pop()
        } else {
            None
        }
    }

    fn extract_callee(&self, node: Node<'_>) -> Option<String> {
        if node.kind() == "identifier" {
            return Some(self.node_text(node).trim().to_string());
        }
        if node.kind() == "member_expression" {
            for child in children(node).into_iter().rev() {
                if child.kind() == "property_identifier" {
                    return Some(self.node_text(child).trim().to_string());
                }
            }
        }
        None
    }

    fn parse_jsx_ref(&self, node: Node<'_>) -> Option<Edge> {
        // Return a component reference Edge for capitalized JSX tag names.
        let name_node = node.child_by_field_name("name")?;
        let name: Option<String> = match name_node.kind() {
            "identifier" => Some(self.node_text(name_node).trim().to_string()),
            // <UI.Card/> -> resolve to the property name "Card" (matches how
            // extract_callee treats member_expression calls, and what the
            // resolver's bare-name index expects).
            "member_expression" => self.extract_callee(name_node),
            // <foo:Bar/> -> take the trailing identifier.
            "jsx_namespace_name" => children(name_node)
                .into_iter()
                .rev()
                .find(|c| c.kind() == "identifier")
                .map(|c| self.node_text(c).trim().to_string()),
            _ => return None,
        };
        let name =
            name.filter(|n| !n.is_empty() && n.chars().next().is_some_and(|c| c.is_uppercase()))?; // HTML host tag or unrecognizable name shape
        Some(Edge {
            source_name: self.current_edge_owner(),
            kind: "references".to_string(),
            target_name: name,
            line: node.start_position().row + 1,
            ..Edge::default()
        })
    }

    fn current_edge_owner(&self) -> String {
        self.callable_scope
            .last()
            .or_else(|| self.scope.last())
            .cloned()
            .unwrap_or_default()
    }

    fn node_text(&self, node: Node<'_>) -> String {
        String::from_utf8_lossy(&self.source[node.start_byte()..node.end_byte()]).into_owned()
    }
}

/// Return a receiver text only when its capitalized shape looks like a type.
fn infer_receiver_type(receiver_text: &str) -> Option<String> {
    if receiver_text.is_empty() {
        return None;
    }
    // Heuristic: a capitalized leading char suggests a type, not a package.
    receiver_text
        .chars()
        .next()
        .is_some_and(|c| c.is_uppercase())
        .then(|| receiver_text.to_string())
}

/// Scope-ordered variable to type tracker for receiver-type inference.
struct ScopeTypeTracker {
    // Innermost scope last. None marks a name whose recorded types conflict:
    // it resolves to None ("ambiguous"), distinct from "never recorded".
    scopes: Vec<HashMap<String, Option<String>>>,
}

impl ScopeTypeTracker {
    fn new() -> Self {
        Self {
            scopes: vec![HashMap::new()],
        }
    }

    fn push(&mut self) {
        self.scopes.push(HashMap::new());
    }

    fn pop(&mut self) {
        if self.scopes.len() > 1 {
            self.scopes.pop();
        }
    }

    fn record(&mut self, name: &str, type_name: Option<String>) {
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
                    .expect("root scope never popped")
                    .insert(name.to_string(), Some(type_name));
            }
            Some(existing)
                if existing.is_none() || existing.as_deref() == Some(type_name.as_str()) => {}
            Some(_) => {
                self.scopes
                    .last_mut()
                    .expect("root scope never popped")
                    .insert(name.to_string(), None);
            }
        }
    }

    fn resolve(&self, name: &str) -> Option<String> {
        for scope in self.scopes.iter().rev() {
            if let Some(entry) = scope.get(name) {
                return entry.clone();
            }
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse_ts(source: &str) -> ParsedFile {
        WebParser::typescript().parse_source(Path::new("sample.ts"), source.as_bytes())
    }

    fn parse_js(source: &str) -> ParsedFile {
        WebParser::javascript().parse_source(Path::new("sample.js"), source.as_bytes())
    }

    fn parse_tsx(source: &str) -> ParsedFile {
        WebParser::typescript().parse_source(Path::new("sample.tsx"), source.as_bytes())
    }

    fn symbol_tuples(parsed: &ParsedFile) -> Vec<(String, String, String, Option<usize>)> {
        parsed
            .symbols
            .iter()
            .map(|s| {
                (
                    s.name.clone(),
                    s.kind.clone(),
                    s.qualified_name.clone().expect("qualified"),
                    s.arity,
                )
            })
            .collect()
    }

    fn call_tuples(parsed: &ParsedFile) -> Vec<(String, String, Option<String>, Option<usize>)> {
        parsed
            .edges
            .iter()
            .filter(|e| e.kind == "calls")
            .map(|e| {
                (
                    e.source_name.clone(),
                    e.target_name.clone(),
                    e.receiver_type.clone(),
                    e.call_arity,
                )
            })
            .collect()
    }

    #[test]
    fn routes_web_suffixes() {
        for (suffix, language) in [
            ("ts", "typescript"),
            ("tsx", "typescript"),
            ("mts", "typescript"),
            ("cts", "typescript"),
            ("js", "javascript"),
            ("jsx", "javascript"),
            ("mjs", "javascript"),
            ("cjs", "javascript"),
        ] {
            assert_eq!(language_for_suffix(suffix), Some(language), "{suffix}");
        }
        assert_eq!(language_for_suffix("py"), None);
        assert!(WebParser::for_path(Path::new("src/App.tsx")).is_some());
        assert!(WebParser::for_path(Path::new("src/App.rs")).is_none());
        assert!(WebParser::for_path(Path::new("notes.md")).is_none());
    }

    #[test]
    fn tsx_files_route_to_the_tsx_grammar() {
        // JSX tags only produce jsx_* nodes under the TSX grammar.
        let parsed = parse_tsx("const el = <Widget name=\"x\" />;\n");
        assert_eq!(parsed.language, "typescript");
        let refs: Vec<&str> = parsed
            .edges
            .iter()
            .filter(|e| e.kind == "references")
            .map(|e| e.target_name.as_str())
            .collect();
        assert_eq!(refs, vec!["Widget"]);
    }

    #[test]
    fn function_valued_top_level_vars_become_function_symbols() {
        let source = r#"
const makeCounter = (start: number) => {
    const bump = () => start + 1;
    return bump();
};
const limit = 10;
"#;
        let parsed = parse_ts(source);
        assert_eq!(
            symbol_tuples(&parsed),
            vec![
                (
                    "makeCounter".into(),
                    "function".into(),
                    "sample.makeCounter".into(),
                    Some(1)
                ),
                (
                    "limit".into(),
                    "variable".into(),
                    "sample.limit".into(),
                    None
                ),
            ]
        );
        // The nested `bump()` call is owned by makeCounter.
        assert_eq!(
            call_tuples(&parsed),
            vec![("makeCounter".into(), "bump".into(), None, Some(0))]
        );
    }

    #[test]
    fn destructuring_object_patterns_bind_pattern_names_not_the_rhs() {
        let parsed = parse_js("const { useState } = React;\n");
        assert_eq!(
            symbol_tuples(&parsed),
            vec![(
                "useState".into(),
                "variable".into(),
                "sample.useState".into(),
                None
            )]
        );

        let rest = parse_js("let { a, ...rest } = cfg;\n");
        assert_eq!(
            symbol_tuples(&rest),
            vec![
                ("a".into(), "variable".into(), "sample.a".into(), None),
                ("rest".into(), "variable".into(), "sample.rest".into(), None),
            ]
        );

        let renamed = parse_ts("const { width: w, opts: { depth: d } } = cfg;\n");
        assert_eq!(
            symbol_tuples(&renamed),
            vec![
                ("w".into(), "variable".into(), "sample.w".into(), None),
                ("d".into(), "variable".into(), "sample.d".into(), None),
            ]
        );

        let scoped = parse_ts("function run(cfg) {\n    const { a } = cfg;\n}\n");
        assert_eq!(
            symbol_tuples(&scoped),
            vec![(
                "run".into(),
                "function".into(),
                "sample.run".into(),
                Some(1)
            )]
        );
    }

    #[test]
    fn destructuring_array_patterns_bind_elements_and_defaults() {
        let parsed = parse_js("const [x] = arr;\n");
        assert_eq!(
            symbol_tuples(&parsed),
            vec![("x".into(), "variable".into(), "sample.x".into(), None)]
        );

        let defaults = parse_js("const [x, y = z, ...rest] = arr;\n");
        assert_eq!(
            symbol_tuples(&defaults),
            vec![
                ("x".into(), "variable".into(), "sample.x".into(), None),
                ("y".into(), "variable".into(), "sample.y".into(), None),
                ("rest".into(), "variable".into(), "sample.rest".into(), None),
            ]
        );

        let nested = parse_js("const [p, { q }] = pair;\n");
        assert_eq!(
            symbol_tuples(&nested),
            vec![
                ("p".into(), "variable".into(), "sample.p".into(), None),
                ("q".into(), "variable".into(), "sample.q".into(), None),
            ]
        );
    }

    #[test]
    fn namespaces_scope_qualified_names_and_simple_decls_emit_symbols() {
        let source = r#"
namespace Util {
    export function helper(): void {}
}
type Dict = Record<string, number>;
enum Color { Red, Green }
"#;
        let parsed = parse_ts(source);
        assert_eq!(
            symbol_tuples(&parsed),
            vec![
                (
                    "Util".into(),
                    "namespace".into(),
                    "sample.Util".into(),
                    None
                ),
                (
                    "helper".into(),
                    "function".into(),
                    "sample.Util.helper".into(),
                    Some(0)
                ),
                ("Dict".into(), "type".into(), "sample.Dict".into(), None),
                ("Color".into(), "enum".into(), "sample.Color".into(), None),
            ]
        );
    }

    #[test]
    fn annotated_and_constructed_receivers_resolve_to_types() {
        let source = r#"
class Repo {
    ping(): void {}
    saveAll(): void {
        this.ping();
    }
}
function run(repo: Repo) {
    repo.ping();
}
const cached = new Repo();
cached.ping();
"#;
        let parsed = parse_ts(source);
        let pings: Vec<(String, Option<String>)> = parsed
            .edges
            .iter()
            .filter(|e| e.kind == "calls" && e.target_name == "ping")
            .map(|e| (e.source_name.clone(), e.receiver_type.clone()))
            .collect();
        assert_eq!(
            pings,
            vec![
                ("saveAll".into(), Some("Repo".into())),
                ("run".into(), Some("Repo".into())),
                // Top-level call sites have no enclosing symbol to own them.
                ("".into(), Some("Repo".into())),
            ]
        );
    }

    #[test]
    fn conflicting_rebind_poisons_receiver_type() {
        let source = r#"
class Repo { ping(): void {} }
class Other { ping(): void {} }
let client = new Repo();
client = new Other();
client.ping();
"#;
        let parsed = parse_ts(source);
        let call = parsed
            .edges
            .iter()
            .find(|e| e.kind == "calls" && e.target_name == "ping")
            .expect("call edge");
        assert_eq!(call.source_name, "");
        assert_eq!(call.receiver_type, None);
    }

    #[test]
    fn arity_is_none_when_parameters_or_arguments_are_ambiguous() {
        let source = r#"
function opt(a?: number): void {}
function rest(...args: number[]): void {}
function defaulted(a: number, b: number = 2): void {}
function add(a: number, b: number): number {
    return a + b;
}
const items = [1, 2];
spread(...items);
const thing = new Thing(1);
"#;
        let parsed = parse_ts(source);
        let arities: Vec<(String, Option<usize>)> = parsed
            .symbols
            .iter()
            .filter(|s| s.kind == "function")
            .map(|s| (s.name.clone(), s.arity))
            .collect();
        assert_eq!(
            arities,
            vec![
                ("opt".into(), None),
                ("rest".into(), None),
                ("defaulted".into(), None),
                ("add".into(), Some(2)),
            ]
        );
        let spread_call = parsed
            .edges
            .iter()
            .find(|e| e.target_name == "spread")
            .expect("spread call");
        assert_eq!(spread_call.call_arity, None);
        let new_call = parsed
            .edges
            .iter()
            .find(|e| e.target_name == "Thing")
            .expect("new call");
        assert_eq!(new_call.kind, "calls");
        assert_eq!(new_call.call_arity, Some(1));
    }

    #[test]
    fn jsx_tags_emit_capitalized_component_references_once() {
        let source = r#"
export function Page() {
    return (
        <div>
            <Card title="hi" />
            <UI.Card />
            <Card />
        </div>
    );
}
"#;
        let parsed = parse_tsx(source);
        let refs: Vec<(String, String)> = parsed
            .edges
            .iter()
            .filter(|e| e.kind == "references")
            .map(|e| (e.source_name.clone(), e.target_name.clone()))
            .collect();
        // Host tag `div` is skipped; each opening/self-closing tag emits once;
        // member tag resolves to Card.
        assert_eq!(
            refs,
            vec![
                ("Page".into(), "Card".into()),
                ("Page".into(), "Card".into()),
                ("Page".into(), "Card".into()),
            ]
        );
    }

    #[test]
    fn javascript_grammar_parses_jsx_tags() {
        let source = "const el = <Widget name=\"x\" />;\n";
        let parsed = parse_js(source);
        assert_eq!(parsed.language, "javascript");
        assert_eq!(symbol_tuples(&parsed)[0].1, "variable");
        let refs: Vec<&str> = parsed
            .edges
            .iter()
            .filter(|e| e.kind == "references")
            .map(|e| e.target_name.as_str())
            .collect();
        assert_eq!(refs, vec!["Widget"]);
    }

    #[test]
    fn import_aliases_and_package_specs_survive_import_parsing() {
        let source =
            "import defaultExport, { a as aliasA } from './mod';\nimport * as ns from 'pkg';\n";
        let parsed = parse_ts(source);
        let imports: Vec<(String, Option<String>)> = parsed
            .imports
            .iter()
            .map(|i| (i.imported_path.clone(), i.local_alias.clone()))
            .collect();
        // './mod' does not exist on disk in this test's cwd-relative space.
        assert_eq!(
            imports,
            vec![
                ("./mod".into(), Some("aliasA".into())),
                ("pkg".into(), Some("ns".into())),
            ]
        );
    }

    #[test]
    fn resolves_relative_imports_against_the_filesystem() {
        let dir = std::env::temp_dir().join(format!("cairn-web-parse-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("pkg")).expect("mkdir");
        std::fs::write(dir.join("mod.ts"), "export const a = 1;\n").expect("write mod");
        std::fs::write(dir.join("pkg/index.ts"), "export const b = 2;\n").expect("write index");
        let importer = dir.join("src.ts");
        std::fs::write(
            &importer,
            "import { a } from './mod';\nimport { b } from './pkg';\nimport { c } from 'external';\n",
        )
        .expect("write importer");
        let parsed = WebParser::typescript()
            .parse_file(&importer)
            .expect("parse");
        let imports: Vec<String> = parsed
            .imports
            .iter()
            .map(|i| i.imported_path.clone())
            .collect();
        // Resolved bases are absolute and symlink-resolved, like Path.resolve().
        let resolved_dir = dir.canonicalize().expect("resolved temp dir");
        assert_eq!(
            imports,
            vec![
                resolved_dir.join("mod").to_string_lossy().into_owned(),
                // index resolution keeps the /index suffix.
                resolved_dir
                    .join("pkg/index")
                    .to_string_lossy()
                    .into_owned(),
                "external".into(),
            ]
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rationale_comments_are_collected_for_web_languages() {
        let parsed = parse_ts("// NOTE: keep in sync with the schema\nconst x = 1;\n");
        assert_eq!(parsed.rationale.len(), 1);
        assert_eq!(parsed.rationale[0].kind, "note");
        assert_eq!(parsed.rationale[0].text, "keep in sync with the schema");
    }
}
