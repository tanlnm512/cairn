//! Framework-aware route detection (URL -> code), merged in at insert time.

use std::collections::HashSet;
use std::path::Path;

use cairn_parse::model::{Edge, Metadata, ParsedFile, Symbol};
use regex::Regex;

/// Route/reference extraction merged into a parsed file before insertion.
#[derive(Debug, Default)]
pub struct RouteExtraction {
    pub routes: Vec<Symbol>,
    pub references: Vec<Edge>,
}

/// Route detectors worth trying for a TypeScript or JavaScript file.
pub fn candidate_frameworks(path: &str, language: &str) -> Vec<&'static str> {
    if language != "typescript" && language != "javascript" {
        return Vec::new();
    }
    let mut frameworks = vec!["nestjs", "react_router"];
    let parts_lower: Vec<String> = Path::new(path)
        .components()
        .map(|c| c.as_os_str().to_string_lossy().to_lowercase())
        .collect();
    if parts_lower.iter().any(|p| p == "pages" || p == "app") {
        frameworks.push("nextjs");
    }
    frameworks
}

/// Dispatch to every candidate framework detector and merge their results.
pub fn detect_routes(pf: &ParsedFile, language: &str) -> Option<RouteExtraction> {
    let frameworks = candidate_frameworks(&pf.path, language);
    if frameworks.is_empty() {
        return None;
    }
    let mut extraction = RouteExtraction::default();
    for fw in frameworks {
        let result = match fw {
            "nestjs" => detect_nestjs(pf),
            "react_router" => detect_react_router(pf),
            "nextjs" => detect_nextjs(pf),
            _ => None,
        };
        if let Some(result) = result {
            extraction.routes.extend(result.routes);
            extraction.references.extend(result.references);
        }
    }
    if extraction.routes.is_empty() {
        None
    } else {
        Some(extraction)
    }
}

