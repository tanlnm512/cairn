//! JVM-family parsers: kotlin, java.

use std::collections::HashMap;
use std::path::Path;

use tree_sitter::Node;

use crate::generic::children;
use crate::model::{Edge, Import, ParsedFile, Symbol, sha256_hex};
use crate::rationale;
use crate::registry;

fn node_text(source: &[u8], node: Node<'_>) -> String {
    String::from_utf8_lossy(&source[node.start_byte()..node.end_byte()]).into_owned()
}

fn node_text_trimmed(source: &[u8], node: Node<'_>) -> String {
    node_text(source, node).trim().to_string()
}

fn starts_lower(name: &str) -> bool {
    name.chars().next().is_some_and(|c| c.is_lowercase())
}

fn starts_upper(name: &str) -> bool {
    name.chars().next().is_some_and(|c| c.is_uppercase())
}

fn qualified_name(scope: &[String], name: &str) -> String {
    if scope.is_empty() {
        return name.to_string();
    }
    let mut qualified = scope.join(".");
    qualified.push('.');
    qualified.push_str(name);
    qualified
}

fn current_edge_owner(callable_scope: &[String], scope: &[String]) -> String {
    callable_scope
        .last()
        .cloned()
        .or_else(|| scope.last().cloned())
        .unwrap_or_default()
}

fn rationale_from_comment(
    language: &str,
    node: Node<'_>,
    source: &[u8],
    parsed: &mut ParsedFile,
) -> bool {
    if !rationale::comment_node_types(language).contains(&node.kind()) {
        return false;
    }
    if let Some(record) = rationale::extract_rationale(node, source) {
        parsed.rationale.push(record);
    }
    true
}

fn child_of_type<'a>(node: Node<'a>, types: &[&str]) -> Option<Node<'a>> {
    children(node)
        .into_iter()
        .find(|child| types.contains(&child.kind()))
}

// --- kotlin -----------------------------------------------------------------

const KOTLIN_MODIFIERS: &[&str] = &[
    "public",
    "private",
    "protected",
    "internal",
    "open",
    "abstract",
    "sealed",
    "data",
    "suspend",
    "companion",
    "lateinit",
    "override",
    "final",
    "const",
    "static",
    "value",
    "tailrec",
    "external",
    "infix",
    "operator",
];

const KOTLIN_TYPE_DECL_NODES: &[&str] = &[
    "class_declaration",
    "object_declaration",
    "interface_declaration",
    "enum_declaration",
];

pub struct KotlinParser {
    scope: Vec<String>,
    scope_kinds: Vec<String>,
    callable_scope: Vec<String>,
    pending_edges: Vec<Edge>,
    var_types: Vec<HashMap<String, String>>,
    field_types: HashMap<String, HashMap<String, String>>,
}

impl Default for KotlinParser {
    fn default() -> Self {
        Self::new()
    }
}

impl KotlinParser {
    pub fn new() -> Self {
        Self {
            scope: Vec::new(),
            scope_kinds: Vec::new(),
            callable_scope: Vec::new(),
            pending_edges: Vec::new(),
            var_types: vec![HashMap::new()],
            field_types: HashMap::new(),
        }
    }

    /// Parse the kotlin file at `path`.
    pub fn parse(&mut self, path: &Path) -> std::io::Result<ParsedFile> {
        let source = std::fs::read(path)?;
        Ok(self.parse_source(path, &source))
    }

    /// Parse in-memory kotlin `source` as if read from `path`.
    pub fn parse_source(&mut self, path: &Path, source: &[u8]) -> ParsedFile {
        let mut parser = registry::parser("kotlin").expect("kotlin grammar is routed");
        let tree = parser.parse(source, None).expect("tree-sitter parse");
        let mut pf = ParsedFile {
            path: path.to_string_lossy().into_owned(),
            language: "kotlin".to_string(),
            hash: sha256_hex(source),
            line_count: source.iter().filter(|&&b| b == b'\n').count() + 1,
            ..ParsedFile::default()
        };
        self.scope.clear();
        self.scope_kinds.clear();
        self.callable_scope.clear();
        self.pending_edges.clear();
        self.var_types = vec![HashMap::new()];
        self.field_types.clear();
        let root = tree.root_node();
        self.prescan_field_types(root, source);
        self.walk(root, source, &mut pf);
        pf.edges.extend(std::mem::take(&mut self.pending_edges));
        pf
    }

    fn walk(&mut self, node: Node<'_>, source: &[u8], pf: &mut ParsedFile) {
        for child in children(node) {
            if rationale_from_comment("kotlin", child, source, pf) {
                continue;
            }
            self.visit(child, source, pf);
        }
    }

