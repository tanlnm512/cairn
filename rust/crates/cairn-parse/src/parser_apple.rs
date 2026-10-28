//! Apple-family parsers: swift, objc.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use tree_sitter::Node;

use crate::model::{Edge, Import, ParsedFile, Symbol, sha256_hex};
use crate::rationale;
use crate::registry;

// --- shared helpers --------------------------------------------------------

/// Scope-ordered variable-to-type tracker for receiver-type inference.
#[derive(Default)]
pub struct ScopeTypeTracker {
    /// Innermost scope last; a None value marks an ambiguous name.
    scopes: Vec<HashMap<String, Option<String>>>,
}

impl ScopeTypeTracker {
    pub fn new() -> Self {
        Self {
            scopes: vec![HashMap::new()],
        }
    }

    /// Drop every scope and recorded type; parsers are reused per file.
    pub fn reset(&mut self) {
        self.scopes = vec![HashMap::new()];
    }

    /// Enter a nested block scope.
    pub fn push(&mut self) {
        self.scopes.push(HashMap::new());
    }

    /// Exit the innermost scope; the root scope is never popped.
    pub fn pop(&mut self) {
        if self.scopes.len() > 1 {
            self.scopes.pop();
        }
    }

    /// Record a scope type, making conflicting visible types ambiguous.
    pub fn record(&mut self, name: Option<&str>, type_name: Option<&str>) {
        let (Some(name), Some(type_name)) = (name, type_name) else {
            return;
        };
        if name.is_empty() || type_name.is_empty() {
            return;
        }
        let mut visible: Option<String> = None;
        let mut found = false;
        for scope in self.scopes.iter().rev() {
            if let Some(value) = scope.get(name) {
                visible = value.clone();
                found = true;
                break;
            }
        }
        if !found {
            self.scopes
                .last_mut()
                .expect("root scope always present")
                .insert(name.to_string(), Some(type_name.to_string()));
        } else if visible.is_some() && visible.as_deref() != Some(type_name) {
            self.scopes
                .last_mut()
                .expect("root scope always present")
                .insert(name.to_string(), None);
        }
    }

    /// Innermost visible type for `name`, else None (unknown/ambiguous).
    pub fn resolve(&self, name: &str) -> Option<String> {
        for scope in self.scopes.iter().rev() {
            if let Some(value) = scope.get(name) {
                return value.clone();
            }
        }
        None
    }
}

fn node_text(node: Node<'_>, source: &[u8]) -> String {
    String::from_utf8_lossy(&source[node.start_byte()..node.end_byte()]).into_owned()
}

/// Collect a node's direct children; the cursor borrow must outlive them.
fn children(node: Node<'_>) -> Vec<Node<'_>> {
    let mut cursor = node.walk();
    node.children(&mut cursor).collect()
}

fn child_of_type<'a>(node: Node<'a>, types: &[&str]) -> Option<Node<'a>> {
    children(node)
        .into_iter()
        .find(|child| types.contains(&child.kind()))
}

/// Text of the first direct identifier child.
fn find_name(node: Node<'_>, source: &[u8]) -> Option<String> {
    children(node)
        .into_iter()
        .find(|child| child.kind() == "identifier")
        .map(|child| node_text(child, source).trim().to_string())
}

fn same_node(a: &Node<'_>, b: &Node<'_>) -> bool {
    a.start_byte() == b.start_byte() && a.end_byte() == b.end_byte() && a.kind() == b.kind()
}

/// A receiver text only when its capitalized shape looks like a type.
fn infer_receiver_type(receiver: Option<&str>) -> Option<String> {
    let receiver = receiver?;
    match receiver.chars().next() {
        Some(first) if first.is_uppercase() => Some(receiver.to_string()),
        _ => None,
    }
}

fn is_comment(node: Node<'_>, language: &str) -> bool {
    rationale::comment_node_types(language).contains(&node.kind())
}

// --- swift -----------------------------------------------------------------

const SWIFT_MODIFIERS: &[&str] = &[
    "public",
    "private",
    "fileprivate",
    "internal",
    "open",
    "final",
    "static",
    "override",
    "weak",
    "lazy",
    "mutating",
    "async",
    "throws",
    "rethrows",
    "convenience",
    "required",
    "optional",
    "indirect",
];

const TYPE_DECL_NODES: &[&str] = &[
    "class_declaration",
    "struct_declaration",
    "protocol_declaration",
    "enum_declaration",
    "actor_declaration",
];

/// Scope kinds whose direct members are methods; extensions included.
const TYPE_SCOPE_NODES: &[&str] = &[
    "class_declaration",
    "struct_declaration",
    "protocol_declaration",
    "enum_declaration",
    "actor_declaration",
    "extension_declaration",
];

/// Swift symbol/edge extraction.
pub struct SwiftParser;

impl SwiftParser {
    pub const LANGUAGE: &'static str = "swift";

    /// Parse the file at `path`.
    pub fn parse_file(&self, path: &Path) -> std::io::Result<ParsedFile> {
        let source = std::fs::read(path)?;
        Ok(self.parse_source(path, &source))
    }

    /// Parse `source`.
    pub fn parse_source(&self, path: &Path, source: &[u8]) -> ParsedFile {
        let mut parser = registry::parser(Self::LANGUAGE).expect("swift is routed");
        let tree = parser.parse(source, None).expect("tree-sitter parse");
        let mut parsed = ParsedFile {
            path: path.to_string_lossy().into_owned(),
            language: Self::LANGUAGE.to_string(),
            hash: sha256_hex(source),
            line_count: source.iter().filter(|&&b| b == b'\n').count() + 1,
            ..ParsedFile::default()
        };
        let mut walk = SwiftWalk {
            source,
            scope: Vec::new(),
            scope_kinds: Vec::new(),
            var_types: ScopeTypeTracker::new(),
            pending_edges: Vec::new(),
        };
        walk.walk(tree.root_node(), &mut parsed);
        parsed.edges.extend(walk.pending_edges);
        parsed
    }
}

