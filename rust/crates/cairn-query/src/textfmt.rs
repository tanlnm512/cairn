//! Text renderings shared across crates: `True`/`False`, quoted reprs, line splitting, OS error text.

/// `True` / `False`.
pub fn title_bool(b: bool) -> &'static str {
    if b { "True" } else { "False" }
}

/// `'text'` with only `\` and `'` escaped.
pub fn repr_single_quoted(s: &str) -> String {
    format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'"))
}

/// Line split on `\n`, `\r`, `\r\n`, `\v`, `\f`, `\x1c`-`\x1e`, `\x85`, ` `, ` `; no trailing empty line.
pub fn split_lines_universal(source: &str) -> Vec<&str> {
    let is_boundary = |c: char| {
        matches!(
            c,
            '\n' | '\r'
                | '\u{b}'
                | '\u{c}'
                | '\u{1c}'
                | '\u{1d}'
                | '\u{1e}'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        )
    };
    let mut lines = Vec::new();
    let bytes = source.as_bytes();
    let mut start = 0usize;
    let mut i = 0usize;
    while let Some(c) = source[i..].chars().next() {
        let len = c.len_utf8();
        if is_boundary(c) {
            lines.push(&source[start..i]);
            i += len;
            if c == '\r' && bytes.get(i) == Some(&b'\n') {
                i += 1;
            }
            start = i;
        } else {
            i += len;
        }
    }
    if start < source.len() {
        lines.push(&source[start..]);
    }
    lines
}

/// `[Errno <n>] <message>: '<path>'`, or `<message>: '<path>'` without an OS code.
pub fn os_error_text(e: &std::io::Error, path: &str) -> String {
    let message = e
        .to_string()
        .trim_end_matches(&format!(
            " (os error {})",
            e.raw_os_error().unwrap_or_default()
        ))
        .to_string();
    match e.raw_os_error() {
        Some(code) => format!("[Errno {code}] {message}: '{path}'"),
        None => format!("{message}: '{path}'"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn split_lines_universal_covers_the_boundary_set() {
        assert_eq!(
            split_lines_universal("a\rb\nc\r\nd"),
            vec!["a", "b", "c", "d"]
        );
        assert_eq!(
            split_lines_universal("a\u{2028}b\u{85}c"),
            vec!["a", "b", "c"]
        );
        assert_eq!(split_lines_universal("no-newline"), vec!["no-newline"]);
        assert_eq!(split_lines_universal("a\n\nb"), vec!["a", "", "b"]);
        assert_eq!(split_lines_universal("a\n"), vec!["a"]);
        assert_eq!(split_lines_universal("a\n\n"), vec!["a", ""]);
        assert_eq!(split_lines_universal(""), Vec::<&str>::new());
    }
}