    fn visit(&mut self, node: Node<'_>, source: &[u8], pf: &mut ParsedFile) {
        let kind = node.kind();

        if kind == "import" || kind == "import_header" {
            if let Some(imp) = self.parse_import(node, source) {
                pf.imports.push(imp);
            }
            return;
        }

        if KOTLIN_TYPE_DECL_NODES.contains(&kind) {
            if let Some(sym) = self.parse_type_decl(node, source) {
                let name = sym.name.clone();
                pf.symbols.push(sym);
                self.scope.push(name);
                self.scope_kinds.push(kind.to_string());
                self.walk(node, source, pf);
                self.scope.pop();
                self.scope_kinds.pop();
            }
            return;
        }

        if kind == "function_declaration" {
            if let Some(sym) = self.parse_function(node, source) {
                let name = sym.name.clone();
                self.callable_scope.push(name);
                let mut var_scope = HashMap::new();
                if let Some(enclosing) = self.scope.last() {
                    var_scope.insert("this".to_string(), enclosing.clone());
                }
                for (pname, ptype) in self.param_types(node, source) {
                    var_scope.insert(pname, ptype);
                }
                self.var_types.push(var_scope);
                pf.symbols.push(sym);
                self.walk(node, source, pf);
                self.var_types.pop();
                self.callable_scope.pop();
            }
            return;
        }

        if kind == "property_declaration" {
            if let Some(sym) = self.parse_property(node, source) {
                pf.symbols.push(sym);
            }
            if !self.callable_scope.is_empty()
                && let Some((name, vtype)) = self.var_name_and_type(node, source)
                && let Some(scope) = self.var_types.last_mut()
            {
                scope.insert(name, vtype);
            }
            self.walk(node, source, pf);
            return;
        }

        if kind == "class_parameter" {
            if let Some(sym) = self.parse_class_parameter(node, source) {
                pf.symbols.push(sym);
            }
            self.walk(node, source, pf);
            return;
        }

        if kind == "call_expression"
            && let Some(edge) = self.parse_call(node, source)
        {
            pf.edges.push(edge);
        }

        self.walk(node, source, pf);
    }

    // --- name & modifier helpers ------------------------------------------

    fn classify_type_decl(&self, node: Node<'_>, source: &[u8]) -> &'static str {
        if node.kind() == "class_declaration" {
            for child in children(node) {
                match node_text_trimmed(source, child).as_str() {
                    "interface" => return "interface",
                    "enum" => return "enum",
                    "class" => return "class",
                    _ => {}
                }
            }
            return "class";
        }
        match node.kind() {
            "object_declaration" => "class",
            "interface_declaration" => "interface",
            "enum_declaration" => "enum",
            _ => "class",
        }
    }

    fn collect_modifiers(&self, node: Node<'_>, source: &[u8]) -> Vec<String> {
        let mut mods = Vec::new();
        for child in children(node) {
            if child.kind() == "modifiers" {
                for m in children(child) {
                    let kws = if m.kind() == "inheritance_modifier" {
                        children(m)
                    } else {
                        vec![m]
                    };
                    for kw in kws {
                        let txt = node_text_trimmed(source, kw);
                        if KOTLIN_MODIFIERS.contains(&txt.as_str()) {
                            mods.push(txt);
                        }
                    }
                }
            } else if KOTLIN_MODIFIERS.contains(&child.kind()) {
                let txt = node_text_trimmed(source, child);
                if !txt.is_empty() {
                    mods.push(txt);
                }
            }
        }
        mods
    }

    fn type_identifier(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        children(node)
            .into_iter()
            .find(|child| {
                matches!(
                    child.kind(),
                    "type_identifier" | "simple_identifier" | "identifier"
                )
            })
            .map(|child| node_text_trimmed(source, child))
    }

    // --- declarations ------------------------------------------------------

    fn parse_type_decl(&mut self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = self.type_identifier(node, source)?;

        let kind = self.classify_type_decl(node, source);
        let mods = self.collect_modifiers(node, source);
        let sym = Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name,
            kind: kind.to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: mods,
            ..Symbol::default()
        };
        self.parse_inheritance(node, source, &sym.name);
        Some(sym)
    }

