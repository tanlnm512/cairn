//! `cairn.json` reader for the scanner's include/exclude layers.

use std::path::Path;

/// Repository config fields the build path consumes.
#[derive(Debug, Clone, Default)]
pub struct CairnConfig {
    pub exclude: Vec<String>,
    pub include: Vec<String>,
    pub include_nested_repos: bool,
    pub scip_indexes: Vec<(String, String)>,
    /// Languages from `scip.generated` set to true: the index lives at
    /// `<cairn store>/scip/<lang>.scip`, regenerated there when missing, and
    /// never inside the workspace.
    pub scip_generated: Vec<String>,
    /// Per-language indexer argv templates from `scip.commands`, overriding
    /// the built-in registry; `$out`/`$repo` substitute the index output path
    /// and workspace root.
    pub scip_commands: Vec<(String, Vec<String>)>,
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
    if let Some(v) = obj.get("scip") {
        let Some(scip) = v.as_object() else {
            return Err(format!("{}: scip must be a JSON object", path.display()));
        };
        if let Some(indexes) = scip.get("indexes") {
            let Some(indexes) = indexes.as_object() else {
                return Err(format!(
                    "{}: scip.indexes must be a JSON object",
                    path.display()
                ));
            };
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
        if let Some(generated) = scip.get("generated") {
            let Some(generated) = generated.as_object() else {
                return Err(format!(
                    "{}: scip.generated must be a JSON object",
                    path.display()
                ));
            };
            for (lang, on) in generated {
                match on.as_bool() {
                    Some(true) => cfg.scip_generated.push(lang.clone()),
                    Some(false) => {}
                    None => {
                        return Err(format!(
                            "{}: scip.generated.{} must be a bool",
                            path.display(),
                            lang
                        ));
                    }
                }
            }
        }
        if let Some(commands) = scip.get("commands") {
            let Some(commands) = commands.as_object() else {
                return Err(format!(
                    "{}: scip.commands must be a JSON object",
                    path.display()
                ));
            };
            for (lang, argv) in commands {
                let Some(argv) = argv.as_array() else {
                    return Err(format!(
                        "{}: scip.commands.{} must be a list of strings",
                        path.display(),
                        lang
                    ));
                };
                let mut parts = Vec::with_capacity(argv.len());
                for part in argv {
                    let Some(part) = part.as_str() else {
                        return Err(format!(
                            "{}: scip.commands.{} must be a list of strings",
                            path.display(),
                            lang
                        ));
                    };
                    parts.push(part.to_string());
                }
                cfg.scip_commands.push((lang.clone(), parts));
            }
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

#[cfg(test)]
mod tests {
    use super::*;

    fn load_from(text: &str) -> Result<CairnConfig, String> {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("cairn-cfg-{}-{nanos}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dir");
        std::fs::write(dir.join("cairn.json"), text).expect("write");
        let cfg = load_config(&dir);
        let _ = std::fs::remove_dir_all(&dir);
        cfg
    }

    #[test]
    fn generated_non_bool_value_is_an_error() {
        let err = load_from(r#"{"scip": {"generated": {"python": "yes"}}}"#).expect_err("non-bool");
        assert!(
            err.contains("scip.generated.python must be a bool"),
            "{err}"
        );
    }

    #[test]
    fn scip_containers_must_be_json_objects() {
        for key in ["indexes", "generated", "commands"] {
            let err = load_from(&format!(r#"{{"scip": {{"{key}": []}}}}"#))
                .expect_err("non-object container");
            assert!(
                err.contains(&format!("scip.{key} must be a JSON object")),
                "{err}"
            );
        }
    }
}
