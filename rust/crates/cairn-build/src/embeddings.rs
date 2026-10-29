//! Embeddings build path: chunking, stale re-embedding, multivector, orphan
//! reap, and term-df refresh for the hash and embeddings-server backends.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::env;

use rusqlite::Connection;
use sha2::{Digest, Sha256};

use crate::scanner;
use crate::timefmt::now_iso;
use cairn_query::tools_search::{
    self, DEFAULT_DIM, EffectiveBackend, NATIVE_MODEL, floats_to_blob, hash_vec, install_hint,
    native_embed_texts, semantic_env, server_base_url, server_embed, server_model,
};
use cairn_store::vec0;

/// Chunk token budget approximated at 4 chars per token.
pub const DEFAULT_MAX_TOKENS: usize = 512;

// --- backend resolution -------------------------------------------------------

enum Transport {
    Hash,
    Server {
        base: String,
        model: String,
        api_key: Option<String>,
    },
    Native,
}

struct EmbedEnv {
    transport: Transport,
    /// Row stamp scoping staleness and vec0 tables (`current_model()`).
    model: String,
}

/// Resolve the effective backend once: transport, availability, and stamp.
fn resolve_embed_env() -> Result<EmbedEnv, String> {
    let env = semantic_env(None);
    match env.backend {
        EffectiveBackend::Hash => Ok(EmbedEnv {
            transport: Transport::Hash,
            model: env.model,
        }),
        EffectiveBackend::Server => Ok(EmbedEnv {
            transport: Transport::Server {
                base: env
                    .server_base_url
                    .clone()
                    .ok_or_else(|| "server backend resolved without a base URL".to_string())?,
                model: server_model(),
                api_key: env.server_api_key.clone(),
            },
            model: env.model,
        }),
        EffectiveBackend::Native => Ok(EmbedEnv {
            transport: Transport::Native,
            model: NATIVE_MODEL.to_string(),
        }),
        EffectiveBackend::Unavailable => {
            let name = tools_search::backend_name();
            if name == "native" {
                Err(format!(
                    "embedding backend 'native' unavailable; {}",
                    tools_search::native_feature_hint()
                ))
            } else if name == "server" {
                let base = server_base_url(&name).unwrap_or_default();
                Err(format!(
                    "embedding server unavailable: GET {base}/models must return 200 and \
                     list model '{}'; or set CAIRN_EMBED_BACKEND=hash for a dep-free fallback",
                    server_model()
                ))
            } else {
                Err(format!(
                    "embedding backend '{name}' unavailable; {}",
                    install_hint()
                ))
            }
        }
    }
}

/// Embed texts through the effective backend; returns `(blobs, dim)`.
fn embed_texts(env: &EmbedEnv, texts: &[String]) -> Result<(Vec<Vec<u8>>, usize), String> {
    match &env.transport {
        Transport::Hash => {
            let blobs = texts
                .iter()
                .map(|t| {
                    let vec = hash_vec(t);
                    let floats: Vec<f32> = vec.iter().map(|&v| v as f32).collect();
                    floats_to_blob(&floats)
                })
                .collect();
            Ok((blobs, DEFAULT_DIM))
        }
        Transport::Server {
            base,
            model,
            api_key,
        } => server_embed(texts, base, model, api_key.as_deref()),
        Transport::Native => native_embed_texts(texts, texts.len()),
    }
}

// --- chunking -----------------------------------------------------------------

/// One symbol row with the chunk inputs selected from `symbols`/`files`.
pub struct EmbedRow {
    pub id: String,
    pub name: String,
    pub qualified_name: Option<String>,
    pub kind: String,
    pub docstring: Option<String>,
    pub line_start: Option<i64>,
    pub parameters: Option<String>,
    pub return_type: Option<String>,
    pub parent_scope: Option<String>,
    pub imports_summary: Option<String>,
    pub body: Option<String>,
    pub file_path: Option<String>,
    pub repo: Option<String>,
    pub existing_hash: Option<String>,
}

fn truthy(value: &Option<String>) -> Option<&str> {
    value.as_deref().filter(|s| !s.is_empty())
}

/// One symbol's chunk using the selected ablation variant (default `B`).
pub fn chunk_for_symbol(
    row: &EmbedRow,
    signature: Option<&str>,
    variant: Option<&str>,
    max_tokens: usize,
) -> String {
    let v = variant
        .map(str::to_string)
        .or_else(|| env::var("CAIRN_CHUNK_VARIANT").ok())
        .unwrap_or_else(|| "B".to_string())
        .to_uppercase();
    let kind = row.kind.trim();
    let qname = match truthy(&row.qualified_name) {
        Some(q) => q.trim(),
        None => row.name.trim(),
    };
    let doc = row.docstring.as_deref().map(str::trim).unwrap_or("");
    let sig = signature.unwrap_or("").trim();

    let drop_scope_extras = v == "B_NO_SCOPE" || v == "B_IDENTITIES";

    let mut scope_header: Vec<String> = Vec::new();
    if let Some(file_path) = truthy(&row.file_path) {
        scope_header.push(format!("File: {file_path}"));
    }
    if let Some(parent_scope) = truthy(&row.parent_scope).filter(|_| !drop_scope_extras) {
        scope_header.push(format!("Enclosing Scope: {parent_scope}"));
    }
    if let Some(imports_summary) = truthy(&row.imports_summary).filter(|_| !drop_scope_extras) {
        scope_header.push(format!("Imports: {imports_summary}"));
    }

    let mut parts: Vec<String> = Vec::new();
    if !scope_header.is_empty() {
        parts.push(scope_header.join("\n"));
    }

    let header = format!("{kind} {qname}").trim().to_string();
    if !header.is_empty() {
        parts.push(header.clone());
    }

    if v == "A" {
        let first_line = sig.split('\n').next().unwrap_or("");
        if !first_line.is_empty() && first_line != header {
            parts.push(first_line.to_string());
        }
        if !doc.is_empty() {
            parts.push(doc.to_string());
        }
    } else if matches!(
        v.as_str(),
        "B" | "C" | "B_NO_SCOPE" | "B_NO_SIG" | "B_IDENTITIES" | "C_TRIM"
    ) {
        if !sig.is_empty() && sig != header {
            parts.push(format!("Signature: {sig}"));
        }
        let keep_sig = v != "B_NO_SIG" && v != "B_IDENTITIES";
        if let Some(params_raw) = truthy(&row.parameters).filter(|_| keep_sig) {
            parts.push(format!("Parameters: {params_raw}"));
        }
        if let Some(ret_type) = truthy(&row.return_type).filter(|_| keep_sig) {
            parts.push(format!("Return Type: {ret_type}"));
        }
        if !doc.is_empty() {
            parts.push(format!("Docstring: {doc}"));
        }
        if (v == "C" || v == "C_TRIM")
            && let Some(body) = truthy(&row.body)
        {
            let body = if v == "C_TRIM" {
                let cap = max_tokens * 4 / 2;
                body.chars().take(cap).collect::<String>()
            } else {
                body.to_string()
            };
            parts.push(format!("Body:\n{body}"));
        }
    }

    let res = if parts.is_empty() {
        if !qname.is_empty() {
            qname.to_string()
        } else {
            kind.to_string()
        }
    } else {
        parts.join("\n")
    };
    let max_chars = max_tokens * 4;
    if res.chars().count() > max_chars {
        res.chars().take(max_chars).collect()
    } else {
        res
    }
}

