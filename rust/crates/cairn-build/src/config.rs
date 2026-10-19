//! `cairn.json` reader for the scanner's include/exclude layers.

use std::path::Path;

/// Repository config fields the build path consumes.
#[derive(Debug, Clone, Default)]
pub struct CairnConfig {
    pub exclude: Vec<String>,
    pub include: Vec<String>,
    pub include_nested_repos: bool,
    pub scip_indexes: Vec<(String, String)>,
}

/// Load `root/cairn.json`; missing file yields defaults, malformed content is an error.
pub fn load_config(root: &Path) -> Result<CairnConfig, String> {
    let path = root.join("cairn.json");
    let mut cfg = CairnConfig::default();
    if !path.is_file() {
        return Ok(cfg);
    }
    let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    let raw: serde_json::Value =
        serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    let obj = raw
        .as_object()
        .ok_or_else(|| format!("{}: expected a JSON object", path.display()))?;
    if let Some(v) = obj.get("exclude") {
        cfg.exclude = string_list(v, &path, "exclude")?;
    }
    if let Some(v) = obj.get("include") {
        cfg.include = string_list(v, &path, "include")?;
    }
    if let Some(v) = obj.get("include_nested_repos") {
        cfg.include_nested_repos = v
            .as_bool()
            .ok_or_else(|| format!("{}: include_nested_repos must be a bool", path.display()))?;
    }
    if let Some(v) = obj.get("scip")
        && let Some(indexes) = v.get("indexes").and_then(|i| i.as_object())
    {
        for (lang, p) in indexes {
            let Some(p) = p.as_str() else {
                return Err(format!(
                    "{}: scip.indexes.{} must be a string",
                    path.display(),
                    lang
                ));
            };
            cfg.scip_indexes.push((lang.clone(), p.to_string()));
        }
    }
    Ok(cfg)
}

fn string_list(v: &serde_json::Value, path: &Path, key: &str) -> Result<Vec<String>, String> {
    let arr = v
        .as_array()
        .ok_or_else(|| format!("{}: {} must be a list of strings", path.display(), key))?;
    arr.iter()
        .map(|item| {
            item.as_str()
                .map(str::to_string)
                .ok_or_else(|| format!("{}: {} must be a list of strings", path.display(), key))
        })
        .collect()
}
