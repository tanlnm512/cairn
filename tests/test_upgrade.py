"""Tests for the `version` / `upgrade` CLI commands.

All network and subprocess calls are stubbed -- no real PyPI lookup, no real
reinstall. We monkeypatch the module-level helpers in upgrade.py
(`_installed_version`, `_pypi_latest`, `_detect_install_method`, `_reinstall`)
and `subprocess.run` for the install-method sniff.
"""
from __future__ import annotations

import subprocess
import sys

import pytest
from click.testing import CliRunner

from cairn.cli import main
from cairn.cli import upgrade as upgrade_mod


# --- Fixtures --------------------------------------------------------------

def _set_versions(monkeypatch, installed=None, latest=None, reinstall=None):
    """Stub the upgrade helpers. `reinstall` records calls instead of shelling.

    `installed`/`latest` are always patched when the helper is invoked -- using
    a sentinel instead of `if x is not None` so that `latest=None` (the very
    value the "PyPI unreachable" tests exercise) still replaces the real
    ``_pypi_latest`` rather than letting it hit the network. Without this,
    those tests leaked a real call to pypi.org and only stayed green while the
    project was unpublished.
    """
    _UNSET = object()
    calls = []
    if installed is not _UNSET:
        monkeypatch.setattr(upgrade_mod, "_installed_version", lambda: installed)
    if latest is not _UNSET:
        monkeypatch.setattr(upgrade_mod, "_pypi_latest", lambda: latest)
    if reinstall is None:
        def reinstall(method, version):
            calls.append((method, version))
            return True
    monkeypatch.setattr(upgrade_mod, "_reinstall", reinstall)
    return calls


def _stub_run(stdout="", returncode=0):
    """Return a fake subprocess.run that yields a CompletedProcess with stdout."""
    def fake_run(cmd, *rest, **kw):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
    return fake_run


# --- `cairn version` -------------------------------------------------------

def test_version_prints_installed(monkeypatch):
    monkeypatch.setattr(
        "importlib.metadata.version", lambda name: "1.2.3"
    )
    runner = CliRunner()
    result = runner.invoke(main, ["version"])
    assert result.exit_code == 0
    assert "1.2.3" in result.output


def test_version_falls_back_to_source_checkout(monkeypatch):
    import importlib.metadata as md
    monkeypatch.setattr(
        md, "version", lambda name: (_ for _ in ()).throw(ModuleNotFoundError("nope"))
    )
    # Also force the lazy import inside `version()` to raise by pointing the
    # looked-up name at something missing.
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda name: (_ for _ in ()).throw(ModuleNotFoundError("nope")),
    )
    runner = CliRunner()
    result = runner.invoke(main, ["version"])
    assert result.exit_code == 0
    # Falls back to cairn.__version__ with a "(source checkout)" marker.
    assert "source checkout" in result.output


# --- `cairn upgrade --check` ----------------------------------------------

def test_upgrade_check_shows_latest(monkeypatch):
    _set_versions(monkeypatch, installed="0.6.0", latest="0.7.0")
    runner = CliRunner()
    result = runner.invoke(main, ["upgrade", "--check"])
    assert result.exit_code == 0
    assert "0.6.0" in result.output
    assert "0.7.0" in result.output
    assert "latest" in result.output


def test_upgrade_check_when_already_latest(monkeypatch):
    _set_versions(monkeypatch, installed="0.7.0", latest="0.7.0")
    runner = CliRunner()
    result = runner.invoke(main, ["upgrade", "--check"])
    assert result.exit_code == 0
    # --check prints both versions regardless of equality.
    assert "0.7.0" in result.output


def test_upgrade_check_pypi_unreachable(monkeypatch):
    _set_versions(monkeypatch, installed="0.6.0", latest=None)
    runner = CliRunner()
    result = runner.invoke(main, ["upgrade", "--check"])
    assert result.exit_code == 0
    assert "0.6.0" in result.output
    assert "PyPI" in result.output


# --- `cairn upgrade` (without --check) ------------------------------------

