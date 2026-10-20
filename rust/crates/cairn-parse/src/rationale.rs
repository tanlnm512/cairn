//! Per-language comment-node map and NOTE/WHY/HACK marker extraction.

use tree_sitter::Node;

use crate::model::RationaleRecord;

/// Comment node types per registry language; unmapped languages yield no records.
pub fn comment_node_types(language: &str) -> &'static [&'static str] {
    match language {
        "python" | "go" | "c" | "cpp" | "csharp" | "ruby" | "dart" | "objc" | "typescript"
        | "tsx" | "javascript" | "php" => &["comment"],
        "rust" => &["line_comment", "block_comment"],
        "java" => &["line_comment", "block_comment"],
        "kotlin" => &["line_comment", "multiline_comment"],
        "swift" => &["comment", "multiline_comment"],
        _ => &[],
    }
}

/// RationaleRecord for a NOTE/WHY/HACK comment node, else None.
pub fn extract_rationale(node: Node<'_>, source: &[u8]) -> Option<RationaleRecord> {
    let text = String::from_utf8_lossy(&source[node.start_byte()..node.end_byte()]);
    let body = comment_body(&text);
    let (marker, rest) = split_marker(&body)?;
    Some(RationaleRecord {
        line: node.start_position().row + 1,
        kind: marker.to_lowercase(),
        text: rest.trim().to_string(),
    })
}

/// Strip the comment opener and fold continuation lines to single spaces.
fn comment_body(text: &str) -> String {
    let stripped = text.trim();
    if let Some(body) = stripped.strip_prefix("/*") {
        let body = body.trim_end();
        let body = body.strip_suffix("*/").unwrap_or(body);
        let mut folded = String::new();
        for line in body.split('\n') {
            let line = line.trim();
            let line = line.trim_start_matches('*').trim();
            if !line.is_empty() {
                if !folded.is_empty() {
                    folded.push(' ');
                }
                folded.push_str(line);
            }
        }
        return folded;
    }
    stripped.trim_start_matches(['#', '/']).trim().to_string()
}

/// (marker, rest) when body starts with NOTE|WHY|HACK (case-insensitive) + ':'.
fn split_marker(body: &str) -> Option<(&str, &str)> {
    let (head, rest) = body.split_once(':')?;
    for marker in ["NOTE", "WHY", "HACK"] {
        if head.eq_ignore_ascii_case(marker) {
            return Some((marker, rest));
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_line_comment_markers() {
        assert_eq!(
            split_marker(&comment_body("// NOTE: uses helper")),
            Some(("NOTE", " uses helper"))
        );
        assert_eq!(
            split_marker(&comment_body("# why: kept simple")),
            Some(("WHY", " kept simple"))
        );
        assert_eq!(
            split_marker(&comment_body("/// hack: pending upstream")),
            Some(("HACK", " pending upstream"))
        );
    }

    #[test]
    fn folds_block_comments() {
        assert_eq!(
            split_marker(&comment_body("/* WHY: x\n * more */")),
            Some(("WHY", " x more"))
        );
    }

    #[test]
    fn ignores_non_marker_comments() {
        assert_eq!(split_marker(&comment_body("// plain comment")), None);
        assert_eq!(
            split_marker(&comment_body("// notes: lowercase plural")),
            None
        );
    }

    #[test]
    fn marker_case_insensitive() {
        assert_eq!(
            split_marker(&comment_body("// note: x")),
            Some(("NOTE", " x"))
        );
    }
}