    fn parse_function(&mut self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = children(node)
            .into_iter()
            .find(|child| matches!(child.kind(), "simple_identifier" | "identifier"))
            .map(|child| node_text_trimmed(source, child))?;

        let kind = if self
            .scope_kinds
            .iter()
            .any(|k| k == "class_declaration" || k == "object_declaration")
        {
            "method"
        } else {
            "function"
        };

        let mods = self.collect_modifiers(node, source);
        let sym = Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name: name.clone(),
            kind: kind.to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: mods,
            arity: self.count_parameters(node),
            ..Symbol::default()
        };
        self.emit_type_references(node, source, &name);
        Some(sym)
    }

    fn parse_property(&mut self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let mut name: Option<String> = None;
        for child in children(node) {
            if child.kind() == "variable_declaration" {
                for vc in children(child) {
                    if matches!(vc.kind(), "simple_identifier" | "identifier") {
                        name = Some(node_text_trimmed(source, vc));
                        break;
                    }
                }
            } else if matches!(child.kind(), "simple_identifier" | "identifier") && name.is_none() {
                name = Some(node_text_trimmed(source, child));
            }
        }
        let name = name?;
        let mods = self.collect_modifiers(node, source);
        let sym = Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name: name.clone(),
            kind: "property".to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: mods,
            ..Symbol::default()
        };
        self.emit_type_references(node, source, &name);
        Some(sym)
    }

    fn emit_type_references(&mut self, node: Node<'_>, source: &[u8], owner: &str) {
        for child in children(node) {
            if matches!(
                child.kind(),
                "function_body" | "class_body" | "enum_class_body" | "statements"
            ) {
                continue;
            }
            self.collect_user_types(child, source, owner);
        }
    }

    fn collect_user_types(&mut self, node: Node<'_>, source: &[u8], owner: &str) {
        if node.kind() == "user_type" {
            if let Some(name) = self.usertype_name(node, source) {
                self.pending_edges.push(Edge {
                    source_name: owner.to_string(),
                    kind: "references".to_string(),
                    target_name: name,
                    line: node.start_position().row + 1,
                    ..Edge::default()
                });
            }
            return;
        }
        for child in children(node) {
            self.collect_user_types(child, source, owner);
        }
    }

    fn parse_class_parameter(&mut self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let mut has_val_or_var = false;
        let mut name: Option<String> = None;
        for child in children(node) {
            let txt = node_text_trimmed(source, child);
            if txt == "val" || txt == "var" {
                has_val_or_var = true;
            } else if matches!(child.kind(), "simple_identifier" | "identifier") && name.is_none() {
                name = Some(txt);
            }
        }
        let name = name.filter(|_| has_val_or_var)?;
        let mods = self.collect_modifiers(node, source);
        self.emit_type_references(node, source, &name);
        Some(Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name,
            kind: "property".to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: mods,
            ..Symbol::default()
        })
    }

    fn parse_import(&self, node: Node<'_>, source: &[u8]) -> Option<Import> {
        let mut text = node_text_trimmed(source, node);
        if let Some(rest) = text.strip_prefix("import ") {
            text = rest.trim().to_string();
        }
        text = text.split(" as ").next().unwrap_or("").trim().to_string();
        let mut local_alias: Option<String> = None;
        for child in children(node) {
            if child.kind() == "identifier" {
                text = node_text_trimmed(source, child);
            } else if child.kind() == "import_alias" {
                local_alias = self.usertype_name(child, source);
            }
        }
        if text.is_empty() {
            return None;
        }
        Some(Import {
            imported_path: text,
            line: node.start_position().row + 1,
            local_alias,
        })
    }

    // --- edges ---------------------------------------------------------------

    fn parse_inheritance(&mut self, node: Node<'_>, source: &[u8], child_name: &str) {
        let mut targets = Vec::new();
        for child in children(node) {
            if child.kind() == "delegation_specifier" {
                self.collect_inheritance_targets(child, source, &mut targets);
            }
        }
        for (name, is_extends) in targets {
            self.pending_edges.push(Edge {
                source_name: child_name.to_string(),
                kind: if is_extends { "extends" } else { "implements" }.to_string(),
                target_name: name,
                line: node.start_position().row + 1,
                ..Edge::default()
            });
        }
    }

    fn collect_inheritance_targets(
        &mut self,
        node: Node<'_>,
        source: &[u8],
        targets: &mut Vec<(String, bool)>,
    ) {
        if node.kind() == "constructor_invocation" {
            for child in children(node) {
                if child.kind() == "user_type" {
                    if let Some(name) = self.usertype_name(child, source) {
                        targets.push((name, true));
                    }
                    return;
                }
            }
            return;
        }
        if node.kind() == "user_type" {
            if let Some(name) = self.usertype_name(node, source) {
                targets.push((name, false));
            }
            return;
        }
        for child in children(node) {
            self.collect_inheritance_targets(child, source, targets);
        }
    }

    fn parse_call(&self, node: Node<'_>, source: &[u8]) -> Option<Edge> {
        let mut target = self.extract_call_target_name(node, source)?;
        let receiver_type = self.infer_call_receiver_type(node, source);

        if receiver_type.is_none()
            && let Some(bare) = self.bare_callee_identifier(node, source)
            && bare == target
            && starts_lower(&bare)
            && let Some(inferred) = self.resolve_bare_name_type(&bare)
            && inferred != bare
            && starts_upper(&inferred)
        {
            target = inferred;
        }

        let enclosing = self.scope.last().cloned();
        let declared_type_of_target = enclosing
            .as_ref()
            .and_then(|enclosing| self.field_types.get(enclosing).and_then(|f| f.get(&target)));
        if let Some(receiver) = &receiver_type
            && starts_lower(&target)
            && starts_upper(receiver)
            && target != *receiver
            && Some(receiver) != enclosing.as_ref()
            && declared_type_of_target == Some(receiver)
        {
            target = receiver.clone();
        }

        Some(Edge {
            source_name: current_edge_owner(&self.callable_scope, &self.scope),
            kind: "calls".to_string(),
            target_name: target,
            line: node.start_position().row + 1,
            receiver_type,
            call_arity: self.count_call_arguments(node),
            ..Edge::default()
        })
    }

    fn count_call_arguments(&self, node: Node<'_>) -> Option<usize> {
        let suffix = child_of_type(node, &["call_suffix"])?;
        let mut count = 0;
        for child in children(suffix) {
            if child.kind() == "value_arguments" {
                count += children(child)
                    .into_iter()
                    .filter(|c| c.kind() == "value_argument")
                    .count();
            } else if child.kind() == "annotated_lambda" {
                count += 1;
            }
        }
        Some(count)
    }

    // --- receiver-type inference ------------------------------------------

    fn count_parameters(&self, fn_node: Node<'_>) -> Option<usize> {
        let fvp = child_of_type(fn_node, &["function_value_parameters"])?;
        Some(
            children(fvp)
                .into_iter()
                .filter(|c| c.kind() == "parameter")
                .count(),
        )
    }

    fn param_types(&self, fn_node: Node<'_>, source: &[u8]) -> Vec<(String, String)> {
        let mut out = Vec::new();
        for ch in children(fn_node) {
            if ch.kind() != "function_value_parameters" {
                continue;
            }
            for p in children(ch) {
                if !matches!(p.kind(), "parameter" | "function_value_parameter") {
                    continue;
                }
                let mut pname: Option<String> = None;
                let mut ptype: Option<String> = None;
                for pc in children(p) {
                    if matches!(pc.kind(), "simple_identifier" | "identifier") && pname.is_none() {
                        pname = Some(node_text_trimmed(source, pc));
                    } else if matches!(pc.kind(), "user_type" | "type_reference" | "nullable_type")
                    {
                        ptype = self.usertype_name(pc, source);
                    }
                }
                if let (Some(pname), Some(ptype)) = (pname, ptype) {
                    out.push((pname, ptype));
                }
            }
        }
        out
    }

    fn var_name_and_type(&self, prop_node: Node<'_>, source: &[u8]) -> Option<(String, String)> {
        let mut name: Option<String> = None;
        let mut vtype: Option<String> = None;
        for child in children(prop_node) {
            if child.kind() == "variable_declaration" {
                for vc in children(child) {
                    if matches!(vc.kind(), "simple_identifier" | "identifier") && name.is_none() {
                        name = Some(node_text_trimmed(source, vc));
                    } else if matches!(vc.kind(), "user_type" | "type_reference" | "nullable_type")
                    {
                        vtype = self.usertype_name(vc, source);
                    }
                }
            } else if matches!(child.kind(), "simple_identifier" | "identifier") && name.is_none() {
                name = Some(node_text_trimmed(source, child));
            }
        }
        let vtype = vtype.or_else(|| self.ctor_call_type(prop_node, source));
        Some((name?, vtype?))
    }

    fn class_param_name_and_type(&self, node: Node<'_>, source: &[u8]) -> Option<(String, String)> {
        let mut has_val_or_var = false;
        let mut name: Option<String> = None;
        let mut vtype: Option<String> = None;
        for child in children(node) {
            let txt = node_text_trimmed(source, child);
            if txt == "val" || txt == "var" {
                has_val_or_var = true;
            } else if matches!(child.kind(), "simple_identifier" | "identifier") && name.is_none() {
                name = Some(txt);
            } else if matches!(
                child.kind(),
                "user_type" | "type_reference" | "nullable_type"
            ) {
                vtype = self.usertype_name(child, source);
            }
        }
        if has_val_or_var {
            Some((name?, vtype?))
        } else {
            None
        }
    }

    fn ctor_call_type(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        let mut stack: Vec<Node<'_>> = children(node);
        while let Some(n) = stack.pop() {
            if n.kind() == "call_expression" {
                let lead = children(n).into_iter().next();
                if let Some(lead) = lead
                    && matches!(lead.kind(), "simple_identifier" | "identifier")
                {
                    let text = node_text_trimmed(source, lead);
                    if starts_upper(&text) {
                        return Some(text);
                    }
                }
            }
            stack.extend(children(n));
        }
        None
    }

    fn infer_node_type(&self, node: Option<Node<'_>>, source: &[u8]) -> Option<String> {
        let node = node?;
        match node.kind() {
            "this_expression" => self.scope.last().cloned(),
            "simple_identifier" | "identifier" => {
                let name = node_text_trimmed(source, node);
                self.resolve_bare_name_type(&name)
            }
            "call_expression" => {
                let lead = children(node).into_iter().next();
                if let Some(lead) = lead
                    && matches!(lead.kind(), "simple_identifier" | "identifier")
                {
                    let text = node_text_trimmed(source, lead);
                    if starts_upper(&text) {
                        return Some(text);
                    }
                }
                None
            }
            "navigation_expression" => {
                let member_node = self.nav_member_identifier(node)?;
                let member_name = node_text_trimmed(source, member_node);
                let recv_type = self.infer_node_type(children(node).into_iter().next(), source)?;
                self.field_types.get(&recv_type)?.get(&member_name).cloned()
            }
            _ => None,
        }
    }

    fn resolve_bare_name_type(&self, name: &str) -> Option<String> {
        for scope in self.var_types.iter().rev() {
            if let Some(t) = scope.get(name) {
                return Some(t.clone());
            }
        }
        if let Some(enclosing) = self.scope.last()
            && let Some(t) = self.field_types.get(enclosing).and_then(|f| f.get(name))
        {
            return Some(t.clone());
        }
        if starts_upper(name) {
            return Some(name.to_string());
        }
        None
    }

    fn nav_member_identifier<'a>(&self, nav: Node<'a>) -> Option<Node<'a>> {
        for c in children(nav).into_iter().rev() {
            if matches!(c.kind(), "simple_identifier" | "identifier") {
                return Some(c);
            }
            if c.kind() == "navigation_suffix" {
                for sc in children(c).into_iter().rev() {
                    if matches!(sc.kind(), "simple_identifier" | "identifier") {
                        return Some(sc);
                    }
                }
            }
        }
        None
    }

    fn infer_call_receiver_type(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        let mut nav: Option<Node<'_>> = None;
        for child in children(node) {
            if child.kind() == "navigation_expression" {
                nav = Some(child);
            }
        }
        let nav = nav?;
        let member_node = self.nav_member_identifier(nav);
        let member_name = member_node
            .map(|m| node_text_trimmed(source, m))
            .unwrap_or_default();

        if !member_name.is_empty()
            && !starts_upper(&member_name)
            && let Some(whole_type) = self.infer_node_type(Some(nav), source)
        {
            return Some(whole_type);
        }

        let receiver_expr = children(nav).into_iter().next();
        self.infer_node_type(receiver_expr, source)
    }

    fn bare_callee_identifier(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        let lead = children(node).into_iter().next()?;
        if matches!(lead.kind(), "simple_identifier" | "identifier") {
            return Some(node_text_trimmed(source, lead));
        }
        None
    }

    fn extract_call_target_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        let lead = children(node).into_iter().next();
        if let Some(lead) = lead
            && lead.kind() == "call_expression"
        {
            return None;
        }
        let mut last_id: Option<String> = None;
        for child in children(node) {
            if matches!(child.kind(), "simple_identifier" | "identifier") {
                last_id = Some(node_text_trimmed(source, child));
            }
            if child.kind() == "navigation_expression" {
                last_id = self.tail_identifier(child, source).or(last_id);
            }
            if child.kind() == "call_suffix" {
                break;
            }
        }
        if last_id.is_none() {
            last_id = self.tail_identifier(node, source);
        }
        last_id
    }

    fn usertype_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        children(node)
            .into_iter()
            .find(|child| matches!(child.kind(), "type_identifier" | "identifier"))
            .map(|child| node_text_trimmed(source, child))
    }

    fn tail_identifier(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        let mut last: Option<String> = None;
        let mut stack = vec![node];
        while let Some(n) = stack.pop() {
            if matches!(n.kind(), "simple_identifier" | "identifier") {
                last = Some(node_text_trimmed(source, n));
            }
            if n.kind() == "call_suffix" {
                continue;
            }
            stack.extend(children(n).into_iter().rev());
        }
        last
    }

    // --- field-type pre-scan ----------------------------------------------

    fn prescan_field_types(&mut self, node: Node<'_>, source: &[u8]) {
        for child in children(node) {
            if !KOTLIN_TYPE_DECL_NODES.contains(&child.kind()) {
                self.prescan_field_types(child, source);
                continue;
            }
            let Some(type_name) = self.type_identifier(child, source) else {
                continue;
            };
            let mut fields = Vec::new();
            let mut bodies = Vec::new();
            for cc in children(child) {
                match cc.kind() {
                    "primary_constructor" => {
                        for pc in children(cc) {
                            if pc.kind() == "class_parameter"
                                && let Some((pname, ptype)) =
                                    self.class_param_name_and_type(pc, source)
                            {
                                fields.push((pname, ptype));
                            }
                        }
                    }
                    "class_body" | "enum_class_body" => {
                        for member in children(cc) {
                            if member.kind() == "property_declaration"
                                && let Some((pname, ptype)) = self.var_name_and_type(member, source)
                            {
                                fields.push((pname, ptype));
                            }
                        }
                        bodies.push(cc);
                    }
                    _ => {}
                }
            }
            let entry = self.field_types.entry(type_name).or_default();
            for (fname, ftype) in fields {
                entry.insert(fname, ftype);
            }
            for body in bodies {
                self.prescan_field_types(body, source);
            }
        }
    }
}

