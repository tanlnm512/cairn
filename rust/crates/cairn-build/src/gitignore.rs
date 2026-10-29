//! gitignore matcher with `pathspec` 1.1.1 `GitIgnoreSpec` semantics.

use regex::Regex;

/// One compiled pattern; `include` mirrors pathspec's tri-state (None = no-op).
struct Pattern {
    regex: Option<Regex>,
    include: Option<bool>,
}

/// A compiled spec: last matching pattern wins, dir matches yield to file matches.
#[derive(Default)]
pub struct GitIgnoreSpec {
    patterns: Vec<Pattern>,
}

impl GitIgnoreSpec {
    /// Compile lines with `GitIgnoreSpec.from_lines("gitignore", ...)` semantics;
    /// malformed patterns become no-ops like pathspec's internal handling.
    pub fn from_lines(lines: &[&str]) -> Self {
        let patterns = lines
            .iter()
            .map(|line| {
                let (regex, include) = compile(line);
                Pattern { regex, include }
            })
            .collect();
        Self { patterns }
    }

    /// `GitIgnoreSpec.match_file`: true when the final decision is a plain
    /// (ignoring) pattern; a `!pattern` decision re-includes the file.
    pub fn is_ignored(&self, file: &str) -> bool {
        let mut out_include: Option<bool> = None;
        let mut out_priority: u8 = 0;
        for pattern in &self.patterns {
            let Some(include) = pattern.include else {
                continue;
            };
            let Some(regex) = &pattern.regex else {
                continue;
            };
            let Some(caps) = regex.captures(file) else {
                continue;
            };
            let dir_mark = caps.name("ps_d").is_some_and(|m| !m.as_str().is_empty());
            let priority: u8 = if dir_mark { 1 } else { 2 };
            if (include && dir_mark) || priority >= out_priority {
                out_include = Some(include);
                out_priority = priority;
            }
        }
        out_include == Some(true)
    }
}

/// Compile one line to (regex, include); None regex = no-op pattern.
fn compile(line: &str) -> (Option<Regex>, Option<bool>) {
    let mut pattern = line;
    if !pattern.ends_with("\\ ") {
        pattern = pattern.trim_end_matches([' ', '\t', '\r']);
    }
    if pattern.is_empty() || pattern.starts_with('#') {
        return (None, None);
    }
    let (include, pattern) = match pattern.strip_prefix('!') {
        Some(rest) => (false, rest),
        None => (true, pattern),
    };
    let orig_segs: Vec<&str> = pattern.split('/').collect();
    let mut is_dir_pattern = orig_segs.last().is_some_and(|s| s.is_empty());
    if pattern == "/" {
        is_dir_pattern = false;
    }

    let regex_src = normalize_and_translate(&orig_segs, is_dir_pattern);
    let regex = regex_src.and_then(|src| Regex::new(&src).ok());
    if regex.is_none() {
        return (None, None); // malformed patterns are no-ops, never file-skipping
    }
    (regex, Some(include))
}

/// The pathspec normalize-segments + translate-segments pipeline; None = no-op.
fn normalize_and_translate(orig_segs: &[&str], is_dir_pattern: bool) -> Option<String> {
    let mut segs: Vec<String> = orig_segs.iter().map(|s| s.to_string()).collect();

    if segs[0].is_empty() {
        segs.remove(0);
    } else if (segs.len() == 1 || (segs.len() == 2 && segs[1].is_empty())) && segs[0] != "**" {
        segs.insert(0, "**".to_string());
    }
    if segs.is_empty() {
        return None; // normalized to nothing
    }
    let dir_pattern = is_dir_pattern;
    if segs.last().is_some_and(|s| s.is_empty()) {
        let n = segs.len();
        segs[n - 1] = "**".to_string();
    }
    let mut i = segs.len() - 1;
    while i > 0 {
        if segs[i] == "**" && segs[i - 1] == "**" {
            segs.remove(i);
        }
        i -= 1;
    }

    // Special-case full-match overrides.
    if segs.len() == 1 && segs[0] == "**" {
        return Some(if dir_pattern {
            "(?P<ps_d>/)".to_string()
        } else {
            ".".to_string()
        });
    }
    if segs.len() == 2 && segs[0] == "**" && segs[1] == "*" {
        return Some(".".to_string());
    }
    if segs.len() == 3 && segs[0] == "**" && segs[1] == "*" && segs[2] == "**" {
        return Some(if dir_pattern {
            "(?P<ps_d>/)".to_string()
        } else {
            "/".to_string()
        });
    }

    let mut parts: Vec<String> = Vec::new();
    let mut need_slash = false;
    let end = segs.len() - 1;
    for (i, seg) in segs.iter().enumerate() {
        if seg == "**" {
            if i == 0 {
                parts.push("^(?:.+/)?".to_string());
            } else if i < end {
                parts.push("(?:/.+)?".to_string());
                need_slash = true;
            } else if dir_pattern {
                parts.push("(?P<ps_d>/)".to_string());
            } else {
                parts.push("/".to_string());
            }
        } else {
            if i == 0 {
                parts.push("^".to_string());
            }
            if need_slash {
                parts.push("/".to_string());
            }
            if seg == "*" {
                parts.push("[^/]+".to_string());
            } else {
                parts.push(translate_segment_glob(seg)?);
            }
            if i == end {
                parts.push("(?:(?P<ps_d>/)|$)".to_string());
            }
            need_slash = true;
        }
    }
    Some(parts.concat())
}

