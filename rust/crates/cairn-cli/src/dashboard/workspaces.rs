//! Store enumeration + probes for the workspaces overview (workspaces.py port).

use std::path::Path;

use rusqlite::Connection;
use serde_json::{Value, json};

/// Every state a store can be presented in; `unreadable` comes from the probe.
pub const STORE_STATES: [&str; 4] = ["populated", "empty", "missing", "unreadable"];

/// Layout constant mirroring paths.StorePaths (db = `<home>/<key>/.kg`).
const DB_FILENAME: &str = ".kg";

/// Cap on probe DB opens; past it rows degrade visibly (`counts_capped`).
pub const PROBE_MAX_OPENS: usize = 100;

/// Registry/directory store union with filesystem-only classification.
pub fn enumerate_stores(cairn_home: &Path) -> Vec<Value> {
    if !cairn_home.is_dir() {
        return Vec::new();
    }
    let mut registered = registry_under(cairn_home);
    registered.sort();

    let mut on_disk: Vec<String> = Vec::new();
    let entries = match std::fs::read_dir(cairn_home) {
        Ok(read) => read,
        Err(_) => return Vec::new(),
    };
    for entry in entries.filter_map(|e| e.ok()) {
        let name = entry.file_name().to_string_lossy().into_owned();
        if entry.path().is_dir() && is_store_key(&name) {
            on_disk.push(name);
        }
    }

    let mut keys: Vec<String> = registered.iter().map(|(k, _)| k.clone()).collect();
    keys.extend(on_disk.iter().cloned());
    keys.sort();
    keys.dedup();

    let mut rows: Vec<(String, Option<String>, String)> = Vec::new();
    for key in keys {
        let store_dir = cairn_home.join(&key);
        let state = if !on_disk.contains(&key) {
            "missing"
        } else if store_dir.join(DB_FILENAME).is_file() {
            "populated"
        } else {
            "empty"
        };
        let path = registered
            .iter()
            .find(|(k, _)| *k == key)
            .and_then(|(_, p)| p.clone());
        rows.push((key, path, state.to_string()));
    }
    // populated first, then key order.
    rows.sort_by(|a, b| {
        (a.2 != "populated")
            .cmp(&(b.2 != "populated"))
            .then(a.0.cmp(&b.0))
    });
    rows.into_iter()
        .map(|(key, path, state)| {
            json!({
                "key": key,
                "path": path.map(Value::from).unwrap_or(Value::Null),
                "state": state,
            })
        })
        .collect()
}