// --- java ---------------------------------------------------------------------

const JAVA_MODIFIERS: &[&str] = &[
    "public",
    "private",
    "protected",
    "static",
    "final",
    "abstract",
    "synchronized",
    "volatile",
    "transient",
    "native",
    "strictfp",
    "default",
];

const JAVA_TYPE_DECL_NODES: &[&str] = &[
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
];

pub struct JavaParser {
    scope: Vec<String>,
    scope_kinds: Vec<String>,
    callable_scope: Vec<String>,
    pending_edges: Vec<Edge>,
}

impl Default for JavaParser {
    fn default() -> Self {
        Self::new()
    }
}

impl JavaParser {
    pub fn new() -> Self {
        Self {
            scope: Vec::new(),
            scope_kinds: Vec::new(),
            callable_scope: Vec::new(),
            pending_edges: Vec::new(),
        }
    }

    /// Parse the java file at `path`.
    pub fn parse(&mut self, path: &Path) -> std::io::Result<ParsedFile> {
        let source = std::fs::read(path)?;
        Ok(self.parse_source(path, &source))
    }

    /// Parse in-memory java `source` as if read from `path`.
    pub fn parse_source(&mut self, path: &Path, source: &[u8]) -> ParsedFile {
        let mut parser = registry::parser("java").expect("java grammar is routed");
        let tree = parser.parse(source, None).expect("tree-sitter parse");
        let mut pf = ParsedFile {
            path: path.to_string_lossy().into_owned(),
            language: "java".to_string(),
            hash: sha256_hex(source),
            line_count: source.iter().filter(|&&b| b == b'\n').count() + 1,
            ..ParsedFile::default()
        };
        self.scope.clear();
        self.scope_kinds.clear();
        self.callable_scope.clear();
        self.pending_edges.clear();
        let root = tree.root_node();
        self.walk(root, source, &mut pf);
        pf.edges.extend(std::mem::take(&mut self.pending_edges));
        pf
    }

