//! Span-grouped regular-expression search over indexed files (`graph/grep.py`).

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::PathBuf;

use regex::{Regex, RegexBuilder};
use rusqlite::Connection;
use serde_json::{Value, json};

pub const DEFAULT_MAX_HITS: usize = 256;

struct SymbolSpan {
    id: String,
    qualified_name: Option<String>,
    kind: String,
    line_start: i64,
    line_end: i64,
    incoming: i64,
}

struct Group {
    repo: String,
    path: String,
    symbol: Option<SymbolSpan>,
    hits: Vec<Value>,
}

impl Group {
    fn first_line(&self) -> i64 {
        self.hits
            .first()
            .and_then(|h| h["line"].as_i64())
            .unwrap_or(0)
    }

    fn incoming(&self) -> i64 {
        self.symbol.as_ref().map(|s| s.incoming).unwrap_or(0)
    }

    fn qualified_name(&self) -> &str {
        self.symbol
            .as_ref()
            .and_then(|s| s.qualified_name.as_deref())
            .unwrap_or("")
    }

    fn payload(&self) -> Value {
        let symbol = &self.symbol;
        json!({
            "repo": self.repo,
            "path": self.path,
            "qualified_name": symbol.as_ref().and_then(|s| s.qualified_name.clone()),
            "kind": symbol.as_ref().map(|s| s.kind.clone()).unwrap_or_else(|| "file".into()),
            "incoming": self.incoming(),
            "hits": self.hits,
        })
    }
}

/// Grouped hits by innermost symbol; `truncated` scans report dropped counts
/// as lower bounds. `resolve` maps (repo_id, stored_path) to a disk path.
pub fn graph_grep(
    conn: &Connection,
    pattern: &str,
    ignore_case: bool,
    fixed: bool,
    path_prefix: &str,
    max_hits: usize,
    resolve: &dyn Fn(&str, &str) -> PathBuf,
) -> Result<Value, String> {
    if max_hits < 1 {
        return Err("max_hits must be at least 1".to_string());
    }
    let expression = if fixed {
        regex::escape(pattern)
    } else {
        pattern.to_string()
    };
    let matcher = RegexBuilder::new(&expression)
        .case_insensitive(ignore_case)
        .build()
        .map_err(|e| format!("invalid pattern: {e}"))?;

    let incoming = incoming_counts(conn);
    let mut file_rows: Vec<(String, String, String)> = Vec::new();
    {
        let mut stmt = conn
            .prepare("SELECT id, repo_id, path FROM files ORDER BY repo_id, path")
            .map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
            .map_err(|e| e.to_string())?;
        for row in mapped {
            file_rows.push(row.map_err(|e| e.to_string())?);
        }
    }
    let prefix = normalized_prefix(path_prefix);
    let symbols_by_file = symbols_by_file(conn, &incoming)?;

    let mut searched = 0usize;
    let mut unreadable = 0usize;
    let mut truncated = false;
    let mut collected_hits = 0usize;
    let mut dropped_hits = 0usize;
    let mut dropped_groups = 0usize;
    let mut groups: BTreeMap<(String, String, String), Group> = BTreeMap::new();
    'files: for (file_id, repo, path) in file_rows {
        if !under_prefix(&path, &prefix) {
            continue;
        }
        let source_path = resolve(&repo, &path);
        let Ok(bytes) = std::fs::read(&source_path) else {
            unreadable += 1;
            continue;
        };
        let source = String::from_utf8_lossy(&bytes).into_owned();
        searched += 1;
        let empty = Vec::new();
        let symbols = symbols_by_file.get(&file_id).unwrap_or(&empty);
        let (stored, excess_hits, excess_groups) = collect_file_hits(
            &matcher,
            &crate::textfmt::split_lines_universal(&source),
            &repo,
            &path,
            symbols,
            &mut groups,
            max_hits - collected_hits,
        );
        collected_hits += stored;
        dropped_hits += excess_hits;
        dropped_groups += excess_groups;
        if collected_hits >= max_hits {
            truncated = true;
            break 'files;
        }
    }

    let mut ordered: Vec<Group> = groups.into_values().collect();
    ordered.sort_by(|a, b| {
        b.incoming()
            .cmp(&a.incoming())
            .then_with(|| a.path.cmp(&b.path))
            .then_with(|| a.qualified_name().cmp(b.qualified_name()))
            .then_with(|| a.first_line().cmp(&b.first_line()))
    });
    let mut capped: Vec<Value> = Vec::new();
    let mut returned_hits = 0usize;
    for group in ordered {
        if returned_hits >= max_hits {
            break;
        }
        let allowed: Vec<Value> = group
            .hits
            .iter()
            .take(max_hits - returned_hits)
            .cloned()
            .collect();
        if !allowed.is_empty() {
            returned_hits += allowed.len();
            let group = Group {
                hits: allowed,
                ..group
            };
            capped.push(group.payload());
        }
    }

    Ok(json!({
        "searched_files": searched,
        "unreadable_files": unreadable,
        "groups": capped,
        "dropped_hits": dropped_hits,
        "dropped_groups": dropped_groups,
        "truncated": truncated,
    }))
}

