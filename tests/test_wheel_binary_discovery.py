"""Bundled-binary discovery pins: CAIRN_RUST_BIN > bundled package binary > PATH.

Every case monkeypatches paths.BUNDLED_BIN_DIR into tmp_path, so the module is
independent of whether a wheel build has injected a real binary into the source
tree. No cargo, no network, no real exec; cairn.cli / cairn.mcp_server imports
stay inside the test functions.
"""
from __future__ import annotations

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


def test_no_bundled_binary_falls_through_to_python(monkeypatch, tmp_path):
    """A source checkout (no injected binary) discovers nothing and keeps the python default."""
    from cairn import paths
    from cairn.mcp_server import lifecycle as lc

    monkeypatch.setattr(paths, "BUNDLED_BIN_DIR", tmp_path / "_no_bin")
    monkeypatch.setattr(lc, "shutil_which", lambda name: None)

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
