"""Bundled-binary discovery pins: CAIRN_RUST_BIN > bundled package binary > PATH; the console shim execs it.

Every case monkeypatches paths.BUNDLED_BIN_DIR into tmp_path, so the module is
independent of whether a wheel build has injected a real binary into the source
tree. No cargo, no network, no real exec.
"""
from __future__ import annotations

import os
import sys

import pytest

from cairn import console, paths

# Never executed -- only read back as bytes; the content just must not start
# with "#!" so it would pass the native-binary exclusion if a PATH leg saw it.
_STUB_BYTES = b"CAIRN-STUB-BINARY\x00"


def _write_stub_bundled_bin(monkeypatch, tmp_path):
    """Inject a stub engine binary as the bundled package-data location."""
    bin_dir = tmp_path / "_pkg_bin"
    bin_dir.mkdir()
    stub = bin_dir / "cairn"
    stub.write_bytes(_STUB_BYTES)
    stub.chmod(0o755)
    monkeypatch.setattr(paths, "BUNDLED_BIN_DIR", bin_dir)
    return stub


def _no_binary_anywhere(monkeypatch, tmp_path):
    monkeypatch.delenv("CAIRN_RUST_BIN", raising=False)
    monkeypatch.setattr(paths, "BUNDLED_BIN_DIR", tmp_path / "_no_bin")
    monkeypatch.setattr(paths, "shutil_which", lambda name: None)
    # rust_bin()'s ~/.local/bin leg reads HOME; pin it so a native binary
    # installed there can't flip the no-binary premise.
    monkeypatch.setenv("HOME", str(tmp_path))


def _capture_exec(monkeypatch):
    execved: list[tuple[str, list[str]]] = []

    def fake_execv(path, argv):
        execved.append((path, argv))
        sys.exit(0)  # the real execv never returns

    monkeypatch.setattr(os, "execv", fake_execv)
    return execved


def test_no_bundled_binary_discovers_nothing(monkeypatch, tmp_path):
    """A source checkout (no injected binary) discovers nothing."""
    _no_binary_anywhere(monkeypatch, tmp_path)

    assert paths.bundled_binary() is None
    assert paths.rust_bin() is None


def test_bundled_binary_discovered_over_path(monkeypatch, tmp_path):
    """The bundled package binary wins over a native `cairn` on PATH: a wheel install execs its own version-locked engine."""
    monkeypatch.delenv("CAIRN_RUST_BIN", raising=False)
    stub = _write_stub_bundled_bin(monkeypatch, tmp_path)
    decoy = tmp_path / "decoy-cairn"
    decoy.write_bytes(_STUB_BYTES)
    monkeypatch.setattr(paths, "shutil_which", lambda name: str(decoy))

    assert paths.rust_bin() == str(stub)


def test_env_override_beats_bundled_binary(monkeypatch, tmp_path):
    """CAIRN_RUST_BIN stays first in the discovery order, ahead of the bundled leg."""
    _write_stub_bundled_bin(monkeypatch, tmp_path)
    env_bin = tmp_path / "env-cairn"
    env_bin.write_bytes(_STUB_BYTES)
    monkeypatch.setenv("CAIRN_RUST_BIN", str(env_bin))

    assert paths.rust_bin() == str(env_bin)


def test_path_leg_skips_the_console_script(monkeypatch, tmp_path):
    """A `#!` console script named cairn on PATH is not an engine; a native one is."""
    _no_binary_anywhere(monkeypatch, tmp_path)
    script = tmp_path / "cairn"
    script.write_bytes(b"#!/usr/bin/env python\n")
    monkeypatch.setattr(paths, "shutil_which", lambda name: str(script))
    assert paths.rust_bin() is None

    script.write_bytes(_STUB_BYTES)
    assert paths.rust_bin() == str(script)


def test_windows_console_launcher_is_never_an_engine(monkeypatch, tmp_path):
    """On a Windows host the PATH legs never run: pip's native cairn.exe launcher must not be taken for an engine; CAIRN_RUST_BIN still overrides."""
    _no_binary_anywhere(monkeypatch, tmp_path)
    # pip's Windows console script is a native PE launcher also named cairn(.exe);
    # it passes the #!-only native check, so only skipping the legs tells it apart.
    launcher = tmp_path / "cairn.exe"
    launcher.write_bytes(b"MZ\x90\x00" + b"\x00" * 64)
    monkeypatch.setattr(paths, "shutil_which", lambda name: str(launcher))

    monkeypatch.setattr(paths, "_is_windows_host", lambda: True)
    assert paths.rust_bin() is None

    env_bin = tmp_path / "env-cairn"
    env_bin.write_bytes(_STUB_BYTES)
    monkeypatch.setenv("CAIRN_RUST_BIN", str(env_bin))
    assert paths.rust_bin() == str(env_bin)

    monkeypatch.delenv("CAIRN_RUST_BIN", raising=False)
    monkeypatch.setattr(paths, "_is_windows_host", lambda: False)
    assert paths.rust_bin() == str(launcher)


