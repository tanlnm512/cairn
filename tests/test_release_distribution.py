"""Release distribution wiring: rust binaries, Homebrew tap, crates.io publish, PyPI bin wheels."""

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

# FR-005: one bin wheel per rust target, built on that platform's runner family.
WHEEL_TARGET_RUNNERS = {
    "x86_64-unknown-linux-gnu": "ubuntu",
    "aarch64-unknown-linux-gnu": "ubuntu",
    "x86_64-unknown-linux-musl": "ubuntu",
    "aarch64-unknown-linux-musl": "ubuntu",
    "x86_64-apple-darwin": "macos",
    "aarch64-apple-darwin": "macos",
    "x86_64-pc-windows-msvc": "windows",
}
LINUX_TARGETS = tuple(t for t in WHEEL_TARGET_RUNNERS if "linux" in t)
# Legs whose runner can install its own wheel run the installed-product smoke.
NATIVE_SMOKE_TARGETS = {"x86_64-unknown-linux-gnu", "x86_64-apple-darwin", "aarch64-apple-darwin"}


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


def _wheels_doc() -> dict:
    return yaml.safe_load(WHEELS_WORKFLOW.read_text())


def _wheel_legs(doc: dict) -> dict[str, dict]:
    """The wheels job's matrix rows keyed by rust target."""
    return {leg["target"]: leg for leg in doc["jobs"]["wheels"]["strategy"]["matrix"]["include"]}


def _maturin_step(job: dict) -> dict:
    for step in job["steps"]:
        if str(step.get("uses", "")).startswith("PyO3/maturin-action@"):
            return step
    raise AssertionError("maturin-action step not found")


def test_wheel_matrix_covers_the_seven_targets_on_their_own_runner_family():
    """FR-005: exactly the plan's seven (rust target, runner) rows build wheels."""
    legs = _wheel_legs(_wheels_doc())
    assert {target: leg["os"].split("-")[0] for target, leg in legs.items()} == WHEEL_TARGET_RUNNERS


def test_every_wheel_is_a_maturin_release_build_with_no_launcher_injection():
    """US3-AC1: maturin-action builds each wheel from the cargo workspace; no cibuildwheel, setup.py or _bin copy remains."""
    doc = _wheels_doc()
    with_ = _maturin_step(doc["jobs"]["wheels"])["with"]
    assert with_["target"] == "${{ matrix.target }}"
    assert with_["manylinux"] == "${{ matrix.manylinux }}"
    assert "--release" in with_["args"] and "--out dist" in with_["args"]
    text = WHEELS_WORKFLOW.read_text()
    for gone in ("cibuildwheel", "src/cairn/_bin", "setup.py"):
        assert gone not in text, gone


def test_linux_wheels_cross_compile_with_zig_and_carry_their_platform_tag():
    """Every linux leg builds on the host through zig; gnu legs tag manylinux (auto), musl legs musllinux_1_2."""
    legs = _wheel_legs(_wheels_doc())
    for target in LINUX_TARGETS:
        assert legs[target]["zig"] == "--zig", target
        expected = "musllinux_1_2" if target.endswith("musl") else "auto"
        assert legs[target]["manylinux"] == expected, target


def test_windows_leg_is_non_blocking_until_the_engine_ports():
    """The win_amd64 row is in the matrix but cannot fail the release before the Windows engine lands."""
    doc = _wheels_doc()
    legs = _wheel_legs(doc)
    assert legs["x86_64-pc-windows-msvc"]["experimental"] is True
    assert all("experimental" not in leg for t, leg in legs.items() if t != "x86_64-pc-windows-msvc")
    assert doc["jobs"]["wheels"]["continue-on-error"] == "${{ matrix.experimental || false }}"


def test_wheels_are_smoked_on_their_own_runner_before_publish():
    """Each leg whose runner can install its wheel runs wheel_smoke.sh on the built wheel; cross legs skip."""
    doc = _wheels_doc()
    legs = _wheel_legs(doc)
    assert {t for t, leg in legs.items() if leg["smoke"]} == NATIVE_SMOKE_TARGETS
    smoke = [s for s in doc["jobs"]["wheels"]["steps"] if "wheel_smoke.sh" in str(s.get("run", ""))]
    assert len(smoke) == 1 and smoke[0]["if"] == "matrix.smoke"
    assert "dist/*.whl" in smoke[0]["run"]


def test_sdist_is_built_by_maturin_and_publish_gates_on_every_artifact():
    """FR-006: one maturin sdist job; PyPI publish needs the wheels and the sdist and keeps Trusted Publishing."""
    doc = _wheels_doc()
    assert _maturin_step(doc["jobs"]["sdist"])["with"]["command"] == "sdist"
    publish = doc["jobs"]["publish-pypi"]
    assert set(publish["needs"]) == {"wheels", "sdist"}
    assert publish["permissions"] == {"id-token": "write"}
    assert any(str(s.get("uses", "")).startswith("pypa/gh-action-pypi-publish@") for s in publish["steps"])
