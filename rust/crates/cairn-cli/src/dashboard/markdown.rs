//! Escape-first markdown renderer for wiki/knowledge/memory bodies.

use std::collections::HashMap;

/// Leftmost inline token: code spans first at a tie, so a link inside a
/// span is atomic; otherwise whichever kind starts first.
#[derive(Debug)]
enum InlineToken<'a> {
    Code(&'a str),
    Link { label: &'a str, target: &'a str },
}

fn next_inline_token(text: &str) -> Option<(InlineToken<'_>, usize, usize)> {
    let code = text.find('`').and_then(|start| {
        text[start + 1..]
            .find('`')
            .map(|rest| (start, start + 1 + rest))
    });
    let mut link: Option<(InlineToken<'_>, usize, usize)> = None;
    let mut search = 0;
    while let Some(open) = text[search..].find('[') {
        let open = search + open;
        search = open + 1;
        let Some(close) = text[open + 1..].find(']') else {
            break;
        };
        let close = open + 1 + close;
        let label = &text[open + 1..close];
        if label.is_empty() || close + 1 >= text.len() || text.as_bytes()[close + 1] != b'(' {
            continue;
        }
        let Some(paren) = text[close + 2..].find(')') else {
            continue;
        };
        let target = &text[close + 2..close + 2 + paren];
        if target.is_empty() || target.contains(' ') {
            continue;
        }
        link = Some((
            InlineToken::Link { label, target },
            open,
            close + 2 + paren + 1,
        ));
        break;
    }
    match (code, link) {
        (Some((cstart, _)), Some((lval, lstart, lend))) if lstart < cstart => {
            Some((lval, lstart, lend))
        }
        (Some((start, end)), _) => Some((InlineToken::Code(&text[start + 1..end]), start, end + 1)),
        (None, Some(found)) => Some(found),
        (None, None) => None,
    }
}

/// Inline pass over escaped text: code spans (optionally link-mapped) and
/// allowlisted links; everything else stays escaped literal text.
fn inline(escaped_text: &str, link_map: &HashMap<String, String>) -> String {
    let mut out = String::new();
    let mut rest = escaped_text;
    while let Some((token, start, end)) = next_inline_token(rest) {
        out.push_str(&rest[..start]);
        match token {
            InlineToken::Code(code) => match link_map.get(code) {
                Some(href) => {
                    out.push_str(&format!(
                        "<a class=\"code-ref\" href=\"{}\"><code>{code}</code></a>",
                        html_escape(href, true)
                    ));
                }
                None => out.push_str(&format!("<code>{code}</code>")),
            },
            InlineToken::Link { label, target } => {
                if target.starts_with("http://")
                    || target.starts_with("https://")
                    || target.starts_with('/')
                    || target.starts_with('#')
                {
                    out.push_str(&format!(
                        "<a href=\"{}\">{label}</a>",
                        target.replace('"', "&quot;")
                    ));
                } else {
                    out.push_str(&rest[start..end]);
                }
            }
        }
        rest = &rest[end..];
    }
    out.push_str(rest);
    out
}

pub fn html_escape(text: &str, quote: bool) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' if quote => out.push_str("&quot;"),
            '\'' if quote => out.push_str("&#x27;"),
            other => out.push(other),
        }
    }
    out
}

/// Deterministic heading slug; an all-symbol heading reads `section`.
fn slugify(text: &str) -> String {
    let mut slug = String::new();
    let mut last_dash = false;
    for ch in text.to_lowercase().chars() {
        if ch.is_ascii_lowercase() || ch.is_ascii_digit() {
            slug.push(ch);
            last_dash = false;
        } else if !last_dash {
            slug.push('-');
            last_dash = true;
        }
    }
    let slug = slug.trim_matches('-').to_string();
    if slug.is_empty() {
        "section".into()
    } else {
        slug
    }
}

