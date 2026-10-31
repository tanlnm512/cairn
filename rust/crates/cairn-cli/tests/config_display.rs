//! `cairn config` displays the documented nested `scip.*` cairn.json shape.

use std::path::Path;
use std::process::Command;

use serde_json::Value;

fn cairn(home: &Path, workspace: &Path, args: &[&str]) -> (bool, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_cairn"))
        .args(args)
        .env("HOME", home)
        .env("CAIRN_HOME", home)
        .env("CAIRN_WORKSPACE", workspace)
        .env_remove("CAIRN_DB")
        .env_remove("CAIRN_KNOWLEDGE")
        .current_dir(workspace)
        .output()
        .expect("spawn cairn");
    (
        out.status.success(),
        String::from_utf8_lossy(&out.stdout).into_owned(),
    )
}

#[test]
fn config_displays_the_documented_nested_scip_sections() {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let home =
        std::env::temp_dir().join(format!("cairn-config-scip-{}-{nanos}", std::process::id()));
    let ws = home.join("ws");
    std::fs::create_dir_all(&ws).expect("workspace");
    std::fs::write(
        ws.join("cairn.json"),
        r#"{"scip": {"indexes": {"python": "index.scip"},
                    "generated": {"java": true},
                    "commands": {"rust": ["my-scip", "$out"]}}}"#,
    )
    .expect("config");

    let (ok, json) = cairn(&home, &ws, &["config", "--json"]);
    assert!(ok, "{json}");
    let parsed: Value = serde_json::from_str(json.trim()).expect("json output");
    assert_eq!(
        parsed["scip"]["indexes"]["python"], "index.scip",
        "{parsed}"
    );
    assert_eq!(parsed["scip"]["generated"], serde_json::json!(["java"]));
    assert_eq!(
        parsed["scip"]["commands"]["rust"],
        serde_json::json!(["my-scip", "$out"])
    );

    let (ok, text) = cairn(&home, &ws, &["config"]);
    assert!(ok, "{text}");
    assert!(text.contains("python -> index.scip"), "{text}");
    assert!(text.lines().any(|l| l == "  java"), "{text}");
    assert!(text.contains("rust -> my-scip $out"), "{text}");
    assert!(!text.contains("(none configured"), "{text}");

    let _ = std::fs::remove_dir_all(&home);
}
