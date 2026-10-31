//! Settings state, config.json writes, cross-site guard, and the embedding
//! status/probe seams the banner and embeddings view read.

use std::sync::OnceLock;

use serde_json::{Value, json};

use cairn_query::tools_search;

/// The CAIRN_EMBED_* knobs the settings page reads and writes in config.json.
pub const SETTINGS_BACKENDS: [&str; 5] = ["local", "server", "omlx", "ollama", "hash"];
pub const SETTINGS_KEYS: [&str; 7] = [
    "CAIRN_EMBED_BACKEND",
    "CAIRN_EMBED_SERVER_MODEL",
    "CAIRN_EMBED_API_KEY",
    "CAIRN_EMBED_TIMEOUT",
    "CAIRN_EMBED_SERVER_BATCH",
    "CAIRN_EMBED_MODEL_STAMP",
    "CAIRN_EMBED_BASE_URL",
];

/// Keys whose file values must parse as numbers (validated at save time).
pub const SETTINGS_NUMERIC: [(&str, &str); 2] = [
    ("CAIRN_EMBED_TIMEOUT", "a number of seconds"),
    ("CAIRN_EMBED_SERVER_BATCH", "a whole batch size"),
];

/// Server-family backends (omlx/ollama are preset aliases of `server`).
pub fn is_server_family(name: &str) -> bool {
    matches!(name, "server" | "omlx" | "ollama")
}

/// Raw config-file value stringified the way `str(paths.get_config_value)` does.
fn file_value(key: &str) -> String {
    tools_search::cairn_home()
        .and_then(|home| file_value_at(&home, key))
        .unwrap_or_default()
}

fn file_value_at(home: &std::path::Path, key: &str) -> Option<String> {
    let raw = std::fs::read_to_string(home.join("config.json")).ok()?;
    let parsed: Value = serde_json::from_str(&raw).ok()?;
    match parsed.get(key) {
        Some(Value::String(s)) => Some(s.clone()),
        Some(Value::Number(n)) => Some(n.to_string()),
        Some(Value::Bool(b)) => Some(b.to_string()),
        _ => Some(String::new()),
    }
}

/// File/effective settings state without reflecting the API key.
pub fn settings_context(store_key: &str) -> Value {
    settings_context_full(store_key, false, "", None)
}

pub fn settings_context_full(
    store_key: &str,
    saved: bool,
    error: &str,
    parity: Option<Value>,
) -> Value {
    let mut cfg = serde_json::Map::new();
    for key in SETTINGS_KEYS {
        let env_value = std::env::var(key)
            .map(|v| v.trim().to_string())
            .unwrap_or_default();
        let file_value = file_value(key).trim().to_string();
        let effective = if env_value.is_empty() {
            file_value.clone()
        } else {
            env_value.clone()
        };
        let prefill = if key == "CAIRN_EMBED_API_KEY" {
            String::new()
        } else if file_value.is_empty() {
            env_value.clone()
        } else {
            file_value
        };
        cfg.insert(
            key.to_string(),
            json!({
                "prefill": prefill,
                "effective": effective,
                "pinned": !env_value.is_empty(),
            }),
        );
    }
    let backend_value = cfg["CAIRN_EMBED_BACKEND"]["prefill"]
        .as_str()
        .filter(|v| !v.is_empty())
        .unwrap_or("local");
    json!({
        "cfg": cfg,
        "backends": SETTINGS_BACKENDS,
        "backend_value": backend_value,
        "saved": saved,
        "error": error,
        "parity": parity,
        "store_key": store_key,
    })
}

/// Merge `values` into CONFIG_FILE atomically (tmp file + rename); Err carries
/// the parse or write failure reason for display.
pub fn set_config_values(values: &[(String, String)]) -> Result<(), String> {
    let result = tools_search::cairn_home()
        .ok_or_else(|| "no CAIRN_HOME or HOME to locate config.json".to_string())
        .and_then(|home| set_config_values_at(&home, values));
    if let Err(reason) = &result {
        eprintln!("cairn: could not save config.json: {reason}");
    }
    result
}

