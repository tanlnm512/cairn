//! Embed the dashboard template + static asset tree into the binary.

use std::fmt::Write as _;
use std::path::{Path, PathBuf};

fn collect(dir: &Path, rel: &str, out: &mut Vec<(String, PathBuf)>) {
    let mut entries: Vec<PathBuf> = match std::fs::read_dir(dir) {
        Ok(read) => read.filter_map(|e| e.ok()).map(|e| e.path()).collect(),
        Err(_) => return,
    };
    entries.sort();
    for path in entries {
        let name = match path.file_name().and_then(|n| n.to_str()) {
            Some(name) => name.to_string(),
            None => continue,
        };
        let child_rel = if rel.is_empty() {
            name
        } else {
            format!("{rel}/{name}")
        };
        if path.is_dir() {
            collect(&path, &child_rel, out);
        } else {
            out.push((child_rel, path));
        }
    }
}

fn main() {
    println!("cargo:rerun-if-changed=dashboard-assets");
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("dashboard-assets");
    let mut files = Vec::new();
    collect(&root, "", &mut files);
    let mut code = String::from("pub static ASSETS: &[(&str, &[u8])] = &[\n");
    for (rel, _) in &files {
        writeln!(
            code,
            "    ({rel:?}, include_bytes!(concat!(env!(\"CARGO_MANIFEST_DIR\"), \"/dashboard-assets/{rel}\"))),"
        )
        .unwrap();
    }
    code.push_str("];\n");
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR");
    std::fs::write(Path::new(&out_dir).join("dashboard_assets.rs"), code)
        .expect("write dashboard_assets.rs");
}
