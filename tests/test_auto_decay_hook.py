"""Tests for auto-decay hook (VAL-MK-003, M10).

Verifies that the decay() function is called from a periodic maintenance path
(server boot catch-up or `cairn update`), not only via manual CLI/MCP.
"""
from __future__ import annotations

from pathlib import Path


def test_decay_called_in_update_command(tmp_path, monkeypatch):
    """VAL-MK-003: decay() is called during `cairn update` command.

    This test monkeypatches the decay() function to spy on whether it's called,
    then drives the update command entry point.
    """
    # Set up test environment
    knowledge_path = str(tmp_path / "knowledge")
    db_path = str(tmp_path / "test.db")
    workspace = str(tmp_path / "workspace")
    
    Path(workspace).mkdir(parents=True, exist_ok=True)
    Path(knowledge_path).mkdir(parents=True, exist_ok=True)
    
    # Monkeypatch decay to track if it's called
    from cairn.memory import promotion
    decay_called = {"called": False}
    
    def mock_decay(bundle, *args, **kwargs):
        decay_called["called"] = True
        return {"expired_raw": 0, "archived_tribal": 0}
    
    monkeypatch.setattr(promotion, "decay", mock_decay)
    
    # Set up environment for update command
    monkeypatch.setenv("CAIRN_DB", db_path)
    monkeypatch.setenv("CAIRN_KNOWLEDGE", knowledge_path)
    
    # Import and run the update command (non-interactive)
    from click.testing import CliRunner
    from cairn.cli.update import update
    
    runner = CliRunner()
    runner.invoke(update, ['--workspace', workspace, '--db', db_path])

    # Assert that decay was called
    assert decay_called["called"], "decay() should be called during cairn update"


