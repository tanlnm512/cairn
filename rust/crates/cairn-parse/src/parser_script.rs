//! Script-family parsers: python, php, ruby, dart.

use std::collections::HashMap;
use std::ffi::OsStr;
use std::path::{Component, Path, PathBuf};

use tree_sitter::Node;

use crate::model::{Edge, Import, ParsedFile, Symbol, sha256_hex};
use crate::rationale;
use crate::registry;

// --- Shared helpers (mirror TreeSitterParserBase) ---

const BODY_MAX_CHARS: usize = 1500;

fn children(node: Node<'_>) -> Vec<Node<'_>> {
    let mut cursor = node.walk();
    node.children(&mut cursor).collect()
}

fn node_text(node: Node<'_>, source: &[u8]) -> String {
    String::from_utf8_lossy(&source[node.start_byte()..node.end_byte()]).into_owned()
}

fn child_of_type<'a>(node: Node<'a>, types: &[&str]) -> Option<Node<'a>> {
    children(node)
        .into_iter()
        .find(|child| types.contains(&child.kind()))
}

fn find_name(node: Node<'_>, source: &[u8], types: &[&str]) -> Option<String> {
    children(node)
        .into_iter()
        .find(|child| types.contains(&child.kind()))
        .map(|child| node_text(child, source).trim().to_string())
}

fn qualified_name(scope: &[String], name: &str) -> String {
    if scope.is_empty() {
        return name.to_string();
    }
    let mut parts = Vec::with_capacity(scope.len() + 1);
    parts.extend(scope.iter().cloned());
    parts.push(name.to_string());
    parts.join(".")
}

fn current_edge_owner(callable_scope: &[String], scope: &[String]) -> String {
    callable_scope
        .last()
        .or_else(|| scope.last())
        .cloned()
        .unwrap_or_default()
}

/// Receiver text only when its capitalized shape looks like a type.
fn infer_receiver_type(receiver: Option<&str>) -> Option<String> {
    let receiver = receiver?;
    receiver
        .chars()
        .next()
        .is_some_and(|c| c.is_uppercase())
        .then(|| receiver.to_string())
}

/// Bounded implementation body without its signature or docstring.
fn extract_body(node: Node<'_>, source: &[u8], block_types: &[&str]) -> Option<String> {
    let block = children(node)
        .into_iter()
        .find(|c| block_types.contains(&c.kind()))?;
    let mut stmts: Vec<String> = Vec::new();
    let mut skipped_docstring = false;
    for stmt in children(block) {
        if !skipped_docstring
            && stmt.kind() == "expression_statement"
            && children(stmt).iter().any(|c| c.kind() == "string")
        {
            skipped_docstring = true;
            continue;
        }
        skipped_docstring = true;
        let text = node_text(stmt, source).trim().to_string();
        if !text.is_empty() {
            stmts.push(text);
        }
    }
    if stmts.is_empty() {
        return None;
    }
    let mut body = stmts.join("\n");
    if body.chars().count() > BODY_MAX_CHARS {
        body = body.chars().take(BODY_MAX_CHARS).collect();
    }
    Some(body)
}

fn rationale_from_comment(
    language: &str,
    node: Node<'_>,
    source: &[u8],
    pf: &mut ParsedFile,
) -> bool {
    if !rationale::comment_node_types(language).contains(&node.kind()) {
        return false;
    }
    if let Some(record) = rationale::extract_rationale(node, source) {
        pf.rationale.push(record);
    }
    true
}

/// [A-Za-z_][A-Za-z0-9_]* fullmatch.
fn is_ident(text: &str) -> bool {
    let mut chars = text.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphabetic() || c == '_' => {}
        _ => return false,
    }
    chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// Identifier tokens in text, left to right ([A-Za-z_][A-Za-z0-9_]*).
fn find_identifiers(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i].is_ascii_alphabetic() || bytes[i] == b'_' {
            let start = i;
            i += 1;
            while i < bytes.len() && (bytes[i].is_ascii_alphanumeric() || bytes[i] == b'_') {
                i += 1;
            }
            out.push(&text[start..i]);
        } else {
            i += 1;
        }
    }
    out
}

/// Scope-ordered variable to type tracker for receiver-type inference.
#[derive(Default)]
struct ScopeTypeTracker {
    /// Innermost scope last; None marks a name whose recorded types conflict.
    scopes: Vec<HashMap<String, Option<String>>>,
}

impl ScopeTypeTracker {
    fn new() -> Self {
        Self {
            scopes: vec![HashMap::new()],
        }
    }

    fn reset(&mut self) {
        self.scopes = vec![HashMap::new()];
    }

    fn push(&mut self) {
        self.scopes.push(HashMap::new());
    }

    fn pop(&mut self) {
        if self.scopes.len() > 1 {
            self.scopes.pop();
        }
    }

    /// Record a scope type, making conflicting visible types ambiguous.
    fn record(&mut self, name: &str, type_name: &str) {
        if name.is_empty() || type_name.is_empty() {
            return;
        }
        let visible = self.scopes.iter().rev().find_map(|s| s.get(name).cloned());
        match visible {
            None => {
                self.scopes
                    .last_mut()
                    .expect("root scope")
                    .insert(name.to_string(), Some(type_name.to_string()));
            }
            Some(None) => {}
            Some(Some(existing)) if existing == type_name => {}
            Some(Some(_)) => {
                self.scopes
                    .last_mut()
                    .expect("root scope")
                    .insert(name.to_string(), None);
            }
        }
    }

    /// Innermost visible type for `name`, else None (unknown/ambiguous).
    fn resolve(&self, name: &str) -> Option<String> {
        self.scopes
            .iter()
            .rev()
            .find_map(|s| s.get(name).cloned())
            .flatten()
    }
}

// --- Python ---

/// Annotation tokens that never name a repo symbol.
const PY_BUILTIN_TYPE_TOKENS: &[&str] = &[
    "any",
    "bool",
    "bytes",
    "callable",
    "class",
    "cls",
    "dict",
    "final",
    "float",
    "frozenset",
    "int",
    "iterable",
    "list",
    "mapping",
    "none",
    "optional",
    "self",
    "sequence",
    "set",
    "str",
    "tuple",
    "type",
    "typing",
    "union",
];

pub struct PythonParser {
    scope: Vec<String>,
    pending_edges: Vec<Edge>,
    types: ScopeTypeTracker,
}

impl Default for PythonParser {
    fn default() -> Self {
        Self::new()
    }
}

impl PythonParser {
    pub fn new() -> Self {
        Self {
            scope: Vec::new(),
            pending_edges: Vec::new(),
            types: ScopeTypeTracker::new(),
        }
    }

    /// Parse the python file at `path`.
    pub fn parse(&mut self, path: &Path) -> std::io::Result<ParsedFile> {
        let source = std::fs::read(path)?;
        Ok(self.parse_source(path, &source))
    }

    /// Parse in-memory python `source` as if read from `path`.
    pub fn parse_source(&mut self, path: &Path, source: &[u8]) -> ParsedFile {
        let mut parser = registry::parser("python").expect("python grammar is routed");
        let tree = parser.parse(source, None).expect("tree-sitter parse");
        let mut pf = ParsedFile {
            path: path.to_string_lossy().into_owned(),
            language: "python".to_string(),
            hash: sha256_hex(source),
            line_count: source.iter().filter(|&&b| b == b'\n').count() + 1,
            ..ParsedFile::default()
        };
        self.pending_edges.clear();
        self.scope.clear();
        self.types.reset();
        let root = tree.root_node();
        self.walk(root, source, &mut pf, &[]);
        pf.edges.extend(std::mem::take(&mut self.pending_edges));
        pf
    }

    fn walk<'a>(
        &mut self,
        node: Node<'a>,
        source: &[u8],
        pf: &mut ParsedFile,
        decorators: &[Node<'a>],
    ) {
        for child in children(node) {
            if rationale_from_comment("python", child, source, pf) {
                continue;
            }
            self.visit(child, source, pf, decorators);
        }
    }

    fn visit<'a>(
        &mut self,
        node: Node<'a>,
        source: &[u8],
        pf: &mut ParsedFile,
        decorators: &[Node<'a>],
    ) {
        match node.kind() {
            "import_statement" | "import_from_statement" => {
                pf.imports.extend(self.parse_imports(node, source));
            }
            "decorated_definition" => {
                let wrapped: Vec<Node<'a>> = children(node)
                    .into_iter()
                    .filter(|c| c.kind() == "decorator")
                    .collect();
                self.walk(node, source, pf, &wrapped);
            }
            "decorator" => {}
            "class_definition" => {
                if let Some(sym) = self.parse_class(node, source, decorators) {
                    let name = sym.name.clone();
                    pf.symbols.push(sym);
                    self.scope.push(name.clone());
                    self.types.record(&name, &name);
                    self.types.push();
                    self.walk(node, source, pf, decorators);
                    self.types.pop();
                    self.scope.pop();
                }
            }
            "function_definition" => {
                if let Some(sym) = self.parse_function(node, source, decorators) {
                    let name = sym.name.clone();
                    pf.symbols.push(sym);
                    let owning_class = if self.is_class_owned(node) {
                        self.scope.last().cloned()
                    } else {
                        None
                    };
                    self.scope.push(name);
                    self.types.push();
                    self.record_function_bindings(
                        node,
                        source,
                        owning_class.as_deref(),
                        decorators,
                    );
                    self.walk(node, source, pf, decorators);
                    self.types.pop();
                    self.scope.pop();
                }
            }
            "assignment" => {
                self.record_assignment(node, source);
                self.walk(node, source, pf, decorators);
            }
            "call" => {
                if let Some(edge) = self.parse_call(node, source) {
                    pf.edges.push(edge);
                }
                self.walk(node, source, pf, decorators);
            }
            _ => self.walk(node, source, pf, decorators),
        }
    }

