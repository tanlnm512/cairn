"""Tests for L4 (port constant DRY) and L5 (unload() returns real status) audit findings.

This file tests:
1. L4: No literal 9876 in serve.py or agent_install.py (VAL-MC-005)
2. L5: lifecycle.unload() returns real exit status, not always True (VAL-MC-006)
"""
from __future__ import annotations

import subprocess
from pathlib import Path



class TestPortConstantDRY:
    """VAL-MC-005: Port constant DRY (L4)

    Literal 9876 should be replaced with lifecycle.DEFAULT_PORT imports.
    """

    def test_serve_py_no_literal_9876(self):
        """serve.py should not contain the literal 9876.

        The port should be imported from lifecycle.DEFAULT_PORT instead.
        """
        serve_py = Path(__file__).parent.parent / "src" / "cairn" / "cli" / "serve.py"
        content = serve_py.read_text(encoding="utf-8")
        # Check that no bare "9876" appears (not part of longer numbers)
        # We need to be careful to match the literal 9876, not things like "9876" inside
        # other numbers or IDs
        lines_with_9876 = [line for line in content.splitlines() if "9876" in line]
        
        # Filter out lines that are only in comments/docstrings about 9876 being the default
        # The audit finding specifically says we should import DEFAULT_PORT instead
        # So even in docstrings, we should reference the constant
        assert len(lines_with_9876) == 0, (
            f"Found literal 9876 in serve.py. These lines should import lifecycle.DEFAULT_PORT:\n"
            f"{chr(10).join(lines_with_9876)}"
        )

    def test_agent_install_py_no_literal_9876(self):
        """No file in the agent_install package should contain the literal 9876.

        After the Phase 1.3 split, agent_install is a package
        (src/cairn/agent_install/). The port must be imported from
        lifecycle.DEFAULT_PORT in every module, so scan them all.
        """
        pkg_dir = Path(__file__).parent.parent / "src" / "cairn" / "agent_install"
        lines_with_9876 = []
        for src in sorted(pkg_dir.rglob("*.py")):
            for line in src.read_text(encoding="utf-8").splitlines():
                if "9876" in line:
                    lines_with_9876.append(f"{src.name}: {line}")

        assert len(lines_with_9876) == 0, (
            f"Found literal 9876 in agent_install/. These lines should import lifecycle.DEFAULT_PORT:\n"
            f"{chr(10).join(lines_with_9876)}"
        )

    def test_lifecycle_has_default_port_constant(self):
        """lifecycle.py should define DEFAULT_PORT constant."""
        lifecycle_py = Path(__file__).parent.parent / "src" / "cairn" / "mcp_server" / "lifecycle.py"
        content = lifecycle_py.read_text(encoding="utf-8")
        assert "DEFAULT_PORT" in content
        assert "9876" in content  # The constant should have the value


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
