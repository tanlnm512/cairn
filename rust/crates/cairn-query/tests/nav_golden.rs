//! Golden parity for the nav tools: served text must equal the recorded
//! Python fixtures over the checked-in nav store.
//!
//! These four responses carry no volatile fields (the recorder's
//! normalization rules were verified as no-ops on them at capture time), so
//! the comparison is raw byte equality: any diff is a real divergence.

use std::fs;
use std::path::{Path, PathBuf};

use cairn_query::tools_nav;
use rusqlite::Connection;
use serde_json::Value;

const TOOLS: [&str; 4] = [
    "find_definition",
    "get_callers",
    "get_callees",
    "impact_analysis",
];

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("repo root")
}

/// The store a fixture replays over: its `replay_store` pointer when present,
/// else the legacy checked-in default.
fn fixture_store(root: &Path, fixture: &Value, default: &str) -> PathBuf {
    root.join(fixture["replay_store"].as_str().unwrap_or(default))
}

/// Copy-on-create scratch store; removes the directory on drop.
struct TempStore(PathBuf);

impl TempStore {
    fn from_fixture(src: &Path) -> Self {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir =
            std::env::temp_dir().join(format!("cairn-p1b-nav-{}-{nanos}", std::process::id()));
        fs::create_dir_all(&dir).expect("temp dir");
        fs::copy(src, dir.join("nav-store.kg")).expect("copy fixture store");
        Self(dir)
    }

    fn db(&self) -> PathBuf {
        self.0.join("nav-store.kg")
    }
}

impl Drop for TempStore {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn nav_store_from(src: &Path) -> (Connection, TempStore) {
    let temp = TempStore::from_fixture(src);
    let conn = cairn_store::conn::open_read_only(&temp.db()).expect("open nav store");
    (conn, temp)
}

/// Python's `"\n".join(sorted(text.splitlines()))`.
fn sorted_lines(text: &str) -> String {
    let mut lines: Vec<&str> = text.lines().collect();
    lines.sort();
    lines.join("\n")
}

fn arg_i64(args: &Value, key: &str, default: i64) -> i64 {
    args.get(key).and_then(Value::as_i64).unwrap_or(default)
}

fn call_tool(conn: &Connection, tool: &str, args: &Value) -> String {
    let name = args["name"].as_str().expect("string name arg");
    let fuzzy = args.get("fuzzy").and_then(Value::as_bool).unwrap_or(false);
    match tool {
        "find_definition" => {
            tools_nav::find_definition_text(conn, name, arg_i64(args, "limit", 50), "")
                .expect("find_definition")
        }
        "get_callers" => {
            tools_nav::callers_text(conn, name, fuzzy, arg_i64(args, "limit", 200), "")
                .expect("get_callers")
        }
        "get_callees" => {
            tools_nav::callees_text(conn, name, fuzzy, arg_i64(args, "limit", 200), "")
                .expect("get_callees")
        }
        "impact_analysis" => tools_nav::impact_analysis_text(
            conn,
            name,
            arg_i64(args, "depth", 5),
            fuzzy,
            arg_i64(args, "limit", 500),
            "",
            tools_nav::DEFAULT_REPO_NAMESPACES,
        )
        .expect("impact_analysis"),
        other => panic!("unknown nav tool {other}"),
    }
}

#[test]
fn nav_tools_are_golden_equal() {
    for tool in TOOLS {
        let path = repo_root().join(format!("rust/golden/mcp/{tool}.json"));
        let fixture: Value = serde_json::from_str(
            &fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display())),
        )
        .expect("fixture json");
        let expected = fixture["response"]["content"][0]["text"]
            .as_str()
            .unwrap_or_else(|| panic!("{tool}: no text content"));
        assert_eq!(
            fixture["response"]["structuredContent"]["result"].as_str(),
            Some(expected),
            "{tool}: fixture envelope drift"
        );
        let (conn, _temp) = nav_store_from(&fixture_store(
            &repo_root(),
            &fixture,
            "rust/golden/nav/nav-store.kg",
        ));
        let mut actual = call_tool(&conn, tool, &fixture["arguments"]);
        if fixture["normalization"]["sort_lines"]
            .as_bool()
            .unwrap_or(false)
        {
            // The fixture stores the capture's sorted line order; tie order
            // must be compared rank-insensitively (the verify-time ruling).
            actual = sorted_lines(&actual);
        }
        assert_eq!(actual, expected, "{tool} diverges from the golden fixture");
    }
}