fn set_config_values_at(home: &std::path::Path, values: &[(String, String)]) -> Result<(), String> {
    let config = home.join("config.json");
    let existing = match std::fs::read_to_string(&config) {
        Ok(raw) => match serde_json::from_str::<Value>(&raw) {
            Ok(parsed) => parsed,
            Err(err) => return Err(format!("not valid JSON ({err})")),
        },
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            Value::Object(serde_json::Map::new())
        }
        Err(err) => return Err(format!("unreadable ({err})")),
    };
    let Value::Object(mut merged) = existing else {
        return Err("top-level value is not a JSON object".into());
    };
    for (key, value) in values {
        merged.insert(key.clone(), Value::from(value.clone()));
    }
    if let Err(err) = std::fs::create_dir_all(home) {
        return Err(format!("could not create the config directory ({err})"));
    }
    let tmp = home.join(format!(".config-{}.tmp", std::process::id()));
    let Ok(mut body) = serde_json::to_string_pretty(&Value::Object(merged)) else {
        return Err("could not serialize the merged config".into());
    };
    body.push('\n');
    if let Err(err) = std::fs::write(&tmp, body) {
        return Err(format!("could not write the staged config ({err})"));
    }
    match std::fs::rename(&tmp, &config) {
        Ok(()) => Ok(()),
        Err(err) => {
            let _ = std::fs::remove_file(&tmp);
            Err(format!("could not replace the config ({err})"))
        }
    }
}

/// Host header names the loopback bind (localhost, 127/8, ::1)?
fn host_is_loopbind(host: &str) -> bool {
    let mut name = host.trim();
    if name.starts_with('[') {
        name = name.trim_start_matches('[').split(']').next().unwrap_or("");
    } else if name.matches(':').count() == 1 {
        name = name.split(':').next().unwrap_or("");
    }
    if name.eq_ignore_ascii_case("localhost") {
        return true;
    }
    name.parse::<std::net::IpAddr>()
        .map(|ip| ip.is_loopback())
        .unwrap_or(false)
}

/// Reject POSTs whose Host is not the loopback bind or that name another origin.
pub fn cross_site_rejected(
    host: Option<&str>,
    sec_fetch_site: Option<&str>,
    origin: Option<&str>,
    referer: Option<&str>,
) -> bool {
    let host = host.unwrap_or("").trim();
    let site = sec_fetch_site.unwrap_or("").trim().to_lowercase();
    let trusted = if !host_is_loopbind(host) {
        false
    } else if !site.is_empty() {
        // "same-site" is not trusted: another localhost port counts as
        // same-site and is exactly the cross-site write shape.
        site == "same-origin" || site == "none"
    } else {
        let origin = origin.unwrap_or("").trim();
        let origin = if origin.is_empty() {
            referer.unwrap_or("").trim()
        } else {
            origin
        };
        origin.is_empty() || url_netloc(origin) == host
    };
    !trusted
}

/// The netloc of an Origin/Referer URL (scheme://netloc/...).
fn url_netloc(url: &str) -> String {
    let rest = url.split_once("://").map(|(_, rest)| rest).unwrap_or(url);
    rest.split(['/', '?', '#'])
        .next()
        .unwrap_or(rest)
        .to_string()
}

/// One uncached server probe verdict per process (None: not a server backend).
pub fn server_probe_once() -> Option<bool> {
    static PROBED: OnceLock<Option<bool>> = OnceLock::new();
    *PROBED.get_or_init(|| {
        let name = tools_search::backend_name();
        if !is_server_family(&name) {
            return None;
        }
        Some(tools_search::resolve_backend(&name).0 != tools_search::EffectiveBackend::Unavailable)
    })
}

/// The degradation banner text for this request ("" when healthy).
pub fn embed_banner() -> String {
    match server_probe_once() {
        Some(false) => ("Embedding backend degraded -- rung 3 (server_down): embedding server \
             unreachable; dense leg off, queries ride the bm25/fts5 hybrid; check \
             server health and CAIRN_EMBED_BASE_URL (cairn doctor probes it)")
            .to_string(),
        _ => String::new(),
    }
}