/// fnmatch-style segment translation; None on malformed bracket/escape notation.
fn translate_segment_glob(pattern: &str) -> Option<String> {
    let mut regex = String::new();
    let mut escaped = false;
    let chars: Vec<char> = pattern.chars().collect();
    let mut i = 0usize;
    while i < chars.len() {
        let ch = chars[i];
        i += 1;
        if escaped {
            escaped = false;
            regex.push_str(&escape_regex_char(ch));
        } else if ch == '\\' {
            escaped = true;
        } else if ch == '*' {
            regex.push_str("[^/]*");
        } else if ch == '?' {
            regex.push_str("[^/]");
        } else if ch == '[' {
            // Bracket expression: negation, then a leading literal ']', then the
            // closing ']' ends the scan; the body (including that closer, which
            // terminates the class) is copied like pathspec does.
            let mut j = i;
            if j < chars.len() && (chars[j] == '!' || chars[j] == '^') {
                j += 1;
            }
            if j < chars.len() && chars[j] == ']' {
                j += 1;
            }
            while j < chars.len() && chars[j] != ']' {
                j += 1;
            }
            if j >= chars.len() {
                return None; // unterminated bracket expression
            }
            j += 1;
            let mut expr = String::from("[");
            if chars[i] == '!' || chars[i] == '^' {
                expr.push('^');
                i += 1;
            }
            for (k, &c) in chars[i..j].iter().enumerate() {
                if k == 0 && c == ']' {
                    // The rust regex crate has no POSIX first-']' rule.
                    expr.push_str("\\]");
                } else if c == '\\' {
                    expr.push_str("\\\\");
                } else {
                    expr.push(c);
                }
            }
            regex.push_str(&expr);
            i = j;
        } else {
            regex.push_str(&escape_regex_char(ch));
        }
    }
    if escaped {
        return None; // dangling escape
    }
    Some(regex)
}

/// Escape one literal char for the `regex` crate (segment context, outside classes).
fn escape_regex_char(ch: char) -> String {
    if "\\.+*?()|[]{}^$".contains(ch) {
        format!("\\{ch}")
    } else {
        ch.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ignored(patterns: &[&str], file: &str) -> bool {
        GitIgnoreSpec::from_lines(patterns).is_ignored(file)
    }

    #[test]
    fn matches_the_probe_battery() {
        // Every expectation below was produced by running the repo venv's
        // pathspec 1.1.1 GitIgnoreSpec.match_file on the same inputs.
        assert!(ignored(&["build/"], "build/x.py"));
        assert!(!ignored(&["build/"], "build"));
        assert!(!ignored(&["*/"], "b"));
        assert!(ignored(&["*/"], "a/b"));
        assert!(ignored(&["*.min.js"], "x/y.min.js"));
        assert!(ignored(&["*.py[cod]"], "x.pyc"));
        assert!(ignored(&["*.py[cod]"], "x.pyo"));
        assert!(ignored(&["target"], "target/debug/a.rs"));
        assert!(ignored(&["docs/**/*.md"], "docs/a/b.md"));
        assert!(ignored(&["docs/**/*.md"], "docs/b.md"));
        assert!(ignored(&["**/x"], "q/r/x"));
        assert!(!ignored(&["**/x"], "q/r/x.py"));
        assert!(ignored(&["foo"], "a/foo/c.py"));
        assert!(!ignored(&["/foo"], "a/foo/c.py"));
        assert!(ignored(&["/foo"], "foo/a.py"));
        // Last-match-wins with dir/file priority.
        assert!(!ignored(&["logs/**", "!logs/keep.log"], "logs/keep.log"));
        assert!(!ignored(&["*.log", "!keep.log"], "keep.log"));
        assert!(ignored(&["*.log", "!keep.log"], "other.log"));
        // Comments, blanks, and malformed patterns are no-ops.
        assert!(!ignored(&["# comment", "", "  "], "anything.py"));
        assert!(ignored(&["a[b", "*.py"], "b.py"));
        assert!(!ignored(&["a[b"], "a[x.py"));
        // Trailing spaces are stripped unless backslash-escaped.
        assert!(ignored(&["trailing "], "trailing"));
        assert!(ignored(&["esc\\ space"], "esc space"));
        // Root-anchored dir patterns from the cairn self-store.
        assert!(ignored(&[".zcode/"], ".zcode/workflow-drafts/x.ts"));
        assert!(ignored(
            &["benchmarks/datasource/"],
            "benchmarks/datasource/ds2/x.py"
        ));
        assert!(ignored(
            &["src/cairn/dashboard/static/chunks/"],
            "src/cairn/dashboard/static/chunks/a.js"
        ));
        assert!(ignored(&["*.py[cod]", "*.so", ".venv/"], ".venv/lib/x.so"));
    }
}