    fn walk(&mut self, node: Node<'_>, source: &[u8], pf: &mut ParsedFile) {
        for child in children(node) {
            if rationale_from_comment("java", child, source, pf) {
                continue;
            }
            self.visit(child, source, pf);
        }
    }

    fn visit(&mut self, node: Node<'_>, source: &[u8], pf: &mut ParsedFile) {
        let kind = node.kind();

        if kind == "import_declaration" {
            if let Some(imp) = self.parse_import(node, source) {
                pf.imports.push(imp);
            }
            return;
        }

        if JAVA_TYPE_DECL_NODES.contains(&kind) {
            if let Some(sym) = self.parse_type_decl(node, source) {
                let name = sym.name.clone();
                self.scope.push(name);
                self.scope_kinds.push(kind.to_string());
                pf.symbols.push(sym);
                self.walk(node, source, pf);
                self.scope.pop();
                self.scope_kinds.pop();
            }
            return;
        }

        if kind == "method_declaration" {
            if let Some(sym) = self.parse_method(node, source) {
                let name = sym.name.clone();
                self.callable_scope.push(name);
                pf.symbols.push(sym);
                self.walk(node, source, pf);
                self.callable_scope.pop();
            }
            return;
        }

        if kind == "field_declaration" {
            for sym in self.parse_field(node, source) {
                pf.symbols.push(sym);
            }
            self.walk(node, source, pf);
            return;
        }

        if kind == "method_invocation" {
            if let Some(edge) = self.parse_call(node, source) {
                pf.edges.push(edge);
            }
            self.walk(node, source, pf);
            return;
        }

        if kind == "object_creation_expression" {
            if let Some(edge) = self.parse_new(node, source) {
                pf.edges.push(edge);
            }
            self.walk(node, source, pf);
            return;
        }

        self.walk(node, source, pf);
    }