/// The name-only multi-vector text for a symbol.
pub fn name_text_for_symbol(row: &EmbedRow, signature: Option<&str>) -> String {
    let kind = row.kind.trim();
    let qname = match truthy(&row.qualified_name) {
        Some(q) => q.trim(),
        None => row.name.trim(),
    };
    let sig = signature.unwrap_or("").trim();
    let header = format!("{kind} {qname}").trim().to_string();
    let mut parts: Vec<String> = Vec::new();
    if !header.is_empty() {
        parts.push(header.clone());
    }
    let first_line = sig.split('\n').next().unwrap_or("");
    if !first_line.is_empty() && first_line != header {
        parts.push(first_line.to_string());
    }
    parts.join("\n")
}

/// The symbol's stripped docstring, or an empty string.
pub fn docstring_text_for_symbol(row: &EmbedRow) -> String {
    row.docstring
        .as_deref()
        .map(str::trim)
        .unwrap_or("")
        .to_string()
}

/// One multi-vector text for a symbol and kind; `Err` on an unknown kind.
pub fn mv_text_for_kind(
    row: &EmbedRow,
    vector_kind: &str,
    signature: Option<&str>,
) -> Result<String, String> {
    match vector_kind {
        "name" => Ok(name_text_for_symbol(row, signature)),
        "docstring" => Ok(docstring_text_for_symbol(row)),
        other => Err(format!("unknown vector_kind: {other:?}")),
    }
}

fn chunk_hash(chunk: &str) -> String {
    scanner::hex(&Sha256::digest(chunk.as_bytes()))
}

/// Declaration lines keyed by symbol id, omitting unreadable symbols.
fn signature_lines_for_rows(workspace: &str, rows: &[EmbedRow]) -> HashMap<String, String> {
    let mut by_file: BTreeMap<(String, String), Vec<(String, i64)>> = BTreeMap::new();
    for r in rows {
        let (Some(path), Some(repo)) = (truthy(&r.file_path), truthy(&r.repo)) else {
            continue;
        };
        let Some(ls) = r.line_start else { continue };
        if ls < 1 {
            continue;
        }
        by_file
            .entry((repo.to_string(), path.to_string()))
            .or_default()
            .push((r.id.clone(), ls));
    }

    let mut out = HashMap::new();
    for ((repo, path), entries) in by_file {
        let abs = scanner::resolve_file_path(workspace, &repo, &path);
        // errors="replace" parity: undecodable bytes become U+FFFD, not a skip.
        let bytes = match std::fs::read(&abs) {
            Ok(bytes) => bytes,
            Err(_) => continue,
        };
        let content = String::from_utf8_lossy(&bytes);
        let lines: Vec<&str> = content.lines().collect();
        for (sid, ls) in entries {
            let idx = (ls - 1) as usize;
            if idx < lines.len() {
                out.insert(sid, lines[idx].trim().to_string());
            }
        }
    }
    out
}

// --- stale selection ------------------------------------------------------------

struct StaleRow {
    id: String,
    chunk: String,
    hash: String,
}

/// Rows, stale chunks, and declaration lines for the selection.
type Selection = (Vec<EmbedRow>, Vec<StaleRow>, HashMap<String, String>);

