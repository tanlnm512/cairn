"""Bundled-binary discovery pins: CAIRN_RUST_BIN > bundled package binary > PATH.

Every case monkeypatches paths.BUNDLED_BIN_DIR into tmp_path, so the module is
independent of whether a wheel build has injected a real binary into the source
tree. No cargo, no network, no real exec; cairn.cli / cairn.mcp_server imports
stay inside the test functions.
"""
from __future__ import annotations

import os
import sys

import pytest

# Never executed -- only read back as bytes; the content just must not start
# with "#!" so it would pass the native-binary exclusion if a PATH leg saw it.
_STUB_BYTES = b"CAIRN-STUB-BINARY\x00"


def _write_stub_bundled_bin(monkeypatch, tmp_path):
    """Inject a stub engine binary as the bundled package-data location."""
    from cairn import paths

    bin_dir = tmp_path / "_pkg_bin"
    bin_dir.mkdir()
    stub = bin_dir / "cairn"
    stub.write_bytes(_STUB_BYTES)
    stub.chmod(0o755)
    monkeypatch.setattr(paths, "BUNDLED_BIN_DIR", bin_dir)
    return stub


def _patch_shim_env(monkeypatch, tmp_path, *, bundled: bool):
    """Point the console shim at a deterministic discovery state and capture both exits."""
    from cairn import paths
    from cairn.mcp_server import lifecycle as lc

    monkeypatch.delenv("CAIRN_RUST_BIN", raising=False)
    if bundled:
        _write_stub_bundled_bin(monkeypatch, tmp_path)
    else:
        monkeypatch.setattr(paths, "BUNDLED_BIN_DIR", tmp_path / "_no_bin")
        monkeypatch.setattr(lc, "shutil_which", lambda name: None)
        # rust_bin()'s ~/.local/bin leg reads HOME; pin it so a native binary
        # installed there can't flip this test's no-binary premise.
        monkeypatch.setenv("HOME", str(tmp_path))

    execved: list[tuple[str, list[str]]] = []

    def fake_execv(path, argv):
        execved.append((path, argv))
        sys.exit(0)  # the real execv never returns

    monkeypatch.setattr(os, "execv", fake_execv)
    cli_calls: list[list[str]] = []

    def fake_cli_main(args=None, **_):
        cli_calls.append(list(args) if args is not None else sys.argv[1:])

    import cairn.cli

    monkeypatch.setattr(cairn.cli, "main", fake_cli_main)
    return execved, cli_calls