def test_upgrade_noop_when_already_up_to_date(monkeypatch):
    calls = _set_versions(monkeypatch, installed="0.7.0", latest="0.7.0")
    runner = CliRunner()
    result = runner.invoke(main, ["upgrade"])
    assert result.exit_code == 0
    assert "already up to date" in result.output
    assert calls == []  # no reinstall attempted


def test_upgrade_runs_reinstall_when_newer(monkeypatch):
    calls = _set_versions(monkeypatch, installed="0.6.0", latest="0.7.0")
    monkeypatch.setattr(upgrade_mod, "_detect_install_method", lambda: "uv")
    runner = CliRunner()
    result = runner.invoke(main, ["upgrade"])
    assert result.exit_code == 0
    assert calls == [("uv", "0.7.0")]
    assert "0.6.0" in result.output
    assert "0.7.0" in result.output


def test_upgrade_when_pypi_unreachable(monkeypatch):
    _set_versions(monkeypatch, installed="0.6.0", latest=None)
    runner = CliRunner()
    result = runner.invoke(main, ["upgrade"])
    assert result.exit_code == 0
    assert "PyPI unreachable" in result.output
    # Message may be terminal-wrapped in non-TTY mode, so check the pieces
    # rather than the exact "pip install --upgrade" substring.
    assert "pip install" in result.output
    assert "--upgrade" in result.output


# --- reinstall output (quiet progress, no installer spam) ------------------
#
# Regression guard: _reinstall used to hand the terminal straight to
# pipx/uv/pip (`subprocess.run` with inherited stdout), so an upgrade dumped
# 50+ lines of installer noise -- venv creation, every "Collecting /
# Downloading / Requirement already satisfied" line, install progress bars.
# The reinstall now runs under the same single-line progress helper the
# `embed --install-deps` path uses (graph.embeddings._run_subprocess_with_
# progress): output is drained silently behind one progress line and shown
# only on failure. The subprocess is faked at that same seam (never by
# patching subprocess.Popen globally -- the mcp package evaluates
# `subprocess.Popen[...]` annotations at import time and explodes under a
# patched Popen).

@pytest.fixture
def fake_quiet_run(monkeypatch):
    """Replace the shared quiet-subprocess helper; record calls."""
    from cairn.graph import embeddings

    calls = []

    def _run(cmd, description, env=None):
        calls.append({"cmd": cmd, "description": description})
        return ""

    monkeypatch.setattr(embeddings, "_run_subprocess_with_progress", _run)
    return calls


def test_reinstall_runs_quiet_with_one_progress_line(fake_quiet_run, capsys):
    """The installer runs behind the quiet helper, not raw subprocess.run."""
    assert upgrade_mod._reinstall("pipx", "9.9.9") is True

    assert len(fake_quiet_run) == 1
    assert fake_quiet_run[0]["cmd"] == [
        "pipx", "install", "--force", "cairn-intel==9.9.9",
    ]
    # The user still sees WHAT is about to run and a final result line --
    # silence about intent would be its own regression (see the 0.14.1
    # dead-silent-import fixes).
    out = capsys.readouterr().out
    assert "pipx install --force cairn-intel==9.9.9" in out
    assert "Upgraded cairn-intel -> 9.9.9" in out


@pytest.mark.parametrize(
    "method, prefix",
    [
        ("uv", ["uv", "tool", "install", "--force"]),
        ("pipx", ["pipx", "install", "--force"]),
        ("pip", [sys.executable, "-m", "pip", "install", "--upgrade"]),
    ],
)
def test_reinstall_method_commands(fake_quiet_run, method, prefix):
    """Every install method goes through the quiet helper with its own cmd."""
    assert upgrade_mod._reinstall(method, "9.9.9") is True
    cmd = fake_quiet_run[0]["cmd"]
    assert cmd[: len(prefix)] == prefix
    assert cmd[-1] == "cairn-intel==9.9.9"