/// Per-corpus embedding rows, degrading unreadable state to unknown.
pub fn embeddings_rows(conn: &rusqlite::Connection) -> Vec<Value> {
    let corpora = [
        ("code", "embeddings"),
        ("knowledge", "knowledge_embeddings"),
        ("memory", "memory_embeddings"),
    ];
    let stamp = super::data::current_model_stamp();
    corpora
        .iter()
        .map(|(corpus, table)| {
            let count = stamp.as_deref().and_then(|model| {
                let sql = match *corpus {
                    "knowledge" => "SELECT COUNT(*) FROM knowledge_embeddings WHERE model = ?",
                    "memory" => {
                        "SELECT COUNT(*) FROM memory_embeddings WHERE model = ? AND chunk_index = 0"
                    }
                    _ => "SELECT COUNT(*) FROM embeddings WHERE model = ?",
                };
                rusqlite::Connection::query_row(conn, sql, [model], |row| row.get::<_, i64>(0)).ok()
            });
            let last = stamp.as_deref().and_then(|model| {
                rusqlite::Connection::query_row(
                    conn,
                    &format!("SELECT MAX(embedded_at) FROM {table} WHERE model = ?"),
                    [model],
                    |row| row.get::<_, Option<String>>(0),
                )
                .ok()
                .flatten()
            });
            json!({
                "corpus": corpus,
                "model": stamp.clone().map(Value::from).unwrap_or(Value::Null),
                "count": count.map(Value::from).unwrap_or(Value::Null),
                "last": last.map(Value::from).unwrap_or(Value::Null),
            })
        })
        .collect()
}

/// Sample stored chunks under `stamp` and parity-check them against the
/// server (embed_ladder.check_parity port; embed_fn is always the server).
pub fn check_parity(conn: &rusqlite::Connection, stamp: &str) -> Value {
    let sampled = 16usize;
    let rows = super::data::query_rows(
        conn,
        "SELECT chunk, vec FROM embeddings WHERE model = ? ORDER BY rowid LIMIT ?",
        &[&stamp, &(sampled as i64)],
    );
    if rows.is_empty() {
        return json!({"passed": true, "mean": "\u{2014}", "sampled": 0, "reason": "vacuous_no_stored_rows"});
    }
    let texts: Vec<String> = rows
        .iter()
        .map(|row| {
            row.get("chunk")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string()
        })
        .collect();
    let embedded = (|| {
        let name = tools_search::backend_name();
        let base = tools_search::server_base_url(&name)?;
        let model = tools_search::server_model();
        let api_key = tools_search::config_value("CAIRN_EMBED_API_KEY");
        tools_search::server_embed(&texts, &base, &model, api_key.as_deref())
    })();
    let blobs = match embedded {
        Ok((blobs, _dim)) => blobs,
        Err(err) => {
            return json!({
                "passed": false, "mean": "\u{2014}", "sampled": null,
                "reason": format!("EmbeddingError: {err}"),
            });
        }
    };
    if blobs.len() != rows.len() {
        return json!({
            "passed": false, "mean": "\u{2014}", "sampled": rows.len(),
            "reason": format!("embed_count_mismatch texts={} blobs={}", rows.len(), blobs.len()),
        });
    }
    let mut stored: Vec<Vec<f32>> = Vec::with_capacity(rows.len());
    for row in &rows {
        let Some(hex) = row.get("vec").and_then(Value::as_str) else {
            return json!({
                "passed": false, "mean": "\u{2014}", "sampled": rows.len(),
                "reason": "vec_decode_failed",
            });
        };
        stored.push(hex_to_f32s(hex));
    }
    let served: Vec<Vec<f32>> = blobs
        .iter()
        .map(|blob| crate::parity::decode_f32(blob))
        .collect();
    let mean = match crate::parity::mean_cosine(&stored, &served) {
        Ok(mean) => mean,
        Err(reason) => {
            return json!({
                "passed": false, "mean": "\u{2014}", "sampled": rows.len(),
                "reason": reason,
            });
        }
    };
    // Parity gate 0.98 — the shared constant (embed_ladder.PARITY_GATE).
    if mean >= 0.98 {
        json!({"passed": true, "mean": format!("{mean:.4}"), "sampled": rows.len(), "reason": "parity_ok"})
    } else {
        json!({
            "passed": false, "mean": format!("{mean:.4}"), "sampled": rows.len(),
            "reason": format!("mean_cosine {mean:.4} below gate 0.98"),
        })
    }
}