def test_no_bundled_binary_falls_through_to_python(monkeypatch, tmp_path):
    """A source checkout (no injected binary) discovers nothing and keeps the python default."""
    from cairn import paths
    from cairn.mcp_server import lifecycle as lc

    monkeypatch.setattr(paths, "BUNDLED_BIN_DIR", tmp_path / "_no_bin")
    monkeypatch.setattr(lc, "shutil_which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert paths.bundled_binary() is None
    assert lc.rust_bin() is None
    assert lc.daemon_impl() == "python"


def test_bundled_binary_discovered_over_path(monkeypatch, tmp_path):
    """The bundled package binary wins over a native `cairn` on PATH: a wheel install execs its own version-locked engine."""
    from cairn.mcp_server import lifecycle as lc

    stub = _write_stub_bundled_bin(monkeypatch, tmp_path)
    decoy = tmp_path / "decoy-cairn"
    decoy.write_bytes(_STUB_BYTES)
    monkeypatch.setattr(lc, "shutil_which", lambda name: str(decoy))

    assert lc.rust_bin() == str(stub)
    assert lc.daemon_impl() == "rust"


def test_env_override_beats_bundled_binary(monkeypatch, tmp_path):
    """CAIRN_RUST_BIN stays first in the discovery order, ahead of the bundled leg."""
    from cairn.mcp_server import lifecycle as lc

    _write_stub_bundled_bin(monkeypatch, tmp_path)
    env_bin = tmp_path / "env-cairn"
    env_bin.write_bytes(_STUB_BYTES)
    monkeypatch.setenv("CAIRN_RUST_BIN", str(env_bin))

    assert lc.rust_bin() == str(env_bin)


def test_windows_console_launcher_keeps_python_default(monkeypatch, tmp_path):
    """On a Windows host the PATH legs never run: pip's native cairn.exe launcher must not flip the default to rust; CAIRN_RUST_BIN still overrides."""
    from cairn import paths
    from cairn.mcp_server import lifecycle as lc

    monkeypatch.setattr(paths, "BUNDLED_BIN_DIR", tmp_path / "_no_bin")
    monkeypatch.delenv("CAIRN_RUST_BIN", raising=False)
    # pip's Windows console script is a native PE launcher also named cairn(.exe);
    # it passes the #!-only native check, so only skipping the legs tells it apart.
    launcher = tmp_path / "cairn.exe"
    launcher.write_bytes(b"MZ\x90\x00" + b"\x00" * 64)
    monkeypatch.setattr(lc, "shutil_which", lambda name: str(launcher))

    monkeypatch.setattr(lc, "_is_windows_host", lambda: True)
    assert lc.rust_bin() is None
    assert lc.daemon_impl() == "python"

    env_bin = tmp_path / "env-cairn"
    env_bin.write_bytes(_STUB_BYTES)
    monkeypatch.setenv("CAIRN_RUST_BIN", str(env_bin))
    assert lc.rust_bin() == str(env_bin)

    monkeypatch.delenv("CAIRN_RUST_BIN", raising=False)
    monkeypatch.setattr(lc, "_is_windows_host", lambda: False)
    assert lc.rust_bin() == str(launcher)


def test_cli_default_start_execs_bundled_binary(monkeypatch, tmp_path):
    """A cold connect with no impl env starts the bundled binary by default; explicit python still wins."""
    from click.testing import CliRunner

    from cairn.cli import main
    from cairn.mcp_server import lifecycle as lc

    stub = _write_stub_bundled_bin(monkeypatch, tmp_path)
    monkeypatch.delenv("CAIRN_DAEMON_IMPL", raising=False)
    monkeypatch.setattr(lc, "is_macos", lambda: True)
    monkeypatch.setattr(lc, "healthz_json", lambda *a, **k: None)
    monkeypatch.setattr(lc, "sweep_strays", lambda *a, **k: 0)
    monkeypatch.setattr(lc, "is_loaded", lambda: False)
    monkeypatch.setattr(lc, "load", lambda: True)
    monkeypatch.setattr(lc, "healthz_responds", lambda *a, **k: True)
    monkeypatch.setattr(lc, "running_pid", lambda: 4321)
    written: list[dict] = []
    monkeypatch.setattr(lc, "write_plist", written.append)

    result = CliRunner().invoke(main, ["connect"])
    assert result.exit_code == 0, result.output
    assert written, "a cold connect with a bundled binary must start the daemon"
    assert written[-1]["ProgramArguments"][0] == str(stub)

    # The python fallback stays reachable: explicit CAIRN_DAEMON_IMPL wins.
    monkeypatch.setenv("CAIRN_DAEMON_IMPL", "python")
    written.clear()
    result = CliRunner().invoke(main, ["connect"])
    assert result.exit_code == 0, result.output
    assert written[-1]["ProgramArguments"][0] == lc.cg_bin()
    assert written[-1]["ProgramArguments"][0] != str(stub)


def test_console_script_execs_bundled_binary(monkeypatch, tmp_path):
    """The console entry execs the discovered binary with the original argv; the python CLI never runs."""
    from cairn import console

    execved, cli_calls = _patch_shim_env(monkeypatch, tmp_path, bundled=True)
    stub = tmp_path / "_pkg_bin" / "cairn"
    monkeypatch.setattr(sys, "argv", ["cairn", "config", "--json"])

    with pytest.raises(SystemExit):
        console.main()

    assert execved == [(str(stub), [str(stub), "config", "--json"])]
    assert not cli_calls


def test_console_script_env_override_execs(monkeypatch, tmp_path):
    """CAIRN_RUST_BIN stays the first discovery leg through the console shim."""
    from cairn import console

    execved, cli_calls = _patch_shim_env(monkeypatch, tmp_path, bundled=True)
    env_bin = tmp_path / "env-cairn"
    env_bin.write_bytes(_STUB_BYTES)
    monkeypatch.setenv("CAIRN_RUST_BIN", str(env_bin))
    monkeypatch.setattr(sys, "argv", ["cairn", "def", "x"])

    with pytest.raises(SystemExit):
        console.main()

    assert execved == [(str(env_bin), [str(env_bin), "def", "x"])]
    assert not cli_calls


def test_console_script_falls_back_to_python_without_binary(monkeypatch, tmp_path):
    """No discoverable binary (source checkouts, Windows wheels) keeps the console script on the python CLI."""
    from cairn import console

    execved, cli_calls = _patch_shim_env(monkeypatch, tmp_path, bundled=False)
    monkeypatch.setattr(sys, "argv", ["cairn", "doctor"])

    console.main()

    assert not execved
    assert cli_calls == [["doctor"]]


@pytest.mark.parametrize("cmd", ["prs", "bench", "eval", "communities"])
def test_console_script_keeps_pypi_only_commands_on_python(monkeypatch, tmp_path, cmd):
    """D-013/D-014: the sidecar-ruled commands stay on the python CLI even with a binary present."""
    from cairn import console

    execved, cli_calls = _patch_shim_env(monkeypatch, tmp_path, bundled=True)
    monkeypatch.setattr(sys, "argv", ["cairn", cmd, "--flag"])

    console.main()

    assert not execved
    assert cli_calls == [[cmd, "--flag"]]


def test_console_script_pypi_only_survives_verbose_prefix(monkeypatch, tmp_path):
    """The documented `-v <cmd>` spelling must not land sidecar commands on the binary's stub arm."""
    from cairn import console

    execved, cli_calls = _patch_shim_env(monkeypatch, tmp_path, bundled=True)
    spellings = [
        ["-v", "prs"],
        ["--verbose", "bench", "--flag"],
        ["-v", "communities"],
        ["-v", "eval"],
    ]

    for argv in spellings:
        execved.clear()
        cli_calls.clear()
        monkeypatch.setattr(sys, "argv", ["cairn", *argv])
        try:
            console.main()
        except SystemExit:
            pass

        assert not execved, argv
        assert cli_calls == [argv], argv


def test_console_script_survives_exec_failure(monkeypatch, tmp_path, capsys):
    """A bundled binary that fails to exec falls back to the python CLI instead of stranding the user."""
    from cairn import console

    execved, cli_calls = _patch_shim_env(monkeypatch, tmp_path, bundled=True)
    stub = tmp_path / "_pkg_bin" / "cairn"
    monkeypatch.setattr(os, "execv", lambda path, argv: (_ for _ in ()).throw(OSError("no exec")))
    monkeypatch.setattr(sys, "argv", ["cairn", "doctor"])

    console.main()

    assert cli_calls == [["doctor"]]
    assert str(stub) in capsys.readouterr().err
