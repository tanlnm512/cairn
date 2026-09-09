"""`cairn build` fails loudly when it produces no store.

A build whose persist phase has nothing to write (a workspace with no
indexable files) leaves the db's parent directory uncreated, and the
derived-index open fails with "store parent directory does not exist".
That no-store outcome is a failed build, not a skippable step: the exit
code is the contract scripting keys "the store exists" on.
"""
from __future__ import annotations

from click.testing import CliRunner

from cairn.cli import main


def _empty_workspace(tmp_path):
    """A workspace with no indexable files."""
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def test_build_without_store_exits_non_zero(tmp_path):
    """A build over a missing store parent exits non-zero, names the
    no-store outcome, and creates no db file."""
    runner = CliRunner()
    db = tmp_path / "missing-parent" / "store.kg"

    result = runner.invoke(
        main,
        ["build", "--db", str(db), "--workspace", str(_empty_workspace(tmp_path))],
        catch_exceptions=False,
    )

    assert result.exit_code != 0, result.output
    assert "no store" in result.output
    assert not db.exists()


def test_build_over_openable_store_exits_zero(tmp_path):
    """A store whose parent directory exists keeps building (exit 0) even
    with an empty workspace: sqlite creates the db file."""
    runner = CliRunner()
    db = tmp_path / "store" / "store.kg"
    db.parent.mkdir()

    result = runner.invoke(
        main,
        ["build", "--db", str(db), "--workspace", str(_empty_workspace(tmp_path))],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert db.exists()
