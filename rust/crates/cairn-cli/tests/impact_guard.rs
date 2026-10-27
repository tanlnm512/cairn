//! `cairn impact --guard` suppresses cycle-carrying blowups above the threshold.

use std::path::Path;
use std::process::Command;

use serde_json::Value;

fn cairn(home: &Path, workspace: Option<&Path>, args: &[&str]) -> (bool, String) {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_cairn"));
    cmd.args(args).env("HOME", home).env("CAIRN_HOME", home);
    match workspace {
        Some(ws) => {
            cmd.env("CAIRN_WORKSPACE", ws);
        }
        None => {
            cmd.env_remove("CAIRN_WORKSPACE");
        }
    }
    let out = cmd.output().expect("spawn cairn");
    (
        out.status.success(),
        String::from_utf8_lossy(&out.stdout).into_owned(),
    )
}

fn parse_json(out: &str) -> Value {
    serde_json::from_str(out.trim()).expect("json output")
}

#[test]
fn guard_replaces_a_collision_dump_with_the_dataflow_fallback() {
    let home = std::env::temp_dir().join(format!("cairn-impact-guard-{}", std::process::id()));
    let ws = home.join("ws");
    let repo = ws.join("repo");
    std::fs::create_dir_all(repo.join(".git")).expect("scratch repo");
    std::fs::write(
        repo.join("mod.py"),
        "def a():\n    b()\n\n\ndef b():\n    a()\n\n\ndef c():\n    a()\n",
    )
    .expect("fixture");
    let db = home.join("store.kg");
    let db = db.to_str().expect("utf8 path");
    let (built, _) = cairn(
        &home,
        None,
        &[
            "build",
            "--workspace",
            ws.to_str().expect("utf8"),
            "--db",
            db,
        ],
    );
    assert!(built);

    let impact = |extra: &[&str]| {
        let mut args = vec!["impact", "a", "--db", db, "--no-refresh"];
        args.extend_from_slice(extra);
        cairn(&home, None, &args)
    };

    let (ok, plain) = impact(&[]);
    assert!(ok);
    let (_, plain_json) = impact(&["--json"]);
    let total = parse_json(&plain_json)["total"].as_u64().expect("total") as usize;
    assert!(
        total >= 2,
        "fixture must impact more than one symbol: {total}"
    );

    let (ok, below) = impact(&["--guard"]);
    assert!(ok);
    assert_eq!(
        below, plain,
        "below the threshold --guard must not change output"
    );
    let (ok, at_boundary) = impact(&["--guard", "--threshold", &total.to_string()]);
    assert!(ok);
    assert_eq!(at_boundary, plain, "total == threshold must not trip");
    let (_, one_under) = impact(&["--guard", "--threshold", &(total - 1).to_string()]);
    assert!(
        one_under.contains("name-collision artifact"),
        "total - 1 must trip: {one_under}"
    );

    let (_, below_json) = impact(&["--json", "--guard"]);
    let below_json = parse_json(&below_json);
    assert_eq!(below_json["guard"]["collision"], false);
    assert_eq!(below_json["guard"]["threshold"], 100);
    assert!(
        below_json.as_object().unwrap().contains_key("impacted"),
        "below the threshold the impacted array stays: {below_json}"
    );
    assert!(
        !below_json["guard"]
            .as_object()
            .expect("guard object")
            .contains_key("dataflow"),
        "dataflow only appears on a collision: {below_json}"
    );

    let (ok, tripped_json) = impact(&["--json", "--guard", "--threshold", "1"]);
    assert!(ok, "a collision is a successful guarded run");
    let tripped_json = parse_json(&tripped_json);
    assert!(
        !tripped_json.as_object().unwrap().contains_key("impacted"),
        "a collision must omit the impacted dump: {tripped_json}"
    );
    assert_eq!(tripped_json["total"], total as u64);
    assert_eq!(tripped_json["guard"]["collision"], true);
    assert_eq!(tripped_json["guard"]["threshold"], 1);
    assert!(
        tripped_json["guard"]["dataflow"].is_null(),
        "no dataflow index yet: {tripped_json}"
    );

    let (ok, tripped) = impact(&["--guard", "--threshold", "1"]);
    assert!(ok);
    assert!(tripped.contains("name-collision artifact"), "{tripped}");
    assert!(tripped.contains("No dataflow entry for 'a'"), "{tripped}");
    assert!(!tripped.contains("Depth "), "raw dump leaked: {tripped}");

    let (ok, built_df) = cairn(&home, Some(&ws), &["dataflow", "build", "--db", db]);
    assert!(ok, "{built_df}");

    let (ok, with_row) = impact(&["--guard", "--threshold", "1"]);
    assert!(ok);
    assert!(with_row.contains("Symbol: a"), "dataflow view: {with_row}");
    assert!(!with_row.contains("Depth "), "raw dump leaked: {with_row}");

    let (_, row_json) = impact(&["--json", "--guard", "--threshold", "1"]);
    let row_json = parse_json(&row_json);
    assert_eq!(row_json["guard"]["dataflow"]["symbol"], "a");
    assert!(
        row_json["guard"]["dataflow"]["within_repo"].is_array(),
        "{row_json}"
    );

    let _ = std::fs::remove_dir_all(&home);
}
