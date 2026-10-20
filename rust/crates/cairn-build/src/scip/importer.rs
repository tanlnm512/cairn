//! Edges-only SCIP index overlay: position-joined exact call/reference edges over the tree-sitter graph.

use std::collections::{HashMap, HashSet};

use prost::Message as _;
use rusqlite::Connection;

use super::proto;
use crate::scanner;
use crate::timefmt::now_iso;

/// scip.SymbolRole.Definition (bit 0x1); definition occurrences never emit edges.
const ROLE_DEFINITION: i32 = 0x1;
/// scip.SymbolInformation.Kind values that classify an edge as a call.
const KIND_FUNCTION: i32 = 17;
const KIND_METHOD: i32 = 26;

const SKIP_PARSE_ERROR: &str = "scip_parse_error";
const SKIP_JOIN_ANOMALY: &str = "scip_join_anomaly";

/// Per-document edge-yield rate below which the file keeps its tree-sitter
/// edges and the anomaly is recorded.
const JOIN_ANOMALY_THRESHOLD: f64 = 0.5;

const SYMBOL_SPANS_SQL: &str = "
    SELECT id, line_start, line_end
    FROM symbols
    WHERE file_id = ? AND line_start IS NOT NULL AND line_end IS NOT NULL
    ORDER BY (line_end - line_start) ASC, line_start DESC,
             column_start DESC, column_end ASC
";

const INSERT_EDGE: &str = "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, \
     column, resolution, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scip')";

/// Per-file authority: the covered file's tree-sitter call/reference edges give
/// way to the index's; other kinds and other files stay untouched.
const DELETE_FILE_CALLREF_EDGES: &str = "DELETE FROM edges WHERE kind IN ('calls','references') AND source_id IN \
     (SELECT id FROM symbols WHERE file_id = ?)";

/// Importer-owned skip rows are index-scoped, not repo-scoped, so repo_id stays empty.
const INSERT_SKIP: &str = "INSERT INTO skipped_files (id, repo_id, path, reason, recorded_at) \
     VALUES (?, '', ?, ?, ?)";

/// An anomalous-join row names a matched file, so it carries that file's repo id.
const INSERT_ANOMALY_SKIP: &str = "INSERT INTO skipped_files (id, repo_id, path, reason, recorded_at) \
     VALUES (?, ?, ?, ?, ?)";

/// Re-imports replace the earlier skip row for the same (repo, path, reason).
const DELETE_PRIOR_SKIP: &str =
    "DELETE FROM skipped_files WHERE repo_id = ? AND path = ? AND reason = ?";

/// Tree-sitter call/reference sites of one file, for (kind, line) disagreement
/// matching before the authority DELETE; scip rows never count as the incumbent.
const TS_SITES_SQL: &str = "SELECT e.kind, e.line, e.target_id, e.resolution \
     FROM edges e JOIN symbols s ON e.source_id = s.id \
     WHERE s.file_id = ? AND e.kind IN ('calls','references') \
     AND coalesce(e.source, '') != 'scip'";

/// Counterpart of the Python import record dict; field names mirror its keys.
#[derive(Debug, Clone, Default, PartialEq, serde::Serialize)]
pub struct ImportRecord {
    pub index: String,
    pub documents: usize,
    pub matched_documents: usize,
    pub skipped_documents: usize,
    pub edges: usize,
    pub calls: usize,
    pub references: usize,
    pub exact: usize,
    pub unresolved: usize,
    pub joined_occurrences: usize,
    pub unjoined_occurrences: usize,
    pub join_anomalies: usize,
    pub disagreements: usize,
    pub upgrades: usize,
}

/// (symbol id, 1-based line span) rows from `SYMBOL_SPANS_SQL`, innermost first.
type SpanRow = (String, i64, i64);

/// (start_line, end_line, start_char, end_char), 0-based inclusive lines.
type Span = [i64; 4];

/// One planned edge row; the Python 8-tuple handed to `INSERT_EDGE`.
struct EdgeRow {
    id: String,
    source_id: String,
    target_id: Option<String>,
    target_name: String,
    kind: &'static str,
    line: i64,
    column: i64,
    resolution: &'static str,
}

/// Triage from `resolve_target`: exact join, unresolved target, or dropped occurrence.
enum TargetResolution {
    Exact(String),
    Unresolved,
    Dropped,
}

/// uuid4().hex counterpart: 32 lowercase hex chars, unique per row.
fn new_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