def test_console_script_execs_bundled_binary(monkeypatch, tmp_path):
    """The console entry execs the discovered binary with the original argv."""
    monkeypatch.delenv("CAIRN_RUST_BIN", raising=False)
    stub = _write_stub_bundled_bin(monkeypatch, tmp_path)
    execved = _capture_exec(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["cairn", "config", "--json"])

    with pytest.raises(SystemExit):
        console.main()

    assert execved == [(str(stub), [str(stub), "config", "--json"])]


def test_console_script_hands_its_interpreter_to_the_engine(monkeypatch, tmp_path):
    """The exec'd engine learns which interpreter carries the sidecar extras; an explicit value is kept."""
    monkeypatch.delenv("CAIRN_RUST_BIN", raising=False)
    monkeypatch.delenv("CAIRN_SIDECAR_PYTHON", raising=False)
    _write_stub_bundled_bin(monkeypatch, tmp_path)
    seen: list[str | None] = []

    def fake_execv(path, argv):
        seen.append(os.environ.get("CAIRN_SIDECAR_PYTHON"))
        sys.exit(0)

    monkeypatch.setattr(os, "execv", fake_execv)
    monkeypatch.setattr(sys, "argv", ["cairn", "knowledge", "ingest"])
    with pytest.raises(SystemExit):
        console.main()
    assert seen == [sys.executable]

    monkeypatch.setenv("CAIRN_SIDECAR_PYTHON", "/opt/other/python")
    with pytest.raises(SystemExit):
        console.main()
    assert seen[-1] == "/opt/other/python"


def test_console_script_env_override_execs(monkeypatch, tmp_path):
    """CAIRN_RUST_BIN stays the first discovery leg through the console shim."""
    _write_stub_bundled_bin(monkeypatch, tmp_path)
    execved = _capture_exec(monkeypatch)
    env_bin = tmp_path / "env-cairn"
    env_bin.write_bytes(_STUB_BYTES)
    monkeypatch.setenv("CAIRN_RUST_BIN", str(env_bin))
    monkeypatch.setattr(sys, "argv", ["cairn", "def", "x"])

    with pytest.raises(SystemExit):
        console.main()

    assert execved == [(str(env_bin), [str(env_bin), "def", "x"])]


def test_console_script_without_binary_fails_loudly(monkeypatch, tmp_path, capsys):
    """No discoverable binary is an error naming the fix, exit 1: no Python engine exists to fall back to."""
    _no_binary_anywhere(monkeypatch, tmp_path)
    execved = _capture_exec(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["cairn", "doctor"])

    with pytest.raises(SystemExit) as exc:
        console.main()

    assert exc.value.code == 1
    assert not execved
    err = capsys.readouterr().err
    assert "no engine binary" in err and "CAIRN_RUST_BIN" in err


def test_console_script_without_binary_on_windows_points_at_wsl(monkeypatch, tmp_path, capsys):
    """On a Windows host the no-engine message names WSL: no native Windows engine ships yet."""
    _no_binary_anywhere(monkeypatch, tmp_path)
    monkeypatch.setattr(paths, "_is_windows_host", lambda: True)
    execved = _capture_exec(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["cairn", "doctor"])

    with pytest.raises(SystemExit) as exc:
        console.main()

    assert exc.value.code == 1
    assert not execved
    err = capsys.readouterr().err
    assert "no engine binary" in err and "WSL" in err


@pytest.mark.parametrize(
    "argv",
    [["communities"], ["communities", "--top-k", "3"], ["-v", "communities"], ["--verbose", "communities", "--db", "x.kg"]],
)
def test_console_script_execs_communities_into_the_engine(monkeypatch, tmp_path, argv):
    """The engine serves `communities` natively: argv is exec'd like every other command."""
    from cairn import communities

    def _must_not_run(rest):
        raise AssertionError("communities sidecar must not be invoked")

    monkeypatch.setattr(communities, "main", _must_not_run)
    stub = _write_stub_bundled_bin(monkeypatch, tmp_path)
    execved = _capture_exec(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["cairn", *argv])

    with pytest.raises(SystemExit):
        console.main()

    assert execved == [(str(stub), [str(stub), *argv])]


def test_console_script_reports_exec_failure(monkeypatch, tmp_path, capsys):
    """A bundled binary that fails to exec is reported with its path, exit 1."""
    monkeypatch.delenv("CAIRN_RUST_BIN", raising=False)
    stub = _write_stub_bundled_bin(monkeypatch, tmp_path)
    monkeypatch.setattr(os, "execv", lambda path, argv: (_ for _ in ()).throw(OSError("no exec")))
    monkeypatch.setattr(sys, "argv", ["cairn", "doctor"])

    with pytest.raises(SystemExit) as exc:
        console.main()

    assert exc.value.code == 1
    assert str(stub) in capsys.readouterr().err