fn select_stale_symbols(
    conn: &Connection,
    model: &str,
    symbol_ids: Option<&[String]>,
    workspace: &str,
) -> rusqlite::Result<Selection> {
    let owned_ids: Vec<String> = match symbol_ids {
        None => Vec::new(),
        Some(ids) => ids.iter().filter(|s| !s.is_empty()).cloned().collect(),
    };
    if symbol_ids.is_some() && owned_ids.is_empty() {
        return Ok((Vec::new(), Vec::new(), HashMap::new()));
    }
    let (where_clause, params): (String, Vec<&dyn rusqlite::ToSql>) = {
        let mut params: Vec<&dyn rusqlite::ToSql> = vec![&model];
        let where_clause = if symbol_ids.is_none() {
            "WHERE s.kind IS NOT NULL".to_string()
        } else {
            let placeholders = vec!["?"; owned_ids.len()].join(",");
            for id in &owned_ids {
                params.push(id);
            }
            format!("WHERE s.kind IS NOT NULL AND s.id IN ({placeholders})")
        };
        (where_clause, params)
    };
    let sql = format!(
        "SELECT s.id, s.name, s.qualified_name, s.kind, s.docstring,
                s.line_start, s.parameters, s.return_type,
                s.parent_scope, s.imports_summary, s.body,
                f.path AS file_path, f.repo_id AS repo,
                e.content_hash AS existing_hash
         FROM symbols s
         JOIN files f ON s.file_id = f.id
         LEFT JOIN embeddings e ON e.symbol_id = s.id AND e.model = ?
         {where_clause}
         ORDER BY s.id"
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(params.as_slice(), |r| {
        Ok(EmbedRow {
            id: r.get(0)?,
            name: r.get(1)?,
            qualified_name: r.get(2)?,
            kind: r.get::<_, Option<String>>(3)?.unwrap_or_default(),
            docstring: r.get(4)?,
            line_start: r.get(5)?,
            parameters: r.get(6)?,
            return_type: r.get(7)?,
            parent_scope: r.get(8)?,
            imports_summary: r.get(9)?,
            body: r.get(10)?,
            file_path: r.get(11)?,
            repo: r.get(12)?,
            existing_hash: r.get(13)?,
        })
    })?;
    let mut all: Vec<EmbedRow> = Vec::new();
    for row in rows {
        all.push(row?);
    }

    let signatures = signature_lines_for_rows(workspace, &all);
    let mut stale: Vec<StaleRow> = Vec::new();
    for r in &all {
        let chunk = chunk_for_symbol(
            r,
            signatures.get(&r.id).map(String::as_str),
            None,
            DEFAULT_MAX_TOKENS,
        );
        if chunk.trim().is_empty() {
            continue;
        }
        let hash = chunk_hash(&chunk);
        if r.existing_hash.as_deref() != Some(hash.as_str()) {
            stale.push(StaleRow {
                id: r.id.clone(),
                chunk,
                hash,
            });
        }
    }
    Ok((all, stale, signatures))
}

// --- corpus embedding ------------------------------------------------------------

/// Counts reported by one embed pass (`embed_all`/`embed_symbols` summaries).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EmbedSummary {
    pub model: String,
    pub embedded: usize,
    pub attempted: usize,
    pub failed_batches: usize,
    pub skipped: usize,
    pub total: usize,
    pub reaped: usize,
    pub mv_embedded: Option<usize>,
    /// Rows whose vec0 entry was synced in place (`embed_symbols` only).
    pub ann_synced: usize,
}