/// The `{workspace: key}` registry under the supplied home; corrupt input is empty.
fn registry_under(cairn_home: &Path) -> Vec<(String, Option<String>)> {
    let Ok(text) = std::fs::read_to_string(cairn_home.join("workspaces.json")) else {
        return Vec::new();
    };
    let Ok(parsed) = serde_json::from_str::<Value>(&text) else {
        return Vec::new();
    };
    parsed
        .as_object()
        .map(|map| {
            map.iter()
                .filter_map(|(ws, key)| {
                    let key = key.as_str()?;
                    Some((key.to_string(), Some(ws.clone())))
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Store dirs under CAIRN_HOME are named by the 16-hex sha256 prefix.
fn is_store_key(name: &str) -> bool {
    name.len() == 16
        && name
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

fn stat_kg(cairn_home: &Path, key: &str) -> (Option<u64>, Option<i64>) {
    let meta = match std::fs::metadata(cairn_home.join(key).join(DB_FILENAME)) {
        Ok(meta) => meta,
        Err(_) => return (None, None),
    };
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs() as i64);
    (Some(meta.len()), mtime)
}

/// One read-only tool-call count: zero valid, None unreadable; a store
/// predating tool_metrics reads as zero (the Python "no such table" branch).
fn count_tool_calls(kg_path: &Path) -> Option<i64> {
    let conn = open_read_only(kg_path)?;
    match conn.query_row("SELECT COUNT(*) FROM tool_metrics", [], |row| {
        row.get::<_, i64>(0)
    }) {
        Ok(count) => Some(count),
        Err(err) if err.to_string().contains("no such table") => Some(0),
        Err(_) => None,
    }
}

fn open_read_only(path: &Path) -> Option<Connection> {
    let flags = rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY;
    Connection::open_with_flags(path, flags).ok()
}

fn probe(cairn_home: &Path, entry: &Value, count_allowed: bool) -> Value {
    let mut row = entry.clone();
    let (size, mtime) = stat_kg(cairn_home, row["key"].as_str().unwrap_or(""));
    row["size_bytes"] = size.map(Value::from).unwrap_or(Value::Null);
    row["last_modified"] = mtime.map(Value::from).unwrap_or(Value::Null);
    if row["state"].as_str() != Some("populated") {
        row["call_count"] = Value::Null;
        row["counts_capped"] = Value::from(false);
        return row;
    }
    if !count_allowed {
        row["call_count"] = Value::Null;
        row["counts_capped"] = Value::from(true);
        return row;
    }
    let call_count = count_tool_calls(
        &cairn_home
            .join(row["key"].as_str().unwrap_or(""))
            .join(DB_FILENAME),
    );
    if call_count.is_none() {
        row["state"] = Value::from("unreadable");
    }
    row["call_count"] = call_count.map(Value::from).unwrap_or(Value::Null);
    row["counts_capped"] = Value::from(false);
    row
}

/// Probe rows in order while bounding every attempted DB open.
pub fn probe_stores(cairn_home: &Path, entries: &[Value], max_opens: usize) -> Vec<Value> {
    let mut opens_used = 0usize;
    entries
        .iter()
        .map(|entry| {
            let count_allowed = opens_used < max_opens;
            let row = probe(cairn_home, entry, count_allowed);
            if count_allowed && entry["state"].as_str() == Some("populated") {
                opens_used += 1;
            }
            row
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_store(home: &Path, key: &str, with_db: bool) {
        std::fs::create_dir_all(home.join(key)).unwrap();
        if with_db {
            std::fs::write(home.join(key).join(".kg"), b"not-a-db").unwrap();
        }
    }

    #[test]
    fn enumeration_classifies_registered_and_disk_stores() {
        let home = std::env::temp_dir().join(format!("cairn-ws-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home);
        write_store(&home, "aaaaaaaaaaaaaaaa", true);
        write_store(&home, "cccccccccccccccc", false);
        std::fs::write(
            home.join("workspaces.json"),
            r#"{"/work/yarl": "aaaaaaaaaaaaaaaa", "/work/x": "bbbbbbbbbbbbbbbb"}"#,
        )
        .unwrap();
        let rows = enumerate_stores(&home);
        let keys: Vec<&str> = rows.iter().map(|r| r["key"].as_str().unwrap()).collect();
        assert_eq!(
            keys,
            vec!["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb", "cccccccccccccccc"]
        );
        assert_eq!(rows[0]["state"], "populated");
        assert_eq!(rows[0]["path"], "/work/yarl");
        assert_eq!(rows[1]["state"], "missing");
        assert_eq!(rows[2]["state"], "empty");
        let probed = probe_stores(&home, &rows, PROBE_MAX_OPENS);
        assert_eq!(probed[0]["counts_capped"], false);
        assert_eq!(
            probed[0]["call_count"],
            Value::Null,
            "not-a-db degrades to unreadable"
        );
        assert_eq!(probed[0]["state"], "unreadable");
        let _ = std::fs::remove_dir_all(&home);
    }

    #[test]
    fn probe_opens_are_budgeted_past_the_cap() {
        let home = std::env::temp_dir().join(format!("cairn-ws2-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home);
        for key in ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"] {
            write_store(&home, key, true);
        }
        let rows = enumerate_stores(&home);
        let probed = probe_stores(&home, &rows, 1);
        assert_eq!(probed[0]["counts_capped"], false);
        assert_eq!(probed[1]["counts_capped"], true);
        assert_eq!(probed[1]["state"], "populated");
        let _ = std::fs::remove_dir_all(&home);
    }
}
