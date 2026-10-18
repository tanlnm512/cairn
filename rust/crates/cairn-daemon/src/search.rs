//! `search_symbols` tool semantics: FTS query conversion and response rendering.

use crate::store::SymbolRow;

/// Shown rows before the "... and N more" truncation line.
const SHOWN: usize = 50;

/// True iff `pattern` is a pure single-trailing-wildcard token (`"Api*"`).
pub fn is_fts_prefix_pattern(pattern: &str) -> bool {
    let p = pattern.trim();
    let Some(stripped) = p.strip_suffix('*') else {
        return false;
    };
    if stripped.contains('*') || p.contains('_') {
        return false;
    }
    let token = stripped.trim();
    !token.is_empty() && !token.contains([' ', '"', ':', '(', ')'])
}

/// Convert a user pattern into an FTS5 MATCH query; `None` selects the LIKE path.
pub fn pattern_to_fts(pattern: &str) -> Option<String> {
    let p = pattern.trim();
    if p.is_empty() {
        return None;
    }
    if is_fts_prefix_pattern(p) {
        return Some(format!(
            "{}*",
            p.strip_suffix('*').expect("suffix checked").trim()
        ));
    }
    let cleaned: String = p
        .chars()
        .map(|c| if c == '*' || c == '%' { ' ' } else { c })
        .collect();
    let tokens: Vec<&str> = cleaned
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|t| !t.is_empty())
        .collect();
    if tokens.is_empty() {
        return None;
    }
    Some(format!("\"{}\"*", tokens.join(" ")))
}

/// Render the `search_symbols` prose response (the Python tool text contract).
pub fn render_search_symbols(pattern: &str, rows: &[SymbolRow]) -> String {
    let total = rows.len();
    if total == 0 {
        return format!(
            "No symbols matching '{pattern}'. The token may not be indexed or \
             may use different casing/wording. Try a broader pattern (fewer \
             characters, a leading wildcard), or semantic_search(\"{pattern}\") \
             to match by meaning."
        );
    }
    let mut out = format!("{total} symbols matching '{pattern}':");
    for s in rows.iter().take(SHOWN) {
        // Python renders line_start via f-string: a NULL line shows as "None".
        let line = s
            .line_start
            .map(|l| l.to_string())
            .unwrap_or_else(|| "None".to_string());
        out.push_str(&format!(
            "\n  {} {}  {}:{}  ({})",
            s.kind, s.name, s.file_path, line, s.repo
        ));
    }
    if total > SHOWN {
        out.push_str(&format!("\n  ... and {} more", total - SHOWN));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::{is_fts_prefix_pattern, pattern_to_fts};

    #[test]
    fn prefix_patterns_pass_through() {
        assert_eq!(pattern_to_fts("Acti*").as_deref(), Some("Acti*"));
        assert!(is_fts_prefix_pattern("Acti*"));
    }

    #[test]
    fn underscored_names_take_the_phrase_path() {
        assert_eq!(
            pattern_to_fts("_render_active").as_deref(),
            Some("\"render active\"*")
        );
        assert!(!is_fts_prefix_pattern("_render_active"));
    }

    #[test]
    fn non_ascii_and_empty_patterns_select_like() {
        assert_eq!(pattern_to_fts(""), None);
        assert_eq!(pattern_to_fts("  "), None);
        assert!(pattern_to_fts("***").is_none());
        assert_eq!(pattern_to_fts("café").as_deref(), Some("\"caf\"*"));
    }
}
