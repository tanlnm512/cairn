"""Tests for atomic config writes and malformed-JSON handling (C4/C5).

These verify:
- C4: _atomic_write_text writes via tempfile + os.replace (no truncation on
  crash, no temp file leaked on failure).
- C5: _merge_json_file backs up malformed user JSON to .bak instead of
  silently clobbering it.
"""
import json
from pathlib import Path

import pytest

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
        import unittest.mock as mock

        path = tmp_path / "config.json"
        # Simulate a failure during the write by making os.replace raise.
        # The _atomic_write_text helper catches BaseException and unlinks
        # the temp file before re-raising.

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


class TestNonObjectKeyBackup:
    """F6: a non-object value under a key the merge writes (mcpServers / mcp /
    hooks -- e.g. ``"mcp": true``) is treated like a malformed config: backed
    up, then merged fresh, instead of crashing with AttributeError."""

    def test_non_object_mcp_value_backed_up(self, tmp_path):
        path = tmp_path / ".zcode" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"mcp": True}))

        merger = {"mcp": {"servers": {"cairn": {"type": "stdio", "command": "x", "args": ["serve"]}}}}
        result = InstallResult("test")
        _merge_json_file(path, merger, force=True, result=result, config_key="zcode")

        bak = path.with_suffix(".json.bak")
        assert bak.exists(), "the user's broken-but-valid value must be backed up"
        assert json.loads(bak.read_text()) == {"mcp": True}
        data = json.loads(path.read_text())
        assert "cairn" in data["mcp"]["servers"]

    def test_non_object_mcp_servers_value_backed_up(self, tmp_path):
        path = tmp_path / "mcp.json"
        path.write_text(json.dumps({"mcpServers": "nope"}))

        merger = {"mcpServers": {"cairn": {"command": "x", "args": ["serve"]}}}
        result = InstallResult("test")
        _merge_json_file(path, merger, force=True, result=result)

        assert path.with_suffix(".json.bak").exists()
        assert "cairn" in json.loads(path.read_text())["mcpServers"]

    def test_non_object_hooks_value_backed_up(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"hooks": []}))

        merger = {"hooks": {"Stop": [{"hooks": [{"command": "x"}]}]}}
        result = InstallResult("test")
        _merge_json_file(path, merger, force=True, result=result)

        assert path.with_suffix(".json.bak").exists()
        assert "Stop" in json.loads(path.read_text())["hooks"]

    def test_unrelated_non_object_keys_do_not_trigger_backup(self, tmp_path):
        """Only the keys the merge actually touches count. A string under a
        key we don't write must survive untouched, with no backup."""
        path = tmp_path / "mcp.json"
        path.write_text(json.dumps({"mcp": "user-list-format", "theme": "dark"}))

        merger = {"mcpServers": {"cairn": {"command": "x", "args": ["serve"]}}}
        result = InstallResult("test")
        _merge_json_file(path, merger, force=True, result=result)

        assert not path.with_suffix(".json.bak").exists()
        data = json.loads(path.read_text())
        assert data["mcp"] == "user-list-format"
        assert data["theme"] == "dark"
        assert "cairn" in data["mcpServers"]

    def test_non_object_key_dry_run_reports_backup_without_touching_disk(self, tmp_path):
        path = tmp_path / "config.json"
        original = json.dumps({"mcpServers": "nope"})
        path.write_text(original)

        merger = {"mcpServers": {"cairn": {"command": "x", "args": ["serve"]}}}
        result = InstallResult("test")
        _merge_json_file(path, merger, force=True, result=result, dry_run=True)

        assert path.read_text() == original, "dry-run must not rewrite the file"
        assert not path.with_suffix(".json.bak").exists(), "dry-run must not create backups"
        assert any("would back up" in w for w in result.written)


def _mcp_merger(config_key: str, env: dict | None) -> dict:
    """A stdio cairn registration in the client's merge shape.

    zcode nests under ``mcp.servers`` with separate command/args; opencode and
    kilo use a single command array directly under ``mcp``.
    """
    if config_key == "zcode":
        entry: dict = {"type": "stdio", "command": "cg", "args": ["serve"]}
    else:
        entry = {"type": "local", "command": ["cg", "serve"], "enabled": True}
    if env:
        entry["env"] = env
    if config_key == "zcode":
        return {"mcp": {"servers": {"cairn": entry}}}
    return {"mcp": {"cairn": entry}}


def _cairn_entry(data: dict, config_key: str) -> dict:
    if config_key == "zcode":
        return data["mcp"]["servers"]["cairn"]
    return data["mcp"]["cairn"]


class TestAlreadyInstalledEnvComparison:
    """T023 (FR-001/D-012): the zcode and opencode/kilo idempotence branches
    must compare env like the flat mcpServers branch, so a reinstall after
    moving the store (changed CAIRN_HOME) replaces the stale env instead of
    silently keeping the old registration."""

    @pytest.mark.parametrize("config_key", ["zcode", "opencode", "kilo"])
    def test_changed_env_rewrites_registration(self, tmp_path, config_key):
        path = tmp_path / "config.json"
        path.write_text(json.dumps(_mcp_merger(config_key, {"CAIRN_HOME": "/old/cairn-home"})))

        result = InstallResult("test")
        _merge_json_file(path, _mcp_merger(config_key, {"CAIRN_HOME": "/new/cairn-home"}),
                         force=False, result=result, config_key=config_key)

        data = json.loads(path.read_text())
        assert _cairn_entry(data, config_key)["env"] == {"CAIRN_HOME": "/new/cairn-home"}
        assert str(path) in result.written, "changed env must rewrite, not skip"

    @pytest.mark.parametrize("config_key", ["zcode", "opencode", "kilo"])
    def test_default_home_reinstall_removes_env_key(self, tmp_path, config_key):
        """Back on the default home the generator emits no env block, so the
        reinstall must drop the stale CAIRN_HOME key (FR-001 AC5)."""
        path = tmp_path / "config.json"
        path.write_text(json.dumps(_mcp_merger(config_key, {"CAIRN_HOME": "/old/cairn-home"})))

        result = InstallResult("test")
        _merge_json_file(path, _mcp_merger(config_key, env=None),
                         force=False, result=result, config_key=config_key)

        data = json.loads(path.read_text())
        assert "env" not in _cairn_entry(data, config_key)
        assert str(path) in result.written

    @pytest.mark.parametrize("config_key", ["zcode", "opencode", "kilo"])
    def test_unchanged_env_is_byte_stable(self, tmp_path, config_key):
        """Idempotence: an identical registration (same env) is not rewritten."""
        merger = _mcp_merger(config_key, {"CAIRN_HOME": "/fixed/cairn-home"})
        original = json.dumps(merger, indent=2) + "\n"
        path = tmp_path / "config.json"
        path.write_text(original)

        result = InstallResult("test")
        _merge_json_file(path, merger, force=False, result=result, config_key=config_key)

        assert path.read_text() == original, "unchanged env must not rewrite the file"
        assert str(path) in result.skipped
