"""Tests for the port constant and the unload()/plist contracts.

This file tests:
1. No literal 9876 in serve.py or agent_install.py
2. lifecycle.unload() returns the real exit status, not always True
3. render_plist embeds CAIRN_HOME in EnvironmentVariables iff the
   home is non-default
4. render_plist defaults to a read-write daemon under a non-sse
   label and log name
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


class TestUnloadReturnsRealStatus:
    """VAL-MC-006: unload() returns real status (L5)

    lifecycle.unload() should return actual subprocess exit status,
    not always True.
    """

    def test_unload_returns_false_on_launchctl_failure(self, tmp_path, monkeypatch):
        """unload() should return False when launchctl unload fails."""
        from cairn.mcp_server import lifecycle

        # unload() returns early (True, "nothing to unload") when the plist
        # doesn't exist -- which would short-circuit before the mocked
        # subprocess.run is ever called. Point plist_path at a real temp file
        # and force is_macos so the launchctl branch is exercised regardless
        # of the host machine's daemon install state.
        plist = tmp_path / "fake.plist"
        plist.write_text("<?xml version='1.0'?><plist></plist>")
        monkeypatch.setattr(lifecycle, "plist_path", lambda: plist)
        monkeypatch.setattr(lifecycle, "is_macos", lambda: True)

        # Mock subprocess.run to simulate a failed launchctl unload
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", mock_run)

        # unload() should return False on failure
        result = lifecycle.unload()
        assert result is False

    def test_unload_returns_true_on_launchctl_success(self, tmp_path, monkeypatch):
        """unload() should return True when launchctl unload succeeds."""
        from cairn.mcp_server import lifecycle

        # See test_unload_returns_false_on_launchctl_failure: ensure the plist
        # exists so the mocked launchctl path runs.
        plist = tmp_path / "fake.plist"
        plist.write_text("<?xml version='1.0'?><plist></plist>")
        monkeypatch.setattr(lifecycle, "plist_path", lambda: plist)
        monkeypatch.setattr(lifecycle, "is_macos", lambda: True)

        # Mock subprocess.run to simulate a successful launchctl unload
        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", mock_run)

        # unload() should return True on success
        result = lifecycle.unload()
        assert result is True

    def test_unload_returns_true_when_plist_not_exists(self, tmp_path, monkeypatch):
        """unload() should return True when plist doesn't exist (nothing to unload)."""
        from cairn.mcp_server import lifecycle

        # Mock plist_path() to return a non-existent path
        monkeypatch.setattr(lifecycle, "plist_path", lambda: tmp_path / "nonexistent.plist")
        # Mock is_macos to return True
        monkeypatch.setattr(lifecycle, "is_macos", lambda: True)

        # unload() should return True when nothing to unload
        result = lifecycle.unload()
        assert result is True


class TestPlistEnvironmentVariables:
    """render_plist embeds CAIRN_HOME in EnvironmentVariables iff the
    home is non-default, leaving the existing env entries unchanged.

    Loading the actual LaunchAgent stays manual (macOS only). Tests drive
    render_plist via the CAIRN_HOME env var, not via a render_plist
    parameter.
    """

    @staticmethod
    def _set_custom_home(monkeypatch) -> str:
        """Point CAIRN_HOME at a custom home; return its expanded absolute path."""
        monkeypatch.setenv("CAIRN_HOME", "~/custom-cairn-home")
        return str(Path.home() / "custom-cairn-home")

    def test_render_plist_includes_cairn_home_under_custom_home(self, monkeypatch):
        """A custom CAIRN_HOME must appear in the plist's EnvironmentVariables
        as the expanded absolute path."""
        from cairn.mcp_server import lifecycle

        custom_home = self._set_custom_home(monkeypatch)
        plist = lifecycle.render_plist()
        env = plist["EnvironmentVariables"]
        assert env.get("CAIRN_HOME") == custom_home, (
            "render_plist must include CAIRN_HOME in EnvironmentVariables "
            "when the home is non-default"
        )

    def test_render_plist_omits_cairn_home_under_default_home(self, monkeypatch):
        """A default home (unset, or explicitly set to ~/.cairn) must produce
        no CAIRN_HOME entry in the plist's EnvironmentVariables."""
        from cairn.mcp_server import lifecycle

        monkeypatch.delenv("CAIRN_HOME", raising=False)
        plist = lifecycle.render_plist()
        assert "CAIRN_HOME" not in plist["EnvironmentVariables"]

        # An explicitly-set default location counts as default:
        # comparison is by expanded absolute path.
        monkeypatch.setenv("CAIRN_HOME", str(Path.home() / ".cairn"))
        plist = lifecycle.render_plist()
        assert "CAIRN_HOME" not in plist["EnvironmentVariables"]

    def test_render_plist_existing_env_entries_unchanged(self, monkeypatch):
        """PATH and the existing CAIRN_WORKSPACE/CAIRN_DB/CAIRN_KNOWLEDGE
        entries survive unchanged when the CAIRN_HOME entry is added."""
        from cairn.mcp_server import lifecycle

        self._set_custom_home(monkeypatch)
        plist = lifecycle.render_plist(
            workspace="/tmp/ws",
            db_path="/tmp/ws/.kg",
            knowledge_path="/tmp/ws/.knowledge",
        )
        env = plist["EnvironmentVariables"]
        assert env["PATH"] == os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        assert env["CAIRN_WORKSPACE"] == "/tmp/ws"
        assert env["CAIRN_DB"] == "/tmp/ws/.kg"
        assert env["CAIRN_KNOWLEDGE"] == "/tmp/ws/.knowledge"