/// Embed stale symbols and refresh multivector, orphan, and term-df state.
pub fn embed_all(
    conn: &Connection,
    workspace: &str,
    batch_size: usize,
    limit: Option<usize>,
    reap_orphans: bool,
    multivector: bool,
) -> Result<EmbedSummary, String> {
    if batch_size == 0 {
        return Err("batch_size must be >= 1".to_string());
    }
    let env = resolve_embed_env()?;
    let (all_rows, stale_rows, signatures) =
        select_stale_symbols(conn, &env.model, None, workspace).map_err(|e| e.to_string())?;

    let mut stale_rows = stale_rows;
    if let Some(limit) = limit {
        stale_rows.truncate(limit);
    }

    let total = stale_rows.len();
    let now = now_iso();
    let mut embedded = 0usize;
    for batch in stale_rows.chunks(batch_size) {
        let texts: Vec<String> = batch.iter().map(|s| s.chunk.clone()).collect();
        let (blobs, _dim) = embed_texts(&env, &texts)?;
        let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
        for (stale, blob) in batch.iter().zip(&blobs) {
            let dim = (blob.len() / 4) as i64;
            tx.execute(
                "INSERT INTO embeddings
                 (symbol_id, model, dim, vec, chunk, content_hash, embedded_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(symbol_id, model) DO UPDATE SET
                 dim=excluded.dim, vec=excluded.vec, chunk=excluded.chunk,
                 content_hash=excluded.content_hash, embedded_at=excluded.embedded_at",
                rusqlite::params![stale.id, env.model, dim, blob, stale.chunk, stale.hash, now],
            )
            .map_err(|e| e.to_string())?;
        }
        tx.commit().map_err(|e| e.to_string())?;
        embedded += batch.len();
    }

    let reaped = if reap_orphans {
        reap_orphaned_embeddings(conn).map_err(|e| e.to_string())?
    } else {
        0
    };

    let mv_embedded = if multivector {
        Some(
            embed_mv_kinds(conn, &env, &all_rows, &signatures, batch_size, limit)
                .map_err(|e| e.to_string())?,
        )
    } else {
        None
    };

    rebuild_term_df(conn).map_err(|e| e.to_string())?;

    Ok(EmbedSummary {
        model: env.model,
        embedded,
        attempted: total,
        failed_batches: 0,
        skipped: all_rows.len() - total,
        total: all_rows.len(),
        reaped,
        mv_embedded,
        ann_synced: 0,
    })
}

/// Embed selected symbols and synchronously sync their vec0 rows.
pub fn embed_symbols(
    conn: &Connection,
    workspace: &str,
    symbol_ids: &[String],
    sync_ann: bool,
) -> Result<EmbedSummary, String> {
    let env = resolve_embed_env()?;
    let ids: Vec<String> = symbol_ids
        .iter()
        .filter(|s| !s.is_empty())
        .cloned()
        .collect();
    if ids.is_empty() {
        return Ok(EmbedSummary {
            model: env.model,
            embedded: 0,
            attempted: 0,
            failed_batches: 0,
            skipped: 0,
            total: 0,
            reaped: 0,
            mv_embedded: None,
            ann_synced: 0,
        });
    }

    let (rows, stale_rows, _signatures) =
        select_stale_symbols(conn, &env.model, Some(&ids), workspace).map_err(|e| e.to_string())?;
    if stale_rows.is_empty() {
        return Ok(EmbedSummary {
            model: env.model,
            embedded: 0,
            attempted: 0,
            failed_batches: 0,
            skipped: rows.len(),
            total: rows.len(),
            reaped: 0,
            mv_embedded: None,
            ann_synced: 0,
        });
    }

    let texts: Vec<String> = stale_rows.iter().map(|s| s.chunk.clone()).collect();
    let (blobs, _dim) = embed_texts(&env, &texts)?;
    let now = now_iso();

    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    let mut ann_synced = 0usize;
    for (stale, blob) in stale_rows.iter().zip(&blobs) {
        let dim = (blob.len() / 4) as i64;
        tx.execute(
            "INSERT INTO embeddings
             (symbol_id, model, dim, vec, chunk, content_hash, embedded_at)
             VALUES (?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(symbol_id, model) DO UPDATE SET
             dim=excluded.dim, vec=excluded.vec, chunk=excluded.chunk,
             content_hash=excluded.content_hash, embedded_at=excluded.embedded_at",
            rusqlite::params![stale.id, env.model, dim, blob, stale.chunk, stale.hash, now],
        )
        .map_err(|e| e.to_string())?;
        if sync_ann {
            let rowid: Option<i64> = tx
                .query_row(
                    "SELECT rowid FROM embeddings WHERE symbol_id = ?1 AND model = ?2",
                    rusqlite::params![stale.id, env.model],
                    |r| r.get(0),
                )
                .map(Some)
                .or_else(|e| match e {
                    rusqlite::Error::QueryReturnedNoRows => Ok(None),
                    other => Err(other),
                })
                .map_err(|e| e.to_string())?;
            if let Some(rowid) = rowid
                && tools_search::ann_backend_enabled()
                && vec0::sync_index_row(conn, &env.model, rowid, blob).unwrap_or(false)
            {
                ann_synced += 1;
            }
        }
    }
    tx.commit().map_err(|e| e.to_string())?;

    Ok(EmbedSummary {
        model: env.model,
        embedded: stale_rows.len(),
        attempted: stale_rows.len(),
        failed_batches: 0,
        skipped: rows.len() - stale_rows.len(),
        total: rows.len(),
        reaped: 0,
        mv_embedded: None,
        ann_synced,
    })
}

/// Refresh stale multi-vector rows and delete vanished docstring rows.
fn embed_mv_kinds(
    conn: &Connection,
    env: &EmbedEnv,
    rows: &[EmbedRow],
    signatures: &HashMap<String, String>,
    batch_size: usize,
    limit: Option<usize>,
) -> Result<usize, String> {
    let mut existing: HashMap<(String, String), String> = HashMap::new();
    {
        let mut stmt = conn
            .prepare(
                "SELECT symbol_id, vector_kind, content_hash FROM embeddings_mv WHERE model = ?1",
            )
            .map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map([&env.model], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, Option<String>>(2)?.unwrap_or_default(),
                ))
            })
            .map_err(|e| e.to_string())?;
        for row in mapped {
            let (sid, kind, chash) = row.map_err(|e| e.to_string())?;
            existing.insert((sid, kind), chash);
        }
    }

    let mut stale: Vec<(String, String, String, String)> = Vec::new();
    let mut emptied_docstring: Vec<String> = Vec::new();
    for r in rows {
        for kind in ["name", "docstring"] {
            let text = mv_text_for_kind(r, kind, signatures.get(&r.id).map(String::as_str))
                .map_err(|e| e.to_string())?;
            if text.trim().is_empty() {
                if kind == "docstring" && existing.contains_key(&(r.id.clone(), kind.to_string())) {
                    emptied_docstring.push(r.id.clone());
                }
                continue;
            }
            let chash = chunk_hash(&text);
            if existing
                .get(&(r.id.clone(), kind.to_string()))
                .map(String::as_str)
                != Some(chash.as_str())
            {
                stale.push((r.id.clone(), kind.to_string(), text, chash));
            }
        }
    }

    if !emptied_docstring.is_empty() {
        let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
        for sid in &emptied_docstring {
            tx.execute(
                "DELETE FROM embeddings_mv
                 WHERE model = ?1 AND vector_kind = 'docstring' AND symbol_id = ?2",
                rusqlite::params![env.model, sid],
            )
            .map_err(|e| e.to_string())?;
        }
        tx.commit().map_err(|e| e.to_string())?;
    }

    if let Some(limit) = limit {
        stale.truncate(limit);
    }

    let mut embedded = 0usize;
    let now = now_iso();
    for batch in stale.chunks(batch_size) {
        let texts: Vec<String> = batch.iter().map(|(_, _, t, _)| t.clone()).collect();
        let (blobs, _dim) = embed_texts(env, &texts)?;
        let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
        for ((sid, kind, text, chash), blob) in batch.iter().zip(&blobs) {
            let dim = (blob.len() / 4) as i64;
            tx.execute(
                "INSERT INTO embeddings_mv
                 (symbol_id, model, vector_kind, dim, vec, chunk, content_hash, embedded_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(symbol_id, model, vector_kind) DO UPDATE SET
                 dim=excluded.dim, vec=excluded.vec, chunk=excluded.chunk,
                 content_hash=excluded.content_hash, embedded_at=excluded.embedded_at",
                rusqlite::params![sid, env.model, kind, dim, blob, text, chash, now],
            )
            .map_err(|e| e.to_string())?;
        }
        tx.commit().map_err(|e| e.to_string())?;
        embedded += batch.len();
    }
    Ok(embedded)
}

// --- knowledge corpus -----------------------------------------------------------

/// Counts reported by one knowledge-embed pass (`embed_knowledge` summary).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KnowledgeEmbedSummary {
    pub model: String,
    pub embedded: usize,
    pub skipped: usize,
    pub total: usize,
}

/// The backend stamp for embedding passes (`current_model()`).
pub fn current_model() -> Result<String, String> {
    Ok(resolve_embed_env()?.model)
}

