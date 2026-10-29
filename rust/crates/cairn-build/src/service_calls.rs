//! Service-topology edge detection (code -> external service / HTTP call).

use cairn_parse::model::{Edge, ParsedFile};
use regex::Regex;

/// HTTP/service-call edges found by language-specific patterns.
#[derive(Debug, Default)]
pub struct ServiceCallExtraction {
    pub edges: Vec<Edge>,
}

/// Quote-paired string literals can't use backreferences in the `regex` crate,
/// so each pattern expands to one alternation branch per quote kind and
/// the URL is the first captured group of the matching branch.
fn js_http_patterns() -> Vec<Regex> {
    vec![
        Regex::new(
            r#"\bfetch\s*\(\s*'(https?://[^']*)'|\bfetch\s*\(\s*"(https?://[^"]*)"|\bfetch\s*\(\s*`(https?://[^`]*)`"#,
        )
        .expect("static regex"),
        Regex::new(
            r#"\baxios\.(?:get|post|put|delete|patch|head|options|request)\s*\(\s*'(https?://[^']*)'|\baxios\.(?:get|post|put|delete|patch|head|options|request)\s*\(\s*"(https?://[^"]*)"|\baxios\.(?:get|post|put|delete|patch|head|options|request)\s*\(\s*`(https?://[^`]*)`"#,
        )
        .expect("static regex"),
    ]
}

fn go_http_patterns() -> Vec<Regex> {
    vec![Regex::new(
        r#"\bhttp\.(?:Get|Post|Head|PostForm|NewRequest)\s*\(\s*(?:["']([A-Z]+)["'],\s*)?["'](https?://[^"']+)["']"#,
    )
    .expect("static regex")]
}

fn jvm_http_patterns() -> Vec<Regex> {
    vec![
        Regex::new(r#"\.url\s*\(\s*'(https?://[^']*)'|\.url\s*\(\s*"(https?://[^"]*)""#)
            .expect("static regex"),
        Regex::new(r#"\.baseUrl\s*\(\s*'(https?://[^']*)'|\.baseUrl\s*\(\s*"(https?://[^"]*)""#)
            .expect("static regex"),
    ]
}

fn patterns_for(language: &str) -> Option<Vec<Regex>> {
    Some(match language {
        "typescript" | "javascript" => js_http_patterns(),
        "go" => go_http_patterns(),
        "kotlin" | "java" => jvm_http_patterns(),
        _ => return None,
    })
}

/// The first Some capture group (the URL) of the matched alternation branch.
fn url_of(caps: &regex::Captures<'_>) -> Option<String> {
    caps.iter()
        .skip(1)
        .find_map(|m| m.map(|m| m.as_str().to_string()))
}

/// HTTP and service-call Edges found by language-specific patterns.
pub fn detect_service_calls(pf: &ParsedFile, language: &str) -> Option<ServiceCallExtraction> {
    let patterns = patterns_for(language)?;
    let Ok(source) = std::fs::read(&pf.path) else {
        return None;
    };
    let source = String::from_utf8_lossy(&source).into_owned();

    let mut extraction = ServiceCallExtraction::default();

    // Calls owned by route handlers become `service_call` rather than `http_call`.
    let mut route_owners: std::collections::HashSet<String> = pf
        .symbols
        .iter()
        .filter(|s| s.kind == "route")
        .map(|s| s.name.clone())
        .collect();
    for s in &pf.symbols {
        if s.kind == "route"
            && let Some(metadata) = &s.metadata
            && let Some(handler) = metadata.get("handler").and_then(|h| h.as_str())
        {
            route_owners.insert(handler.to_string());
        }
    }

    for pattern in &patterns {
        for caps in pattern.captures_iter(&source) {
            let whole = caps.get(0).expect("match").start();
            let line = source[..whole].matches('\n').count() + 1;
            let Some(url) = url_of(&caps) else {
                continue;
            };
            let owner = owner_for_line(pf, line);
            let kind = if route_owners.contains(&owner) {
                "service_call"
            } else {
                "http_call"
            };
            extraction.edges.push(Edge {
                source_name: owner,
                kind: kind.to_string(),
                target_name: url,
                line,
                ..Edge::default()
            });
        }
    }

    if extraction.edges.is_empty() {
        None
    } else {
        Some(extraction)
    }
}

/// Innermost symbol whose span contains `line`; "" if none.
fn owner_for_line(pf: &ParsedFile, line: usize) -> String {
    let mut candidate = String::new();
    let mut best_span: Option<usize> = None;
    for s in &pf.symbols {
        if s.line_start > line || s.line_end < line {
            continue;
        }
        let span = s.line_end - s.line_start;
        if best_span.is_none() || span < best_span.expect("best span") {
            best_span = Some(span);
            candidate = s.name.clone();
        }
    }
    candidate
}
