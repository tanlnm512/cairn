//! Embedded dashboard templates and static assets (build-script table).

use std::sync::OnceLock;

include!(concat!(env!("OUT_DIR"), "/dashboard_assets.rs"));

/// Embedded file body by its `/`-relative path (no leading slash).
pub fn get(path: &str) -> Option<&'static [u8]> {
    ASSETS
        .iter()
        .find(|(name, _)| *name == path)
        .map(|(_, body)| *body)
}

/// Template source text by bare template name (e.g. `base.html`).
pub fn template_source(name: &str) -> Option<&'static str> {
    get(&format!("templates/{name}")).and_then(|bytes| std::str::from_utf8(bytes).ok())
}

fn content_type(path: &str) -> &'static str {
    let ext = path.rsplit('.').next().unwrap_or("").to_ascii_lowercase();
    match ext.as_str() {
        "css" => "text/css; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "svg" => "image/svg+xml",
        "json" => "application/json",
        "png" => "image/png",
        "html" => "text/html; charset=utf-8",
        "woff2" => "font/woff2",
        "txt" => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    }
}

/// Content-Type for one static asset path (leading slash optional).
pub fn static_content_type(path: &str) -> &'static str {
    content_type(path.trim_start_matches('/'))
}

/// Static asset body by URL path (`/app.css`).
pub fn static_file(path: &str) -> Option<&'static [u8]> {
    get(&format!("static{}", path.trim_end_matches('/')))
}

/// Embedded static assets as `(path, body)` pairs, sorted by path.
pub fn static_files() -> impl Iterator<Item = (&'static str, &'static [u8])> {
    ASSETS
        .iter()
        .filter_map(|(name, body)| name.strip_prefix("static/").map(|p| (p, *body)))
}

/// Stable cache-busting stamp: FNV-1a over every embedded asset name+body.
pub fn asset_version() -> &'static str {
    static VERSION: OnceLock<String> = OnceLock::new();
    VERSION.get_or_init(|| {
        let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
        for (name, body) in ASSETS {
            for byte in name.bytes().chain(body.iter().copied()) {
                hash ^= u64::from(byte);
                hash = hash.wrapping_mul(0x1000_0000_01b3);
            }
        }
        format!("{hash:016x}")
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn templates_and_key_static_files_are_embedded() {
        assert!(template_source("base.html").unwrap().contains("cairn"));
        assert_eq!(
            ASSETS
                .iter()
                .filter(|(n, _)| n.starts_with("templates/"))
                .count(),
            38
        );
        assert!(!static_file("/app.css").unwrap().is_empty());
        assert!(static_file("/htmx.min.js").is_some());
        assert!(static_file("/favicon.svg").is_some());
        assert!(static_file("/chunks/mermaid.esm.min").is_none());
    }

    #[test]
    fn asset_version_is_stable_within_the_process() {
        assert_eq!(asset_version(), asset_version());
        assert_eq!(asset_version().len(), 16);
    }
}