/// Embed knowledge concepts missing under the current model.
pub fn embed_knowledge(
    conn: &Connection,
    bundle: &cairn_query::tools_km::Bundle,
    batch_size: usize,
    mut progress: impl FnMut(usize, usize),
) -> Result<KnowledgeEmbedSummary, String> {
    if batch_size == 0 {
        return Err("batch_size must be >= 1".to_string());
    }
    let env = resolve_embed_env()?;
    let cids = bundle.list_concepts(Some("knowledge/"));
    if cids.is_empty() {
        return Ok(KnowledgeEmbedSummary {
            model: env.model,
            embedded: 0,
            skipped: 0,
            total: 0,
        });
    }
    let mut existing: HashSet<String> = HashSet::new();
    {
        let mut stmt = conn
            .prepare(
                "SELECT doc_id FROM knowledge_embeddings \
                 WHERE model = ?1 AND chunk_index = 0",
            )
            .map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map([&env.model], |r| r.get::<_, String>(0))
            .map_err(|e| e.to_string())?;
        for row in mapped {
            existing.insert(row.map_err(|e| e.to_string())?);
        }
    }
    let to_embed: Vec<String> = cids
        .iter()
        .filter(|cid| !existing.contains(*cid))
        .cloned()
        .collect();

    let total = to_embed.len();
    let mut embedded = 0usize;
    let now = now_iso();
    for (i, batch) in to_embed.chunks(batch_size).enumerate() {
        let mut pairs: Vec<(String, String)> = Vec::new();
        for cid in batch {
            let Ok(concept) = bundle.read_concept(cid) else {
                continue;
            };
            let chunk = [concept.title, concept.description, Some(concept.body)]
                .into_iter()
                .flatten()
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>()
                .join(" ");
            if !chunk.trim().is_empty() {
                pairs.push((cid.clone(), chunk));
            }
        }
        if pairs.is_empty() {
            continue;
        }
        let texts: Vec<String> = pairs.iter().map(|(_, c)| c.clone()).collect();
        let (blobs, _dim) = embed_texts(&env, &texts)?;
        let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
        for ((cid, chunk), blob) in pairs.iter().zip(&blobs) {
            let dim = (blob.len() / 4) as i64;
            // Rowid-stable upsert keeps the vec0 ANN index aligned across re-embeds.
            tx.execute(
                "INSERT INTO knowledge_embeddings \
                 (doc_id, chunk_index, model, dim, vec, chunk, embedded_at) \
                 VALUES (?, 0, ?, ?, ?, ?, ?) \
                 ON CONFLICT(doc_id, chunk_index, model) DO UPDATE SET \
                 dim=excluded.dim, vec=excluded.vec, chunk=excluded.chunk, \
                 embedded_at=excluded.embedded_at",
                rusqlite::params![cid, env.model, dim, blob, chunk, now],
            )
            .map_err(|e| e.to_string())?;
        }
        tx.commit().map_err(|e| e.to_string())?;
        embedded += pairs.len();
        progress(std::cmp::min((i + 1) * batch_size, total), total);
    }

    Ok(KnowledgeEmbedSummary {
        model: env.model,
        embedded,
        skipped: total - embedded,
        total,
    })
}

// --- memory corpus ---------------------

/// Cap on how many chunks one memory body contributes.
const MAX_MEMORY_CHUNKS: usize = 5;

/// Split a titled memory body into bounded embeddable chunks (`chunk_memory_body`).
pub fn chunk_memory_body(concept: &cairn_query::tools_km::Concept) -> Vec<String> {
    let body = concept.body.as_str();
    // re.split(r"\n(?=Why:|How to apply:)"): a part boundary before each
    // Why:/How to apply: line (the newline is consumed by the split).
    let lines: Vec<&str> = body.split('\n').collect();
    let mut parts: Vec<String> = Vec::new();
    let mut current: Vec<&str> = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        if i > 0 && (line.starts_with("Why:") || line.starts_with("How to apply:")) {
            parts.push(current.join("\n"));
            current.clear();
        }
        current.push(line);
    }
    parts.push(current.join("\n"));
    let mut parts: Vec<String> = parts.into_iter().filter(|p| !p.trim().is_empty()).collect();
    if parts.len() < 2 {
        parts = body
            .split("\n\n")
            .map(str::to_string)
            .filter(|p| !p.trim().is_empty())
            .collect();
    }
    if parts.is_empty() {
        parts = vec![body.to_string()];
    }
    let header = concept.title.clone().unwrap_or_default();
    let mut chunks: Vec<String> = Vec::new();
    for (i, part) in parts.iter().take(MAX_MEMORY_CHUNKS).enumerate() {
        let text = if i == 0 {
            format!("{header} {part}").trim().to_string()
        } else {
            part.trim().to_string()
        };
        if !text.is_empty() {
            chunks.push(text);
        }
    }
    if chunks.is_empty() && !header.is_empty() {
        chunks.push(header);
    }
    chunks
}

