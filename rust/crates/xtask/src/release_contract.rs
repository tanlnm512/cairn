//! Release wiring contracts: crates.io publish order and release asset naming.

use std::collections::BTreeSet;
use std::fs;
use std::process::Command;

use regex::Regex;

use crate::check_doc_links::repo_root;

/// crates.io needs every path dep registered first, ending with the binstall crate.
const PUBLISH_ORDER: [&str; 6] = [
    "cairn-store",
    "cairn-parse",
    "cairn-query",
    "cairn-daemon",
    "cairn-build",
    "cairn-cli",
];

fn read(rel: &str) -> String {
    fs::read_to_string(repo_root().join(rel)).unwrap_or_else(|e| panic!("{rel}: {e}"))
}

#[test]
fn no_python_packaging_manifests_remain() {
    for gone in [
        "pyproject.toml",
        "uv.lock",
        "setup.py",
        "setup.cfg",
        "MANIFEST.in",
    ] {
        assert!(!repo_root().join(gone).exists(), "{gone}");
    }
    let tracked = Command::new("git")
        .args(["ls-files", "--", "src"])
        .current_dir(repo_root())
        .output()
        .expect("git ls-files");
    assert!(tracked.status.success());
    assert!(
        tracked.stdout.is_empty(),
        "nothing may be tracked under src/"
    );
}

#[test]
fn crates_publish_in_dependency_order_and_skip_without_a_token() {
    let text = read(".github/workflows/release-rust.yml");
    let order = Regex::new(r"(?m)^ *for crate in (.+); do$")
        .unwrap()
        .captures(&text)
        .expect("crates.io publish loop not found in release-rust.yml");
    assert_eq!(
        order[1].split_whitespace().collect::<Vec<_>>(),
        PUBLISH_ORDER
    );
    let gate = Regex::new(r#"(?s)\[ -z "\$CARGO_REGISTRY_TOKEN" \][^\n]*; then\n(.*?)\n\s*fi"#)
        .unwrap()
        .captures(&text)
        .expect("CARGO_REGISTRY_TOKEN gate not found in release-rust.yml");
    assert!(gate[1].contains("::notice::") && gate[1].contains("exit 0"));
}

#[test]
fn release_asset_naming_is_consistent_across_channels() {
    assert!(
        read(".github/workflows/release-rust.yml")
            .contains(r#""cairn-cli-${version}-${{ matrix.target }}.tar.gz""#)
    );
    assert!(read("rust/crates/cairn-cli/Cargo.toml").contains(
        r#"pkg-url = "{ repo }/releases/download/v{ version }/cairn-cli-{ version }-{ target }.tar.gz""#
    ));
    let formula = read("packaging/homebrew/cairn.rb.template");
    let targets: BTreeSet<&str> = Regex::new(r#"/cairn-cli-@VERSION@-([^"/]+)\.tar\.gz"#)
        .unwrap()
        .captures_iter(&formula)
        .map(|c| c.get(1).unwrap().as_str())
        .collect();
    assert_eq!(
        targets,
        BTreeSet::from([
            "aarch64-apple-darwin",
            "x86_64-apple-darwin",
            "aarch64-unknown-linux-musl",
            "x86_64-unknown-linux-musl",
        ])
    );
}
