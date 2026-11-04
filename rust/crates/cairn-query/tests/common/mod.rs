//! Shared golden-replay harness: repo root, scratch stores, scrub rules.

#![allow(dead_code)]

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use regex::Regex;
use serde_json::{Value, json};

pub fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("repo root")
}

/// The store a fixture replays over: its `replay_store` pointer when present,
/// else the legacy checked-in default.
pub fn fixture_store(root: &Path, fixture: &Value, default: &str) -> PathBuf {
    root.join(fixture["replay_store"].as_str().unwrap_or(default))
}

/// Copy-on-create scratch store; removes the directory on drop.
pub struct TempStore(pub PathBuf);

impl TempStore {
    pub fn from_fixture(src: &Path, tag: &str) -> Self {
        // Counter not clock: parallel test threads read the same clock tick.
        static SEQ: AtomicU64 = AtomicU64::new(0);
        let dir = std::env::temp_dir().join(format!(
            "cairn-query-{tag}-{}-{}",
            std::process::id(),
            SEQ.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&dir).expect("temp dir");
        fs::copy(src, dir.join(src.file_name().expect("store name"))).expect("copy fixture store");
        Self(dir)
    }

    pub fn db(&self, src: &Path) -> PathBuf {
        self.0.join(src.file_name().expect("store name"))
    }
}

impl Drop for TempStore {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// `"\n".join(sorted(text.splitlines()))`.
pub fn sorted_lines(text: &str) -> String {
    let mut lines: Vec<&str> = text.lines().collect();
    lines.sort();
    lines.join("\n")
}

// --- fixture normalization (the recorder's scrub rules) ----------------------

pub type Rule = Box<dyn Fn(&str) -> String>;

pub fn re_replace(pattern: &str, replacement: &'static str) -> Rule {
    let re = Regex::new(pattern).expect("rule regex");
    Box::new(move |text: &str| re.replace_all(text, replacement).to_string())
}

fn round_float(text: &str) -> String {
    let re = Regex::new(r"\b\d+\.\d{5,}\b").expect("float regex");
    re.replace_all(text, |caps: &regex::Captures| {
        format!("{:.4}", caps[0].parse::<f64>().unwrap_or(0.0))
    })
    .to_string()
}

fn mask_id6(text: &str) -> String {
    // Port of the lookbehind rule (?<=[-_.])[0-9a-f]{6}\b.
    let re = Regex::new(r"([-_.])[0-9a-f]{6}\b").expect("id6 regex");
    re.replace_all(text, "${1}<ID6>").to_string()
}

fn no_op(text: &str) -> String {
    text.to_string()
}

/// The fixture's declared normalization rules, in order. Path rules
/// (store_home/home/workspace) were already applied at capture time; replay
/// responses carry store-relative paths only, so they map to no-ops.
pub fn normalization_rules(fixture: &Value) -> Vec<Rule> {
    fixture["normalization"]["rules"]
        .as_array()
        .expect("rules list")
        .iter()
        .map(|r| r.as_str().expect("rule name"))
        .map(|name| match name {
            "iso_ts" => re_replace(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
                "<ISO>",
            ),
            "epoch" => re_replace(r"\b1[0-9]{9}\.\d+\b", "<EPOCH>"),
            "date" => re_replace(r"\d{4}-\d{2}-\d{2}", "<DATE>"),
            "duration" => re_replace(r"\b\d+\.\d+ ?m?s\b|\b\d+ ?ms\b", "<DUR>"),
            "age" => re_replace(r"\bjust now\b|\b\d+[smhd] old\b", "<AGE>"),
            "id12" => re_replace(r"\b[0-9a-f]{12}\b", "<ID>"),
            "id6" => Box::new(mask_id6 as fn(&str) -> String),
            "float" => Box::new(round_float as fn(&str) -> String),
            _ => Box::new(no_op as fn(&str) -> String),
        })
        .collect()
}

pub fn normalize_value(value: &Value, rules: &[Rule]) -> Value {
    match value {
        Value::String(text) => {
            Value::String(rules.iter().fold(text.clone(), |acc, rule| rule(&acc)))
        }
        Value::Array(items) => {
            Value::Array(items.iter().map(|v| normalize_value(v, rules)).collect())
        }
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(k, v)| (k.clone(), normalize_value(v, rules)))
                .collect(),
        ),
        other => other.clone(),
    }
}

/// The served-tool response envelope the recorder captured.
pub fn envelope(text: String) -> Value {
    json!({
        "isError": false,
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"result": text},
    })
}