def test_reinstall_failure_surfaces_child_output_and_manual_hint(monkeypatch, capsys):
    """A failed upgrade shows the captured installer output + manual command.

    Quiet must never mean opaque: the helper prints the child's captured
    output before raising, and _reinstall converts that into a warning with
    the exact manual re-run command instead of raising past the user.
    """
    from cairn.graph import embeddings

    def _failing_run(cmd, description, env=None):
        child = "ERROR: no matching distribution for cairn-intel==9.9.9\n"
        print(child)
        raise subprocess.CalledProcessError(1, cmd, child)

    monkeypatch.setattr(embeddings, "_run_subprocess_with_progress", _failing_run)

    assert upgrade_mod._reinstall("pipx", "9.9.9") is False

    out = capsys.readouterr().out
    assert "no matching distribution" in out
    assert "pipx install --force cairn-intel==9.9.9" in out


def test_reinstall_unknown_method_warns_without_subprocess(fake_quiet_run, capsys):
    assert upgrade_mod._reinstall("unknown", "9.9.9") is False
    assert fake_quiet_run == []
    assert "Cannot auto-upgrade" in capsys.readouterr().out


def test_upgrade_command_exits_nonzero_when_reinstall_fails(monkeypatch):
    """End-to-end: a failed reinstall makes `cairn upgrade` exit 1."""
    _set_versions(
        monkeypatch,
        installed="0.1.0",
        latest="9.9.9",
        reinstall=lambda method, version: False,
    )
    monkeypatch.setattr(upgrade_mod, "_detect_install_method", lambda: "pipx")

    runner = CliRunner()
    result = runner.invoke(main, ["upgrade"])

    assert result.exit_code == 1
    # The command-level intent line is still shown before the reinstall.
    assert "Upgrading cairn-intel 0.1.0 -> 9.9.9 (via pipx)" in result.output


# --- install-method detection ---------------------------------------------

def test_detect_install_method_uv(monkeypatch):
    monkeypatch.setattr("subprocess.run", _stub_run(stdout="cairn-intel v0.6.0\n"))
    assert upgrade_mod._detect_install_method() == "uv"


def test_detect_install_method_pipx(monkeypatch):
    # First call (uv tool list) returns nothing; second (pipx) matches.
    def fake_run(cmd, *rest, **kw):
        stdout = "" if cmd[0] == "uv" else "package cairn-intel 0.6.0\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
    monkeypatch.setattr("subprocess.run", fake_run)
    assert upgrade_mod._detect_install_method() == "pipx"


def test_detect_install_method_pip(monkeypatch):
    monkeypatch.setattr("subprocess.run", _stub_run(stdout=""))
    monkeypatch.setattr(upgrade_mod.sys, "executable", "/home/me/venv/bin/python")
    assert upgrade_mod._detect_install_method() == "pip"


def test_detect_install_method_unknown(monkeypatch):
    monkeypatch.setattr("subprocess.run", _stub_run(stdout=""))
    monkeypatch.setattr(upgrade_mod.sys, "executable", "/usr/local/bin/python")
    assert upgrade_mod._detect_install_method() == "unknown"


# --- PEP 440 version comparison -------------------------------------------

def test_is_up_to_date_string_equal():
    assert upgrade_mod._is_up_to_date("0.6.0", "0.6.0") is True


def test_is_up_to_date_local_newer():
    assert upgrade_mod._is_up_to_date("0.7.0", "0.6.0") is True


def test_is_up_to_date_local_older():
    assert upgrade_mod._is_up_to_date("0.6.0", "0.7.0") is False


def test_is_up_to_date_local_vs_release_candidate():
    # 0.6.0 final is newer than 0.6.0rc1.
    assert upgrade_mod._is_up_to_date("0.6.0", "0.6.0rc1") is True


def test_is_up_to_date_post_release_is_newer():
    # 0.6.0.post1 is newer than 0.6.0.
    assert upgrade_mod._is_up_to_date("0.6.0", "0.6.0.post1") is False


def test_is_up_to_date_local_version_segment():
    # 0.6.0+local is equal-ish to 0.6.0 (local segment doesn't change ordering).
    assert upgrade_mod._is_up_to_date("0.6.0+local", "0.6.0") is True
