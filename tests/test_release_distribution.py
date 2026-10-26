"""Release distribution wiring: rust binaries, Homebrew tap, crates.io publish, PyPI wheels."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github/workflows/release-rust.yml"
WHEELS_WORKFLOW = REPO / ".github/workflows/release.yml"
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


def _cibuildwheel_env() -> dict[str, str]:
    """The env block of the cibuildwheel step in release.yml."""
    doc = yaml.safe_load(WHEELS_WORKFLOW.read_text())
    for step in doc["jobs"]["build"]["steps"]:
        if str(step.get("uses", "")).startswith("pypa/cibuildwheel"):
            return step["env"]
    raise AssertionError("cibuildwheel step not found in release.yml")


def test_wheel_platform_set_spans_linux_and_macos():
    """Wheels cover manylinux+musllinux x86_64/aarch64 and macOS arm64+x86_64 (FR-005 minus Windows)."""
    env = _cibuildwheel_env()
    assert env["CIBW_ARCHS_LINUX"] == "x86_64 aarch64"
    assert env["CIBW_ARCHS_MACOS"] == "arm64 x86_64"
    assert "musllinux" not in env.get("CIBW_SKIP", ""), "musllinux legs must not be skipped"


def test_macos_wheels_bundle_the_matching_arch_engine():
    """Both darwin engines are built and each macOS wheel copies its own arch, keyed on the build identifier."""
    env = _cibuildwheel_env()
    before = env["CIBW_BEFORE_BUILD_MACOS"]
    assert "CIBUILDWHEEL_BUILD_IDENTIFIER" in before
    assert "aarch64-apple-darwin" in before
    assert "x86_64-apple-darwin" in before
    assert 'cp "rust/target/$tgt/release/cairn" src/cairn/_bin/cairn' in before
    text = WHEELS_WORKFLOW.read_text()
    for target in ("aarch64-apple-darwin", "x86_64-apple-darwin"):
        assert f"cargo build --release --target {target} -p cairn-cli" in text


def test_linux_wheels_bundle_the_matching_arch_musl_engine():
    """Each linux wheel build (manylinux and musllinux alike) copies the musl-static engine for its arch."""
    env = _cibuildwheel_env()
    before = env["CIBW_BEFORE_BUILD_LINUX"]
    assert "x86_64-unknown-linux-musl" in before
    assert "aarch64-unknown-linux-musl" in before
    assert 'cp "rust/target/$tgt/release/cairn" src/cairn/_bin/cairn' in before