struct SwiftWalk<'a> {
    source: &'a [u8],
    scope: Vec<String>,
    scope_kinds: Vec<&'static str>,
    var_types: ScopeTypeTracker,
    pending_edges: Vec<Edge>,
}

impl SwiftWalk<'_> {
    fn walk(&mut self, node: Node<'_>, parsed: &mut ParsedFile) {
        for child in children(node) {
            if is_comment(child, SwiftParser::LANGUAGE) {
                if let Some(record) = rationale::extract_rationale(child, self.source) {
                    parsed.rationale.push(record);
                }
                continue;
            }
            self.visit(child, parsed);
        }
    }

    fn visit(&mut self, node: Node<'_>, parsed: &mut ParsedFile) {
        let t = node.kind();

        if t == "import_declaration" {
            parsed.imports.push(self.parse_import(node));
            return;
        }

        if t == "extension_declaration" || (TYPE_DECL_NODES.contains(&t) && self.is_extension(node))
        {
            // An extension declares no new type: scope members under the
            // extended type's name without emitting a symbol for it.
            if let Some(name) = self.extension_type_name(node) {
                self.scope.push(name.clone());
                self.scope_kinds.push(t);
                self.var_types.push();
                self.var_types.record(Some("self"), Some(&name));
                self.walk(node, parsed);
                self.var_types.pop();
                self.scope.pop();
                self.scope_kinds.pop();
            } else {
                self.walk(node, parsed);
            }
            return;
        }

        if TYPE_DECL_NODES.contains(&t) {
            if let Some(sym) = self.parse_type_decl(node) {
                let name = sym.name.clone();
                parsed.symbols.push(sym);
                self.scope.push(name.clone());
                self.scope_kinds.push(t);
                self.var_types.push();
                self.var_types.record(Some("self"), Some(&name));
                self.walk(node, parsed);
                self.var_types.pop();
                self.scope.pop();
                self.scope_kinds.pop();
            }
            return;
        }

        if t == "function_declaration" || t == "init_declaration" {
            if let Some(sym) = self.parse_function(node) {
                let name = sym.name.clone();
                parsed.symbols.push(sym);
                self.scope.push(name);
                self.scope_kinds.push(t);
                self.var_types.push();
                self.record_param_types(node);
                self.walk(node, parsed);
                self.var_types.pop();
                self.scope.pop();
                self.scope_kinds.pop();
            }
            return;
        }

        if t == "property_declaration" {
            if let Some(sym) = self.parse_property(node) {
                parsed.symbols.push(sym);
            }
            self.record_property_type(node);
            self.walk(node, parsed);
            return;
        }

        if t == "call_expression" {
            if let Some(edge) = self.parse_call(node) {
                parsed.edges.push(edge);
            }
            self.walk(node, parsed);
            return;
        }

        if t == "statements" {
            // Each brace level is a lexical scope for var-to-type tracking.
            self.var_types.push();
            self.walk(node, parsed);
            self.var_types.pop();
            return;
        }

        self.walk(node, parsed);
    }

    fn qualified(&self, name: &str) -> String {
        if self.scope.is_empty() {
            return name.to_string();
        }
        let mut qualified = self.scope.join(".");
        qualified.push('.');
        qualified.push_str(name);
        qualified
    }

    fn edge_owner(&self) -> String {
        self.scope.last().cloned().unwrap_or_default()
    }

    fn is_extension(&self, node: Node<'_>) -> bool {
        children(node)
            .iter()
            .any(|c| !c.is_named() && node_text(*c, self.source) == "extension")
    }

    /// Bare name of the type an extension declaration extends.
    fn extension_type_name(&self, node: Node<'_>) -> Option<String> {
        let mut name = None;
        for child in children(node) {
            if child.kind() == "user_type" {
                // `Ns.Foo` nests both segments: the last one is the bare name.
                for sub in children(child) {
                    if sub.kind() == "type_identifier" {
                        name = Some(node_text(sub, self.source).trim().to_string());
                    }
                }
            } else if child.kind() == "type_identifier" {
                name = Some(node_text(child, self.source).trim().to_string());
            }
        }
        name
    }

    fn collect_modifiers(&self, node: Node<'_>) -> Vec<String> {
        let mut mods = Vec::new();
        for child in children(node) {
            if child.kind() == "modifiers" {
                // Nested modifiers may contain attributes; keep only modifier keywords.
                for m in children(child) {
                    let txt = node_text(m, self.source).trim().to_string();
                    if SWIFT_MODIFIERS.contains(&txt.as_str()) {
                        mods.push(txt);
                    }
                }
            } else {
                let txt = node_text(child, self.source).trim().to_string();
                if SWIFT_MODIFIERS.contains(&txt.as_str()) {
                    mods.push(txt);
                }
            }
        }
        mods
    }

    fn parse_type_decl(&mut self, node: Node<'_>) -> Option<Symbol> {
        let name = children(node)
            .into_iter()
            .find(|c| c.kind() == "type_identifier")
            .map(|c| node_text(c, self.source).trim().to_string())?;
        let kind = classify_type(node, self.source);
        let mods = self.collect_modifiers(node);
        self.parse_inheritance(node, &name);
        Some(Symbol {
            name: name.clone(),
            kind: kind.into(),
            qualified_name: Some(self.qualified(&name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: mods,
            ..Symbol::default()
        })
    }

    fn parse_function(&self, node: Node<'_>) -> Option<Symbol> {
        let arity = self.param_arity(node);
        let mut name: Option<String> = None;
        for child in children(node) {
            if child.kind() == "identifier" || child.kind() == "simple_identifier" {
                name = Some(node_text(child, self.source).trim().to_string());
                break;
            }
            // init is a special function with no plain identifier
            if child.kind() == "init" {
                name = Some("init".to_string());
                break;
            }
        }
        let name = name?;
        let kind = if self
            .scope_kinds
            .last()
            .is_some_and(|k| TYPE_SCOPE_NODES.contains(k))
        {
            "method"
        } else {
            "function"
        };
        let qualified = self.qualified(&name);
        Some(Symbol {
            name,
            kind: kind.to_string(),
            qualified_name: Some(qualified),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: self.collect_modifiers(node),
            arity,
            ..Symbol::default()
        })
    }

    /// Parameter count; None when a variadic parameter (`...`) makes the
    /// declared count unknowable.
    fn param_arity(&self, node: Node<'_>) -> Option<usize> {
        let mut count = 0;
        for child in children(node) {
            if child.kind() != "parameter" {
                continue;
            }
            if children(child)
                .iter()
                .any(|c| !c.is_named() && node_text(*c, self.source) == "...")
            {
                return None;
            }
            count += 1;
        }
        Some(count)
    }

    fn record_param_types(&mut self, node: Node<'_>) {
        for child in children(node) {
            if child.kind() == "parameter" {
                let (name, type_name) = self.param_name_type(child);
                self.var_types.record(name.as_deref(), type_name.as_deref());
            }
        }
    }

    /// A parameter's local name after any external label, plus declared type.
    fn param_name_type(&self, node: Node<'_>) -> (Option<String>, Option<String>) {
        let mut name = None;
        let mut type_name = None;
        for c in children(node) {
            if c.kind() == "simple_identifier" || c.kind() == "identifier" {
                name = Some(node_text(c, self.source).trim().to_string());
            } else if c.kind() == "user_type" {
                type_name = self.type_identifier_text(c);
                break;
            }
        }
        (name, type_name)
    }

    /// Identifier named by a declaration's `pattern` child.
    fn pattern_name(&self, node: Node<'_>) -> Option<String> {
        let pattern = children(node).into_iter().find(|c| c.kind() == "pattern")?;
        children(pattern)
            .into_iter()
            .find(|c| c.kind() == "simple_identifier" || c.kind() == "identifier")
            .map(|c| node_text(c, self.source).trim().to_string())
    }

    fn record_property_type(&mut self, node: Node<'_>) {
        let name = self.pattern_name(node);
        let mut type_name: Option<String> = None;
        for child in children(node) {
            if child.kind() == "type_annotation" {
                for c in children(child) {
                    if c.kind() == "user_type" {
                        type_name = self.type_identifier_text(c);
                    }
                }
            } else if type_name.is_none()
                && child.kind() == "call_expression"
                && let Some(callee) = self.initializer_callee(child)
            {
                type_name = Some(callee);
            }
        }
        self.var_types.record(name.as_deref(), type_name.as_deref());
    }

    /// Callee of an initializer call when it names a type (capitalized).
    fn initializer_callee(&self, node: Node<'_>) -> Option<String> {
        let lead = children(node).first().copied()?;
        if lead.kind() != "simple_identifier" {
            return None;
        }
        let txt = node_text(lead, self.source).trim().to_string();
        match txt.chars().next() {
            Some(first) if first.is_uppercase() => Some(txt),
            _ => None,
        }
    }

    fn type_identifier_text(&self, node: Node<'_>) -> Option<String> {
        children(node)
            .into_iter()
            .find(|c| c.kind() == "type_identifier")
            .map(|c| node_text(c, self.source).trim().to_string())
    }

    fn parse_property(&self, node: Node<'_>) -> Option<Symbol> {
        // The grammar wraps the binding name: pattern -> simple_identifier.
        let name = self.pattern_name(node)?;
        let qualified = self.qualified(&name);
        Some(Symbol {
            name,
            kind: "property".to_string(),
            qualified_name: Some(qualified),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: self.collect_modifiers(node),
            ..Symbol::default()
        })
    }

    fn parse_import(&self, node: Node<'_>) -> Import {
        let path = children(node)
            .into_iter()
            .find(|c| c.kind() == "identifier")
            .map(|c| node_text(c, self.source).trim().to_string())
            .unwrap_or_else(|| node_text(node, self.source).trim().to_string());
        Import {
            imported_path: path,
            line: node.start_position().row + 1,
            local_alias: None,
        }
    }

    /// Superclass extends edge plus remaining conformance implements edges.
    fn parse_inheritance(&mut self, node: Node<'_>, child_name: &str) {
        let mut specifiers = Vec::new();
        for child in children(node) {
            if child.kind() == "type_inheritance_clause" || child.kind() == "inheritance_specifier"
            {
                Self::collect_specifiers(child, &mut specifiers);
            }
        }
        // Class declarations parsed from struct or enum keywords have no superclass.
        let has_struct_or_enum_keyword = children(node).iter().any(|c| {
            let txt = node_text(*c, self.source);
            let txt = txt.trim();
            txt == "struct" || txt == "enum"
        });
        let superclass_first = node.kind() == "actor_declaration"
            || (node.kind() == "class_declaration" && !has_struct_or_enum_keyword);
        for (i, spec) in specifiers.iter().enumerate() {
            let spec_text = node_text(*spec, self.source);
            let name = spec_text.trim().split('<').next().unwrap_or("").trim();
            if name.is_empty() {
                continue;
            }
            let kind = if superclass_first && i == 0 {
                "extends"
            } else {
                "implements"
            };
            self.pending_edges.push(Edge {
                source_name: child_name.to_string(),
                kind: kind.to_string(),
                target_name: name.to_string(),
                line: spec.start_position().row + 1,
                ..Edge::default()
            });
        }
    }

    fn collect_specifiers<'a>(node: Node<'a>, out: &mut Vec<Node<'a>>) {
        if node.kind() == "inheritance_specifier" {
            out.push(node);
            return;
        }
        for child in children(node) {
            Self::collect_specifiers(child, out);
        }
    }

    fn parse_call(&self, node: Node<'_>) -> Option<Edge> {
        // call_expression: the called function is the first child; for
        // `foo.bar()` it's a navigation_expression/member_expression.
        let lead = children(node).first().copied()?;
        let target = self.extract_callee(lead)?;
        let receiver = self.call_receiver_text(node);
        Some(Edge {
            source_name: self.edge_owner(),
            kind: "calls".to_string(),
            target_name: target,
            line: node.start_position().row + 1,
            receiver_type: self.receiver_type(receiver.as_deref()),
            call_arity: self.call_arity(node),
            ..Edge::default()
        })
    }

    /// Receiver text from the nested navigation expression target.
    fn call_receiver_text(&self, node: Node<'_>) -> Option<String> {
        let lead = children(node).first().copied()?;
        if lead.kind() != "navigation_expression" && lead.kind() != "member_expression" {
            return None;
        }
        let target = lead.child_by_field_name("target")?;
        Some(node_text(target, self.source).trim().to_string())
    }

    fn receiver_type(&self, receiver: Option<&str>) -> Option<String> {
        let receiver = receiver?;
        if let Some(tracked) = self.var_types.resolve(receiver)
            && !tracked.is_empty()
        {
            return Some(tracked);
        }
        infer_receiver_type(Some(receiver))
    }

    /// Argument count: value_argument children plus each trailing closure.
    fn call_arity(&self, node: Node<'_>) -> Option<usize> {
        let suffix = child_of_type(node, &["call_suffix"])?;
        let mut count = 0;
        for child in children(suffix) {
            if child.kind() == "value_arguments" {
                count += children(child)
                    .into_iter()
                    .filter(|a| a.kind() == "value_argument")
                    .count();
            } else if child.kind() == "lambda_literal" {
                count += 1;
            }
        }
        Some(count)
    }

    fn extract_callee(&self, node: Node<'_>) -> Option<String> {
        if node.kind() == "identifier" || node.kind() == "simple_identifier" {
            return Some(node_text(node, self.source).trim().to_string());
        }
        if node.kind() == "navigation_expression" || node.kind() == "member_expression" {
            return self.tail_identifier(node);
        }
        let txt = node_text(node, self.source).trim().to_string();
        let last = txt.rsplit('.').next().unwrap_or("");
        if last.is_empty() {
            None
        } else {
            Some(last.to_string())
        }
    }

    fn tail_identifier(&self, node: Node<'_>) -> Option<String> {
        let mut last = None;
        let mut stack = vec![node];
        while let Some(n) = stack.pop() {
            if n.kind() == "identifier" || n.kind() == "simple_identifier" {
                last = Some(node_text(n, self.source).trim().to_string());
            }
            let mut kids = children(n);
            kids.reverse();
            stack.extend(kids);
        }
        last
    }
}