fn incoming_counts(conn: &Connection) -> HashMap<String, i64> {
    let mut map = HashMap::new();
    let Ok(mut stmt) = conn.prepare(
        "SELECT target_id, COUNT(*) AS incoming FROM edges \
         WHERE target_id IS NOT NULL GROUP BY target_id",
    ) else {
        return map;
    };
    let Ok(mapped) = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
    else {
        return map;
    };
    for row in mapped.flatten() {
        map.insert(row.0, row.1);
    }
    map
}

fn symbols_by_file(
    conn: &Connection,
    incoming: &HashMap<String, i64>,
) -> Result<HashMap<String, Vec<SymbolSpan>>, String> {
    let mut result: HashMap<String, Vec<SymbolSpan>> = HashMap::new();
    let mut stmt = conn
        .prepare(
            "SELECT id, file_id, qualified_name, kind, line_start, line_end FROM symbols \
             WHERE kind != 'module' ORDER BY file_id, line_start, line_end, qualified_name, id",
        )
        .map_err(|e| e.to_string())?;
    let mapped = stmt
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, Option<String>>(2)?,
                r.get::<_, String>(3)?,
                r.get::<_, Option<i64>>(4)?,
                r.get::<_, Option<i64>>(5)?,
            ))
        })
        .map_err(|e| e.to_string())?;
    for row in mapped {
        let (id, file_id, qualified_name, kind, line_start, line_end) =
            row.map_err(|e| e.to_string())?;
        result.entry(file_id).or_default().push(SymbolSpan {
            incoming: incoming.get(&id).copied().unwrap_or(0),
            id,
            qualified_name: qualified_name.filter(|q| !q.is_empty()),
            kind,
            line_start: line_start.unwrap_or(1),
            line_end: line_end.unwrap_or(1),
        });
    }
    Ok(result)
}

fn normalized_prefix(prefix: &str) -> String {
    prefix
        .replace('\\', "/")
        .split('/')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join("/")
}

fn under_prefix(path: &str, prefix: &str) -> bool {
    prefix.is_empty() || path == prefix || path.starts_with(&format!("{prefix}/"))
}

/// Store up to `budget` hits; return stored, excess hits, excess groups.
fn collect_file_hits(
    matcher: &Regex,
    lines: &[&str],
    repo: &str,
    path: &str,
    symbols: &[SymbolSpan],
    groups: &mut BTreeMap<(String, String, String), Group>,
    budget: usize,
) -> (usize, usize, usize) {
    let mut collected = 0usize;
    let mut excess = 0usize;
    let mut excess_keys: BTreeSet<(String, String, String)> = BTreeSet::new();
    for (index, text) in lines.iter().enumerate() {
        let line_number = index as i64 + 1;
        for mat in matcher.find_iter(text) {
            let symbol = innermost_symbol(symbols, line_number);
            let qname = symbol
                .as_ref()
                .and_then(|s| s.qualified_name.clone())
                .unwrap_or_default();
            let key = (repo.to_string(), path.to_string(), qname);
            if collected >= budget {
                // Budget exhausted: keep counting (never storing) the rest of
                // the file so truncated scans report real lower bounds.
                excess += 1;
                if !groups.contains_key(&key) {
                    excess_keys.insert(key);
                }
                continue;
            }
            let column = text[..mat.start()].chars().count() + 1;
            let hit = json!({"line": line_number, "column": column, "text": text});
            match groups.get_mut(&key) {
                None => {
                    groups.insert(
                        key.clone(),
                        Group {
                            repo: repo.to_string(),
                            path: path.to_string(),
                            symbol,
                            hits: vec![hit],
                        },
                    );
                }
                Some(group) => group.hits.push(hit),
            }
            collected += 1;
        }
    }
    (collected, excess, excess_keys.len())
}

fn innermost_symbol(symbols: &[SymbolSpan], line_number: i64) -> Option<SymbolSpan> {
    let mut containing: Vec<&SymbolSpan> = symbols
        .iter()
        .filter(|s| s.line_start <= line_number && line_number <= s.line_end)
        .collect();
    if containing.is_empty() {
        return None;
    }
    containing.sort_by(|a, b| {
        (a.line_end - a.line_start)
            .cmp(&(b.line_end - b.line_start))
            .then_with(|| a.line_start.cmp(&b.line_start))
            .then_with(|| {
                a.qualified_name
                    .as_deref()
                    .unwrap_or("")
                    .cmp(b.qualified_name.as_deref().unwrap_or(""))
            })
            .then_with(|| a.id.cmp(&b.id))
    });
    containing.first().map(|s| SymbolSpan {
        id: s.id.clone(),
        qualified_name: s.qualified_name.clone(),
        kind: s.kind.clone(),
        line_start: s.line_start,
        line_end: s.line_end,
        incoming: s.incoming,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prefix_normalization_collapses_redundant_segments() {
        assert_eq!(normalized_prefix("\\src\\cairn\\"), "src/cairn");
        assert!(under_prefix("src/cairn/x.py", "src/cairn"));
        assert!(!under_prefix("src/cairn_other/x.py", "src/cairn"));
        assert!(under_prefix("any/path", ""));
    }

    #[test]
    fn column_counts_characters_not_bytes() {
        let matcher = Regex::new("target").unwrap();
        let line = "héllo → target";
        let mat = matcher.find(line).unwrap();
        let column = line[..mat.start()].chars().count() + 1;
        assert_eq!(column, 9);
    }
}