fn next_slug(counts: &mut HashMap<String, usize>, text: &str) -> String {
    let base = slugify(text);
    let seen = counts.entry(base.clone()).or_insert(0);
    let slug = if *seen == 0 {
        base.clone()
    } else {
        format!("{base}-{seen}")
    };
    *seen += 1;
    slug
}

/// Drop paired code-span backticks, keep lone ones (the TOC's `_CODE_SPAN_RE.sub`).
fn strip_code_spans(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(start) = rest.find('`') {
        match rest[start + 1..].find('`') {
            Some(end) => {
                out.push_str(&rest[..start]);
                out.push_str(&rest[start + 1..start + 1 + end]);
                rest = &rest[start + end + 2..];
            }
            None => break,
        }
    }
    out.push_str(rest);
    out
}

/// Unescaped-pipe split (backslash-pipe stays a literal cell character).
fn split_row(row: &str) -> Vec<String> {
    let mut cells: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut chars = row.chars().peekable();
    while let Some(ch) = chars.next() {
        match ch {
            '\\' if chars.peek() == Some(&'|') => {
                current.push('|');
                chars.next();
            }
            '|' => {
                cells.push(current.trim().to_string());
                current.clear();
            }
            other => current.push(other),
        }
    }
    cells.push(current.trim().to_string());
    if cells.first().is_some_and(|c| c.is_empty()) {
        cells.remove(0);
    }
    if cells.last().is_some_and(|c| c.is_empty()) {
        cells.pop();
    }
    cells
}

fn column_alignment(delim: &str) -> Vec<Option<&'static str>> {
    split_row(delim)
        .iter()
        .map(|cell| {
            let left = cell.starts_with(':');
            let right = cell.ends_with(':');
            match (left, right) {
                (true, true) => Some("text-align:center"),
                (true, false) => Some("text-align:left"),
                (false, true) => Some("text-align:right"),
                _ => None,
            }
        })
        .collect()
}

fn emit_table_row(
    row: &str,
    tag: &str,
    align: &[Option<&str>],
    link_map: &HashMap<String, String>,
) -> String {
    let cells: Vec<String> = split_row(row)
        .iter()
        .enumerate()
        .map(|(i, cell)| {
            let text = inline(cell, link_map);
            match align.get(i).copied().flatten() {
                Some(style) => format!("<{tag} style=\"{style}\">{text}</{tag}>"),
                None => format!("<{tag}>{text}</{tag}>"),
            }
        })
        .collect();
    format!("<tr>{}</tr>", cells.concat())
}

fn is_delim_row(line: &str) -> bool {
    // ^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$ over the escaped line.
    let trimmed = line.trim();
    let trimmed = trimmed.strip_prefix('|').unwrap_or(trimmed);
    let trimmed = trimmed.strip_suffix('|').unwrap_or(trimmed);
    if trimmed.is_empty() {
        return false;
    }
    trimmed.split('|').all(|cell| {
        let cell = cell.trim();
        let cell = cell.strip_prefix(':').unwrap_or(cell);
        let cell = cell.strip_suffix(':').unwrap_or(cell);
        cell.len() >= 3 && cell.bytes().all(|b| b == b'-')
    })
}

fn has_unescaped_pipe(line: &str) -> bool {
    let mut chars = line.chars();
    while let Some(ch) = chars.next() {
        if ch == '\\' {
            chars.next();
        } else if ch == '|' {
            return true;
        }
    }
    false
}