/// BLOB vecs reach rusqlite as hex text through the shared row reader.
fn hex_to_f32s(hex: &str) -> Vec<f32> {
    let bytes = (0..hex.len() / 2)
        .filter_map(|i| u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).ok())
        .collect::<Vec<u8>>();
    crate::parity::decode_f32(&bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn api_key_is_never_prefilled_but_effective_is_kept() {
        let ctx = settings_context("");
        assert_eq!(ctx["cfg"]["CAIRN_EMBED_API_KEY"]["prefill"], "");
        // backend_value is the prefill when set, else the local default.
        let prefill = ctx["cfg"]["CAIRN_EMBED_BACKEND"]["prefill"]
            .as_str()
            .unwrap_or("");
        let expected = if prefill.is_empty() { "local" } else { prefill };
        assert_eq!(ctx["backend_value"], expected);
        assert_eq!(ctx["saved"], false);
        assert_eq!(ctx["error"], "");
    }

    #[test]
    fn cross_site_guard_verdicts() {
        // Same-origin GET-equivalent POST passes.
        assert!(!cross_site_rejected(
            Some("127.0.0.1:8765"),
            Some("same-origin"),
            None,
            None
        ));
        assert!(!cross_site_rejected(
            Some("[::1]:8765"),
            Some("none"),
            None,
            None
        ));
        assert!(!cross_site_rejected(
            Some("localhost:8765"),
            None,
            None,
            None
        ));
        // Another localhost port (same-site) is exactly the attack shape.
        assert!(cross_site_rejected(
            Some("127.0.0.1:8765"),
            Some("same-site"),
            None,
            None
        ));
        // DNS rebinding: Host not the loopback bind.
        assert!(cross_site_rejected(
            Some("evil.example:8765"),
            Some("same-origin"),
            None,
            None
        ));
        // Missing Sec-Fetch-Site falls back to Origin/Referer comparison.
        assert!(!cross_site_rejected(
            Some("127.0.0.1:8765"),
            None,
            Some("http://127.0.0.1:8765/settings"),
            None
        ));
        assert!(cross_site_rejected(
            Some("127.0.0.1:8765"),
            None,
            Some("http://127.0.0.1:9999/"),
            None
        ));
        assert!(cross_site_rejected(
            Some("127.0.0.1:8765"),
            None,
            Some("http://evil.io/"),
            None
        ));
    }

    #[test]
    fn config_writes_merge_and_survive_a_round_trip() {
        let home = std::env::temp_dir().join(format!("cairn-cfg-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home);
        std::fs::create_dir_all(&home).unwrap();
        std::fs::write(
            home.join("config.json"),
            r#"{"CAIRN_EMBED_BACKEND": "server"}"#,
        )
        .unwrap();
        let ok = set_config_values_at(&home, &[("CAIRN_EMBED_SERVER_MODEL".into(), "m1".into())]);
        assert!(ok.is_ok(), "{ok:?}");
        let saved: Value =
            serde_json::from_str(&std::fs::read_to_string(home.join("config.json")).unwrap())
                .unwrap();
        assert_eq!(saved["CAIRN_EMBED_BACKEND"], "server");
        assert_eq!(saved["CAIRN_EMBED_SERVER_MODEL"], "m1");
        assert_eq!(
            file_value_at(&home, "CAIRN_EMBED_SERVER_MODEL").as_deref(),
            Some("m1")
        );
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn config_writes_refuse_to_replace_an_unparseable_config() {
        let home = std::env::temp_dir().join(format!("cairn-cfg-bad-{}", std::process::id()));
        for (seed, want) in [
            ("{not json", "not valid JSON"),
            ("[1, 2]", "not a JSON object"),
        ] {
            let _ = std::fs::remove_dir_all(&home);
            std::fs::create_dir_all(&home).unwrap();
            std::fs::write(home.join("config.json"), seed).unwrap();
            let result =
                set_config_values_at(&home, &[("CAIRN_EMBED_SERVER_MODEL".into(), "m1".into())]);
            assert!(result.is_err(), "{seed}");
            assert!(result.unwrap_err().contains(want), "{seed}");
            assert_eq!(
                std::fs::read_to_string(home.join("config.json")).unwrap(),
                seed
            );
        }
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn embed_banner_stays_empty_without_a_server_backend() {
        // Default env in tests: no CAIRN_EMBED_BACKEND -> local -> "".
        assert_eq!(embed_banner(), "");
    }
}