fn classify_type(node: Node<'_>, source: &[u8]) -> &'static str {
    match node.kind() {
        // struct maps onto the class kind
        "struct_declaration" | "actor_declaration" => "class",
        "protocol_declaration" => "interface",
        "enum_declaration" => "enum",
        // class_declaration may actually be enum/struct in some grammar builds
        _ => {
            for child in children(node) {
                match node_text(child, source).trim() {
                    "enum" => return "enum",
                    "struct" => return "class",
                    _ => {}
                }
            }
            "class"
        }
    }
}

// --- objc ------------------------------------------------------------------

/// Return an existing extension-stripped path for a quoted Objective-C import.
pub fn resolve_relative_objc_import(importer: &Path, spec: &str) -> Option<String> {
    let base = importer.parent().unwrap_or(Path::new(".")).join(spec);
    if !base.is_file() {
        return None;
    }
    let base = base.canonicalize().ok()?;
    if base.extension().is_none() {
        return Some(base.to_string_lossy().into_owned());
    }
    Some(
        base.parent()?
            .join(base.file_stem()?)
            .to_string_lossy()
            .into_owned(),
    )
}

/// Objective-C symbol/edge extraction.
pub struct ObjCParser;

impl ObjCParser {
    pub const LANGUAGE: &'static str = "objc";

    /// Parse the file at `path`.
    pub fn parse_file(&self, path: &Path) -> std::io::Result<ParsedFile> {
        let source = std::fs::read(path)?;
        Ok(self.parse_source(path, &source))
    }

    /// Parse `source`.
    pub fn parse_source(&self, path: &Path, source: &[u8]) -> ParsedFile {
        let mut parser = registry::parser(Self::LANGUAGE).expect("objc is routed");
        let tree = parser.parse(source, None).expect("tree-sitter parse");
        let mut parsed = ParsedFile {
            path: path.to_string_lossy().into_owned(),
            language: Self::LANGUAGE.to_string(),
            hash: sha256_hex(source),
            line_count: source.iter().filter(|&&b| b == b'\n').count() + 1,
            ..ParsedFile::default()
        };
        let mut walk = ObjcWalk {
            source,
            path: path.to_path_buf(),
            file_stem: path
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default(),
            scope: Vec::new(),
            callable_scope: Vec::new(),
            types: ScopeTypeTracker::new(),
            pending_edges: Vec::new(),
        };
        walk.walk(tree.root_node(), &mut parsed);
        parsed.edges.extend(walk.pending_edges);
        parsed
    }
}

struct ObjcWalk<'a> {
    source: &'a [u8],
    path: PathBuf,
    file_stem: String,
    scope: Vec<String>,
    callable_scope: Vec<String>,
    /// Scope-ordered var-to-type map for receiver inference (params + locals).
    types: ScopeTypeTracker,
    pending_edges: Vec<Edge>,
}

