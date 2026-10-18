//! Per-model vec0 table resolution and ANN query (the `ann_index` read side).

use rusqlite::Connection;

/// Embedding source table backing a per-model vec0 index.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VecSource {
    /// Base `embeddings` table (prefix `vec_`).
    Embeddings,
    /// Multivector `embeddings_mv` table (prefix `vecmv_`).
    EmbeddingsMv,
}

impl VecSource {
    fn prefix(self) -> &'static str {
        match self {
            VecSource::Embeddings => "vec_",
            VecSource::EmbeddingsMv => "vecmv_",
        }
    }
}

/// Sanitize a model name into the per-model vec0 table identifier
/// (every char outside `[a-zA-Z0-9_]` becomes `_`, then the source prefix).
pub fn table_name(model: &str, source: VecSource) -> String {
    let safe: String = model
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    format!("{}{safe}", source.prefix())
}

/// Whether the vec0 table for `model` (and `source`) exists.
pub fn index_exists(conn: &Connection, model: &str, source: VecSource) -> rusqlite::Result<bool> {
    let table = table_name(model, source);
    let found: Option<i64> = conn
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1",
            [&table],
            |r| r.get(0),
        )
        .map(Some)
        .or_else(|e| match e {
            rusqlite::Error::QueryReturnedNoRows => Ok(None),
            other => Err(other),
        })?;
    Ok(found.is_some())
}

/// ANN cosine search against the vec0 table for `model` over `source`;
/// `Ok(None)` means no index is built and the caller falls back to the
/// brute-force scan.
pub fn ann_query(
    conn: &Connection,
    model: &str,
    q_blob: &[u8],
    k: i64,
    source: VecSource,
) -> rusqlite::Result<Option<Vec<(String, f64)>>> {
    let table = table_name(model, source);
    if !index_exists(conn, model, source)? {
        return Ok(None);
    }
    let sql = format!(
        "SELECT e.symbol_id AS symbol_id, v.distance AS distance \
         FROM {table} v JOIN {} e ON e.rowid = v.rowid \
         WHERE v.embedding MATCH ?1 AND v.k = ?2 \
         ORDER BY v.distance",
        source_table_ident(source)
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(rusqlite::params![q_blob, k], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, f64>(1)?))
    })?;
    let mut hits = Vec::new();
    for row in rows {
        let (symbol_id, distance) = row?;
        hits.push((symbol_id, 1.0 - distance));
    }
    Ok(Some(hits))
}

fn source_table_ident(source: VecSource) -> &'static str {
    match source {
        VecSource::Embeddings => "embeddings",
        VecSource::EmbeddingsMv => "embeddings_mv",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitizes_model_names_like_the_python_sanitizer() {
        assert_eq!(
            table_name("all-MiniLM-L6-v2", VecSource::Embeddings),
            "vec_all_MiniLM_L6_v2"
        );
        assert_eq!(
            table_name("BAAI/bge-m3", VecSource::Embeddings),
            "vec_BAAI_bge_m3"
        );
        assert_eq!(
            table_name("hash:abcd12", VecSource::Embeddings),
            "vec_hash_abcd12"
        );
        assert_eq!(table_name("a  b", VecSource::Embeddings), "vec_a__b");
        assert_eq!(
            table_name("server:127.0.0.1:8000/bge-m3", VecSource::EmbeddingsMv),
            "vecmv_server_127_0_0_1_8000_bge_m3"
        );
    }

    fn temp_conn() -> (Connection, std::path::PathBuf) {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir =
            std::env::temp_dir().join(format!("cairn-store-vec0-{}-{nanos}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("temp dir");
        let path = dir.join("t.kg");
        let conn = Connection::open(&path).expect("open");
        (conn, path)
    }

    fn f32_blob(vals: &[f32]) -> Vec<u8> {
        vals.iter().flat_map(|v| v.to_le_bytes()).collect()
    }

    #[test]
    fn ann_query_ranks_by_cosine_distance_and_maps_to_similarity() {
        let (conn, path) = temp_conn();
        conn.execute_batch("CREATE TABLE embeddings (symbol_id TEXT, model TEXT, vec BLOB);")
            .expect("source table");
        for (rowid, symbol, vec) in [
            (1i64, "near", f32_blob(&[1.0, 0.0])),
            (2, "far", f32_blob(&[-1.0, 0.0])),
        ] {
            conn.execute(
                "INSERT INTO embeddings (rowid, symbol_id, model, vec) VALUES (?1, ?2, 'm', ?3)",
                rusqlite::params![rowid, symbol, vec],
            )
            .expect("insert");
        }
        conn.execute_batch(
            "CREATE VIRTUAL TABLE vec_m USING vec0(embedding float[2] distance_metric=cosine);
             INSERT INTO vec_m(rowid, embedding) SELECT rowid, vec FROM embeddings WHERE model='m';",
        )
        .expect("vec0 build");

        assert!(index_exists(&conn, "m", VecSource::Embeddings).expect("exists"));
        assert!(!index_exists(&conn, "missing", VecSource::Embeddings).expect("exists"));

        let hits = ann_query(&conn, "m", &f32_blob(&[1.0, 0.0]), 2, VecSource::Embeddings)
            .expect("query")
            .expect("index present");
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].0, "near");
        assert!(hits[0].1 > 0.999, "near similarity {hits:?}");
        assert!((hits[1].1 + 1.0).abs() < 1e-6, "far similarity {hits:?}");

        let hits = ann_query(&conn, "m", &f32_blob(&[1.0, 0.0]), 1, VecSource::Embeddings)
            .expect("query")
            .expect("index present");
        assert_eq!(hits.len(), 1);

        let none = ann_query(
            &conn,
            "missing",
            &f32_blob(&[1.0, 0.0]),
            1,
            VecSource::Embeddings,
        )
        .expect("query");
        assert!(none.is_none());

        drop(conn);
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_dir(path.parent().expect("parent"));
    }
}
