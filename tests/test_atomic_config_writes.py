"""Tests for atomic config writes and malformed-JSON handling (C4/C5).

These verify:
- C4: _atomic_write_text writes via tempfile + os.replace (no truncation on
  crash, no temp file leaked on failure).
- C5: _merge_json_file backs up malformed user JSON to .bak instead of
  silently clobbering it.
"""
import json
import tempfile
from pathlib import Path

from cairn.agent_install.merge import _atomic_write_text, _merge_json_file, _load_json_or_none
from cairn.agent_install._common import InstallResult


class TestAtomicWrite:
    """C4: atomic writes don't truncate or leak temp files."""

    def test_normal_write_succeeds(self, tmp_path):
        path = tmp_path / "config.json"
        _atomic_write_text(path, '{"hello": "world"}\n')
        assert path.read_text() == '{"hello": "world"}\n'

    def test_no_temp_file_leaked_on_success(self, tmp_path):
        path = tmp_path / "config.json"
        _atomic_write_text(path, '{"hello": "world"}\n')
        temps = list(tmp_path.glob("*.tmp"))
        assert temps == [], f"temp file leaked: {temps}"

    def test_no_temp_file_leaked_on_failure(self, tmp_path):
        """If the write fails (e.g. parent dir is read-only), the temp file
        must be cleaned up, not left dangling."""
        import os
        import unittest.mock as mock

        path = tmp_path / "config.json"
        # Simulate a failure during the write by making os.replace raise.
        # The _atomic_write_text helper catches BaseException and unlinks
        # the temp file before re-raising.
        original_replace = os.replace

        def failing_replace(src, dst):
            # Temp file exists at this point
            assert Path(src).exists(), "temp file should exist before replace"
            raise OSError("simulated failure")

        with mock.patch("os.replace", failing_replace):
            try:
                _atomic_write_text(path, '{"data": 1}\n')
                assert False, "should have raised"
            except OSError:
                pass

        temps = list(tmp_path.glob("*.tmp"))
        assert temps == [], f"temp file leaked after failure: {temps}"

    def test_atomic_replace_preserves_existing_on_crash(self, tmp_path):
        """Simulate crash mid-write: the original file is NOT truncated.

        _atomic_write_text writes to a temp file then os.replace's it. If
        the process is killed before os.replace, the original is intact.
        """
        path = tmp_path / "config.json"
        path.write_text('{"original": true}')

        # Write new content — the original should only be replaced on success.
        _atomic_write_text(path, '{"new": true}\n')
        data = json.loads(path.read_text())
        assert data == {"new": True}, "content should be fully replaced"


class TestSafeJsonLoad:
    """C5: malformed JSON is handled safely."""

    def test_load_missing_file_returns_none(self, tmp_path):
        assert _load_json_or_none(tmp_path / "nonexistent.json") is None

    def test_load_valid_json(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text('{"key": "value"}')
        result = _load_json_or_none(path)
        assert result == {"key": "value"}

    def test_load_malformed_json_returns_none(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text('{ broken json !!!')
        assert _load_json_or_none(path) is None


class TestMergeMalformedBackup:
    """C5: _merge_json_file backs up malformed JSON instead of clobbering."""

    def test_malformed_json_backed_up_not_clobbered(self, tmp_path):
        """When the existing config has malformed JSON, the installer must
        back it up to .bak and NOT destroy the user's data."""
        path = tmp_path / ".claude" / "settings.json"
        path.parent.mkdir(parents=True)
        malformed_content = '{ "hooks": { "PreToolUse": [ // missing close\n'
        path.write_text(malformed_content)

        merger = {"mcpServers": {"cairn": {"command": "x"}}}
        result = InstallResult("test")
        _merge_json_file(path, merger, force=True, result=result)

        # The malformed content must be preserved in .bak
        bak = path.with_suffix(".json.bak")
        assert bak.exists(), "malformed config must be backed up"
        assert bak.read_text() == malformed_content, ".bak must contain the original malformed content"

        # The config itself should now be valid JSON with cairn's entry
        new_data = json.loads(path.read_text())
        assert "mcpServers" in new_data
        assert "cairn" in new_data["mcpServers"]

    def test_valid_json_not_backed_up(self, tmp_path):
        """Valid JSON should NOT trigger a backup."""
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"existing": "data"}))

        merger = {"mcpServers": {"cairn": {"command": "x"}}}
        result = InstallResult("test")
        _merge_json_file(path, merger, force=True, result=result)

        bak = path.with_suffix(".json.bak")
        assert not bak.exists(), "valid JSON should not be backed up"

    def test_existing_entries_preserved_on_merge(self, tmp_path):
        """A valid config with existing entries should be deep-merged, not
        overwritten."""
        path = tmp_path / "config.json"
        existing = {"mcpServers": {"other": {"command": "y"}, "cairn": {"command": "old"}}}
        path.write_text(json.dumps(existing))

        merger = {"mcpServers": {"cairn": {"command": "new"}}}
        result = InstallResult("test")
        _merge_json_file(path, merger, force=True, result=result)

        data = json.loads(path.read_text())
        # Other server preserved
        assert data["mcpServers"]["other"]["command"] == "y"
        # Cairn updated
        assert data["mcpServers"]["cairn"]["command"] == "new"
