//! Template drift guard: the embedded dashboard assets stay identical to
//! `src/cairn/dashboard` except for the enumerated minijinja-compat patches
//! (D-009: minijinja is Jinja2-compatible, not byte-identical).

use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use cairn_cli::dashboard::assets;

/// Templates carrying a deliberate minijinja-compat patch, pinned by sha256
/// so any further edit must update this table consciously:
/// - `_links`, `graph`, `history_region`, `chains_region`, `tokens_region`,
///   `wiki`, `wiki_results`: `.append`/`.update` mutation -> namespace lists
///   and the `merge` filter (the update-method return is discarded otherwise).
/// - `window_control`: same, via `q | merge`.
/// - `projects`: `sum(attribute=)` -> `map(attribute=) | sum`.
/// - `tokens_region`: `tools.token_mode`-style attrs -> `tools_meta`.
/// - `base`: `path.strip('/').split('/')` -> `trim | split` filters.
const PATCHED: [(&str, &str); 10] = [
    (
        "_links.html",
        "741d80af81763b3c484c70dfd750bfb4495a17aa7abb8418408ff4da41d25b38",
    ),
    (
        "base.html",
        "89f6606439701f0e3b3a8b04a48e149ab0912c6df989413a181f5a0215537cd4",
    ),
    (
        "chains_region.html",
        "e960fb8cda983b55f581df524c1170b75b21b8ebb13a4db3038c478513c01142",
    ),
    (
        "graph.html",
        "a3ea3303903f2e2b199d56e3040848d5d7b39c85c27cae46b631a574b61e8d9d",
    ),
    (
        "history_region.html",
        "d1681d96d46bf0e919323fbeada39e80c8579bd69c76fc4c849e0e6bae979bc6",
    ),
    (
        "projects.html",
        "3c9306c009538a60c21bdfcb344241dcd7839cbb9e5eb828f638e3f6b22d35bb",
    ),
    (
        "tokens_region.html",
        "6086ce077301893873bfac94e8e7830ba9bd283f15a428dc2172761f748d4d4b",
    ),
    (
        "wiki.html",
        "74ab1e67a34e18e179321c4225ec9d93798a72c12e478cb6b62ab21af54c7aab",
    ),
    (
        "wiki_results.html",
        "c20efb577bbdeb19c9d5902ebb64d470e6bee48956816bd14d7449528d972feb",
    ),
    (
        "window_control.html",
        "61ef11b0baecb6884c9782be5a859592b2334bf406c959deb11b7f747336d25b",
    ),
];

fn python_tree_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../src/cairn/dashboard")
}

#[test]
fn embedded_assets_match_the_python_tree_except_patched_templates() {
    let root = python_tree_root();
    assert!(
        root.is_dir(),
        "python dashboard tree present at {}",
        root.display()
    );
    let mut checked = 0usize;
    let mut patched_seen = 0usize;
    for (name, body) in assets::ASSETS {
        let python_path = root.join(name);
        let python_bytes = std::fs::read(&python_path)
            .unwrap_or_else(|err| panic!("{} cannot be read: {err}", python_path.display()));
        let patched_template = name
            .strip_prefix("templates/")
            .and_then(|rel| PATCHED.iter().find(|(patched, _)| *patched == rel));
        match patched_template {
            Some((_, pinned)) => {
                patched_seen += 1;
                if !pinned.is_empty() {
                    let digest = hex(&Sha256::digest(body));
                    assert_eq!(
                        digest, *pinned,
                        "{name} changed; refresh the pinned sha256 in this test"
                    );
                }
            }
            None => {
                assert_eq!(
                    body.to_vec(),
                    python_bytes,
                    "{name} drifted from the python dashboard tree"
                );
            }
        }
        checked += 1;
    }
    assert_eq!(
        patched_seen,
        PATCHED.len(),
        "every patched template is embedded"
    );
    assert!(checked > 100, "full tree checked ({checked} files)");
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