/// Best-effort readable name from a SCIP symbol string (a label, never a join key).
fn terminal_name(symbol: &str) -> String {
    let tail = symbol.rsplit(' ').next().unwrap_or("");
    let tail = tail.trim_end_matches(['(', ')', '.']);
    let tail = tail.rsplit('/').next().unwrap_or("");
    let tail = tail.rsplit('.').next().unwrap_or("");
    if tail.is_empty() {
        symbol.to_string()
    } else {
        tail.to_string()
    }
}

/// (start_line, end_line, start_char, end_char), 0-based inclusive lines; None without a range.
// `range` is the deprecated fallback encoding the contract still carries.
#[allow(deprecated)]
fn occurrence_span(occ: &proto::Occurrence) -> Option<Span> {
    match &occ.typed_range {
        Some(proto::occurrence::TypedRange::SingleLineRange(r)) => Some([
            i64::from(r.line),
            i64::from(r.line),
            i64::from(r.start_character),
            i64::from(r.end_character),
        ]),
        Some(proto::occurrence::TypedRange::MultiLineRange(r)) => Some([
            i64::from(r.start_line),
            i64::from(r.end_line),
            i64::from(r.start_character),
            i64::from(r.end_character),
        ]),
        // Deprecated repeated [startLine, startChar, (endLine,) endChar].
        None => match occ.range.as_slice() {
            [a, b, c] => Some([i64::from(*a), i64::from(*a), i64::from(*b), i64::from(*c)]),
            [a, b, c, d] => Some([i64::from(*a), i64::from(*c), i64::from(*b), i64::from(*d)]),
            _ => None,
        },
    }
}

/// Id of the first (innermost) symbol row whose 1-based line span contains the 0-based span.
fn innermost(spans: &[SpanRow], span: &Span) -> Option<String> {
    let (start_line, end_line) = (span[0], span[1]);
    spans
        .iter()
        .find(|(_, line_start, line_end)| {
            *line_start <= start_line + 1 && start_line < end_line + 1 && end_line < *line_end
        })
        .map(|(id, _, _)| id.clone())
}

/// Canonical /-separated form of a document's repo-relative path.
fn norm_rel(rel_path: &str) -> String {
    rel_path.replace('\\', "/")
}

/// Repo ids the scanner identity substrate discovers for this workspace.
fn workspace_repo_ids(workspace: &str) -> Result<HashSet<String>, String> {
    Ok(scanner::discover_repo_records(workspace)?
        .into_iter()
        .map(|record| record.repo_id)
        .collect())
}

/// (files.id, repo_id) attributing a document's repo-relative path to
/// exactly one workspace repo; None when unmatched or ambiguous -- never a
/// guessed repo.
fn match_document_path(
    conn: &Connection,
    rel_path: &str,
    repo_ids: &HashSet<String>,
) -> Result<Option<(String, String)>, String> {
    let mut stmt = conn
        .prepare("SELECT id, repo_id FROM files WHERE path = ?")
        .map_err(|e| e.to_string())?;
    let matches: Vec<(String, String)> = stmt
        .query_map([rel_path], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })
        .map_err(|e| e.to_string())?
        .filter_map(Result::ok)
        .filter(|(_, repo_id)| repo_ids.contains(repo_id))
        .collect();
    Ok(if matches.len() == 1 {
        matches.into_iter().next()
    } else {
        None
    })
}

/// Parsed Index protobuf; a corrupt file surfaces a "corrupt SCIP index" error
/// to the caller, which records scip_parse_error via `record_parse_error`.
fn parse_index(scip_path: &str) -> Result<proto::Index, String> {
    let bytes = std::fs::read(scip_path).map_err(|e| e.to_string())?;
    proto::Index::decode(bytes.as_slice())
        .map_err(|e| format!("corrupt SCIP index: {scip_path} ({e})"))
}