/// `(html, toc)`: rendered body plus the h2/h3 outline as
/// `[{level, text, anchor}]` with anchors matching the emitted heading ids.
#[allow(unused_assignments)]
pub fn render_markdown_with_toc(
    text: &str,
    link_map: &HashMap<String, String>,
) -> (String, Vec<serde_json::Value>) {
    let mut out: Vec<String> = Vec::new();
    let mut toc: Vec<serde_json::Value> = Vec::new();
    let mut slug_counts: HashMap<String, usize> = HashMap::new();
    let mut paragraph: Vec<String> = Vec::new();
    let mut items: Vec<String> = Vec::new();
    let mut fence_lang: Option<String> = None;
    let mut fence_lines: Vec<String> = Vec::new();
    let mut table_cols: Option<usize> = None;
    let mut table_align: Vec<Option<&str>> = Vec::new();
    let mut table_rows: Vec<String> = Vec::new();

    macro_rules! flush_paragraph {
        () => {
            if !paragraph.is_empty() {
                let joined = paragraph.join(" ");
                out.push(format!("<p>{}</p>", inline(&joined, link_map)));
                paragraph.clear();
            }
        };
    }
    macro_rules! flush_list {
        () => {
            if !items.is_empty() {
                let lis: Vec<String> = items
                    .iter()
                    .map(|item| format!("<li>{}</li>", inline(item, link_map)))
                    .collect();
                out.push(format!("<ul>{}</ul>", lis.concat()));
                items.clear();
            }
        };
    }
    macro_rules! flush_table {
        () => {
            if let Some(cols) = table_cols {
                let header = &table_rows[0];
                let body = &table_rows[2..];
                if body.iter().all(|row| split_row(row).len() == cols) {
                    out.push("<table>".into());
                    out.push(emit_table_row(header, "th", &table_align, link_map));
                    for row in body {
                        out.push(emit_table_row(row, "td", &table_align, link_map));
                    }
                    out.push("</table>".into());
                } else {
                    paragraph.extend(table_rows.iter().cloned());
                }
                table_align.clear();
                table_rows.clear();
                table_cols = None;
            }
        };
    }

    for raw in text.split('\n') {
        let line = html_escape(raw, false);
        let fence = line
            .strip_prefix("```")
            .map(|rest| rest.trim_end())
            .filter(|lang| !lang.contains(char::is_whitespace))
            .map(|lang| lang.to_string());
        if let Some(lang) = &fence_lang {
            if fence.is_some() {
                let body = fence_lines.join("\n");
                if lang == "mermaid" {
                    out.push(format!("<pre class=\"mermaid\">{body}</pre>"));
                } else {
                    out.push(format!("<pre><code>{body}</code></pre>"));
                }
                fence_lang = None;
                fence_lines.clear();
            } else {
                fence_lines.push(line.clone());
            }
            continue;
        }
        if fence.is_some() {
            flush_table!();
            flush_paragraph!();
            flush_list!();
            fence_lang = fence.clone();
            continue;
        }
        if table_cols.is_some() {
            if !line.trim().is_empty() && has_unescaped_pipe(&line) {
                table_rows.push(line.trim().to_string());
                continue;
            }
            flush_table!();
        }
        let heading_level = line
            .strip_prefix('#')
            .map(|rest| 1 + rest.chars().take_while(|c| *c == '#').count())
            .filter(|level| {
                *level <= 6 && line.chars().nth(*level).is_some_and(char::is_whitespace)
            });
        if let Some(level) = heading_level {
            flush_paragraph!();
            flush_list!();
            let escaped_title = line[level..].trim_start();
            let raw_title = raw[level..].trim_start();
            let anchor = next_slug(&mut slug_counts, raw_title);
            out.push(format!(
                "<h{level} id=\"{anchor}\">{}</h{level}>",
                inline(escaped_title, link_map)
            ));
            if level == 2 || level == 3 {
                let text = strip_code_spans(raw_title);
                toc.push(serde_json::json!({"level": level, "text": text, "anchor": anchor}));
            }
            continue;
        }
        if !paragraph.is_empty() && is_delim_row(&line) {
            let header_cells = split_row(paragraph.last().unwrap());
            let align = column_alignment(&line);
            if has_unescaped_pipe(paragraph.last().unwrap()) && header_cells.len() == align.len() {
                let header = paragraph.pop().unwrap();
                flush_paragraph!();
                flush_list!();
                table_cols = Some(header_cells.len());
                table_align = align;
                table_rows = vec![header, line.trim().to_string()];
                continue;
            }
        }
        let list_item = line
            .strip_prefix('-')
            .map(|rest| rest.trim_start())
            .filter(|_| line[1..].starts_with(char::is_whitespace));
        if let Some(item) = list_item {
            flush_paragraph!();
            items.push(inline(item, link_map));
            continue;
        }
        if line.trim().is_empty() {
            flush_paragraph!();
            flush_list!();
            continue;
        }
        flush_list!();
        paragraph.push(line.trim().to_string());
    }

    if let Some(lang) = &fence_lang {
        let body = fence_lines.join("\n");
        if lang == "mermaid" {
            out.push(format!("<pre class=\"mermaid\">{body}</pre>"));
        } else {
            out.push(format!("<pre><code>{body}</code></pre>"));
        }
    }
    flush_table!();
    flush_paragraph!();
    flush_list!();
    (out.join("\n"), toc)
}