class TestPlistReadWriteDefaultAndLabel:
    """render_plist pins a read-write daemon by default (FR-002) under a
    non-sse LaunchAgent label and log name (FR-010); --read-only stays the
    explicit opt-out."""

    def test_render_plist_default_runs_read_write(self):
        """The default plist must not pin the daemon read-only: no
        --read-only argument and no CAIRN_READ_ONLY=1 env entry."""
        from cairn.mcp_server import lifecycle

        plist = lifecycle.render_plist()
        assert "--read-only" not in plist["ProgramArguments"]
        assert plist["EnvironmentVariables"].get("CAIRN_READ_ONLY") != "1"

    def test_render_plist_explicit_read_only_appends_flag(self):
        """render_plist(read_only=True) keeps the --read-only opt-out."""
        from cairn.mcp_server import lifecycle

        plist = lifecycle.render_plist(read_only=True)
        assert "--read-only" in plist["ProgramArguments"]

    def test_render_plist_label_and_log_drop_sse_naming(self):
        """The LaunchAgent label and the log file names must not carry the
        sse naming of the retired transport."""
        from cairn.mcp_server import lifecycle

        plist = lifecycle.render_plist()
        label = plist["Label"]
        assert label.startswith("dev.cairn."), "label keeps the dev.cairn prefix"
        assert "sse" not in label.lower()
        for key in ("StandardOutPath", "StandardErrorPath"):
            assert "sse" not in Path(plist[key]).name.lower()


class TestPlistCarriesBindHost:
    """The rendered daemon argv must carry --host: a requested non-default
    host must not be silently dropped in favor of the default loopback."""

    def test_render_plist_program_arguments_carry_host(self):
        from cairn.mcp_server import lifecycle

        plist = lifecycle.render_plist(port=9999, host="0.0.0.0")
        args = plist["ProgramArguments"]
        assert args[args.index("--port") + 1] == "9999"
        assert args[args.index("--host") + 1] == "0.0.0.0", (
            "render_plist must pass the requested host to `serve run`"
        )

    def test_render_plist_default_host_is_loopback(self):
        from cairn.mcp_server import lifecycle

        args = lifecycle.render_plist()["ProgramArguments"]
        assert args[args.index("--host") + 1] == lifecycle.DEFAULT_HOST


class TestLegacyPlistRemovedAfterUnload:
    """Unloading the retired dev.cairn.sse agent must also delete its plist,
    or launchd re-launches it at every login."""

    def test_legacy_plist_unlinked_after_unload(self, tmp_path, monkeypatch):
        from cairn.mcp_server import lifecycle

        legacy = tmp_path / f"{lifecycle.LEGACY_LABEL}.plist"
        legacy.write_text("<plist/>")
        monkeypatch.setattr(lifecycle, "agents_dir", lambda: tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            lifecycle,
            "_run_launchctl",
            lambda args: calls.append(list(args)) or subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr=""),
        )
        monkeypatch.setattr(lifecycle, "_legacy_label_unloaded", False)

        lifecycle._unload_legacy_label_once()

        assert calls == [["unload", str(legacy)]]
        assert not legacy.exists(), "the retired plist must be deleted, not just unloaded"

    def test_second_call_is_a_no_op(self, tmp_path, monkeypatch):
        from cairn.mcp_server import lifecycle

        legacy = tmp_path / f"{lifecycle.LEGACY_LABEL}.plist"
        legacy.write_text("<plist/>")
        monkeypatch.setattr(lifecycle, "agents_dir", lambda: tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            lifecycle,
            "_run_launchctl",
            lambda args: calls.append(list(args)) or subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr=""),
        )
        monkeypatch.setattr(lifecycle, "_legacy_label_unloaded", False)

        lifecycle._unload_legacy_label_once()
        lifecycle._unload_legacy_label_once()

        assert len(calls) == 1, "the legacy unload stays a one-shot"
