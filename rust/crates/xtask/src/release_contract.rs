//! Release wiring contracts: no Python packaging and release asset naming.

use std::fs;
use std::process::Command;

use crate::check_doc_links::repo_root;

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
fn release_asset_naming_matches_binstall_metadata() {
    assert!(
        read(".github/workflows/release-rust.yml")
            .contains(r#""cairn-cli-${version}-${{ matrix.target }}.tar.gz""#)
    );
    assert!(read("rust/crates/cairn-cli/Cargo.toml").contains(
        r#"pkg-url = "{ repo }/releases/download/v{ version }/cairn-cli-{ version }-{ target }.tar.gz""#
    ));
}