fn route_qualified_name(pf: &ParsedFile, discriminator: &str) -> String {
    let stem = Path::new(&pf.path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or_default()
        .to_string();
    let re = Regex::new(r"[^A-Za-z0-9_]+").expect("static regex");
    let safe = re
        .replace_all(discriminator, "_")
        .trim_matches('_')
        .to_string();
    format!("{stem}.route.{safe}")
}

fn join_path(parts: &[&str]) -> String {
    let joined = parts
        .iter()
        .filter(|p| !p.is_empty())
        .map(|p| p.trim_matches('/'))
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("/");
    if joined.is_empty() {
        "/".to_string()
    } else {
        format!("/{joined}")
    }
}

// --- NestJS: @Controller(prefix) class + @Get/@Post/...(path) methods ---

const HTTP_DECORATOR_METHODS: &[&str] = &[
    "Get", "Post", "Put", "Delete", "Patch", "Options", "Head", "All",
];

fn controller_re() -> Regex {
    Regex::new(r#"@Controller\(\s*(?:['"]([^'"]*)['"])?\s*\)"#).expect("static regex")
}

fn http_method_re() -> Regex {
    Regex::new(&format!(
        r#"@({})\(\s*(?:['"]([^'"]*)['"])?\s*\)"#,
        HTTP_DECORATOR_METHODS.join("|")
    ))
    .expect("static regex")
}

fn detect_nestjs(pf: &ParsedFile) -> Option<RouteExtraction> {
    let mut extraction = RouteExtraction::default();
    let controller_re = controller_re();
    let method_re = http_method_re();
    for controller in pf.symbols.iter().filter(|s| s.kind == "class") {
        let mut prefix: Option<String> = None;
        for modifier in &controller.modifiers {
            if let Some(caps) = controller_re.captures(modifier) {
                prefix = Some(
                    caps.get(1)
                        .map(|m| m.as_str().to_string())
                        .unwrap_or_default(),
                );
                break;
            }
        }
        let Some(prefix) = prefix else {
            continue; // not a @Controller class
        };
        let owned_prefix = format!(
            "{}.",
            controller.qualified_name.as_deref().unwrap_or_default()
        );
        for method in &pf.symbols {
            if method.kind != "method" {
                continue;
            }
            let Some(qname) = &method.qualified_name else {
                continue;
            };
            if !qname.starts_with(&owned_prefix) {
                continue;
            }
            let mut matched: Option<(String, Option<String>)> = None;
            for modifier in &method.modifiers {
                if let Some(caps) = method_re.captures(modifier) {
                    matched = Some((
                        caps.get(1).expect("method group").as_str().to_uppercase(),
                        caps.get(2).map(|m| m.as_str().to_string()),
                    ));
                    break;
                }
            }
            let Some((http_method, path_group)) = matched else {
                continue;
            };
            let path_arg = path_group.as_deref().unwrap_or("");
            let route_path = join_path(&[&prefix, path_arg]);
            let route_name = format!("{http_method} {route_path}");
            let mut metadata = Metadata::new();
            metadata.insert("http_method".into(), http_method.clone().into());
            metadata.insert("path".into(), route_path.clone().into());
            metadata.insert("framework".into(), "nestjs".into());
            metadata.insert("handler".into(), method.name.clone().into());
            metadata.insert("provenance".into(), "exact".into());
            extraction.routes.push(Symbol {
                name: route_name.clone(),
                kind: "route".to_string(),
                qualified_name: Some(route_qualified_name(pf, &route_name)),
                line_start: method.line_start,
                line_end: method.line_end,
                metadata: Some(metadata),
                ..Symbol::default()
            });
            extraction.references.push(Edge {
                source_name: route_name,
                kind: "references".to_string(),
                target_name: method.name.clone(),
                line: method.line_start,
                ..Edge::default()
            });
        }
    }
    if extraction.routes.is_empty() {
        None
    } else {
        Some(extraction)
    }
}

// --- React Router: <Route path=... element={...}/> and config objects ---

fn jsx_route_re() -> Regex {
    Regex::new(
        r#"(?s)<Route\b[^<>]*?\bpath\s*=\s*["']([^"']+)["'][^<>]*?(?:element\s*=\s*\{\s*<\s*([A-Za-z_][\w.]*)|Component\s*=\s*\{?\s*([A-Za-z_][\w.]*))"#,
    )
    .expect("static regex")
}

fn config_route_re() -> Regex {
    Regex::new(
        r#"(?s)\{\s*path\s*:\s*["']([^"']+)["'][^{}]*?(?:element\s*:\s*<\s*([A-Za-z_][\w.]*)|Component\s*:\s*([A-Za-z_][\w.]*))"#,
    )
    .expect("static regex")
}

fn detect_react_router(pf: &ParsedFile) -> Option<RouteExtraction> {
    let Ok(source) = std::fs::read_to_string(&pf.path) else {
        return None;
    };
    if !source.contains("<Route")
        && !source.contains("createBrowserRouter")
        && !source.contains("createHashRouter")
    {
        return None; // cheap short-circuit before regex scanning
    }
    let mut extraction = RouteExtraction::default();
    let mut seen: HashSet<(String, String)> = HashSet::new();
    for pattern in [jsx_route_re(), config_route_re()] {
        for caps in pattern.captures_iter(&source) {
            let route_path = caps.get(1).expect("path group").as_str().to_string();
            let handler = caps
                .get(2)
                .or_else(|| caps.get(3))
                .map(|m| m.as_str().to_string());
            let Some(handler) = handler else {
                continue;
            };
            if !seen.insert((route_path.clone(), handler.clone())) {
                continue;
            }
            let line = source[..caps.get(0).expect("match").start()]
                .matches('\n')
                .count()
                + 1;
            let route_name = format!("ROUTE {route_path}");
            let mut metadata = Metadata::new();
            metadata.insert("http_method".into(), serde_json::Value::Null);
            metadata.insert("path".into(), route_path.clone().into());
            metadata.insert("framework".into(), "react_router".into());
            metadata.insert("handler".into(), handler.clone().into());
            metadata.insert("provenance".into(), "heuristic".into());
            extraction.routes.push(Symbol {
                name: route_name.clone(),
                kind: "route".to_string(),
                qualified_name: Some(route_qualified_name(pf, &route_name)),
                line_start: line,
                line_end: line,
                metadata: Some(metadata),
                ..Symbol::default()
            });
            extraction.references.push(Edge {
                source_name: route_name,
                kind: "references".to_string(),
                target_name: handler,
                line,
                ..Edge::default()
            });
        }
    }
    if extraction.routes.is_empty() {
        None
    } else {
        Some(extraction)
    }
}

// --- Next.js: file-based routing under pages/ or app/. ---

fn nextjs_segment(part: &str) -> String {
    if part.starts_with('[') && part.ends_with(']') {
        let inner = &part[1..part.len() - 1];
        if let Some(rest) = inner.strip_prefix("...") {
            return format!("*{rest}");
        }
        return format!(":{inner}");
    }
    part.to_string()
}

fn is_exported(name: &str, source: &str) -> bool {
    let esc = regex::escape(name);
    let patterns = [
        Regex::new(&format!(
            r"\bexport\b[ \t]*(?:async[ \t]+|abstract[ \t]+)?(?:function|class)[ \t]+{esc}\b"
        ))
        .expect("regex"),
        Regex::new(&format!(r"\bexport\b[ \t]+const[ \t]+{esc}\b")).expect("regex"),
        Regex::new(&format!(r"\bexport\b[ \t]+default[ \t]+{esc}\b")).expect("regex"),
    ];
    patterns.iter().any(|re| re.is_match(source))
}

fn default_exported_name(source: &str) -> Option<String> {
    let decl = Regex::new(
        r"\bexport\b[ \t]+default[ \t]*(?:async[ \t]+)?(?:function|class)[ \t]+([A-Za-z_$][\w$]*)",
    )
    .expect("regex");
    if let Some(caps) = decl.captures(source) {
        return Some(caps.get(1).expect("name").as_str().to_string());
    }
    let reference = Regex::new(r"(?m)\bexport\b[ \t]+default[ \t]+([A-Za-z_$][\w$]*)\s*;?[ \t]*$")
        .expect("regex");
    reference
        .captures(source)
        .map(|caps| caps.get(1).expect("name").as_str().to_string())
}

fn select_handler_symbol<'a>(pf: &'a ParsedFile, source: &str) -> Option<&'a Symbol> {
    let candidates: Vec<&Symbol> = pf
        .symbols
        .iter()
        .filter(|s| s.kind == "function" || s.kind == "class")
        .collect();
    let first = candidates.first()?;

    if let Some(default_name) = default_exported_name(source)
        && let Some(found) = candidates.iter().find(|s| s.name == default_name)
    {
        return Some(found);
    }
    if let Some(exported) = candidates.iter().find(|s| is_exported(&s.name, source)) {
        return Some(exported);
    }
    Some(first)
}

