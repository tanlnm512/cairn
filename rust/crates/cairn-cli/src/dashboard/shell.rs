//! Shell chrome context: nav sections, store selector, command palette.

use serde_json::{Value, json};

/// Fallback label for the no-selection option (a launch db outside the registry).
pub const LAUNCH_LABEL: &str = "Launch workspace";

/// Sidebar order; `None` renders the ungrouped workspaces entry.
const NAV_SECTIONS: [(Option<&str>, &[&str]); 5] = [
    (None, &["workspaces"]),
    (Some("Explore"), &["projects", "graph", "communities"]),
    (Some("Knowledge"), &["knowledge", "wiki", "memory", "tasks"]),
    (Some("Activity"), &["history", "tokens", "chains"]),
    (
        Some("System"),
        &["health", "embeddings", "database", "settings"],
    ),
];

/// Palette-only sub-views share the nav store-param composition.
const PALETTE_EXTRA_VIEWS: [(&str, &str); 1] = [("/knowledge/graph", "Knowledge Graph")];

fn nav_label(view_id: &str) -> &'static str {
    match view_id {
        "workspaces" => "Workspaces",
        "projects" => "Projects",
        "graph" => "Graph",
        "communities" => "Communities",
        "history" => "History",
        "tokens" => "Tokens",
        "chains" => "Chains",
        "health" => "Health",
        "memory" => "Memory",
        "knowledge" => "Knowledge",
        "wiki" => "Wiki",
        "tasks" => "Tasks",
        "embeddings" => "Embeddings",
        "database" => "Database",
        "settings" => "Settings",
        _ => "",
    }
}

/// Selector label for one store row: the workspace path's basename, else the key.
pub fn workspace_label(path: Option<&str>, key: &str) -> String {
    if let Some(path) = path {
        let trimmed = path.trim_end_matches('/');
        if let Some(name) = trimmed.rsplit('/').next()
            && !name.is_empty()
        {
            return name.to_string();
        }
    }
    key.to_string()
}

fn launch_option_label(stores: &[Value], launch_db: Option<&str>) -> String {
    let Some(launch_db) = launch_db else {
        return LAUNCH_LABEL.into();
    };
    let key = std::path::Path::new(launch_db)
        .parent()
        .and_then(|p| p.file_name())
        .and_then(|n| n.to_str())
        .unwrap_or("");
    for row in stores {
        if row["key"].as_str() == Some(key) && row["state"].as_str() == Some("populated") {
            return format!("{} (launch)", workspace_label(row["path"].as_str(), key));
        }
    }
    LAUNCH_LABEL.into()
}

fn populated_options(stores: &[Value]) -> Vec<Value> {
    stores
        .iter()
        .filter(|row| row["state"].as_str() == Some("populated"))
        .map(|row| {
            json!({
                "key": row["key"],
                "label": workspace_label(row["path"].as_str(), row["key"].as_str().unwrap_or("")),
                "path": row["path"].as_str().unwrap_or(""),
            })
        })
        .collect()
}

/// Nav, selector, and palette context for one request (shell.shell_context port).
pub fn shell_context(
    stores: &[Value],
    store_key: &str,
    path: &str,
    launch_db: Option<&str>,
) -> Value {
    let nav_query = if store_key.is_empty() {
        String::new()
    } else {
        format!("?store={}", urlencode(store_key))
    };
    let options = populated_options(stores);
    let mut sections = Vec::new();
    let mut palette_views = Vec::new();
    for (section_label, view_ids) in NAV_SECTIONS {
        let mut items = Vec::new();
        for view_id in view_ids {
            let label = nav_label(view_id);
            let href = format!("/{view_id}{nav_query}");
            items.push(json!({
                "id": view_id,
                "label": label,
                "href": href,
                "active": path.starts_with(&format!("/{view_id}")),
            }));
            palette_views.push(json!({"label": label, "href": href}));
        }
        sections.push(json!({"label": section_label, "items": items}));
    }
    for (extra_href, extra_label) in PALETTE_EXTRA_VIEWS {
        palette_views
            .push(json!({"label": extra_label, "href": format!("{extra_href}{nav_query}")}));
    }
    json!({
        "selector": {
            "options": options,
            "selected": store_key,
            "launch_label": launch_option_label(stores, launch_db),
        },
        "nav": {"sections": sections},
        "palette": {"views": palette_views, "workspaces": options},
    })
}

/// `urllib.parse.quote(value, safe="")`.
pub fn urlencode(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => {
                out.push(byte as char)
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stores() -> Vec<Value> {
        vec![
            json!({"key": "aaaaaaaaaaaaaaaa", "path": "/work/yarl", "state": "populated"}),
            json!({"key": "bbbbbbbbbbbbbbbb", "path": Value::Null, "state": "empty"}),
        ]
    }

    #[test]
    fn nav_sections_carry_active_flags_and_store_query() {
        let shell = shell_context(&stores(), "aaaaaaaaaaaaaaaa", "/graph", None);
        assert_eq!(shell["selector"]["selected"], "aaaaaaaaaaaaaaaa");
        assert_eq!(shell["selector"]["launch_label"], LAUNCH_LABEL);
        let sections = shell["nav"]["sections"].as_array().unwrap();
        assert_eq!(sections.len(), 5);
        assert_eq!(sections[0]["label"], Value::Null);
        assert_eq!(sections[1]["label"], "Explore");
        let graph = sections[1]["items"]
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["id"] == "graph")
            .unwrap();
        assert_eq!(graph["href"], "/graph?store=aaaaaaaaaaaaaaaa");
        assert_eq!(graph["active"], true);
        assert_eq!(
            shell["palette"]["views"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|v| v["label"] == "Knowledge Graph")
                .count(),
            1
        );
    }

    #[test]
    fn launch_label_names_a_registered_launch_store() {
        // The launch db lives at <home>/<store-key>/.kg, so the db's parent
        // dir name is the store key the registry maps to the workspace.
        let shell = shell_context(
            &stores(),
            "",
            "/",
            Some("/home/x/.cairn/aaaaaaaaaaaaaaaa/.kg"),
        );
        assert_eq!(shell["selector"]["launch_label"], "yarl (launch)");
        let shell = shell_context(&stores(), "", "/", Some("/other/db.kg"));
        assert_eq!(shell["selector"]["launch_label"], LAUNCH_LABEL);
    }

    #[test]
    fn workspace_label_prefers_the_basename() {
        assert_eq!(workspace_label(Some("/work/yarl/"), "k"), "yarl");
        assert_eq!(
            workspace_label(None, "0123456789abcdef"),
            "0123456789abcdef"
        );
    }

    #[test]
    fn urlencode_matches_quote_with_empty_safe() {
        assert_eq!(urlencode("a b/c?d"), "a%20b%2Fc%3Fd");
        assert_eq!(urlencode("safe-_.~"), "safe-_.~");
    }
}