/// Embed selected memory concepts in one batch and replace their rows
/// (`embed_memory_concepts`).
pub fn embed_memory_concepts(
    conn: &Connection,
    bundle: &cairn_query::tools_km::Bundle,
    concept_ids: &[String],
) -> Result<usize, String> {
    let env = resolve_embed_env()?;
    let model = env.model.clone();
    let now = now_iso();
    let mut plan: Vec<(String, Vec<String>)> = Vec::new();
    for cid in concept_ids {
        let Ok(concept) = bundle.read_concept(cid) else {
            continue; // deleted/moved since being queued -- nothing to embed
        };
        let chunks = chunk_memory_body(&concept);
        if !chunks.is_empty() {
            plan.push((cid.clone(), chunks));
        }
    }
    if plan.is_empty() {
        return Ok(0);
    }
    let all_chunks: Vec<String> = plan
        .iter()
        .flat_map(|(_, chunks)| chunks.iter().cloned())
        .collect();
    let (blobs, _dim) = embed_texts(&env, &all_chunks)?;
    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    let mut offset = 0usize;
    let mut embedded = 0usize;
    for (cid, chunks) in &plan {
        tx.execute(
            "DELETE FROM memory_embeddings WHERE doc_id = ?1 AND model = ?2",
            rusqlite::params![cid, model],
        )
        .map_err(|e| e.to_string())?;
        for (idx, (chunk, blob)) in chunks
            .iter()
            .zip(&blobs[offset..offset + chunks.len()])
            .enumerate()
        {
            let dim = (blob.len() / 4) as i64;
            tx.execute(
                "INSERT INTO memory_embeddings \
                 (doc_id, chunk_index, model, dim, vec, chunk, embedded_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                rusqlite::params![cid, idx as i64, model, dim, blob, chunk, now],
            )
            .map_err(|e| e.to_string())?;
        }
        offset += chunks.len();
        embedded += 1;
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(embedded)
}

/// Backfill memory concepts missing embeddings under the current model.
pub fn embed_memory(
    conn: &Connection,
    bundle: &cairn_query::tools_km::Bundle,
    batch_size: usize,
) -> Result<KnowledgeEmbedSummary, String> {
    let env = resolve_embed_env()?;
    let model = env.model.clone();
    let cids = bundle.list_concepts(Some("memory/"));
    if cids.is_empty() {
        return Ok(KnowledgeEmbedSummary {
            model,
            embedded: 0,
            skipped: 0,
            total: 0,
        });
    }
    let mut existing: HashSet<String> = HashSet::new();
    {
        let mut stmt = conn
            .prepare(
                "SELECT doc_id FROM memory_embeddings \
                 WHERE model = ?1 AND chunk_index = 0",
            )
            .map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map([&model], |r| r.get::<_, String>(0))
            .map_err(|e| e.to_string())?;
        for row in mapped {
            existing.insert(row.map_err(|e| e.to_string())?);
        }
    }
    let to_embed: Vec<String> = cids
        .iter()
        .filter(|cid| !existing.contains(*cid))
        .cloned()
        .collect();
    let total = to_embed.len();
    let mut embedded = 0usize;
    for batch in to_embed.chunks(batch_size.max(1)) {
        embedded += embed_memory_concepts(conn, bundle, batch)?;
    }
    Ok(KnowledgeEmbedSummary {
        model,
        embedded,
        skipped: total - embedded,
        total,
    })
}

/// Delete memory embeddings whose concepts are gone, skipping unreadable ones.
pub fn reap_orphaned_memory_embeddings(
    conn: &Connection,
    bundle: &cairn_query::tools_km::Bundle,
) -> Result<usize, String> {
    let mut doc_ids: Vec<String> = {
        let mut stmt = conn
            .prepare("SELECT DISTINCT doc_id FROM memory_embeddings")
            .map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map([], |r| r.get::<_, String>(0))
            .map_err(|e| e.to_string())?;
        mapped
            .collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())?
    };
    doc_ids.sort();
    let mut orphaned: Vec<String> = Vec::new();
    for cid in &doc_ids {
        // Only a missing file reads as absent; unreadable concepts are kept.
        let Ok(path) = bundle.concept_file(cid) else {
            continue;
        };
        if !path.exists() {
            orphaned.push(cid.clone());
        }
    }
    if orphaned.is_empty() {
        return Ok(0);
    }
    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    let mut deleted = 0usize;
    for cid in &orphaned {
        deleted += tx
            .execute(
                "DELETE FROM memory_embeddings WHERE doc_id = ?1",
                rusqlite::params![cid],
            )
            .map_err(|e| e.to_string())?;
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(deleted)
}

// --- orphan reap -----------------------------------------------------------------

fn purge_embedding_rows(conn: &Connection, where_sql: &str) -> Result<usize, String> {
    let mut doomed: HashMap<String, Vec<i64>> = HashMap::new();
    let ann_on = tools_search::ann_backend_enabled();
    if ann_on {
        let mut stmt = conn
            .prepare(&format!(
                "SELECT model, rowid FROM embeddings WHERE {where_sql}"
            ))
            .map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
            .map_err(|e| e.to_string())?;
        for row in mapped {
            let (model, rowid) = row.map_err(|e| e.to_string())?;
            doomed.entry(model).or_default().push(rowid);
        }
    }

    let deleted = conn
        .execute(&format!("DELETE FROM embeddings WHERE {where_sql}"), [])
        .map_err(|e| e.to_string())?;
    if ann_on {
        for (model, rowids) in &doomed {
            let _ = vec0::delete_index_rows(conn, model, rowids);
        }
    }
    Ok(deleted)
}

/// Delete base and multi-vector embedding rows whose symbols no longer exist.
pub fn reap_orphaned_embeddings(conn: &Connection) -> Result<usize, String> {
    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    let reaped = purge_embedding_rows(&tx, "symbol_id NOT IN (SELECT id FROM symbols)")?;
    let reaped_mv = tx
        .execute(
            "DELETE FROM embeddings_mv WHERE symbol_id NOT IN (SELECT id FROM symbols)",
            [],
        )
        .map_err(|e| e.to_string())?;
    tx.commit().map_err(|e| e.to_string())?;
    Ok(reaped + reaped_mv)
}

// --- term_df -----------------------------------------------------------------

/// Rebuild deterministic per-corpus token statistics (fts5vocab path with a
/// scan fallback); returns the number of term rows written.
pub fn rebuild_term_df(conn: &Connection) -> Result<usize, String> {
    let n_symbols: i64 = conn
        .query_row("SELECT COUNT(*) FROM symbols", [], |r| r.get(0))
        .map_err(|e| e.to_string())?;
    match rebuild_term_df_vocab(conn, n_symbols) {
        Ok(Some(written)) if !(written == 0 && n_symbols > 0) => Ok(written),
        _ => rebuild_term_df_scan(conn, n_symbols),
    }
}

fn rebuild_term_df_vocab(
    conn: &Connection,
    n_symbols: i64,
) -> Result<Option<usize>, rusqlite::Error> {
    conn.execute_batch("BEGIN")?;
    let result = (|| -> rusqlite::Result<usize> {
        conn.execute_batch(
            "CREATE VIRTUAL TABLE IF NOT EXISTS temp.term_df_vocab
             USING fts5vocab(main, symbols_fts, row)",
        )?;
        conn.execute("DELETE FROM term_df", [])?;
        conn.execute(
            "INSERT INTO term_df (token, symbol_df, n_symbols)
             SELECT term, doc, ?1 FROM temp.term_df_vocab",
            [n_symbols],
        )
    })();
    let _ = conn.execute_batch("DROP TABLE IF EXISTS temp.term_df_vocab");
    match result {
        Ok(written) => {
            let _ = conn.execute_batch("COMMIT");
            Ok(Some(written))
        }
        Err(_) => {
            let _ = conn.execute_batch("ROLLBACK");
            Ok(None)
        }
    }
}

fn unicode61_tokens(text: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut cur = String::new();
    for ch in text.to_lowercase().chars() {
        if ch.is_alphanumeric() {
            cur.push(ch);
        } else if !cur.is_empty() {
            tokens.push(std::mem::take(&mut cur));
        }
    }
    if !cur.is_empty() {
        tokens.push(cur);
    }
    tokens
}

fn rebuild_term_df_scan(conn: &Connection, n_symbols: i64) -> Result<usize, String> {
    let mut stmt = conn
        .prepare("SELECT rowid, name, qualified_name, docstring FROM symbols")
        .map_err(|e| e.to_string())?;
    let mapped = stmt
        .query_map([], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, Option<String>>(2)?,
                r.get::<_, Option<String>>(3)?,
            ))
        })
        .map_err(|e| e.to_string())?;
    let mut df: BTreeMap<String, BTreeSet<i64>> = BTreeMap::new();
    for row in mapped {
        let (rowid, name, qname, doc) = row.map_err(|e| e.to_string())?;
        let text = [name, qname, doc]
            .into_iter()
            .flatten()
            .filter(|t| !t.is_empty())
            .collect::<Vec<_>>()
            .join(" ");
        for token in unicode61_tokens(&text) {
            df.entry(token).or_default().insert(rowid);
        }
    }

    let tx = conn.unchecked_transaction().map_err(|e| e.to_string())?;
    tx.execute("DELETE FROM term_df", [])
        .map_err(|e| e.to_string())?;
    {
        let mut insert = tx
            .prepare("INSERT INTO term_df (token, symbol_df, n_symbols) VALUES (?1, ?2, ?3)")
            .map_err(|e| e.to_string())?;
        for (token, ids) in &df {
            insert
                .execute(rusqlite::params![token, ids.len() as i64, n_symbols])
                .map_err(|e| e.to_string())?;
        }
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(df.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row() -> EmbedRow {
        EmbedRow {
            id: "s1".into(),
            name: "greet".into(),
            qualified_name: Some("Greeter.greet".into()),
            kind: "method".into(),
            docstring: Some("  Returns a greeting.  ".into()),
            line_start: Some(4),
            parameters: Some("(name)".into()),
            return_type: Some("str".into()),
            parent_scope: Some("Greeter".into()),
            imports_summary: Some("os, re".into()),
            body: Some("return f\"Hello, {name}!\"".into()),
            file_path: Some("src/greet.py".into()),
            repo: Some("demo".into()),
            existing_hash: None,
        }
    }

    #[test]
    fn chunk_variant_b_assembles_scope_header_signature_and_meta() {
        let sig = Some("def greet(self, name):");
        assert_eq!(
            chunk_for_symbol(&row(), sig, None, DEFAULT_MAX_TOKENS),
            "File: src/greet.py\n\
             Enclosing Scope: Greeter\n\
             Imports: os, re\n\
             method Greeter.greet\n\
             Signature: def greet(self, name):\n\
             Parameters: (name)\n\
             Return Type: str\n\
             Docstring: Returns a greeting."
        );
    }

    #[test]
    fn chunk_variant_a_keeps_first_signature_line_and_doc_only() {
        let chunk = chunk_for_symbol(
            &row(),
            Some("def greet(self, name):"),
            Some("A"),
            DEFAULT_MAX_TOKENS,
        );
        assert_eq!(
            chunk,
            "File: src/greet.py\n\
             Enclosing Scope: Greeter\n\
             Imports: os, re\n\
             method Greeter.greet\n\
             def greet(self, name):\n\
             Returns a greeting."
        );
    }

    #[test]
    fn chunk_drop_scope_variants_remove_extras_but_keep_the_file_line() {
        let sig = Some("def greet(self, name):");
        let b_no_scope = chunk_for_symbol(&row(), sig, Some("B_NO_SCOPE"), DEFAULT_MAX_TOKENS);
        assert!(b_no_scope.starts_with("File: src/greet.py\nmethod Greeter.greet"));
        assert!(!b_no_scope.contains("Enclosing Scope"));
        assert!(!b_no_scope.contains("Imports:"));
        let identities = chunk_for_symbol(&row(), sig, Some("B_IDENTITIES"), DEFAULT_MAX_TOKENS);
        for dropped in ["Enclosing Scope", "Imports:", "Parameters", "Return Type"] {
            assert!(
                !identities.contains(dropped),
                "B_IDENTITIES drops {dropped}"
            );
        }
        // The signature line and docstring stay (only the meta rows are gated).
        assert!(identities.contains("Signature: def greet(self, name):"));
        assert!(identities.contains("Docstring: Returns a greeting."));
    }

    #[test]
    fn chunk_c_trim_caps_the_body_prefix_at_half_the_budget() {
        let mut r = row();
        r.body = Some("x".repeat(1200));
        let chunk = chunk_for_symbol(&r, None, Some("C_TRIM"), DEFAULT_MAX_TOKENS);
        let body_at = chunk.find("Body:\n").expect("body section");
        assert_eq!(
            chunk[body_at..].len(),
            "Body:\n".len() + DEFAULT_MAX_TOKENS * 4 / 2
        );
    }

    #[test]
    fn chunk_truncation_counts_chars_not_bytes() {
        let mut r = row();
        r.docstring = Some("é".repeat(DEFAULT_MAX_TOKENS * 4 + 10));
        let chunk = chunk_for_symbol(&r, None, Some("A"), DEFAULT_MAX_TOKENS);
        assert_eq!(chunk.chars().count(), DEFAULT_MAX_TOKENS * 4);
    }

    #[test]
    fn chunk_falls_back_to_qualified_name_then_kind() {
        let mut r = row();
        r.docstring = None;
        r.body = None;
        // B_IDENTITIES drops the meta rows; the scope header stays while the
        // file path exists.
        let chunk = chunk_for_symbol(&r, Some(""), Some("B_IDENTITIES"), DEFAULT_MAX_TOKENS);
        assert_eq!(chunk, "File: src/greet.py\nmethod Greeter.greet");
        r.file_path = None;
        r.qualified_name = None;
        r.kind = String::new();
        let chunk = chunk_for_symbol(&r, Some(""), Some("B_IDENTITIES"), DEFAULT_MAX_TOKENS);
        assert_eq!(chunk, "greet");
    }

    #[test]
    fn mv_texts_split_name_and_docstring_kinds() {
        assert_eq!(
            name_text_for_symbol(&row(), Some("def greet(self, name):\n    pass")),
            "method Greeter.greet\ndef greet(self, name):"
        );
        assert_eq!(docstring_text_for_symbol(&row()), "Returns a greeting.");
        assert!(matches!(
            mv_text_for_kind(&row(), "body", None),
            Err(msg) if msg.contains("unknown vector_kind")
        ));
    }

    #[test]
    fn unicode61_tokens_mirror_the_approximation() {
        assert_eq!(
            unicode61_tokens("Foo_bar bazQux 42 é-name"),
            vec!["foo", "bar", "bazqux", "42", "é", "name"]
        );
        assert!(unicode61_tokens("   ").is_empty());
    }

    #[test]
    fn rebuild_term_df_prefers_the_vocab_path_and_matches_the_scan() {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "cairn-embed-term-df-{}-{nanos}",
            std::process::id()
        ));
        std::fs::create_dir_all(&dir).expect("temp dir");
        let path = dir.join("t.kg");
        let conn = cairn_store::conn::open_writable(&path).expect("open");
        cairn_store::schema::apply_schema(&conn).expect("schema");
        conn.execute_batch(
            "INSERT INTO repos (id, name, path, language) VALUES ('r', 'r', '.', 'python');
             INSERT INTO files (id, repo_id, path, language) VALUES
               ('f1', 'r', 'a.py', 'python');
             INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES
               ('s1', 'f1', 'Alpha', 'demo.Alpha', 'class'),
               ('s2', 'f1', 'beta', NULL, 'function');",
        )
        .expect("symbols");

        let vocab_written = rebuild_term_df(&conn).expect("vocab rebuild");
        let vocab_rows = rows(&conn);
        assert!(vocab_written > 0);

        // The scan fallback must reproduce the vocab path's rows exactly.
        let scan_written = rebuild_term_df_scan(&conn, 2).expect("scan rebuild");
        assert_eq!(scan_written, vocab_written);
        assert_eq!(rows(&conn), vocab_rows);

        assert_eq!(
            vocab_rows,
            vec![
                ("alpha".to_string(), "1".to_string(), "2".to_string()),
                ("beta".to_string(), "1".to_string(), "2".to_string()),
                ("demo".to_string(), "1".to_string(), "2".to_string()),
            ]
        );
        drop(conn);
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_dir(&dir);
    }

    fn rows(conn: &Connection) -> Vec<(String, String, String)> {
        let mut stmt = conn
            .prepare("SELECT token, symbol_df, n_symbols FROM term_df ORDER BY token")
            .expect("select");
        let mapped = stmt
            .query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, i64>(1)?.to_string(),
                    r.get::<_, i64>(2)?.to_string(),
                ))
            })
            .expect("query");
        mapped.map(|r| r.expect("row")).collect()
    }

    fn mem_concept(cid: &str, title: &str, body: &str) -> cairn_query::tools_km::Concept {
        cairn_query::tools_km::Concept {
            type_: "Tribal-decision".into(),
            title: Some(title.into()),
            concept_id: cid.into(),
            body: body.into(),
            ..Default::default()
        }
    }

    #[test]
    fn chunk_memory_body_splits_headers_and_caps_chunks() {
        let c = mem_concept(
            "memory/tribal/x",
            "Titled",
            "Intro\nWhy: the reason\nHow to apply: the rule\n",
        );
        assert_eq!(
            chunk_memory_body(&c),
            vec!["Titled Intro", "Why: the reason", "How to apply: the rule"]
        );
        let long = "p0\n".to_string() + &["Why: p\n"; 8].concat();
        assert_eq!(chunk_memory_body(&mem_concept("m", "H", &long)).len(), 5);
        assert_eq!(
            chunk_memory_body(&mem_concept("m", "Only Title", "")),
            vec!["Only Title"]
        );
        assert!(chunk_memory_body(&mem_concept("m", "", "")).is_empty());
    }

    #[test]
    fn embed_memory_backfills_and_reaps_orphans() {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir =
            std::env::temp_dir().join(format!("cairn-mem-embed-{}-{nanos}", std::process::id()));
        let root = dir.join(".knowledge");
        std::fs::create_dir_all(root.join("memory/tribal")).expect("bundle");
        let bundle = cairn_query::tools_km::Bundle::new(&root);
        let mut concept = mem_concept(
            "memory/tribal/live",
            "live memory",
            "Body with a Why: line\nWhy: reason\n",
        );
        bundle.write_concept(&mut concept).expect("write");
        let conn = cairn_store::conn::open_writable(&dir.join("t.kg")).expect("open");
        cairn_store::schema::apply_schema(&conn).expect("schema");
        conn.execute(
            "INSERT INTO memory_embeddings \
             (doc_id, chunk_index, model, dim, vec, chunk, embedded_at) \
             VALUES ('memory/tribal/gone', 0, 'hash-256-v1', 1, X'3F800000', 'x', 't')",
            [],
        )
        .expect("orphan");

        let reaped = reap_orphaned_memory_embeddings(&conn, &bundle).expect("reap");
        assert_eq!(reaped, 1, "the gone concept's row is reaped");
        let reaped_again = reap_orphaned_memory_embeddings(&conn, &bundle).expect("reap");
        assert_eq!(reaped_again, 0);

        let summary = embed_memory(&conn, &bundle, 64).expect("embed");
        assert_eq!(summary.total, 1);
        assert_eq!(summary.embedded, 1);
        assert_eq!(summary.skipped, 0);
        let model = summary.model;
        let (chunks, c0): (i64, String) = conn
            .query_row(
                "SELECT COUNT(*), MAX(chunk) FROM memory_embeddings \
                 WHERE doc_id = 'memory/tribal/live' AND model = ?1",
                [&model],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .expect("rows");
        assert_eq!(chunks, 2, "one row per Why-split chunk");
        assert_eq!(c0, "live memory Body with a Why: line");

        let again = embed_memory(&conn, &bundle, 64).expect("embed again");
        assert_eq!(again.total, 0, "already-embedded concepts are skipped");
        drop(conn);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