impl ObjcWalk<'_> {
    fn walk(&mut self, node: Node<'_>, parsed: &mut ParsedFile) {
        for child in children(node) {
            if is_comment(child, ObjCParser::LANGUAGE) {
                if let Some(record) = rationale::extract_rationale(child, self.source) {
                    parsed.rationale.push(record);
                }
                continue;
            }
            self.visit(child, parsed);
        }
    }

    fn visit(&mut self, node: Node<'_>, parsed: &mut ParsedFile) {
        let t = node.kind();

        if t == "preproc_include" {
            if let Some(imp) = self.parse_import(node) {
                parsed.imports.push(imp);
            }
            return;
        }

        if t == "protocol_declaration" {
            if let Some(sym) = self.parse_protocol(node) {
                let name = sym.name.clone();
                parsed.symbols.push(sym);
                self.scope.push(name);
                self.walk(node, parsed);
                self.scope.pop();
            } else {
                self.walk(node, parsed);
            }
            return;
        }

        if t == "class_interface" {
            let (sym, scope_name) = self.parse_class_interface(node);
            if let Some(sym) = sym {
                parsed.symbols.push(sym);
            }
            if let Some(scope_name) = scope_name {
                self.scope.push(scope_name);
                self.walk(node, parsed);
                self.scope.pop();
            } else {
                self.walk(node, parsed);
            }
            return;
        }

        if t == "class_implementation" {
            let (sym, scope_name) = self.parse_class_implementation(node);
            if let Some(sym) = sym {
                parsed.symbols.push(sym);
            }
            if let Some(scope_name) = scope_name {
                self.scope.push(scope_name);
                self.walk(node, parsed);
                self.scope.pop();
            } else {
                self.walk(node, parsed);
            }
            return;
        }

        if t == "property_declaration" {
            if let Some(sym) = self.parse_property(node) {
                parsed.symbols.push(sym);
            }
            return;
        }

        if t == "method_declaration" || t == "method_definition" {
            if let Some(sym) = self.parse_method(node) {
                let name = sym.name.clone();
                parsed.symbols.push(sym);
                self.callable_scope.push(name);
                self.types.push();
                self.record_method_params(node);
                self.walk(node, parsed);
                self.types.pop();
                self.callable_scope.pop();
            } else {
                self.walk(node, parsed);
            }
            return;
        }

        if t == "compound_statement" {
            self.types.push();
            self.walk(node, parsed);
            self.types.pop();
            return;
        }

        if t == "message_expression" {
            if let Some(edge) = self.parse_message(node) {
                parsed.edges.push(edge);
            }
            self.walk(node, parsed);
            return;
        }

        if t == "call_expression" {
            if let Some(edge) = self.parse_call(node) {
                parsed.edges.push(edge);
            }
            self.walk(node, parsed);
            return;
        }

        if t == "declaration" {
            self.record_declaration(node);
        }

        self.walk(node, parsed);
    }

    // --- helpers -----------------------------------------------------------

    /// ObjC uses file-stem prefix like TypeScript.
    fn qualified(&self, name: &str) -> String {
        let mut parts = Vec::with_capacity(self.scope.len() + 2);
        parts.push(self.file_stem.clone());
        parts.extend(self.scope.iter().cloned());
        parts.push(name.to_string());
        parts.join(".")
    }

    fn edge_owner(&self) -> String {
        self.callable_scope
            .last()
            .or_else(|| self.scope.last())
            .cloned()
            .unwrap_or_default()
    }

    fn adopted_protocol_names(&self, params_node: Node<'_>) -> Vec<String> {
        children(params_node)
            .into_iter()
            .filter(|c| c.kind() == "type_name")
            .filter_map(|c| child_of_type(c, &["type_identifier"]))
            .map(|tid| node_text(tid, self.source).trim().to_string())
            .collect()
    }

    // --- declarations ------------------------------------------------------

    fn parse_protocol(&mut self, node: Node<'_>) -> Option<Symbol> {
        let name = find_name(node, self.source)?;
        let sym = Symbol {
            name: name.clone(),
            kind: "protocol".to_string(),
            qualified_name: Some(self.qualified(&name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            ..Symbol::default()
        };
        if let Some(ref_list) = child_of_type(node, &["protocol_reference_list"]) {
            for c in children(ref_list) {
                if c.kind() == "identifier" {
                    self.pending_edges.push(Edge {
                        source_name: name.clone(),
                        kind: "extends".to_string(),
                        target_name: node_text(c, self.source).trim().to_string(),
                        line: node.start_position().row + 1,
                        ..Edge::default()
                    });
                }
            }
        }
        Some(sym)
    }

    fn parse_class_interface(&mut self, node: Node<'_>) -> (Option<Symbol>, Option<String>) {
        let is_category = children(node).iter().any(|c| c.kind() == "(");
        let idents: Vec<Node> = children(node)
            .into_iter()
            .filter(|c| c.kind() == "identifier")
            .collect();
        if idents.is_empty() {
            return (None, None);
        }
        let class_name = node_text(idents[0], self.source).trim().to_string();

        let (sym_name, kind) = if is_category && idents.len() > 1 {
            let cat_name = node_text(idents[1], self.source).trim().to_string();
            (format!("{class_name}+{cat_name}"), "category")
        } else {
            // superclass: ':' identifier
            let mut prev_colon = false;
            for c in children(node) {
                if c.kind() == ":" {
                    prev_colon = true;
                    continue;
                }
                if prev_colon && c.kind() == "identifier" {
                    self.pending_edges.push(Edge {
                        source_name: class_name.clone(),
                        kind: "extends".to_string(),
                        target_name: node_text(c, self.source).trim().to_string(),
                        line: node.start_position().row + 1,
                        ..Edge::default()
                    });
                    prev_colon = false;
                }
            }
            // adopted protocols: <Proto1, Proto2>
            if let Some(params) = child_of_type(node, &["parameterized_arguments"]) {
                for pname in self.adopted_protocol_names(params) {
                    self.pending_edges.push(Edge {
                        source_name: class_name.clone(),
                        kind: "implements".to_string(),
                        target_name: pname,
                        line: node.start_position().row + 1,
                        ..Edge::default()
                    });
                }
            }
            (class_name.clone(), "class")
        };

        let sym = Symbol {
            name: sym_name.clone(),
            kind: kind.to_string(),
            qualified_name: Some(self.qualified(&sym_name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            ..Symbol::default()
        };
        (Some(sym), Some(class_name))
    }

    fn parse_class_implementation(&mut self, node: Node<'_>) -> (Option<Symbol>, Option<String>) {
        let is_category = children(node).iter().any(|c| c.kind() == "(");
        let idents: Vec<Node> = children(node)
            .into_iter()
            .filter(|c| c.kind() == "identifier")
            .collect();
        if idents.is_empty() {
            return (None, None);
        }
        let class_name = node_text(idents[0], self.source).trim().to_string();
        let (sym_name, kind) = if is_category && idents.len() > 1 {
            let cat_name = node_text(idents[1], self.source).trim().to_string();
            (format!("{class_name}+{cat_name}"), "category_impl")
        } else {
            (class_name.clone(), "class")
        };
        let sym = Symbol {
            name: sym_name.clone(),
            kind: kind.to_string(),
            qualified_name: Some(self.qualified(&sym_name)),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            ..Symbol::default()
        };
        (Some(sym), Some(class_name))
    }

    fn parse_property(&self, node: Node<'_>) -> Option<Symbol> {
        let struct_decl = child_of_type(node, &["struct_declaration"])?;
        let mut name: Option<String> = None;
        for c in children(struct_decl) {
            if c.kind() == "struct_declarator" {
                let ptr = child_of_type(c, &["pointer_declarator"]);
                name = find_name(ptr.unwrap_or(c), self.source);
            } else if c.kind() == "identifier" && name.is_none() {
                name = Some(node_text(c, self.source).trim().to_string());
            }
        }
        let name = name?;
        let qualified = self.qualified(&name);
        Some(Symbol {
            name,
            kind: "property".to_string(),
            qualified_name: Some(qualified),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            ..Symbol::default()
        })
    }

    fn parse_method(&self, node: Node<'_>) -> Option<Symbol> {
        let name = find_name(node, self.source)?;
        let mut mods = Vec::new();
        for c in children(node) {
            if c.kind() == "+" {
                mods.push("class_method".to_string());
                break;
            }
            if c.kind() == "-" {
                mods.push("instance_method".to_string());
                break;
            }
        }
        let qualified = self.qualified(&name);
        Some(Symbol {
            name,
            kind: "method".to_string(),
            qualified_name: Some(qualified),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: mods,
            arity: self.method_arity(node),
            ..Symbol::default()
        })
    }

    fn parse_import(&self, node: Node<'_>) -> Option<Import> {
        for c in children(node) {
            if c.kind() == "string_literal" {
                let content = child_of_type(c, &["string_content"]);
                let text = match content {
                    Some(content) => node_text(content, self.source).trim().to_string(),
                    None => node_text(c, self.source)
                        .trim()
                        .trim_matches('"')
                        .to_string(),
                };
                if text.is_empty() {
                    return None;
                }
                let resolved = resolve_relative_objc_import(&self.path, &text);
                return Some(Import {
                    imported_path: resolved.unwrap_or(text),
                    line: node.start_position().row + 1,
                    local_alias: None,
                });
            }
            if c.kind() == "system_lib_string" {
                let mut text = node_text(c, self.source).trim().to_string();
                if text.starts_with('<') && text.ends_with('>') {
                    text = text[1..text.len() - 1].to_string();
                }
                return Some(Import {
                    imported_path: text,
                    line: node.start_position().row + 1,
                    local_alias: None,
                });
            }
        }
        None
    }

    // --- edges ---------------------------------------------------------------

    fn parse_message(&self, node: Node<'_>) -> Option<Edge> {
        let mut cursor = node.walk();
        let keywords: Vec<Node> = node.children_by_field_name("method", &mut cursor).collect();
        if keywords.is_empty() {
            return None;
        }
        let receiver = node.child_by_field_name("receiver");
        let args = children(node)
            .into_iter()
            .filter(|c| c.is_named())
            .filter(|c| {
                !receiver.is_some_and(|r| same_node(c, &r))
                    && !keywords.iter().any(|k| same_node(k, c))
            })
            .count();
        Some(Edge {
            source_name: self.edge_owner(),
            kind: "calls".to_string(),
            target_name: node_text(keywords[0], self.source).trim().to_string(),
            line: node.start_position().row + 1,
            receiver_type: self.receiver_type(receiver),
            call_arity: Some(args),
            ..Edge::default()
        })
    }

    fn parse_call(&self, node: Node<'_>) -> Option<Edge> {
        let ident = children(node)
            .into_iter()
            .find(|c| c.kind() == "identifier")?;
        Some(Edge {
            source_name: self.edge_owner(),
            kind: "calls".to_string(),
            target_name: node_text(ident, self.source).trim().to_string(),
            line: node.start_position().row + 1,
            call_arity: self.call_arity(node),
            ..Edge::default()
        })
    }

    // --- arity + type tracking ----------------------------------------------

    /// method_parameter count; None when variadic (`...`).
    fn method_arity(&self, node: Node<'_>) -> Option<usize> {
        if children(node)
            .iter()
            .any(|c| node_text(*c, self.source) == "...")
        {
            return None;
        }
        Some(
            children(node)
                .into_iter()
                .filter(|c| c.kind() == "method_parameter")
                .count(),
        )
    }

    /// Named-child count of the `argument_list`.
    fn call_arity(&self, node: Node<'_>) -> Option<usize> {
        let args = node.child_by_field_name("arguments")?;
        Some(children(args).into_iter().filter(|c| c.is_named()).count())
    }

    /// Type of a message receiver, or None when not inferable.
    fn receiver_type(&self, receiver: Option<Node<'_>>) -> Option<String> {
        let receiver = receiver?;
        if receiver.kind() != "identifier" {
            return None;
        }
        let name = node_text(receiver, self.source).trim().to_string();
        if name == "self" {
            return self.scope.last().cloned();
        }
        if name == "super" {
            // the superclass is declared in the header, not here
            return None;
        }
        if let Some(tracked) = self.types.resolve(&name) {
            return Some(tracked);
        }
        infer_receiver_type(Some(&name))
    }

    /// Record each typed method_parameter into the tracker's method scope.
    fn record_method_params(&mut self, node: Node<'_>) {
        for c in children(node) {
            if c.kind() != "method_parameter" {
                continue;
            }
            let param_type = self.param_type_name(c);
            let name = find_name(c, self.source);
            if let (Some(param_type), Some(name)) = (param_type, name) {
                self.types.record(Some(&name), Some(&param_type));
            }
        }
    }

    /// Bare class-like type of a method_parameter (`method_type -> type_name ->
    /// type_identifier`); primitives and protocol-qualified shapes stay None.
    fn param_type_name(&self, param: Node<'_>) -> Option<String> {
        let mt = child_of_type(param, &["method_type"])?;
        let tn = child_of_type(mt, &["type_name"])?;
        let tid = child_of_type(tn, &["type_identifier"])?;
        Some(node_text(tid, self.source).trim().to_string())
    }

    /// Record each typed local declarator (`NSString *s = x, *t;`).
    fn record_declaration(&mut self, node: Node<'_>) {
        let type_node = match node.child_by_field_name("type") {
            Some(t) if t.kind() == "type_identifier" => t,
            _ => return,
        };
        let type_name = node_text(type_node, self.source).trim().to_string();
        for child in children(node) {
            if child.is_named()
                && !same_node(&child, &type_node)
                && let Some(name) = self.declared_var_name(child)
            {
                self.types.record(Some(&name), Some(&type_name));
            }
        }
    }

    /// Identifier bound by a declarator chain (init/pointer/array).
    fn declared_var_name(&self, node: Node<'_>) -> Option<String> {
        let mut node = node;
        loop {
            if node.kind() == "identifier" {
                return Some(node_text(node, self.source).trim().to_string());
            }
            if node.kind() == "function_declarator" {
                // a function name, not a variable
                return None;
            }
            let inner = node
                .child_by_field_name("declarator")
                .or_else(|| child_of_type(node, &["identifier"]))?;
            node = inner;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// (name, kind, qualified_name, modifiers, arity) asserted against Python output.
    type SymbolRow = (String, String, String, Vec<String>, Option<usize>);

    /// Mirrors a live Python SwiftParser run over the same source
    /// (`SwiftParser().parse('/tmp/probe_ext.swift')`).
    #[test]
    fn swift_extracts_extensions_modifiers_and_variadic_arity() {
        let source = r#"
import Foundation

protocol Shape {
    var name: String { get }
}

struct Box {
    var label: String = "x"
}

extension Box: Shape {
    var name: String { return label }
    func describe() -> String {
        return name
    }
}

public final class Registry {
    private var items: [String] = []
    lazy var cached: Int = compute()
    func compute() -> Int { return items.count }
    func variadic(_ xs: Int...) -> Int { return 0 }
}
"#;
        let parsed = SwiftParser.parse_source(Path::new("probe_ext.swift"), source.as_bytes());

        let symbols: Vec<SymbolRow> = parsed
            .symbols
            .iter()
            .map(|s| {
                (
                    s.name.clone(),
                    s.kind.clone(),
                    s.qualified_name.clone().expect("qualified"),
                    s.modifiers.clone(),
                    s.arity,
                )
            })
            .collect();
        assert_eq!(
            symbols,
            vec![
                (
                    "Shape".into(),
                    "interface".into(),
                    "Shape".into(),
                    vec![],
                    None
                ),
                ("Box".into(), "class".into(), "Box".into(), vec![], None),
                (
                    "label".into(),
                    "property".into(),
                    "Box.label".into(),
                    vec![],
                    None
                ),
                (
                    "name".into(),
                    "property".into(),
                    "Box.name".into(),
                    vec![],
                    None
                ),
                (
                    "describe".into(),
                    "method".into(),
                    "Box.describe".into(),
                    vec![],
                    Some(0)
                ),
                (
                    "Registry".into(),
                    "class".into(),
                    "Registry".into(),
                    vec!["public".into(), "final".into()],
                    None
                ),
                (
                    "items".into(),
                    "property".into(),
                    "Registry.items".into(),
                    vec!["private".into()],
                    None
                ),
                (
                    "cached".into(),
                    "property".into(),
                    "Registry.cached".into(),
                    vec!["lazy".into()],
                    None
                ),
                (
                    "compute".into(),
                    "method".into(),
                    "Registry.compute".into(),
                    vec![],
                    Some(0)
                ),
                (
                    "variadic".into(),
                    "method".into(),
                    "Registry.variadic".into(),
                    vec![],
                    None
                ),
            ]
        );

        let edges: Vec<(String, String, usize, Option<usize>)> = parsed
            .edges
            .iter()
            .map(|e| {
                (
                    e.source_name.clone(),
                    e.target_name.clone(),
                    e.line,
                    e.call_arity,
                )
            })
            .collect();
        assert_eq!(
            edges,
            vec![("Registry".into(), "compute".into(), 21, Some(0))]
        );

        assert_eq!(parsed.imports.len(), 1);
        assert_eq!(parsed.imports[0].imported_path, "Foundation");
        assert_eq!(parsed.imports[0].line, 2);
    }

    /// Mirrors a live Python ObjCParser run over the same source
    /// (`ObjCParser().parse('/tmp/probe_cat.m')`).
    #[test]
    fn objc_extracts_categories_and_protocols() {
        let source = r#"
#import "Peer.h"
#import <UIKit/UIKit.h>

@class Forward;

@protocol Shape <NSObject>
- (instancetype)init;
@end

@interface Box : NSObject <Shape>
@property (nonatomic, copy) NSString *label;
@property CGFloat size;
@end

@interface Box (Helpers)
- (NSString *)describe;
@end

@implementation Box (Helpers)
- (NSString *)describe {
    return [self label];
}
@end
"#;
        let parsed = ObjCParser.parse_source(Path::new("probe_cat.m"), source.as_bytes());

        let symbols: Vec<SymbolRow> = parsed
            .symbols
            .iter()
            .map(|s| {
                (
                    s.name.clone(),
                    s.kind.clone(),
                    s.qualified_name.clone().expect("qualified"),
                    s.modifiers.clone(),
                    s.arity,
                )
            })
            .collect();
        assert_eq!(
            symbols,
            vec![
                (
                    "Shape".into(),
                    "protocol".into(),
                    "probe_cat.Shape".into(),
                    vec![],
                    None
                ),
                (
                    "init".into(),
                    "method".into(),
                    "probe_cat.Shape.init".into(),
                    vec!["instance_method".into()],
                    Some(0)
                ),
                (
                    "Box".into(),
                    "class".into(),
                    "probe_cat.Box".into(),
                    vec![],
                    None
                ),
                (
                    "label".into(),
                    "property".into(),
                    "probe_cat.Box.label".into(),
                    vec![],
                    None
                ),
                (
                    "size".into(),
                    "property".into(),
                    "probe_cat.Box.size".into(),
                    vec![],
                    None
                ),
                (
                    "Box+Helpers".into(),
                    "category".into(),
                    "probe_cat.Box+Helpers".into(),
                    vec![],
                    None
                ),
                (
                    "describe".into(),
                    "method".into(),
                    "probe_cat.Box.describe".into(),
                    vec!["instance_method".into()],
                    Some(0)
                ),
                (
                    "Box+Helpers".into(),
                    "category_impl".into(),
                    "probe_cat.Box+Helpers".into(),
                    vec![],
                    None
                ),
                (
                    "describe".into(),
                    "method".into(),
                    "probe_cat.Box.describe".into(),
                    vec!["instance_method".into()],
                    Some(0)
                ),
            ]
        );

        let edges: Vec<(String, String, String, usize, Option<usize>)> = parsed
            .edges
            .iter()
            .map(|e| {
                (
                    e.source_name.clone(),
                    e.kind.clone(),
                    e.target_name.clone(),
                    e.line,
                    e.call_arity,
                )
            })
            .collect();
        assert_eq!(
            edges,
            vec![
                (
                    "describe".into(),
                    "calls".into(),
                    "label".into(),
                    22,
                    Some(0)
                ),
                ("Shape".into(), "extends".into(), "NSObject".into(), 7, None),
                ("Box".into(), "extends".into(), "NSObject".into(), 11, None),
                ("Box".into(), "implements".into(), "Shape".into(), 11, None),
            ]
        );

        let imports: Vec<(String, usize)> = parsed
            .imports
            .iter()
            .map(|i| (i.imported_path.clone(), i.line))
            .collect();
        assert_eq!(
            imports,
            vec![("Peer.h".into(), 2), ("UIKit/UIKit.h".into(), 3)]
        );
    }

    #[test]
    fn relative_objc_import_resolves_existing_headers() {
        let dir = std::env::temp_dir().join(format!("cairn-objc-import-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("create temp dir");
        std::fs::write(dir.join("Peer.h"), "").expect("write header");
        let importer = dir.join("Sample.m");

        assert_eq!(
            resolve_relative_objc_import(&importer, "Peer.h"),
            // canonicalize resolves symlinked tmp dirs, like Python's resolve().
            Some(
                dir.canonicalize()
                    .expect("canonical temp dir")
                    .join("Peer")
                    .to_string_lossy()
                    .into_owned()
            )
        );
        assert_eq!(resolve_relative_objc_import(&importer, "Missing.h"), None);

        std::fs::remove_dir_all(&dir).expect("clean temp dir");
    }
}
