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




def _update_env(tmp_path):
    """Empty workspace + knowledge bundle + db path under tmp_path."""
    knowledge_path = str(tmp_path / "knowledge")
    db_path = str(tmp_path / "test.db")
    workspace = str(tmp_path / "workspace")
    Path(workspace).mkdir(parents=True, exist_ok=True)
    Path(knowledge_path).mkdir(parents=True, exist_ok=True)
    return workspace, db_path


def _stub_reindex(monkeypatch, errors):
    """Replace incremental_update with a canned result; decay with a no-op."""
    from cairn.graph import incremental
    from cairn.memory import promotion

    monkeypatch.setattr(
        promotion, "decay",
        lambda *a, **k: {"expired_raw": 0, "archived_tribal": 0},
    )
    monkeypatch.setattr(
        incremental, "incremental_update",
        lambda **kwargs: {
            "errors": errors,
            "files_reindexed": 0,
            "files_deleted": 0,
            "repos_scanned": 0,
            "deferred_embeds": 0,
        },
    )


def test_update_exits_nonzero_when_reindex_reports_errors(tmp_path, monkeypatch):
    """A partial reindex failure exits non-zero (after the warnings) so
    scripts can detect the stale-graph outcome."""
    workspace, db_path = _update_env(tmp_path)
    _stub_reindex(monkeypatch, errors=["boom"])

    from click.testing import CliRunner
    from cairn.cli.update import update

    result = CliRunner().invoke(update, ["--workspace", workspace, "--db", db_path])

    assert result.exit_code == 1, result.output
    assert "boom" in result.output


def test_update_still_exits_zero_on_clean_reindex(tmp_path, monkeypatch):
    """A clean reindex keeps exit code 0; only real reindex errors change it."""
    workspace, db_path = _update_env(tmp_path)
    _stub_reindex(monkeypatch, errors=[])

    from click.testing import CliRunner
    from cairn.cli.update import update

    result = CliRunner().invoke(update, ["--workspace", workspace, "--db", db_path])

    assert result.exit_code == 0, result.output


def test_diff_ref_endpoints_handle_three_dot_ranges():
    """A...B ranges verify their two real endpoints, not a phantom '.B'."""
    from cairn.cli.update import _diff_ref_endpoints

    assert _diff_ref_endpoints("main...HEAD") == ["main", "HEAD"]
    assert _diff_ref_endpoints("HEAD@{1}..HEAD") == ["HEAD@{1}", "HEAD"]
    assert _diff_ref_endpoints("HEAD") == ["HEAD"]
    assert _diff_ref_endpoints("..HEAD") == ["HEAD"]