/// Render a markdown body to HTML: escape first, whitelist blocks.
pub fn render_markdown(text: &str, link_map: &HashMap<String, String>) -> String {
    render_markdown_with_toc(text, link_map).0
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn headings_slugs_and_toc_stay_aligned() {
        let (html, toc) = render_markdown_with_toc(
            "# Title\n\n## One **bright** day\n\n### `code` head\n\n### one\n\ntext\n",
            &HashMap::new(),
        );
        assert!(html.contains("<h1 id=\"title\">Title</h1>"), "{html}");
        assert!(html.contains("<h2 id=\"one-bright-day\">"), "{html}");
        assert!(html.contains("<h3 id=\"code-head\">"), "{html}");
        assert!(html.contains("<h3 id=\"one\">"), "{html}");
        assert_eq!(toc.len(), 3);
        assert_eq!(
            toc[0],
            json!({"level": 2, "text": "One **bright** day", "anchor": "one-bright-day"})
        );
        assert_eq!(toc[1]["text"], "code head");
        assert_eq!(toc[2]["anchor"], "one");
    }

    #[test]
    fn repeated_headings_get_suffixes() {
        let (html, _) = render_markdown_with_toc("## a\n\n## a\n\n## a\n", &HashMap::new());
        assert!(html.contains("id=\"a\""));
        assert!(html.contains("id=\"a-1\""));
        assert!(html.contains("id=\"a-2\""));
    }

    #[test]
    fn code_spans_links_and_ref_map() {
        let mut map = HashMap::new();
        map.insert("explore".to_string(), "/graph?scope=symbol".to_string());
        let (html, _) = render_markdown_with_toc(
            "See `explore` and [docs](https://x.io) and [bad](javascript:x) and `[y](/z)`.\n",
            &map,
        );
        assert!(
            html.contains(
                "<a class=\"code-ref\" href=\"/graph?scope=symbol\"><code>explore</code></a>"
            ),
            "{html}"
        );
        assert!(html.contains("<a href=\"https://x.io\">docs</a>"), "{html}");
        assert!(html.contains("[bad](javascript:x)"), "{html}");
        assert!(html.contains("<code>[y](/z)</code>"), "{html}");
    }

    #[test]
    fn tables_and_fences_render() {
        let body = "| a | b |\n| --- | ---: |\n| 1 | 2 |\n\n```python\nx < 1\n```\n";
        let (html, _) = render_markdown_with_toc(body, &HashMap::new());
        assert!(html.contains("<table>\n<tr><th>a</th><th style=\"text-align:right\">b</th></tr>\n<tr><td>1</td><td style=\"text-align:right\">2</td></tr>\n</table>"), "{html}");
        assert!(html.contains("<td>1</td>"));
        assert!(html.contains("<pre><code>x &lt; 1</code></pre>"), "{html}");
    }

    #[test]
    fn mermaid_fence_gets_the_client_class() {
        let (html, _) = render_markdown_with_toc("```mermaid\ngraph TD\n```\n", &HashMap::new());
        assert!(
            html.contains("<pre class=\"mermaid\">graph TD</pre>"),
            "{html}"
        );
    }
}
