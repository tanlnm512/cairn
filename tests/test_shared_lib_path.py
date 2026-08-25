"""Tests for the shared semantic lib dir's ABI scoping (~/.cairn/lib/cpXY).

Regression guard: the lib dir used to be a single flat `~/.cairn/lib`
populated with `pip install --target`. The semantic stack ships
ABI-specific wheels (a cp311 `_regex`/torch .so cannot load under cp314 and
vice versa), so the moment two interpreters installed into the same dir
(e.g. a 3.11 dev venv and a 3.14 pipx install on one machine) the directory
held a mix of ABI-incompatible binaries that no re-run of pip could repair
(pip --target skips packages already present at a satisfying version, so it
"reports success" while the dir stays broken and every later install dies
in verification). The dir is now scoped per interpreter ABI, with the
legacy flat dir kept on sys.path AFTER it so a pre-existing
single-interpreter install keeps working without a reinstall.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cairn import paths


def _abi_dir(home: Path) -> Path:
    tag = f"cp{sys.version_info[0]}{sys.version_info[1]}"
    return home / "lib" / tag


@pytest.fixture(autouse=True)
def _no_lib_override(monkeypatch):
    monkeypatch.delenv("CAIRN_LIB", raising=False)


@pytest.fixture
def snapshot_sys_path():
    """Restore sys.path around injection tests."""
    before = list(sys.path)
    yield
    sys.path[:] = before


def test_default_lib_dir_is_scoped_to_interpreter_abi(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SHARED_LIB", tmp_path / "lib")
    assert paths.shared_lib_path() == _abi_dir(tmp_path)


def test_cairn_lib_override_is_returned_verbatim(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SHARED_LIB", tmp_path / "lib")
    monkeypatch.setenv("CAIRN_LIB", str(tmp_path / "pinned"))
    # No ABI suffix appended: the override is explicit and exact.
    assert paths.shared_lib_path() == tmp_path / "pinned"


def test_sys_path_puts_abi_dir_before_legacy_flat_dir(
    tmp_path, monkeypatch, snapshot_sys_path
):
    abi = _abi_dir(tmp_path)
    flat = tmp_path / "lib"
    # mkdir(parents=True) on the abi dir creates the flat parent as a side
    # effect, so create flat first with exist_ok for either order.
    flat.mkdir(parents=True, exist_ok=True)
    abi.mkdir(parents=True)
    monkeypatch.setattr(paths, "SHARED_LIB", flat)

    paths._inject_shared_libs()

    # First match on sys.path wins, so the ABI dir must shadow the legacy
    # flat dir, not the other way around.
    assert sys.path[0] == str(abi)
    assert sys.path[1] == str(flat)


def test_sys_path_injection_is_noop_for_absent_dirs(
    tmp_path, monkeypatch, snapshot_sys_path
):
    monkeypatch.setattr(paths, "SHARED_LIB", tmp_path / "lib")  # nothing created
    before = list(sys.path)
    paths._inject_shared_libs()
    assert sys.path == before


def test_sys_path_with_override_injects_only_the_override(
    tmp_path, monkeypatch, snapshot_sys_path
):
    flat = tmp_path / "lib"
    pinned = tmp_path / "pinned"
    flat.mkdir()
    pinned.mkdir()
    monkeypatch.setattr(paths, "SHARED_LIB", flat)
    monkeypatch.setenv("CAIRN_LIB", str(pinned))

    paths._inject_shared_libs()

    assert str(pinned) in sys.path
    # A pinned override must not also drag the default flat dir into scope.
    assert str(flat) not in sys.path