/// Decode-failure counterpart of `_parse_index`: record scip_parse_error for
/// this index, replacing any earlier row, before the caller sees the error.
fn record_parse_error(conn: &Connection, scip_path: &str) -> Result<(), String> {
    conn.execute(
        DELETE_PRIOR_SKIP,
        rusqlite::params!["", scip_path, SKIP_PARSE_ERROR],
    )
    .map_err(|e| e.to_string())?;
    conn.execute(
        INSERT_SKIP,
        rusqlite::params![new_id(), scip_path, SKIP_PARSE_ERROR, now_iso()],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// Return exact position-joined resolution, dropping counted join misses.
fn resolve_target(
    spans_by_rel: &HashMap<String, Option<Vec<SpanRow>>>,
    def_sites: &HashMap<String, Vec<(String, Option<Span>)>>,
    symbol: &str,
    doc_rel: &str,
) -> TargetResolution {
    let filtered;
    let candidates: &[(String, Option<Span>)] = if symbol.starts_with("local ") {
        filtered = def_sites
            .get(symbol)
            .map(|sites| {
                sites
                    .iter()
                    .filter(|(rel, _)| norm_rel(rel) == doc_rel)
                    .cloned()
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        &filtered
    } else {
        def_sites.get(symbol).map(Vec::as_slice).unwrap_or(&[])
    };
    let mut saw_unjoinable = false;
    for (rel, span) in candidates {
        let Some(spans) = spans_by_rel.get(&norm_rel(rel)).and_then(|s| s.as_ref()) else {
            continue;
        };
        let Some(span) = span else { continue };
        if let Some(target) = innermost(spans, span) {
            return TargetResolution::Exact(target);
        }
        saw_unjoinable = true;
    }
    if saw_unjoinable {
        return TargetResolution::Dropped;
    }
    TargetResolution::Unresolved
}

/// Return edge rows and anomaly status for one document's occurrences.
fn plan_document(
    doc: &proto::Document,
    spans_by_rel: &HashMap<String, Option<Vec<SpanRow>>>,
    def_sites: &HashMap<String, Vec<(String, Option<Span>)>>,
    symbol_kinds: &HashMap<String, i32>,
    record: &mut ImportRecord,
) -> (Vec<EdgeRow>, bool) {
    let rel = norm_rel(&doc.relative_path);
    let Some(spans) = spans_by_rel.get(&rel).and_then(|s| s.as_ref()) else {
        return (Vec::new(), false);
    };
    let mut total = 0usize;
    let mut rows: Vec<EdgeRow> = Vec::new();
    for occ in &doc.occurrences {
        if occ.symbol_roles & ROLE_DEFINITION != 0 {
            continue;
        }
        total += 1;
        let Some(span) = occurrence_span(occ) else {
            record.unjoined_occurrences += 1;
            continue;
        };
        let Some(source_id) = innermost(spans, &span) else {
            record.unjoined_occurrences += 1;
            continue;
        };
        record.joined_occurrences += 1;

        let (target_id, resolution) =
            match resolve_target(spans_by_rel, def_sites, &occ.symbol, &rel) {
                TargetResolution::Dropped => {
                    record.unjoined_occurrences += 1;
                    continue;
                }
                TargetResolution::Exact(target) => (Some(target), "exact"),
                TargetResolution::Unresolved => (None, "unresolved"),
            };

        let kind = if matches!(
            symbol_kinds.get(&occ.symbol).copied(),
            Some(KIND_FUNCTION | KIND_METHOD)
        ) {
            "calls"
        } else {
            "references"
        };
        if kind == "calls" {
            record.calls += 1;
        } else {
            record.references += 1;
        }
        if resolution == "unresolved" {
            record.unresolved += 1;
        } else {
            record.exact += 1;
        }
        rows.push(EdgeRow {
            id: new_id(),
            source_id,
            target_id,
            target_name: terminal_name(&occ.symbol),
            kind,
            line: span[0] + 1, // tree-sitter lines are 1-based
            column: span[2],
            resolution,
        });
    }
    // The guard keys on row yield, so target-dropped occurrences count against it.
    let anomalous = total > 0 && (rows.len() as f64) / (total as f64) < JOIN_ANOMALY_THRESHOLD;
    (rows, anomalous)
}

/// (kind, line) -> tree-sitter (target_id, resolution) call/reference sites of one file.
type TsSites = HashMap<(String, Option<i64>), Vec<(Option<String>, Option<String>)>>;

/// Return disagreements and upgrades for SCIP edges by kind and line.
fn count_disagreements(
    conn: &Connection,
    file_id: &str,
    rows: &[EdgeRow],
) -> Result<(usize, usize), String> {
    let mut stmt = conn.prepare(TS_SITES_SQL).map_err(|e| e.to_string())?;
    let mut sites: TsSites = HashMap::new();
    let mapped = stmt
        .query_map([file_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<i64>>(1)?,
                row.get::<_, Option<String>>(2)?,
                row.get::<_, Option<String>>(3)?,
            ))
        })
        .map_err(|e| e.to_string())?;
    for row in mapped {
        let (kind, line, target_id, resolution) = row.map_err(|e| e.to_string())?;
        sites
            .entry((kind, line))
            .or_default()
            .push((target_id, resolution));
    }

    let mut disagreements = 0usize;
    let mut upgrades = 0usize;
    for row in rows {
        let Some(matches) = sites.get(&(row.kind.to_string(), Some(row.line))) else {
            continue;
        };
        if row.resolution == "exact"
            && matches.iter().any(|(_, res)| {
                res.as_deref()
                    .is_some_and(|r| r == "ambiguous" || r == "unresolved")
            })
        {
            upgrades += 1;
        }
        if let Some(target) = &row.target_id
            && matches
                .iter()
                .any(|(tid, _)| tid.as_deref().is_some_and(|t| t != target))
        {
            disagreements += 1;
        }
    }
    Ok((disagreements, upgrades))
}

/// Apply anomaly skip rows, per-file authority DELETEs, and edge INSERTs
/// atomically; undoes them and re-raises on any failure.
fn write_overlay(
    conn: &Connection,
    anomaly_rows: &[(String, String, String)],
    delete_file_ids: &[String],
    kept_rows: &[EdgeRow],
) -> Result<(), String> {
    conn.execute_batch("SAVEPOINT scip_import")
        .map_err(|e| e.to_string())?;
    let apply = (|| -> Result<(), String> {
        for (id, repo_id, path) in anomaly_rows {
            conn.execute(
                DELETE_PRIOR_SKIP,
                rusqlite::params![repo_id, path, SKIP_JOIN_ANOMALY],
            )
            .map_err(|e| e.to_string())?;
            conn.execute(
                INSERT_ANOMALY_SKIP,
                rusqlite::params![id, repo_id, path, SKIP_JOIN_ANOMALY, now_iso()],
            )
            .map_err(|e| e.to_string())?;
        }
        for file_id in delete_file_ids {
            conn.execute(DELETE_FILE_CALLREF_EDGES, rusqlite::params![file_id])
                .map_err(|e| e.to_string())?;
        }
        for row in kept_rows {
            conn.execute(
                INSERT_EDGE,
                rusqlite::params![
                    row.id,
                    row.source_id,
                    row.target_id,
                    row.target_name,
                    row.kind,
                    row.line,
                    row.column,
                    row.resolution
                ],
            )
            .map_err(|e| e.to_string())?;
        }
        Ok(())
    })();
    match apply {
        Ok(()) => conn
            .execute_batch("RELEASE scip_import")
            .map_err(|e| e.to_string()),
        Err(e) => {
            let _ = conn.execute_batch("ROLLBACK TO scip_import; RELEASE scip_import;");
            Err(e)
        }
    }
}

/// Import one .scip index as an edges-only overlay onto the built graph; returns the import record.
pub fn import_scip_file(
    conn: &Connection,
    scip_path: &str,
    workspace: &str,
) -> Result<ImportRecord, String> {
    let index = match parse_index(scip_path) {
        Ok(index) => index,
        Err(e) => {
            record_parse_error(conn, scip_path)?;
            return Err(e);
        }
    };

    let mut record = ImportRecord {
        index: scip_path.to_string(),
        documents: index.documents.len(),
        ..ImportRecord::default()
    };

    // Whole-index validate pass: target kinds and definition sites, no writes.
    let mut symbol_kinds: HashMap<String, i32> = index
        .external_symbols
        .iter()
        .map(|info| (info.symbol.clone(), info.kind))
        .collect();
    let mut def_sites: HashMap<String, Vec<(String, Option<Span>)>> = HashMap::new();
    for doc in &index.documents {
        for info in &doc.symbols {
            symbol_kinds.insert(info.symbol.clone(), info.kind);
        }
        for occ in &doc.occurrences {
            if occ.symbol_roles & ROLE_DEFINITION != 0 {
                def_sites
                    .entry(occ.symbol.clone())
                    .or_default()
                    .push((doc.relative_path.clone(), occurrence_span(occ)));
            }
        }
    }

    // Match every document before planning: an abort must leave no partial overlay.
    let repo_ids = workspace_repo_ids(workspace)?;
    let mut spans_by_rel: HashMap<String, Option<Vec<SpanRow>>> = HashMap::new();
    let mut covered_file_ids: HashMap<String, String> = HashMap::new();
    let mut covered_repo_ids: HashMap<String, String> = HashMap::new();
    for doc in &index.documents {
        let rel = norm_rel(&doc.relative_path);
        match match_document_path(conn, &rel, &repo_ids)? {
            None => {
                spans_by_rel.insert(rel.clone(), None);
                record.skipped_documents += 1;
            }
            Some((file_id, repo_id)) => {
                let mut stmt = conn.prepare(SYMBOL_SPANS_SQL).map_err(|e| e.to_string())?;
                let spans = stmt
                    .query_map([&file_id], |row| {
                        Ok((
                            row.get::<_, String>(0)?,
                            row.get::<_, i64>(1)?,
                            row.get::<_, i64>(2)?,
                        ))
                    })
                    .map_err(|e| e.to_string())?
                    .filter_map(Result::ok)
                    .collect::<Vec<SpanRow>>();
                spans_by_rel.insert(rel.clone(), Some(spans));
                covered_file_ids.insert(rel.clone(), file_id);
                covered_repo_ids.insert(rel, repo_id);
                record.matched_documents += 1;
            }
        }
    }

    let mut planned: Vec<(String, bool, Vec<EdgeRow>)> = Vec::new();
    for doc in &index.documents {
        let (rows, anomalous) =
            plan_document(doc, &spans_by_rel, &def_sites, &symbol_kinds, &mut record);
        planned.push((norm_rel(&doc.relative_path), anomalous, rows));
    }

    // Read phase: every fallible step (per-document disagreement scan, counter
    // updates) runs before the first write, so the write phase below is pure SQL.
    let mut anomaly_rows: Vec<(String, String, String)> = Vec::new();
    let mut kept_rows: Vec<EdgeRow> = Vec::new();
    let mut delete_file_ids: Vec<String> = Vec::new();
    for (rel, anomalous, rows) in planned {
        let Some(matched_id) = covered_file_ids.get(&rel) else {
            continue;
        };
        if anomalous {
            anomaly_rows.push((
                new_id(),
                covered_repo_ids.get(&rel).cloned().unwrap_or_default(),
                rel,
            ));
            record.join_anomalies += 1;
            continue;
        }
        if rows.is_empty() {
            // No occurrences to replace tree-sitter edges with -- keep them
            // rather than deleting the file's calls/references into nothing.
            continue;
        }
        let (disagreements, upgrades) = count_disagreements(conn, matched_id, &rows)?;
        record.disagreements += disagreements;
        record.upgrades += upgrades;
        delete_file_ids.push(matched_id.clone());
        kept_rows.extend(rows);
    }
    write_overlay(conn, &anomaly_rows, &delete_file_ids, &kept_rows)?;
    record.edges = kept_rows.len();
    Ok(record)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn terminal_name_matches_python_label_table() {
        // Expected values produced by cairn.parsers.scip_importer._terminal_name.
        let cases: &[(&str, &str)] = &[
            ("scip-python pypi util 1.0.0/util.py/add( f).", "f"),
            (
                "scip-java maven x 1.0.0 com/example/Foo#bar(Params).",
                "Foo#bar(Params",
            ),
            ("local util.py:1:1-4", "py:1:1-4"),
            ("no-symbols-at-all", "no-symbols-at-all"),
            ("scip-swift scip_module.Foo.method()", "method"),
            ("rust-analyzer cargo util 1.0.0/src/util.rs add(8, f)", "f"),
            ("", ""),
            ("a.b.", "b"),
        ];
        for (symbol, expected) in cases {
            assert_eq!(&terminal_name(symbol), expected, "symbol {symbol:?}");
        }
    }

    #[test]
    fn norm_rel_separator_canonicalization() {
        assert_eq!(norm_rel("a\\b\\c.py"), "a/b/c.py");
        assert_eq!(norm_rel("already/normal.py"), "already/normal.py");
        assert_eq!(norm_rel(""), "");
    }

    #[test]
    fn innermost_containment_is_one_based() {
        let spans = vec![("inner".to_string(), 4, 4), ("outer".to_string(), 2, 6)];
        assert_eq!(innermost(&spans, &[3, 3, 0, 1]).as_deref(), Some("inner"));
        assert_eq!(innermost(&spans, &[5, 5, 0, 1]).as_deref(), Some("outer"));
        assert_eq!(innermost(&spans, &[6, 6, 0, 1]), None);
        assert_eq!(innermost(&spans, &[1, 5, 0, 1]).as_deref(), Some("outer"));
    }
}