    // --- helpers -----------------------------------------------------------

    fn collect_modifiers(&self, node: Node<'_>, source: &[u8]) -> Vec<String> {
        let mut mods = Vec::new();
        for child in children(node) {
            if child.kind() == "modifiers" {
                for m in children(child) {
                    let txt = node_text_trimmed(source, m);
                    if JAVA_MODIFIERS.contains(&txt.as_str()) {
                        mods.push(txt);
                    }
                }
            } else {
                let txt = node_text_trimmed(source, child);
                if JAVA_MODIFIERS.contains(&txt.as_str()) {
                    mods.push(txt);
                }
            }
        }
        mods
    }

    fn parse_type_decl(&mut self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = children(node)
            .into_iter()
            .find(|child| child.kind() == "identifier")
            .map(|child| node_text_trimmed(source, child))?;
        let kind = match node.kind() {
            "class_declaration" => "class",
            "interface_declaration" => "interface",
            "enum_declaration" => "enum",
            _ => "class",
        };
        let mods = self.collect_modifiers(node, source);
        let sym = Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name: name.clone(),
            kind: kind.to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: mods,
            ..Symbol::default()
        };
        self.parse_inheritance(node, source, &name);
        Some(sym)
    }

    fn parse_method(&self, node: Node<'_>, source: &[u8]) -> Option<Symbol> {
        let name = children(node)
            .into_iter()
            .find(|child| child.kind() == "identifier")
            .map(|child| node_text_trimmed(source, child))?;
        let mods = self.collect_modifiers(node, source);
        Some(Symbol {
            qualified_name: Some(qualified_name(&self.scope, &name)),
            name,
            kind: "method".to_string(),
            line_start: node.start_position().row + 1,
            line_end: node.end_position().row + 1,
            column_start: node.start_position().column,
            column_end: node.end_position().column,
            modifiers: mods,
            arity: self.method_arity(node),
            ..Symbol::default()
        })
    }

    fn parse_field(&self, node: Node<'_>, source: &[u8]) -> Vec<Symbol> {
        let mods = self.collect_modifiers(node, source);
        let mut syms = Vec::new();
        for child in children(node) {
            if child.kind() != "variable_declarator" {
                continue;
            }
            for vc in children(child) {
                if vc.kind() == "identifier" {
                    let name = node_text_trimmed(source, vc);
                    syms.push(Symbol {
                        qualified_name: Some(qualified_name(&self.scope, &name)),
                        name,
                        kind: "property".to_string(),
                        line_start: node.start_position().row + 1,
                        line_end: node.end_position().row + 1,
                        column_start: node.start_position().column,
                        column_end: node.end_position().column,
                        modifiers: mods.clone(),
                        ..Symbol::default()
                    });
                }
            }
        }
        syms
    }

    fn parse_import(&self, node: Node<'_>, source: &[u8]) -> Option<Import> {
        let mut path: Option<String> = None;
        for child in children(node) {
            if matches!(child.kind(), "scoped_identifier" | "identifier") {
                path = Some(node_text_trimmed(source, child));
            }
        }
        let mut path = path?;
        if children(node).iter().any(|c| c.kind() == "asterisk") {
            path.push_str(".*");
        }
        Some(Import {
            imported_path: path,
            line: node.start_position().row + 1,
            local_alias: None,
        })
    }

    fn parse_inheritance(&mut self, node: Node<'_>, source: &[u8], child_name: &str) {
        for child in children(node) {
            if child.kind() == "superclass" {
                if let Some(target) = self.extract_type_name(child, source) {
                    self.pending_edges.push(Edge {
                        source_name: child_name.to_string(),
                        kind: "extends".to_string(),
                        target_name: target,
                        line: node.start_position().row + 1,
                        ..Edge::default()
                    });
                }
            } else if child.kind() == "super_interfaces" {
                for tl in self.iter_typelist(child) {
                    if let Some(target) = self.extract_type_name(tl, source) {
                        self.pending_edges.push(Edge {
                            source_name: child_name.to_string(),
                            kind: "implements".to_string(),
                            target_name: target,
                            line: node.start_position().row + 1,
                            ..Edge::default()
                        });
                    }
                }
            }
        }
    }

    fn extract_type_name(&self, node: Node<'_>, source: &[u8]) -> Option<String> {
        if node.kind() == "generic_type" {
            return children(node)
                .into_iter()
                .find(|child| matches!(child.kind(), "type_identifier" | "scoped_type_identifier"))
                .map(|child| node_text_trimmed(source, child));
        }
        if matches!(node.kind(), "type_identifier" | "scoped_type_identifier") {
            return Some(node_text_trimmed(source, node));
        }
        children(node)
            .into_iter()
            .find(|child| {
                matches!(
                    child.kind(),
                    "generic_type" | "type_identifier" | "scoped_type_identifier"
                )
            })
            .and_then(|child| self.extract_type_name(child, source))
    }

    fn iter_typelist<'a>(&self, node: Node<'a>) -> Vec<Node<'a>> {
        children(node)
            .into_iter()
            .filter(|child| child.kind() == "type_list")
            .flat_map(|tl| children(tl))
            .collect()
    }

    fn method_arity(&self, node: Node<'_>) -> Option<usize> {
        let params = child_of_type(node, &["formal_parameters"])?;
        let mut count = 0;
        for child in children(params) {
            if child.kind() == "spread_parameter" {
                return None;
            }
            if child.kind() == "formal_parameter" {
                count += 1;
            }
        }
        Some(count)
    }

    fn call_arity(&self, node: Node<'_>) -> Option<usize> {
        let args = child_of_type(node, &["argument_list"])?;
        Some(
            children(args)
                .into_iter()
                .filter(|c| c.is_named() && !matches!(c.kind(), "line_comment" | "block_comment"))
                .count(),
        )
    }

    fn parse_call(&self, node: Node<'_>, source: &[u8]) -> Option<Edge> {
        let owner = current_edge_owner(&self.callable_scope, &self.scope);
        let mut callee: Option<String> = None;
        let mut receiver_text: Option<String> = None;
        for child in children(node) {
            if child.kind() == "argument_list" {
                break;
            }
            if child.kind() == "identifier" {
                if let Some(previous) = callee.take() {
                    receiver_text = Some(previous);
                }
                callee = Some(node_text_trimmed(source, child));
            }
        }
        let callee = callee?;
        Some(Edge {
            source_name: owner,
            kind: "calls".to_string(),
            target_name: callee,
            line: node.start_position().row + 1,
            receiver_type: self.infer_receiver_type(receiver_text),
            call_arity: self.call_arity(node),
            ..Edge::default()
        })
    }

    fn infer_receiver_type(&self, receiver_text: Option<String>) -> Option<String> {
        let receiver = receiver_text?;
        if starts_upper(&receiver) {
            Some(receiver)
        } else {
            None
        }
    }

    fn parse_new(&self, node: Node<'_>, source: &[u8]) -> Option<Edge> {
        let owner = current_edge_owner(&self.callable_scope, &self.scope);
        for child in children(node) {
            if child.kind() == "argument_list" {
                break;
            }
            if matches!(
                child.kind(),
                "type_identifier" | "class_type" | "scoped_type_identifier" | "generic_type"
            ) {
                let Some(txt) = self.extract_type_name(child, source) else {
                    continue;
                };
                return Some(Edge {
                    source_name: owner,
                    kind: "calls".to_string(),
                    target_name: txt.rsplit('.').next().unwrap_or(&txt).to_string(),
                    line: node.start_position().row + 1,
                    receiver_type: None,
                    call_arity: self.call_arity(node),
                    ..Edge::default()
                });
            }
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Node-type revalidation: the pinned tree-sitter-kotlin crate's parser.c
    // and node-types.json are byte-identical to vendor/tree-sitter-kotlin's
    // (md5 892464b4dac74f0c1a50986572b6cfa7), so coverage is exact. Mismatches:
    // kotlin.py also queries interface_declaration, enum_declaration,
    // function_value_parameter, and type_reference, which NEITHER grammar
    // produces (defensive dead checks kept for parity); java.py's class_type
    // is likewise absent from tree_sitter_java 0.23.5 on both the PyPI wheel
    // and the crate (same version pin). All live node kinds below must exist
    // in the crate grammars.
    const KOTLIN_QUERIED: &[(&str, bool)] = &[
        ("import", false),
        ("import_header", true),
        ("class_declaration", true),
        ("object_declaration", true),
        ("function_declaration", true),
        ("property_declaration", true),
        ("class_parameter", true),
        ("call_expression", true),
        ("modifiers", true),
        ("inheritance_modifier", true),
        ("type_identifier", true),
        ("simple_identifier", true),
        ("identifier", true),
        ("variable_declaration", true),
        ("function_body", true),
        ("class_body", true),
        ("enum_class_body", true),
        ("statements", true),
        ("primary_constructor", true),
        ("delegation_specifier", true),
        ("constructor_invocation", true),
        ("function_value_parameters", true),
        ("parameter", true),
        ("call_suffix", true),
        ("value_arguments", true),
        ("value_argument", true),
        ("annotated_lambda", true),
        ("this_expression", true),
        ("navigation_expression", true),
        ("navigation_suffix", true),
        ("user_type", true),
        ("nullable_type", true),
        ("import_alias", true),
    ];

    const JAVA_QUERIED: &[&str] = &[
        "import_declaration",
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "method_declaration",
        "field_declaration",
        "method_invocation",
        "object_creation_expression",
        "modifiers",
        "identifier",
        "variable_declarator",
        "scoped_identifier",
        "asterisk",
        "superclass",
        "super_interfaces",
        "type_list",
        "generic_type",
        "type_identifier",
        "formal_parameters",
        "spread_parameter",
        "formal_parameter",
        "argument_list",
        "line_comment",
        "block_comment",
    ];

    #[test]
    fn kotlin_crate_covers_queried_node_types() {
        let lang: tree_sitter::Language = tree_sitter_kotlin::LANGUAGE.into();
        for (kind, named) in KOTLIN_QUERIED {
            assert_ne!(
                lang.id_for_node_kind(kind, *named),
                0,
                "kotlin crate grammar lacks {kind}"
            );
        }
    }

    #[test]
    fn java_crate_covers_queried_node_types() {
        let lang: tree_sitter::Language = tree_sitter_java::LANGUAGE.into();
        for kind in JAVA_QUERIED {
            assert_ne!(
                lang.id_for_node_kind(kind, true),
                0,
                "java crate grammar lacks {kind}"
            );
        }
    }

    #[test]
    fn kotlin_declares_symbols_edges_and_imports() {
        let source = r#"package com.example

import kotlinx.coroutines.Job

/**
 * Doc comment, not a rationale.
 */
// WHY: swallowed by the preceding import_header node
class Owner(private val repo: Repo) : Base(), AutoCloseable {
    // NOTE: single owner per resource
    val cache: Cache = Cache()
    private var job: Job? = null

    override fun close() {
        repo.flush()
    }
}
"#;
        let parsed = KotlinParser::new().parse_source(Path::new("Owner.kt"), source.as_bytes());

        assert_eq!(parsed.language, "kotlin");
        assert_eq!(parsed.hash, sha256_hex(source.as_bytes()));
        assert_eq!(
            parsed
                .imports
                .iter()
                .map(|i| i.imported_path.clone())
                .collect::<Vec<_>>(),
            vec!["kotlinx.coroutines.Job"]
        );

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
                ("Owner".into(), "class".into(), "Owner".into()),
                ("repo".into(), "property".into(), "Owner.repo".into()),
                ("cache".into(), "property".into(), "Owner.cache".into()),
                ("job".into(), "property".into(), "Owner.job".into()),
                ("close".into(), "method".into(), "Owner.close".into()),
            ]
        );
        assert_eq!(parsed.symbols[3].modifiers, vec!["private"]);
        assert_eq!(parsed.rationale.len(), 1);
        assert_eq!(parsed.rationale[0].kind, "note");
        assert_eq!(parsed.rationale[0].text, "single owner per resource");
        assert_eq!(parsed.rationale[0].line, 10);

        let kinds: Vec<(String, String)> = parsed
            .edges
            .iter()
            .map(|e| (e.kind.clone(), e.target_name.clone()))
            .collect();
        assert!(kinds.contains(&("extends".into(), "Base".into())));
        assert!(kinds.contains(&("implements".into(), "AutoCloseable".into())));
        assert!(kinds.contains(&("references".into(), "Repo".into())));
        assert!(kinds.contains(&("references".into(), "Cache".into())));
        assert!(kinds.contains(&("references".into(), "Job".into())));
        let calls: Vec<(String, Option<usize>)> = parsed
            .edges
            .iter()
            .filter(|e| e.kind == "calls")
            .map(|e| (e.target_name.clone(), e.call_arity))
            .collect();
        assert_eq!(
            calls,
            vec![("Cache".into(), Some(0)), ("flush".into(), Some(0))]
        );
    }

    #[test]
    fn kotlin_infer_receivers_and_field_types() {
        let source = r#"class Service(private val client: Client) {
    fun run() {
        client.connect()
        fetch().deliver()
    }
}
"#;
        let parsed = KotlinParser::new().parse_source(Path::new("Service.kt"), source.as_bytes());
        let calls: Vec<(String, Option<&str>)> = parsed
            .edges
            .iter()
            .filter(|e| e.kind == "calls")
            .map(|e| (e.target_name.clone(), e.receiver_type.as_deref()))
            .collect();
        // Matches the Python parser edge for edge, including the nested
        // `fetch`/`deliver` pair from a chained call.
        assert_eq!(
            calls,
            vec![
                ("connect".into(), Some("Client")),
                ("deliver".into(), None),
                ("fetch".into(), None),
            ]
        );
    }

    #[test]
    fn java_declares_symbols_edges_and_imports() {
        let source = r#"package com.example;

import java.util.Map;

// NOTE: single mapper per store
public class Store implements AutoCloseable {
    private Map<String, String> entries;

    public Store(Map<String, String> entries) {
        this.entries = new HashMap<>(entries);
    }

    @Override
    public void close() {
        entries.clear();
    }
}
"#;
        let parsed = JavaParser::new().parse_source(Path::new("Store.java"), source.as_bytes());

        assert_eq!(parsed.language, "java");
        assert_eq!(
            parsed
                .imports
                .iter()
                .map(|i| i.imported_path.clone())
                .collect::<Vec<_>>(),
            vec!["java.util.Map"]
        );

        // Constructors are constructor_declaration, not method_declaration.
        let symbols: Vec<(String, String)> = parsed
            .symbols
            .iter()
            .map(|s| (s.name.clone(), s.kind.clone()))
            .collect();
        assert_eq!(
            symbols,
            vec![
                ("Store".into(), "class".into()),
                ("entries".into(), "property".into()),
                ("close".into(), "method".into()),
            ]
        );
        let class_sym = &parsed.symbols[0];
        assert_eq!(class_sym.modifiers, vec!["public"]);
        assert_eq!(class_sym.qualified_name.as_deref(), Some("Store"));
        assert_eq!(parsed.symbols[1].modifiers, vec!["private"]);
        assert_eq!(
            parsed.symbols[2].qualified_name.as_deref(),
            Some("Store.close")
        );
        assert_eq!(parsed.rationale.len(), 1);
        assert_eq!(parsed.rationale[0].kind, "note");

        let calls: Vec<(String, Option<usize>)> = parsed
            .edges
            .iter()
            .filter(|e| e.kind == "calls")
            .map(|e| (e.target_name.clone(), e.call_arity))
            .collect();
        assert_eq!(
            calls,
            vec![("HashMap".into(), Some(1)), ("clear".into(), Some(0))]
        );
        let supertypes: Vec<(String, String)> = parsed
            .edges
            .iter()
            .filter(|e| e.kind != "calls")
            .map(|e| (e.kind.clone(), e.target_name.clone()))
            .collect();
        assert_eq!(
            supertypes,
            vec![("implements".into(), "AutoCloseable".into())]
        );
    }
}