fn detect_nextjs(pf: &ParsedFile) -> Option<RouteExtraction> {
    let p = Path::new(&pf.path);
    let parts: Vec<String> = p
        .components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect();
    let lower_parts: Vec<String> = parts.iter().map(|s| s.to_lowercase()).collect();
    let anchor_idx = lower_parts
        .iter()
        .position(|part| part == "pages" || part == "app")?;
    let anchor = &lower_parts[anchor_idx];
    let rel_dir_parts = &parts[anchor_idx + 1..parts.len().saturating_sub(1)];
    let stem = p
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or_default()
        .to_string();

    let mut segments: Vec<String> = rel_dir_parts.iter().map(|s| nextjs_segment(s)).collect();
    if anchor == "app" {
        if stem != "page" && stem != "index" {
            return None;
        }
    } else {
        if stem.starts_with('_') {
            return None; // _app, _document, _error -- not routes
        }
        if stem != "page" && stem != "index" {
            segments.push(nextjs_segment(&stem));
        }
    }
    let segment_refs: Vec<&str> = segments.iter().map(String::as_str).collect();
    let route_path = join_path(&segment_refs);

    let source = std::fs::read_to_string(p).unwrap_or_default();
    let handler_sym = select_handler_symbol(pf, &source)?;

    let route_name = format!("ROUTE {route_path}");
    let mut metadata = Metadata::new();
    metadata.insert("http_method".into(), serde_json::Value::Null);
    metadata.insert("path".into(), route_path.into());
    metadata.insert("framework".into(), "nextjs".into());
    metadata.insert("handler".into(), handler_sym.name.clone().into());
    metadata.insert("provenance".into(), "heuristic".into());
    let mut extraction = RouteExtraction::default();
    extraction.routes.push(Symbol {
        name: route_name.clone(),
        kind: "route".to_string(),
        qualified_name: Some(route_qualified_name(pf, &route_name)),
        line_start: 1,
        line_end: 1,
        metadata: Some(metadata),
        ..Symbol::default()
    });
    extraction.references.push(Edge {
        source_name: route_name,
        kind: "references".to_string(),
        target_name: handler_sym.name.clone(),
        line: 1,
        ..Edge::default()
    });
    Some(extraction)
}
