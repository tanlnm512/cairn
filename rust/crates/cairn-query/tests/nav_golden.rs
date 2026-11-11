//! Nav-tool golden parity: served text equals the recorded fixtures byte for byte.

mod common;

use std::fs;
use std::path::Path;

use cairn_query::tools_nav;
use rusqlite::Connection;
use serde_json::Value;

use common::{TempStore, fixture_store, repo_root, sorted_lines};

const TOOLS: [&str; 4] = [
    "find_definition",
    "get_callers",
    "get_callees",
    "impact_analysis",
];

fn nav_store_from(src: &Path) -> (Connection, TempStore) {
    let temp = TempStore::from_fixture(src, "nav");
    let conn = cairn_store::conn::open_read_only(&temp.db(src)).expect("open nav store");
    (conn, temp)
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
    let root = repo_root();
    for tool in TOOLS {
        let path = root.join(format!("rust/golden/mcp/{tool}.json"));
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
            &root,
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

#[test]
fn qualified_names_resolve_through_the_definition_ladder() {
    let root = repo_root();
    let (conn, _temp) = nav_store_from(&root.join("rust/golden/nav/nav-store.kg"));
    // The bare name and the repo-qualified name return the same caller set.
    let bare = tools_nav::callers_text(&conn, "renderStatus", false, 200, "").expect("bare");
    let qualified =
        tools_nav::callers_text(&conn, "status.renderStatus", false, 200, "").expect("qualified");
    let body =
        |text: &str| sorted_lines(&text.lines().skip(1).collect::<Vec<_>>().join("\n"));
    assert_eq!(
        body(&qualified),
        body(&bare),
        "qualified form must match the bare form\nbare:\n{bare}\nqualified:\n{qualified}"
    );
    let impact_bare =
        tools_nav::impact_analysis_text(&conn, "worstState", 2, false, 100, "", tools_nav::DEFAULT_REPO_NAMESPACES)
            .expect("impact bare");
    let impact_qualified =
        tools_nav::impact_analysis_text(&conn, "statusModel.worstState", 2, false, 100, "", tools_nav::DEFAULT_REPO_NAMESPACES)
            .expect("impact qualified");
    assert!(
        impact_qualified.contains("total impacted symbols"),
        "qualified impact must resolve: {impact_qualified}"
    );
    let impact_body =
        |text: &str| sorted_lines(&text.lines().skip(1).collect::<Vec<_>>().join("\n"));
    assert_eq!(
        impact_body(&impact_qualified),
        impact_body(&impact_bare),
        "qualified impact must match the bare form"
    );
}
