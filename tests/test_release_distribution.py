"""Release distribution wiring: rust binaries, Homebrew tap, crates.io publish."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github/workflows/release-rust.yml"
CLI_MANIFEST = REPO / "rust/crates/cairn-cli/Cargo.toml"
FORMULA = REPO / "packaging/homebrew/cairn.rb.template"

# crates.io publishes need every path dep already registered, so the order is
# dependency-first, ending with the crate cargo-binstall installs.
PUBLISH_ORDER = [
    "cairn-store",
    "cairn-parse",
    "cairn-query",
    "cairn-daemon",
    "cairn-build",
    "cairn-cli",
]


def test_release_publishes_workspace_crates_in_dependency_order():
    """The v* pipeline publishes every workspace crate before the crates that depend on it."""
    text = WORKFLOW.read_text()
    m = re.search(r"^ *for crate in (.+); do$", text, re.M)
    assert m, "crates.io publish loop not found in release-rust.yml"
    assert m.group(1).split() == PUBLISH_ORDER


def test_unconfigured_registry_token_skips_publish_with_notice():
    """A missing CARGO_REGISTRY_TOKEN skips the publish, never failing the release."""
    text = WORKFLOW.read_text()
    m = re.search(
        r'\[ -z "\$CARGO_REGISTRY_TOKEN" \][^\n]*; then\n(.*?)\n\s*fi', text, re.S
    )
    assert m, "CARGO_REGISTRY_TOKEN gate not found in release-rust.yml"
    assert "::notice::" in m.group(1)
    assert "exit 0" in m.group(1)


def test_release_asset_naming_is_consistent_across_channels():
    """binstall pkg-url, release tarball names, and formula URLs share one asset naming."""
    workflow = WORKFLOW.read_text()
    assert '"cairn-cli-${version}-${{ matrix.target }}.tar.gz"' in workflow
    manifest = CLI_MANIFEST.read_text()
    assert (
        'pkg-url = "{ repo }/releases/download/v{ version }'
        '/cairn-cli-{ version }-{ target }.tar.gz"' in manifest
    )
    targets = set(
        re.findall(r"/cairn-cli-@VERSION@-([^\"/]+)\.tar\.gz", FORMULA.read_text())
    )
    assert targets == {
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "aarch64-unknown-linux-musl",
        "x86_64-unknown-linux-musl",
    }