    fn parse_class(
        &mut self,
        node: Node<'_>,
        source: &[u8],
        decorators: &[Node<'_>],
    ) -> Option<Symbol> {
        let name = find_name(node, source, &["identifier"])?;
        for child in children(node) {
            if child.kind() != "argument_list" {
                continue;
            }
            for arg in children(child) {
                if arg.kind() == "identifier" || arg.kind() == "dotted_name" {
                    self.pending_edges.push(Edge {
                        source_name: name.clone(),
                        kind: "extends".to_string(),
                        target_name: node_text(arg, source).trim().to_string(),
                        line: node.start_position().row + 1,
                        ..Edge::default()
                    });
                }
            }
        }
        self.emit_decorator_edges(node, source, &name, decorators);
        let doc = py_docstring(node, source);
        Some(Symbol {
            kind: "class".to_string(),
            qualified_name: Some(qualified_name(&self.scope, &name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            docstring: doc,
            modifiers: self.collect_decorators(node, source, decorators),
            body: extract_body(node, source, &["block", "body_block"]),
            name,
            ..Symbol::default()
        })
    }

    fn parse_function(
        &mut self,
        node: Node<'_>,
        source: &[u8],
        decorators: &[Node<'_>],
    ) -> Option<Symbol> {
        let name = find_name(node, source, &["identifier"])?;
        let kind = if self.is_class_owned(node) {
            "method"
        } else {
            "function"
        };
        self.emit_decorator_edges(node, source, &name, decorators);
        self.emit_type_references(node, source, &name);
        let mut mods = self.collect_decorators(node, source, decorators);
        for child in children(node) {
            if node_text(child, source).trim() == "async" {
                mods.push("async".to_string());
                break;
            }
        }
        let doc = py_docstring(node, source);
        Some(Symbol {
            kind: kind.to_string(),
            qualified_name: Some(qualified_name(&self.scope, &name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            docstring: doc,
            modifiers: mods,
            body: extract_body(node, source, &["block", "body_block"]),
            arity: self.count_parameters(node, source, decorators),
            name,
            ..Symbol::default()
        })
    }

    fn decorator_nodes<'a>(&self, node: Node<'a>, decorators: &'a [Node<'a>]) -> Vec<Node<'a>> {
        let mut nodes = decorators.to_vec();
        nodes.extend(
            children(node)
                .into_iter()
                .filter(|c| c.kind() == "decorator"),
        );
        nodes
    }

    fn collect_decorators(
        &self,
        node: Node<'_>,
        source: &[u8],
        decorators: &[Node<'_>],
    ) -> Vec<String> {
        self.decorator_nodes(node, decorators)
            .into_iter()
            .map(|child| node_text(child, source).trim().to_string())
            .collect()
    }

    /// Emit decorates edges named by each decorator's callable tail.
    fn emit_decorator_edges(
        &mut self,
        node: Node<'_>,
        source: &[u8],
        owner: &str,
        decorators: &[Node<'_>],
    ) {
        for child in self.decorator_nodes(node, decorators) {
            let name = decorator_call_name(&node_text(child, source));
            if is_ident(&name) {
                self.pending_edges.push(Edge {
                    source_name: owner.to_string(),
                    kind: "decorates".to_string(),
                    target_name: name,
                    line: child.start_position().row + 1,
                    ..Edge::default()
                });
            }
        }
    }

    /// Emit references edges for identifiers in parameter and return annotations.
    fn emit_type_references(&mut self, node: Node<'_>, source: &[u8], owner: &str) {
        let mut type_nodes: Vec<Node<'_>> = Vec::new();
        if let Some(params) = node.child_by_field_name("parameters") {
            for child in children(params) {
                if let Some(ann) = child.child_by_field_name("type") {
                    type_nodes.push(ann);
                }
            }
        }
        if let Some(ret) = node.child_by_field_name("return_type") {
            type_nodes.push(ret);
        }
        let mut seen = std::collections::HashSet::new();
        for ann in type_nodes {
            for tok in find_identifiers(&node_text(ann, source)) {
                let lowered = tok.to_ascii_lowercase();
                if PY_BUILTIN_TYPE_TOKENS.contains(&lowered.as_str())
                    || tok == owner
                    || !seen.insert(tok.to_string())
                {
                    continue;
                }
                self.pending_edges.push(Edge {
                    source_name: owner.to_string(),
                    kind: "references".to_string(),
                    target_name: tok.to_string(),
                    line: ann.start_position().row + 1,
                    ..Edge::default()
                });
            }
        }
    }

    /// One normalized dotted-path Import row per imported name.
    fn parse_imports(&self, node: Node<'_>, source: &[u8]) -> Vec<Import> {
        let line = node.start_position().row + 1;
        let names: Vec<Node<'_>> = children(node)
            .into_iter()
            .enumerate()
            .filter(|(i, _)| node.field_name_for_child(*i as u32) == Some("name"))
            .map(|(_, child)| child)
            .collect();
        let module = self.module_text(node, source);
        let mut imports = Vec::new();
        for child in names {
            if child.kind() == "aliased_import" {
                let (Some(inner), Some(alias)) = (
                    child.child_by_field_name("name"),
                    child.child_by_field_name("alias"),
                ) else {
                    continue;
                };
                imports.push(Import {
                    imported_path: join_import_path(&module, &node_text(inner, source)),
                    line,
                    local_alias: Some(node_text(alias, source).trim().to_string()),
                });
            } else {
                imports.push(Import {
                    imported_path: join_import_path(&module, &node_text(child, source)),
                    line,
                    local_alias: None,
                });
            }
        }
        imports
    }

    fn module_text(&self, node: Node<'_>, source: &[u8]) -> String {
        let Some(mod_node) = node.child_by_field_name("module_name") else {
            return String::new();
        };
        node_text(mod_node, source)
            .split('.')
            .filter(|s| !s.is_empty())
            .collect::<Vec<_>>()
            .join(".")
    }

    fn parse_call(&self, node: Node<'_>, source: &[u8]) -> Option<Edge> {
        let callee = *children(node).first()?;
        let target = self.extract_callee(callee, source)?;
        Some(Edge {
            source_name: self.scope.last().cloned().unwrap_or_default(),
            kind: "calls".to_string(),
            target_name: target,
            line: node.start_position().row + 1,
            receiver_type: self.infer_call_receiver_type(callee, source),
            call_arity: self.count_call_arguments(node),
            ..Edge::default()
        })
    }

    /// Receiver type for unambiguous bare-object method calls.
    fn infer_call_receiver_type(&self, callee: Node<'_>, source: &[u8]) -> Option<String> {
        if callee.kind() != "attribute" {
            return None;
        }
        let obj = callee.child_by_field_name("object")?;
        if obj.kind() != "identifier" {
            return None;
        }
        self.types.resolve(node_text(obj, source).trim())
    }

    /// Argument count of the call's argument_list; splats abstain to None.
    fn count_call_arguments(&self, node: Node<'_>) -> Option<usize> {
        let args = node.child_by_field_name("arguments")?;
        let mut count = 0;
        for child in children(args) {
            match child.kind() {
                "(" | ")" | "," => {}
                "list_splat" | "dictionary_splat" => return None,
                _ => count += 1,
            }
        }
        Some(count)
    }

    fn extract_callee(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        match node.kind() {
            "identifier" | "dotted_name" => Some(node_text(node, source).trim().to_string()),
            "attribute" => children(node)
                .into_iter()
                .rev()
                .find(|child| child.kind() == "identifier")
                .map(|child| node_text(child, source).trim().to_string()),
            "call" => node
                .child_by_field_name("function")
                .and_then(|inner| self.extract_callee(inner, source)),
            _ => None,
        }
    }

    /// Def arity, or None when call-site arity is not one count.
    fn count_parameters(
        &self,
        node: Node<'_>,
        source: &[u8],
        decorators: &[Node<'_>],
    ) -> Option<usize> {
        let params = node.child_by_field_name("parameters")?;
        let static_method = self.is_staticmethod(node, source, decorators);
        let skip_receiver = self.is_class_owned(node) && !static_method;
        let mut count = 0;
        let mut seen_param = false;
        for child in children(params) {
            let ct = child.kind();
            if matches!(
                ct,
                "default_parameter"
                    | "typed_default_parameter"
                    | "list_splat_pattern"
                    | "dictionary_splat_pattern"
            ) {
                return None;
            }
            if ct != "identifier" && ct != "typed_parameter" {
                continue;
            }
            let name = param_name(child, source)?;
            if !seen_param && skip_receiver {
                if name != "self" && name != "cls" {
                    return None;
                }
                seen_param = true;
                continue;
            }
            seen_param = true;
            count += 1;
        }
        Some(count)
    }

    /// Enter a function's bindings in the tracker: self/cls binds the owning
    /// class, annotated parameters bind their annotation's type.
    fn record_function_bindings(
        &mut self,
        node: Node<'_>,
        source: &[u8],
        owning_class: Option<&str>,
        decorators: &[Node<'_>],
    ) {
        let Some(params) = node.child_by_field_name("parameters") else {
            return;
        };
        let static_method = self.is_staticmethod(node, source, decorators);
        let mut first = true;
        for child in children(params) {
            if !matches!(
                child.kind(),
                "identifier" | "typed_parameter" | "default_parameter" | "typed_default_parameter"
            ) {
                continue;
            }
            if let Some(name) = param_name(child, source) {
                if let Some(owner) = owning_class
                    && first
                    && !static_method
                    && (name == "self" || name == "cls")
                {
                    self.types.record(&name, owner);
                } else if let Some(tname) =
                    annotation_type(child.child_by_field_name("type"), source)
                {
                    self.types.record(&name, &tname);
                }
            }
            first = false;
        }
    }

    /// Bind a plain-name assignment: annotation when present, else a
    /// Capitalized constructor call on the right-hand side.
    fn record_assignment(&mut self, node: Node<'_>, source: &[u8]) {
        let Some(left) = node.child_by_field_name("left") else {
            return;
        };
        if left.kind() != "identifier" {
            return;
        }
        let name = node_text(left, source).trim().to_string();
        if name.is_empty() {
            return;
        }
        let tname = annotation_type(node.child_by_field_name("type"), source)
            .or_else(|| constructor_call_type(node.child_by_field_name("right"), source));
        if let Some(tname) = tname {
            self.types.record(&name, &tname);
        }
    }

    fn is_staticmethod(&self, node: Node<'_>, source: &[u8], decorators: &[Node<'_>]) -> bool {
        self.decorator_nodes(node, decorators)
            .iter()
            .any(|c| decorator_call_name(&node_text(*c, source)) == "staticmethod")
    }

    /// True when the def sits directly in a class body, seeing through the
    /// decorated_definition wrapper.
    fn is_class_owned(&self, node: Node<'_>) -> bool {
        let mut parent = node.parent();
        if parent.is_some_and(|p| p.kind() == "decorated_definition") {
            parent = parent.and_then(|p| p.parent());
        }
        parent.is_some_and(|p| p.kind() == "block")
            && parent
                .and_then(|p| p.parent())
                .is_some_and(|gp| gp.kind() == "class_definition")
    }
}

/// Callable name of a decorator's text: `@a.b(c)` -> `b`.
fn decorator_call_name(text: &str) -> String {
    let mut name = text
        .trim()
        .trim_start_matches('@')
        .split('(')
        .next()
        .unwrap_or("")
        .trim()
        .to_string();
    if let Some(idx) = name.rfind('.') {
        name = name[idx + 1..].to_string();
    }
    name
}

/// Name of one parameter node, whichever shape it takes.
fn param_name(param: Node<'_>, source: &[u8]) -> Option<String> {
    if param.kind() == "identifier" {
        return Some(node_text(param, source).trim().to_string());
    }
    if let Some(named) = param.child_by_field_name("name") {
        return Some(node_text(named, source).trim().to_string());
    }
    children(param)
        .into_iter()
        .find(|child| child.kind() == "identifier")
        .map(|child| node_text(child, source).trim().to_string())
}

/// Type name from an annotation node: only plain (possibly dotted) names
/// count; subscripts, string forward-refs and unions abstain.
fn annotation_type(type_node: Option<Node<'_>>, source: &[u8]) -> Option<String> {
    let text = node_text(type_node?, source).trim().to_string();
    if !is_dotted_type(&text) {
        return None;
    }
    text.rsplit('.').next().map(str::to_string)
}

/// Plain (possibly dotted) type name fullmatch.
fn is_dotted_type(text: &str) -> bool {
    !text.is_empty() && text.split('.').all(is_ident)
}

/// Type of a `Foo(...)` right-hand side (bare Capitalized callee).
fn constructor_call_type(right: Option<Node<'_>>, source: &[u8]) -> Option<String> {
    let right = right?;
    if right.kind() != "call" {
        return None;
    }
    let func = right.child_by_field_name("function")?;
    if func.kind() != "identifier" {
        return None;
    }
    let text = node_text(func, source).trim().to_string();
    let first = text.chars().next()?;
    first.is_uppercase().then_some(text)
}

fn join_import_path(module: &str, name: &str) -> String {
    let name = name.trim();
    if !module.is_empty() && !name.is_empty() {
        return format!("{module}.{name}");
    }
    name.to_string()
}

/// Docstring text of a class/function body's first bare string statement.
fn py_docstring(node: Node<'_>, source: &[u8]) -> Option<String> {
    let block = children(node).into_iter().find(|c| c.kind() == "block")?;
    let first_stmt = children(block).into_iter().next()?;
    if first_stmt.kind() != "expression_statement" {
        return None;
    }
    for sub in children(first_stmt) {
        if sub.kind() == "string" {
            let mut text = node_text(sub, source).trim().to_string();
            for quote in ["\"\"\"", "'''", "\"", "'"] {
                if text.starts_with(quote) && text.ends_with(quote) && text.len() >= quote.len() * 2
                {
                    text = text[quote.len()..text.len() - quote.len()]
                        .trim()
                        .to_string();
                    break;
                }
            }
            return if text.is_empty() { None } else { Some(text) };
        }
    }
    None
}

// --- PHP ---

pub struct PhpParser {
    scope: Vec<String>,
    callable_scope: Vec<String>,
    pending_edges: Vec<Edge>,
    anon_counter: usize,
}

impl Default for PhpParser {
    fn default() -> Self {
        Self::new()
    }
}

impl PhpParser {
    pub fn new() -> Self {
        Self {
            scope: Vec::new(),
            callable_scope: Vec::new(),
            pending_edges: Vec::new(),
            anon_counter: 0,
        }
    }

    /// Parse the php file at `path`.
    pub fn parse(&mut self, path: &Path) -> std::io::Result<ParsedFile> {
        let source = std::fs::read(path)?;
        Ok(self.parse_source(path, &source))
    }

    /// Parse in-memory php `source` as if read from `path`.
    pub fn parse_source(&mut self, path: &Path, source: &[u8]) -> ParsedFile {
        let mut parser = registry::parser("php").expect("php grammar is routed");
        let tree = parser.parse(source, None).expect("tree-sitter parse");
        let mut pf = ParsedFile {
            path: path.to_string_lossy().into_owned(),
            language: "php".to_string(),
            hash: sha256_hex(source),
            line_count: source.iter().filter(|&&b| b == b'\n').count() + 1,
            ..ParsedFile::default()
        };
        self.pending_edges.clear();
        self.scope.clear();
        self.callable_scope.clear();
        self.anon_counter = 0;
        let root = tree.root_node();
        self.walk(root, source, &mut pf);
        pf.edges.extend(std::mem::take(&mut self.pending_edges));
        pf
    }

    fn walk(&mut self, node: Node<'_>, source: &[u8], pf: &mut ParsedFile) {
        for child in children(node) {
            if rationale_from_comment("php", child, source, pf) {
                continue;
            }
            self.visit(child, source, pf);
        }
    }

    fn visit(&mut self, node: Node<'_>, source: &[u8], pf: &mut ParsedFile) {
        match node.kind() {
            // Only the bracketed namespace form scopes its body.
            "namespace_definition" => {
                if child_of_type(node, &["compound_statement"]).is_some() {
                    self.walk(node, source, pf);
                }
            }
            "namespace_use_declaration" => {
                pf.imports.extend(self.parse_use_imports(node, source));
            }
            "require_expression"
            | "require_once_expression"
            | "include_expression"
            | "include_once_expression" => {
                if let Some(imp) = self.parse_require_import(node, source) {
                    pf.imports.push(imp);
                }
            }
            "class_declaration"
            | "interface_declaration"
            | "trait_declaration"
            | "enum_declaration"
            | "anonymous_class" => {
                if let Some(sym) = self.parse_type_decl(node, source) {
                    let name = sym.name.clone();
                    pf.symbols.push(sym);
                    self.scope.push(name);
                    self.walk(node, source, pf);
                    self.scope.pop();
                }
            }
            "function_definition" => {
                if let Some(sym) = self.parse_function(node, source) {
                    let name = sym.name.clone();
                    pf.symbols.push(sym);
                    self.callable_scope.push(name);
                    self.walk(node, source, pf);
                    self.callable_scope.pop();
                }
            }
            "method_declaration" => {
                if let Some(sym) = self.parse_method(node, source, pf) {
                    let name = sym.name.clone();
                    pf.symbols.push(sym);
                    self.callable_scope.push(name);
                    self.walk(node, source, pf);
                    self.callable_scope.pop();
                }
            }
            "enum_case" => {
                if let Some(sym) = self.parse_enum_case(node, source) {
                    pf.symbols.push(sym);
                }
            }
            "property_declaration" => {
                for sym in self.parse_property(node, source) {
                    pf.symbols.push(sym);
                }
                self.walk(node, source, pf);
            }
            "function_call_expression"
            | "member_call_expression"
            | "nullsafe_member_call_expression"
            | "scoped_call_expression"
            | "object_creation_expression" => {
                if let Some(edge) = self.parse_call(node, source) {
                    pf.edges.push(edge);
                }
                self.walk(node, source, pf);
            }
            _ => self.walk(node, source, pf),
        }
    }

    /// class/interface/trait/enum/anonymous_class declaration -> Symbol.
    fn parse_type_decl(&mut self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let (kind, name) = self.type_decl_kind_name(node, source)?;
        let sym = Symbol {
            kind,
            qualified_name: Some(qualified_name(&self.scope, &name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            name,
            ..Symbol::default()
        };
        for (clause_kind, clause_node) in heritage_clauses(node) {
            for target in clause_names(clause_node, source) {
                self.pending_edges.push(Edge {
                    source_name: sym.name.clone(),
                    kind: clause_kind.to_string(),
                    target_name: target,
                    line: node.start_position().row + 1,
                    ..Edge::default()
                });
            }
        }
        Some(sym)
    }

    fn type_decl_kind_name(&mut self, node: Node<'_>, source: &[u8]) -> Option<(String, String)> {
        let kind = match node.kind() {
            "class_declaration" => "class",
            "interface_declaration" => "interface",
            "trait_declaration" => "trait",
            "enum_declaration" => "enum",
            "anonymous_class" => {
                // No name in source; synthesize one so inner methods don't leak.
                self.anon_counter += 1;
                return Some((
                    "class".to_string(),
                    format!("__anon_class_{}", self.anon_counter),
                ));
            }
            _ => return None,
        };
        Some((kind.to_string(), self.decl_name(node, source)?))
    }

    fn parse_function(&mut self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = self.decl_name(node, source)?;
        Some(Symbol {
            kind: "function".to_string(),
            qualified_name: Some(qualified_name(&self.scope, &name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            arity: self.formal_arity(node),
            name,
            ..Symbol::default()
        })
    }

    fn parse_method(
        &mut self,
        node: Node<'_>,
        source: &[u8],
        pf: &mut ParsedFile,
    ) -> Option<Symbol> {
        let name = self.decl_name(node, source)?;
        // Constructor property promotion parameters are also properties.
        if let Some(params) = child_of_type(node, &["formal_parameters"]) {
            for p in children(params) {
                if p.kind() != "property_promotion_parameter" {
                    continue;
                }
                let Some(var) = child_of_type(p, &["variable_name"]) else {
                    continue;
                };
                let Some(pname) = self.decl_name(var, source) else {
                    continue;
                };
                pf.symbols.push(Symbol {
                    kind: "property".to_string(),
                    qualified_name: Some(qualified_name(&self.scope, &pname)),
                    line_start: p.start_position().row + 1,
                    line_end: p.end_position().row + 1,
                    column_start: p.start_position().column,
                    column_end: p.end_position().column,
                    name: pname,
                    ..Symbol::default()
                });
            }
        }
        Some(Symbol {
            kind: "method".to_string(),
            qualified_name: Some(qualified_name(&self.scope, &name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            arity: self.formal_arity(node),
            name,
            ..Symbol::default()
        })
    }

    fn parse_enum_case(&self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = self.decl_name(node, source)?;
        Some(Symbol {
            kind: "enum_case".to_string(),
            qualified_name: Some(qualified_name(&self.scope, &name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            name,
            ..Symbol::default()
        })
    }

    /// One Symbol per property_element child.
    fn parse_property(&self, node: Node<'_>, source: &[u8]) -> Vec<Symbol> {
        let mut out = Vec::new();
        for child in children(node) {
            if child.kind() != "property_element" {
                continue;
            }
            let Some(var) = child_of_type(child, &["variable_name"]) else {
                continue;
            };
            let Some(nm) = self.decl_name(var, source) else {
                continue;
            };
            out.push(Symbol {
                kind: "property".to_string(),
                qualified_name: Some(qualified_name(&self.scope, &nm)),
                line_start: node.start_position().row + 1,
                line_end: node.end_position().row + 1,
                column_start: node.start_position().column,
                column_end: node.end_position().column,
                name: nm,
                ..Symbol::default()
            });
        }
        out
    }

    /// function/member/nullsafe/scoped call -> Edge(calls).
    fn parse_call(&self, node: Node<'_>, source: &[u8]) -> Option<Edge> {
        let (callee, receiver) = match node.kind() {
            "function_call_expression" => self.split_function_call(node, source),
            "member_call_expression" | "nullsafe_member_call_expression" => {
                self.split_member_call(node, source)
            }
            "object_creation_expression" => self.split_object_creation(node, source),
            _ => self.split_scoped_call(node, source),
        };
        Some(Edge {
            source_name: current_edge_owner(&self.callable_scope, &self.scope),
            kind: "calls".to_string(),
            target_name: callee?,
            line: node.start_position().row + 1,
            receiver_type: infer_receiver_type(receiver.as_deref()),
            call_arity: self.call_arity(node),
            ..Edge::default()
        })
    }

    fn split_object_creation(
        &self,
        node: Node<'_>,
        source: &[u8],
    ) -> (Option<String>, Option<String>) {
        for child in children(node) {
            if child.kind() == "name" || child.kind() == "qualified_name" {
                return (
                    Some(Self::bare_ns_name(node_text(child, source).trim())),
                    None,
                );
            }
        }
        (None, None)
    }

    fn split_function_call(
        &self,
        node: Node<'_>,
        source: &[u8],
    ) -> (Option<String>, Option<String>) {
        for child in children(node) {
            if child.kind() == "name" || child.kind() == "qualified_name" {
                return (
                    Some(Self::bare_ns_name(node_text(child, source).trim())),
                    None,
                );
            }
        }
        (None, None)
    }

    fn split_member_call(&self, node: Node<'_>, source: &[u8]) -> (Option<String>, Option<String>) {
        let mut callee = None;
        let mut receiver = None;
        for child in children(node) {
            match child.kind() {
                "name" if callee.is_none() => {
                    callee = Some(node_text(child, source).trim().to_string());
                }
                "variable_name" | "member_call_expression" | "function_call_expression"
                    if receiver.is_none() =>
                {
                    receiver = Some(node_text(child, source).trim().to_string());
                }
                _ => {}
            }
        }
        (callee, receiver)
    }

    fn split_scoped_call(&self, node: Node<'_>, source: &[u8]) -> (Option<String>, Option<String>) {
        let names: Vec<Node<'_>> = children(node)
            .into_iter()
            .filter(|c| c.kind() == "name")
            .collect();
        let Some(callee) = names.last() else {
            return (None, None);
        };
        // The trailing name is the method; the scope qualifier is the receiver.
        let mut receiver_text: Option<String> = None;
        for child in children(node) {
            if child.kind() == "::" {
                break;
            }
            if matches!(child.kind(), "name" | "qualified_name" | "variable_name") {
                receiver_text = Some(node_text(child, source).trim().to_string());
            }
        }
        let receiver = receiver_text.as_deref().map(Self::bare_ns_name);
        (
            Some(node_text(*callee, source).trim().to_string()),
            receiver,
        )
    }

    /// formal_parameters count; None when a variadic or defaulted parameter
    /// makes the declared count unable to match call counts.
    fn formal_arity(&self, node: Node<'_>) -> Option<usize> {
        let params = child_of_type(node, &["formal_parameters"])?;
        let mut count = 0;
        for child in children(params) {
            match child.kind() {
                "variadic_parameter" => return None,
                "simple_parameter" | "property_promotion_parameter" => {
                    if child.child_by_field_name("default_value").is_some() {
                        return None;
                    }
                    count += 1;
                }
                _ => {}
            }
        }
        Some(count)
    }

    /// arguments count; None when a spread unpacking or the
    /// first-class-callable `...` placeholder makes it uncountable.
    fn call_arity(&self, node: Node<'_>) -> Option<usize> {
        let Some(args) = child_of_type(node, &["arguments"]) else {
            // `new Foo;` invokes the constructor with zero arguments.
            return if node.kind() == "object_creation_expression" {
                Some(0)
            } else {
                None
            };
        };
        let mut count = 0;
        for child in children(args) {
            if child.kind() == "variadic_placeholder" {
                return None;
            }
            if child.kind() == "argument" {
                if child_of_type(child, &["variadic_unpacking"]).is_some() {
                    return None;
                }
                count += 1;
            }
        }
        Some(count)
    }

    /// Imports for single, multi, and grouped PHP use clauses.
    fn parse_use_imports(&self, node: Node<'_>, source: &[u8]) -> Vec<Import> {
        let mut imports = Vec::new();
        let line = node.start_position().row + 1;
        if let Some(group) = child_of_type(node, &["namespace_use_group"]) {
            let prefix = child_of_type(node, &["namespace_name"])
                .map(|ns| node_text(ns, source).trim().to_string())
                .unwrap_or_default();
            for clause in children(group) {
                if clause.kind() != "namespace_use_clause" {
                    continue;
                }
                let Some(inner) = child_of_type(clause, &["qualified_name", "name"]) else {
                    continue;
                };
                let inner_name = node_text(inner, source).trim().to_string();
                let path = if prefix.is_empty() {
                    inner_name
                } else {
                    format!("{prefix}\\{inner_name}")
                };
                imports.push(Import {
                    imported_path: path,
                    line,
                    local_alias: self.use_clause_alias(clause, source),
                });
            }
            return imports;
        }
        for clause in children(node) {
            if clause.kind() != "namespace_use_clause" {
                continue;
            }
            if let Some(qn) = child_of_type(clause, &["qualified_name", "name"]) {
                imports.push(Import {
                    imported_path: node_text(qn, source).trim().to_string(),
                    line,
                    local_alias: self.use_clause_alias(clause, source),
                });
            }
        }
        imports
    }

    fn use_clause_alias(&self, clause: Node<'_>, source: &[u8]) -> Option<String> {
        clause
            .child_by_field_name("alias")
            .map(|alias| node_text(alias, source).trim().to_string())
    }

    fn parse_require_import(&self, node: Node<'_>, source: &[u8]) -> Option<Import> {
        Some(Import {
            imported_path: node_text(node, source).trim().to_string(),
            line: node.start_position().row + 1,
            local_alias: None,
        })
    }

    /// First `name` child text without its global-namespace prefix.
    fn decl_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        children(node)
            .into_iter()
            .find(|child| child.kind() == "name")
            .map(|child| strip_leading_backslash(node_text(child, source).trim()))
    }

    /// Final backslash segment used by graph symbol matching.
    fn bare_ns_name(name: &str) -> String {
        strip_leading_backslash(name)
            .rsplit('\\')
            .next()
            .unwrap_or("")
            .to_string()
    }
}

/// (edge_kind, clause_node) for extends/implements.
fn heritage_clauses(node: Node<'_>) -> Vec<(&'static str, Node<'_>)> {
    children(node)
        .into_iter()
        .filter_map(|child| match child.kind() {
            "base_clause" => Some(("extends", child)),
            "class_interface_clause" => Some(("implements", child)),
            _ => None,
        })
        .collect()
}

/// All bare names in an extends/implements clause.
fn clause_names(clause: Node<'_>, source: &[u8]) -> Vec<String> {
    children(clause)
        .into_iter()
        .filter(|c| c.kind() == "name")
        .map(|c| strip_leading_backslash(node_text(c, source).trim()))
        .collect()
}

fn strip_leading_backslash(name: &str) -> String {
    name.trim_start_matches('\\').to_string()
}

// --- Ruby ---

/// Calls to these methods at the top of a file are import-like.
const RUBY_REQUIRE_METHODS: &[&str] = &["require", "require_relative", "load"];

pub struct RubyParser {
    scope: Vec<String>,
    callable_scope: Vec<String>,
    pending_edges: Vec<Edge>,
}

impl Default for RubyParser {
    fn default() -> Self {
        Self::new()
    }
}

impl RubyParser {
    pub fn new() -> Self {
        Self {
            scope: Vec::new(),
            callable_scope: Vec::new(),
            pending_edges: Vec::new(),
        }
    }

    /// Parse the ruby file at `path`.
    pub fn parse(&mut self, path: &Path) -> std::io::Result<ParsedFile> {
        let source = std::fs::read(path)?;
        Ok(self.parse_source(path, &source))
    }

    /// Parse in-memory ruby `source` as if read from `path`.
    pub fn parse_source(&mut self, path: &Path, source: &[u8]) -> ParsedFile {
        let mut parser = registry::parser("ruby").expect("ruby grammar is routed");
        let tree = parser.parse(source, None).expect("tree-sitter parse");
        let mut pf = ParsedFile {
            path: path.to_string_lossy().into_owned(),
            language: "ruby".to_string(),
            hash: sha256_hex(source),
            line_count: source.iter().filter(|&&b| b == b'\n').count() + 1,
            ..ParsedFile::default()
        };
        self.pending_edges.clear();
        self.scope.clear();
        self.callable_scope.clear();
        let root = tree.root_node();
        self.walk(root, source, &mut pf);
        pf.edges.extend(std::mem::take(&mut self.pending_edges));
        pf
    }

    fn walk(&mut self, node: Node<'_>, source: &[u8], pf: &mut ParsedFile) {
        for child in children(node) {
            if rationale_from_comment("ruby", child, source, pf) {
                continue;
            }
            self.visit(child, source, pf);
        }
    }

    fn visit(&mut self, node: Node<'_>, source: &[u8], pf: &mut ParsedFile) {
        match node.kind() {
            "class" | "module" => self.visit_type_decl(node, source, pf),
            "method" | "singleton_method" => {
                if let Some(sym) = self.parse_method(node, source) {
                    let name = sym.name.clone();
                    pf.symbols.push(sym);
                    self.callable_scope.push(name);
                    self.walk(node, source, pf);
                    self.callable_scope.pop();
                } else {
                    // Still walk so a body under an unparseable name isn't lost.
                    self.walk(node, source, pf);
                }
            }
            "call" => {
                if let Some(imp) = self.maybe_import(node, source) {
                    pf.imports.push(imp);
                    return;
                }
                if let Some(edge) = self.parse_call(node, source) {
                    pf.edges.push(edge);
                }
                self.walk(node, source, pf);
            }
            _ => self.walk(node, source, pf),
        }
    }

    /// Parse a class or module, emit its superclass edge, then walk its body.
    fn visit_type_decl(&mut self, node: Node<'_>, source: &[u8], pf: &mut ParsedFile) {
        let superclass = self.superclass_name(node, source);
        let Some(name) = self.type_name(node, source) else {
            // Name extraction failed -- still walk the body so nested
            // declarations aren't lost.
            self.walk_excluding_superclass(node, source, pf);
            return;
        };
        pf.symbols.push(Symbol {
            kind: "class".to_string(),
            qualified_name: Some(qualified_name(&self.scope, &name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            name: name.clone(),
            ..Symbol::default()
        });
        if let Some(superclass) = superclass {
            self.pending_edges.push(Edge {
                source_name: name.clone(),
                kind: "extends".to_string(),
                target_name: superclass,
                line: node.start_position().row + 1,
                ..Edge::default()
            });
        }
        self.scope.push(name);
        self.walk_excluding_superclass(node, source, pf);
        self.scope.pop();
    }

    /// Walk a class/module body, skipping the consumed `superclass` child.
    fn walk_excluding_superclass(&mut self, node: Node<'_>, source: &[u8], pf: &mut ParsedFile) {
        for child in children(node) {
            if child.kind() == "superclass" {
                continue;
            }
            if rationale_from_comment("ruby", child, source, pf) {
                continue;
            }
            self.visit(child, source, pf);
        }
    }

    /// Class/module name: `constant` or `scope_resolution` (`A::B`).
    fn type_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        for child in children(node) {
            if child.kind() == "constant" {
                return Some(node_text(child, source).trim().to_string());
            }
            if child.kind() == "scope_resolution" {
                // A::B -> take the trailing constant.
                for sc in children(child).into_iter().rev() {
                    if sc.kind() == "constant" {
                        return Some(node_text(sc, source).trim().to_string());
                    }
                }
            }
        }
        None
    }

    /// Trailing constant of the `superclass` child (`< Base`/`< A::B`).
    fn superclass_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        let sc = child_of_type(node, &["superclass"])?;
        let inner = child_of_type(sc, &["constant", "scope_resolution"])?;
        if inner.kind() == "constant" {
            return Some(node_text(inner, source).trim().to_string());
        }
        children(inner)
            .into_iter()
            .rev()
            .find(|c| c.kind() == "constant")
            .map(|c| node_text(c, source).trim().to_string())
    }

    fn parse_method(&self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = self.method_name(node, source)?;
        Some(Symbol {
            kind: "method".to_string(),
            qualified_name: Some(qualified_name(&self.scope, &name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            arity: Some(self.arity(child_of_type(node, &["method_parameters"]))),
            name,
            ..Symbol::default()
        })
    }

    /// The method or operator name after any singleton receiver.
    fn method_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        let mut last_id = None;
        for child in children(node) {
            if child.kind() == "method_parameters" || child.kind() == "body_statement" {
                break;
            }
            if child.kind() == "identifier" || child.kind() == "operator" {
                last_id = Some(node_text(child, source).trim().to_string());
            }
        }
        last_id
    }

    /// A calls Edge for every tree-sitter-ruby call with a method name.
    fn parse_call(&self, node: Node<'_>, source: &[u8]) -> Option<Edge> {
        let (callee, receiver) = self.split_call(node, source);
        Some(Edge {
            source_name: current_edge_owner(&self.callable_scope, &self.scope),
            kind: "calls".to_string(),
            target_name: callee?,
            line: node.start_position().row + 1,
            receiver_type: infer_receiver_type(receiver.as_deref()),
            call_arity: Some(self.arity(child_of_type(node, &["argument_list"]))),
            ..Edge::default()
        })
    }

    /// Callee and receiver from the identifier after the last dot.
    fn split_call(&self, node: Node<'_>, source: &[u8]) -> (Option<String>, Option<String>) {
        let kids = children(node);
        let mut dot_idx = None;
        for (i, c) in kids.iter().enumerate() {
            if c.kind() == "." || c.kind() == "&." {
                dot_idx = Some(i); // keep the LAST dot (deepest link of a chain)
            }
        }
        let Some(dot_idx) = dot_idx else {
            // Bare call: leading identifier is the callee.
            for c in &kids {
                if c.kind() == "identifier" {
                    return (Some(node_text(*c, source).trim().to_string()), None);
                }
            }
            return (None, None);
        };
        // Method identifier: the first identifier child AFTER the last dot.
        for c in &kids[dot_idx + 1..] {
            if c.kind() == "identifier" {
                let receiver = self.receiver_text(&kids[..dot_idx], source);
                return (Some(node_text(*c, source).trim().to_string()), receiver);
            }
        }
        // No identifier after the dot (`p.(1)` proc shorthand): nothing to record.
        (None, None)
    }

    /// A directly inferable identifier, constant, or self receiver.
    fn receiver_text(&self, before_dot: &[Node<'_>], source: &[u8]) -> Option<String> {
        for c in before_dot {
            match c.kind() {
                "constant" => return Some(node_text(*c, source).trim().to_string()),
                "identifier" => return Some(node_text(*c, source).trim().to_string()),
                "self" => return Some("self".to_string()),
                _ => {}
            }
        }
        None
    }

    /// Positional slots, excluding block parameters and sibling blocks.
    fn arity(&self, list_node: Option<Node<'_>>) -> usize {
        let Some(list) = list_node else {
            return 0;
        };
        children(list)
            .into_iter()
            .filter(|c| {
                c.is_named() && c.kind() != "block_argument" && c.kind() != "block_parameter"
            })
            .count()
    }

    /// An Import only for require or load calls at top-level scope.
    fn maybe_import(&self, node: Node<'_>, source: &[u8]) -> Option<Import> {
        if !self.scope.is_empty() || !self.callable_scope.is_empty() {
            return None;
        }
        let first = children(node)
            .into_iter()
            .find(|c| c.kind() == "identifier")?;
        let method_name = node_text(first, source).trim().to_string();
        if !RUBY_REQUIRE_METHODS.contains(&method_name.as_str()) {
            return None;
        }
        let path = self.require_argument(node, source)?;
        Some(Import {
            imported_path: path,
            line: node.start_position().row + 1,
            local_alias: None,
        })
    }

    /// The string-literal/symbol argument of a require/load call.
    fn require_argument(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        let args = child_of_type(node, &["argument_list"])?;
        for child in children(args) {
            if child.kind() == "string" {
                return self.string_literal(child, source);
            }
            if child.kind() == "simple_symbol" {
                return Some(
                    node_text(child, source)
                        .trim()
                        .trim_start_matches(':')
                        .to_string(),
                );
            }
        }
        None
    }

    fn string_literal(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        let text = node_text(node, source).trim().to_string();
        let bytes = text.as_bytes();
        if text.len() >= 2
            && (bytes[0] == b'"' || bytes[0] == b'\'')
            && bytes[text.len() - 1] == bytes[0]
        {
            return Some(text[1..text.len() - 1].to_string());
        }
        if text.is_empty() { None } else { Some(text) }
    }
}

// --- Dart ---

const DART_TYPE_DECL_NODES: &[&str] = &["class_definition", "mixin_declaration"];
const DART_SIGNATURE_NODES: &[&str] = &["function_signature", "constructor_signature"];

pub struct DartParser {
    path: PathBuf,
    file_stem: String,
    func_depth: usize,
    scope: Vec<String>,
    /// Parallel to scope: true marks extension scopes, where `this` is the
    /// on-type, not the extension name.
    scope_ext: Vec<bool>,
    callable_scope: Vec<String>,
    pending_edges: Vec<Edge>,
    var_types: ScopeTypeTracker,
}

impl Default for DartParser {
    fn default() -> Self {
        Self::new()
    }
}

impl DartParser {
    pub fn new() -> Self {
        Self {
            path: PathBuf::new(),
            file_stem: String::new(),
            func_depth: 0,
            scope: Vec::new(),
            scope_ext: Vec::new(),
            callable_scope: Vec::new(),
            pending_edges: Vec::new(),
            var_types: ScopeTypeTracker::new(),
        }
    }

    /// Parse the dart file at `path`.
    pub fn parse(&mut self, path: &Path) -> std::io::Result<ParsedFile> {
        let source = std::fs::read(path)?;
        Ok(self.parse_source(path, &source))
    }

    /// Parse in-memory dart `source` as if read from `path`.
    pub fn parse_source(&mut self, path: &Path, source: &[u8]) -> ParsedFile {
        let mut parser = registry::parser("dart").expect("dart grammar is routed");
        let tree = parser.parse(source, None).expect("tree-sitter parse");
        let mut pf = ParsedFile {
            path: path.to_string_lossy().into_owned(),
            language: "dart".to_string(),
            hash: sha256_hex(source),
            line_count: source.iter().filter(|&&b| b == b'\n').count() + 1,
            ..ParsedFile::default()
        };
        self.path = path.to_path_buf();
        self.file_stem = path
            .file_stem()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        self.func_depth = 0;
        self.pending_edges.clear();
        self.scope.clear();
        self.scope_ext.clear();
        self.var_types.reset();
        self.callable_scope.clear();
        let root_children = children(tree.root_node());
        self.process_siblings(&root_children, source, &mut pf);
        pf.edges.extend(std::mem::take(&mut self.pending_edges));
        pf
    }

    // --- core traversal: flat sibling lists with lookahead ---

    fn process_siblings<'a>(&mut self, nodes: &[Node<'a>], source: &[u8], pf: &mut ParsedFile) {
        // Each sibling list is one lexical nesting level for var->type
        // tracking; the postfix-chain scan runs first so receivers resolve in
        // source order.
        self.var_types.push();
        self.scan_postfix_chain(nodes, source, pf);

        let n = nodes.len();
        let mut i = 0;
        while i < n {
            let node = nodes[i];
            let mut consumed = 1;

            if rationale_from_comment("dart", node, source, pf) {
                i += consumed;
                continue;
            }

            if node.kind() == "import_or_export" {
                if let Some(imp) = self.parse_import(node, source) {
                    pf.imports.push(imp);
                }
            } else if DART_TYPE_DECL_NODES.contains(&node.kind()) {
                let declared = self.parse_type_decl(node, source).map(|sym| {
                    let name = sym.name.clone();
                    (sym, name)
                });
                let in_scope = declared.is_some();
                if let Some((sym, name)) = declared {
                    pf.symbols.push(sym);
                    self.scope.push(name);
                    self.scope_ext.push(false);
                }
                if let Some(body) = child_of_type(node, &["class_body"]) {
                    let body_children = children(body);
                    self.process_siblings(&body_children, source, pf);
                }
                if in_scope {
                    self.scope.pop();
                    self.scope_ext.pop();
                }
            } else if node.kind() == "enum_declaration" {
                if let Some(sym) = self.parse_simple_decl(node, source, "enum") {
                    pf.symbols.push(sym);
                }
            } else if node.kind() == "extension_declaration" {
                let named = self
                    .parse_simple_decl(node, source, "extension")
                    .map(|sym| {
                        let name = sym.name.clone();
                        (sym, name)
                    });
                let in_scope = named.is_some();
                if let Some((sym, name)) = named {
                    pf.symbols.push(sym);
                    self.scope.push(name);
                    self.scope_ext.push(true);
                }
                if let Some(body) = child_of_type(node, &["extension_body"]) {
                    let body_children = children(body);
                    self.process_siblings(&body_children, source, pf);
                }
                if in_scope {
                    self.scope.pop();
                    self.scope_ext.pop();
                }
            } else if DART_SIGNATURE_NODES.contains(&node.kind()) {
                let kind = if node.kind() == "constructor_signature" {
                    "constructor"
                } else if !self.scope.is_empty() {
                    "method"
                } else {
                    "function"
                };
                let sym = self.parse_function_sig(node, source, kind);
                let (body, extra) = paired_body(nodes, i, n);
                consumed += extra;
                self.emit_with_body(sym, Some(node), body, source, pf);
            } else if node.kind() == "method_signature" {
                let inner = child_of_type(
                    node,
                    &[
                        "function_signature",
                        "constructor_signature",
                        "getter_signature",
                        "setter_signature",
                    ],
                );
                let sym = inner.and_then(|inner| {
                    let kind = if inner.kind() == "constructor_signature" {
                        "constructor"
                    } else {
                        "method"
                    };
                    self.parse_function_sig(inner, source, kind)
                });
                let (body, extra) = paired_body(nodes, i, n);
                consumed += extra;
                self.emit_with_body(sym, inner, body, source, pf);
            } else if node.kind() == "declaration" {
                let inner = child_of_type(node, &["function_signature", "constructor_signature"]);
                if let Some(inner) = inner {
                    let kind = if inner.kind() == "constructor_signature" {
                        "constructor"
                    } else if !self.scope.is_empty() {
                        "method"
                    } else {
                        "function"
                    };
                    if let Some(sym) = self.parse_function_sig(inner, source, kind) {
                        pf.symbols.push(sym);
                    }
                } else {
                    for sym in self.parse_field_declaration(node, source) {
                        pf.symbols.push(sym);
                    }
                }
            } else if node.kind() == "local_variable_declaration" {
                self.record_local_var_types(node, source);
                let inner = children(node);
                self.process_siblings(&inner, source, pf);
            } else {
                // Any other container (block, if/for/while bodies, argument
                // lists, ...): recurse into its own children as a fresh
                // sibling list.
                let inner = children(node);
                self.process_siblings(&inner, source, pf);
            }

            i += consumed;
        }

        self.var_types.pop();
    }

    /// A signature's implementation is its NEXT sibling `function_body`
    /// (absent for interface stubs, where the next sibling is `;`).
    fn emit_with_body(
        &mut self,
        sym: Option<Symbol>,
        sig: Option<Node<'_>>,
        body: Option<Node<'_>>,
        source: &[u8],
        pf: &mut ParsedFile,
    ) {
        let owned = sym.is_some();
        if let Some(sym) = sym {
            let name = sym.name.clone();
            pf.symbols.push(sym);
            self.callable_scope.push(name);
        }
        self.func_depth += 1;
        if let Some(body) = body {
            self.var_types.push();
            if let (Some(scope_last), Some(ext_last)) = (self.scope.last(), self.scope_ext.last())
                && !*ext_last
            {
                self.var_types.record("this", scope_last);
            }
            for (pname, ptype) in self.param_types(sig, source) {
                self.var_types.record(&pname, &ptype);
            }
            let body_children = children(body);
            self.process_siblings(&body_children, source, pf);
            self.var_types.pop();
        }
        self.func_depth -= 1;
        if owned {
            self.callable_scope.pop();
        }
    }

    // --- call-chain scanning (flat identifier + selector* siblings) ---

    fn scan_postfix_chain(&mut self, nodes: &[Node<'_>], source: &[u8], pf: &mut ParsedFile) {
        let n = nodes.len();
        let mut i = 0;
        while i < n {
            let node = nodes[i];
            if node.kind() == "identifier" || node.kind() == "this" {
                let base_name = node_text(node, source).trim().to_string();
                let line = node.start_position().row + 1;
                let mut last_property: Option<String> = None;
                let mut properties_seen = 0;
                let mut j = i + 1;
                while j < n && nodes[j].kind() == "selector" {
                    let sel = nodes[j];
                    if let Some(prop) = self.selector_property_name(sel, source) {
                        last_property = Some(prop);
                        properties_seen += 1;
                    }
                    if self.selector_is_call(sel) {
                        let target = last_property.clone().unwrap_or_else(|| base_name.clone());
                        // Only `base.member()` has a plainly readable
                        // receiver; deeper chains (a.b.c()) do not.
                        let receiver = if last_property.is_some() && properties_seen == 1 {
                            Some(base_name.clone())
                        } else {
                            None
                        };
                        pf.edges.push(Edge {
                            source_name: current_edge_owner(&self.callable_scope, &self.scope),
                            kind: "calls".to_string(),
                            target_name: target,
                            line,
                            receiver_type: self.receiver_type(receiver.as_deref()),
                            call_arity: self.call_arity(sel),
                            ..Edge::default()
                        });
                    }
                    j += 1;
                }
                i = if j > i + 1 { j } else { i + 1 };
            } else {
                i += 1;
            }
        }
    }

    fn receiver_type(&self, receiver: Option<&str>) -> Option<String> {
        let receiver = receiver?;
        self.var_types
            .resolve(receiver)
            .or_else(|| infer_receiver_type(Some(receiver)))
    }

    /// Argument count of a call selector; None when not plainly countable.
    fn call_arity(&self, sel_node: Node<'_>) -> Option<usize> {
        let args = self.find_descendant(sel_node, "arguments")?;
        let mut count = 0;
        for arg in children(args) {
            if !arg.is_named() {
                continue;
            }
            if arg.kind() != "argument" && arg.kind() != "named_argument" {
                return None; // spread/error element: effective arity unknown
            }
            count += 1;
        }
        Some(count)
    }

    /// Record explicitly typed locals (`Foo f = ...`); `var f` abstains.
    fn record_local_var_types(&mut self, node: Node<'_>, source: &[u8]) {
        for c in children(node) {
            if c.kind() != "initialized_variable_definition" {
                continue;
            }
            let name = find_name(c, source, &["identifier"]);
            let type_node = child_of_type(c, &["type_identifier"]);
            if let (Some(name), Some(type_node)) = (name, type_node) {
                self.var_types
                    .record(&name, node_text(type_node, source).trim());
            }
        }
    }

    /// (name, type) pairs for typed parameters of a signature node.
    fn param_types(&self, sig: Option<Node<'_>>, source: &[u8]) -> Vec<(String, String)> {
        let mut out = Vec::new();
        let Some(sig) = sig else {
            return out;
        };
        let Some(fpl) = child_of_type(sig, &["formal_parameter_list"]) else {
            return out;
        };
        for p in dart_formal_parameters(fpl) {
            let type_node = child_of_type(p, &["type_identifier"]);
            let name = find_name(p, source, &["identifier"]);
            if let (Some(type_node), Some(name)) = (type_node, name) {
                out.push((name, node_text(type_node, source).trim().to_string()));
            }
        }
        out
    }

    fn selector_property_name(&self, sel_node: Node<'_>, source: &[u8]) -> Option<String> {
        for c in children(sel_node) {
            if c.kind() == "unconditional_assignable_selector" {
                for gc in children(c) {
                    if gc.kind() == "identifier" {
                        return Some(node_text(gc, source).trim().to_string());
                    }
                }
            } else if c.kind() == "identifier" {
                return Some(node_text(c, source).trim().to_string());
            }
        }
        None
    }

    fn selector_is_call(&self, sel_node: Node<'_>) -> bool {
        children(sel_node)
            .iter()
            .any(|c| c.kind() == "argument_part")
    }

    // --- helpers ---

    fn find_descendant<'a>(&self, node: Node<'a>, type_name: &str) -> Option<Node<'a>> {
        for c in children(node) {
            if c.kind() == type_name {
                return Some(c);
            }
            if let Some(found) = self.find_descendant(c, type_name) {
                return Some(found);
            }
        }
        None
    }

    /// Dart uses file-stem prefix like TypeScript.
    fn dart_qualified_name(&self, name: &str) -> String {
        let mut parts = Vec::with_capacity(self.scope.len() + 2);
        parts.push(self.file_stem.clone());
        parts.extend(self.scope.iter().cloned());
        parts.push(name.to_string());
        parts.join(".")
    }

    // --- declarations ---

    fn parse_type_decl(&mut self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = find_name(node, source, &["identifier"])?;
        let kind = if node.kind() == "mixin_declaration" {
            "mixin"
        } else {
            "class"
        };
        let modifiers = if child_of_type(node, &["abstract"]).is_some() {
            vec!["abstract".to_string()]
        } else {
            Vec::new()
        };
        let sym = Symbol {
            kind: kind.to_string(),
            qualified_name: Some(self.dart_qualified_name(&name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers,
            name: name.clone(),
            ..Symbol::default()
        };
        self.parse_heritage(node, source, &name);
        Some(sym)
    }

    fn parse_heritage(&mut self, node: Node<'_>, source: &[u8], owner: &str) {
        let line = node.start_position().row + 1;
        if let Some(superclass) = child_of_type(node, &["superclass"]) {
            let mut ext_target: Option<String> = None;
            for c in children(superclass) {
                if c.kind() == "type_identifier" && ext_target.is_none() {
                    ext_target = Some(node_text(c, source).trim().to_string());
                } else if c.kind() == "mixins" {
                    for m in children(c) {
                        if m.kind() == "type_identifier" {
                            self.pending_edges.push(Edge {
                                source_name: owner.to_string(),
                                kind: "with".to_string(),
                                target_name: node_text(m, source).trim().to_string(),
                                line,
                                ..Edge::default()
                            });
                        }
                    }
                }
            }
            if let Some(ext_target) = ext_target {
                self.pending_edges.push(Edge {
                    source_name: owner.to_string(),
                    kind: "extends".to_string(),
                    target_name: ext_target,
                    line,
                    ..Edge::default()
                });
            }
        }
        if let Some(interfaces) = child_of_type(node, &["interfaces"]) {
            for c in children(interfaces) {
                if c.kind() == "type_identifier" {
                    self.pending_edges.push(Edge {
                        source_name: owner.to_string(),
                        kind: "implements".to_string(),
                        target_name: node_text(c, source).trim().to_string(),
                        line,
                        ..Edge::default()
                    });
                }
            }
        }
    }

    fn parse_simple_decl(&self, node: Node<'_>, source: &[u8], kind: &str) -> Option<Symbol> {
        let name = find_name(node, source, &["identifier"])?;
        Some(Symbol {
            kind: kind.to_string(),
            qualified_name: Some(self.dart_qualified_name(&name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            name,
            ..Symbol::default()
        })
    }

    fn parse_function_sig(&self, node: Node<'_>, source: &[u8], kind: &str) -> Option<Symbol> {
        let name = find_name(node, source, &["identifier"])?;
        let arity = child_of_type(node, &["formal_parameter_list"])
            .map(|fpl| dart_formal_parameters(fpl).len());
        Some(Symbol {
            kind: kind.to_string(),
            qualified_name: Some(self.dart_qualified_name(&name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            arity,
            name,
            ..Symbol::default()
        })
    }

    fn parse_field_declaration(&self, node: Node<'_>, source: &[u8]) -> Vec<Symbol> {
        let mut syms = Vec::new();
        if self.func_depth != 0 {
            return syms; // local vars use local_variable_declaration, not this
        }
        let Some(id_list) = child_of_type(node, &["initialized_identifier_list"]) else {
            return syms;
        };
        let kind = if !self.scope.is_empty() {
            "property"
        } else {
            "variable"
        };
        for child in children(id_list) {
            if child.kind() == "initialized_identifier"
                && let Some(name) = find_name(child, source, &["identifier"])
            {
                syms.push(Symbol {
                    kind: kind.to_string(),
                    qualified_name: Some(self.dart_qualified_name(&name)),
                    line_start: node.start_position().row + 1,
                    line_end: node.end_position().row + 1,
                    column_start: node.start_position().column,
                    column_end: node.end_position().column,
                    name,
                    ..Symbol::default()
                });
            }
        }
        syms
    }

    fn parse_import(&self, node: Node<'_>, source: &[u8]) -> Option<Import> {
        let uri_node = self.find_descendant(node, "uri")?;
        let mut text = node_text(uri_node, source).trim().to_string();
        let bytes = text.as_bytes();
        if text.len() >= 2
            && (bytes[0] == b'\'' || bytes[0] == b'"')
            && (bytes[text.len() - 1] == b'\'' || bytes[text.len() - 1] == b'"')
        {
            text = text[1..text.len() - 1].to_string();
        }
        if text.is_empty() {
            return None;
        }
        let imported_path = resolve_relative_dart_import(&self.path, &text).unwrap_or(text);
        Some(Import {
            imported_path,
            line: node.start_position().row + 1,
            local_alias: self.import_alias(node, source),
        })
    }

    /// The local alias after as, ignoring show and hide combinators.
    fn import_alias(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        let spec = self.find_descendant(node, "import_specification")?;
        let mut seen_as = false;
        for c in children(spec) {
            if c.kind() == "as" {
                seen_as = true;
            } else if seen_as && c.kind() == "identifier" {
                return Some(node_text(c, source).trim().to_string());
            }
        }
        None
    }
}

/// A signature's implementation is its NEXT sibling `function_body`
/// (absent for interface stubs, where the next sibling is `;`).
fn paired_body<'a>(nodes: &[Node<'a>], i: usize, n: usize) -> (Option<Node<'a>>, usize) {
    if i + 1 < n && nodes[i + 1].kind() == "function_body" {
        return (Some(nodes[i + 1]), 1);
    }
    (None, 0)
}

/// Direct formal parameters, excluding nested function parameter lists.
fn dart_formal_parameters(fpl: Node<'_>) -> Vec<Node<'_>> {
    let mut params = Vec::new();
    for c in children(fpl) {
        if c.kind() == "formal_parameter" {
            params.push(c);
        } else if c.kind() == "optional_formal_parameters" {
            params.extend(
                children(c)
                    .into_iter()
                    .filter(|cc| cc.kind() == "formal_parameter"),
            );
        }
    }
    params
}

/// An existing extension-stripped relative Dart import path, else None.
pub fn resolve_relative_dart_import(importer: &Path, spec: &str) -> Option<String> {
    if !spec.starts_with('.') {
        return None;
    }
    let joined = match importer.parent() {
        Some(parent) => parent.join(spec),
        None => PathBuf::from(spec),
    };
    // Lexical `.`/`..` normalization (Python's non-strict Path.resolve).
    let mut base = PathBuf::new();
    for component in joined.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                let parentable = base
                    .components()
                    .next_back()
                    .is_some_and(|c| matches!(c, Component::Normal(_)));
                if parentable {
                    base.pop();
                } else {
                    base.push(component.as_os_str());
                }
            }
            _ => base.push(component.as_os_str()),
        }
    }
    if base.extension() == Some(OsStr::new("dart")) {
        base.set_extension("");
    }
    let mut with_ext = base.clone().into_os_string();
    with_ext.push(".dart");
    if Path::new(&with_ext).is_file() {
        Some(base.to_string_lossy().into_owned())
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn edge_triples(parsed: &ParsedFile) -> Vec<(String, String, String)> {
        parsed
            .edges
            .iter()
            .map(|e| (e.source_name.clone(), e.kind.clone(), e.target_name.clone()))
            .collect()
    }

    #[test]
    fn python_decorators_become_modifiers_and_decorates_edges() {
        let src = b"@functools.cache\n@staticmethod_like\ndef helper(x):\n    return x\n";
        let parsed = PythonParser::new().parse_source(Path::new("sample.py"), src);
        let sym = &parsed.symbols[0];
        assert_eq!(sym.name, "helper");
        assert_eq!(sym.kind, "function");
        assert_eq!(
            sym.modifiers,
            vec!["@functools.cache", "@staticmethod_like"]
        );
        let mut decorates: Vec<(String, String, usize)> = parsed
            .edges
            .iter()
            .filter(|e| e.kind == "decorates")
            .map(|e| (e.source_name.clone(), e.target_name.clone(), e.line))
            .collect();
        decorates.sort();
        assert_eq!(
            decorates,
            vec![
                ("helper".into(), "cache".into(), 1),
                ("helper".into(), "staticmethod_like".into(), 2),
            ]
        );
    }

    #[test]
    fn python_annotations_feed_references_and_receiver_types() {
        let src = b"
class Status:
    def save(self):
        pass

def watch(s: Status) -> Status:
    return s

s = Status()
s.save()
";
        let parsed = PythonParser::new().parse_source(Path::new("sample.py"), src);
        let references: Vec<(String, String)> = parsed
            .edges
            .iter()
            .filter(|e| e.kind == "references")
            .map(|e| (e.source_name.clone(), e.target_name.clone()))
            .collect();
        assert_eq!(references, vec![("watch".into(), "Status".into())]);
        let save = parsed
            .edges
            .iter()
            .find(|e| e.target_name == "save" && e.kind == "calls")
            .expect("save call edge");
        assert_eq!(save.receiver_type.as_deref(), Some("Status"));
    }

    #[test]
    fn python_arity_abstains_on_defaulted_and_splat_params() {
        let src = b"class C:\n    def m(self, a, b=1, *rest):\n        pass\n";
        let parsed = PythonParser::new().parse_source(Path::new("sample.py"), src);
        assert_eq!(parsed.symbols[0].name, "C");
        assert_eq!(parsed.symbols[1].name, "m");
        assert_eq!(parsed.symbols[1].kind, "method");
        assert_eq!(parsed.symbols[1].arity, None);
    }

    #[test]
    fn php_promotion_parameters_and_heritage_edges() {
        let src = b"<?php\nclass Base {}\nclass Child extends Base implements Contr {\n    public function __construct(private int $id, string $name = \"x\") {}\n}\n";
        let parsed = PhpParser::new().parse_source(Path::new("sample.php"), src);
        let edges = edge_triples(&parsed);
        assert!(edges.contains(&("Child".into(), "extends".into(), "Base".into())));
        assert!(edges.contains(&("Child".into(), "implements".into(), "Contr".into())));
        let promotion = parsed
            .symbols
            .iter()
            .find(|s| s.name == "id" && s.kind == "property")
            .expect("promoted property");
        assert_eq!(promotion.qualified_name.as_deref(), Some("Child.id"));
        let ctor = parsed
            .symbols
            .iter()
            .find(|s| s.name == "__construct")
            .expect("ctor");
        assert_eq!(ctor.arity, None); // defaulted parameter abstains
    }

    #[test]
    fn php_scoped_and_object_calls_reduce_to_bare_names() {
        let src = b"<?php\nfunction run() {\n    $x = new App\\Thing();\n    return App\\Thing::make($x);\n}\n";
        let parsed = PhpParser::new().parse_source(Path::new("sample.php"), src);
        let calls: Vec<(String, String, Option<usize>)> = parsed
            .edges
            .iter()
            .filter(|e| e.kind == "calls")
            .map(|e| (e.source_name.clone(), e.target_name.clone(), e.call_arity))
            .collect();
        assert!(calls.contains(&("run".into(), "Thing".into(), Some(0))));
        assert!(calls.contains(&("run".into(), "make".into(), Some(1))));
    }

    #[test]
    fn ruby_singleton_methods_requires_and_chain_split() {
        let src = b"require_relative \"helpers\"\nclass A\n    def self.find(id)\n        helper(1).first\n    end\nend\n";
        let parsed = RubyParser::new().parse_source(Path::new("sample.rb"), src);
        assert_eq!(parsed.imports.len(), 1);
        assert_eq!(parsed.imports[0].imported_path, "helpers");
        let find = parsed
            .symbols
            .iter()
            .find(|s| s.name == "find")
            .expect("find");
        assert_eq!(find.kind, "method");
        assert_eq!(find.arity, Some(1));
        let calls: Vec<(String, String)> = parsed
            .edges
            .iter()
            .filter(|e| e.kind == "calls")
            .map(|e| (e.source_name.clone(), e.target_name.clone()))
            .collect();
        // helper(1).first -> deepest link's callee is first.
        assert!(calls.contains(&("find".into(), "helper".into())));
        assert!(calls.contains(&("find".into(), "first".into())));
    }

    #[test]
    fn ruby_block_and_splat_params_do_not_count_as_arity() {
        let src = b"def m(a, &blk)\n    yield\nend\n";
        let parsed = RubyParser::new().parse_source(Path::new("sample.rb"), src);
        assert_eq!(parsed.symbols[0].arity, Some(1));
    }

    #[test]
    fn dart_pairs_signature_with_body_and_types_receivers() {
        let src = b"class Repo {\n    void sync() {\n        Store store = open();\n        store.flush(1);\n    }\n}\n";
        let parsed = DartParser::new().parse_source(Path::new("sample.dart"), src);
        let sync = parsed
            .symbols
            .iter()
            .find(|s| s.name == "sync")
            .expect("sync");
        assert_eq!(sync.kind, "method");
        assert_eq!(sync.qualified_name.as_deref(), Some("sample.Repo.sync"));
        let flush = parsed
            .edges
            .iter()
            .find(|e| e.target_name == "flush")
            .expect("flush call edge");
        assert_eq!(flush.source_name, "sync");
        assert_eq!(flush.receiver_type.as_deref(), Some("Store"));
        assert_eq!(flush.call_arity, Some(1));
    }

    #[test]
    fn dart_toplevel_function_uses_function_kind() {
        let src = b"void main() {\n    helper();\n}\n";
        let parsed = DartParser::new().parse_source(Path::new("app.dart"), src);
        let main = parsed
            .symbols
            .iter()
            .find(|s| s.name == "main")
            .expect("main");
        assert_eq!(main.kind, "function");
        assert_eq!(main.qualified_name.as_deref(), Some("app.main"));
        let helper = parsed
            .edges
            .iter()
            .find(|e| e.target_name == "helper")
            .expect("body call must be scanned");
        assert_eq!(helper.source_name, "main");
    }

    #[test]
    fn dart_relative_import_resolution() {
        let dir = std::env::temp_dir().join(format!("cairn-dart-imports-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("temp dir");
        std::fs::write(dir.join("target.dart"), "class T {}\n").expect("write target");
        let importer = dir.join("importer.dart");
        std::fs::write(&importer, "").expect("write importer");

        let resolved = resolve_relative_dart_import(&importer, "./target.dart").expect("resolves");
        assert_eq!(resolved, dir.join("target").to_string_lossy());

        assert_eq!(
            resolve_relative_dart_import(&importer, "./missing.dart"),
            None
        );
        assert_eq!(resolve_relative_dart_import(&importer, "dart:async"), None);

        let _ = std::fs::remove_dir_all(&dir);
    }
}
