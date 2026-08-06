"""Smoke tests for `cairn uninstall`.

Covers the command surface (help text, option wiring, dry-run never deletes)
without touching the user's real ~/.cairn — every case points CAIRN_HOME
at a throwaway tempdir.
"""
import tempfile
from pathlib import Path

from click.testing import CliRunner

from cairn.cli import main


def test_uninstall_help_lists_steps():
    runner = CliRunner()
    result = runner.invoke(main, ["uninstall", "--help"])
    assert result.exit_code == 0
    # The four removal steps should each appear as an option.
    for opt in ("--full", "--agents-only", "--hooks-only",
                "--graph-only", "--package-only", "--dry-run"):
        assert opt in result.output


def test_uninstall_appears_in_top_level_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "uninstall" in result.output


def test_dry_run_deletes_nothing_when_store_absent():
    """Empty home → 'nothing to remove', and nothing is created/deleted."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        result = runner.invoke(
            main,
            ["uninstall", "--graph-only", "--dry-run", "-y"],
            env={"CAIRN_HOME": tmp},
        )
        assert result.exit_code == 0
        assert "nothing to remove" in result.output
        # Home dir untouched by dry-run.
        assert Path(tmp).exists()


def test_dry_run_targets_store_when_present():
    """A home with a store subdir is detected and reported (not deleted)."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "fake_home"
        store = home / "deadbeefdeadbeef"  # any 16-hex key
        (store / ".knowledge").mkdir(parents=True)
        (store / ".kg").write_bytes(b"\x00")  # a fake DB file

        result = runner.invoke(
            main,
            ["uninstall", "--graph-only", "--dry-run", "-y"],
            env={"CAIRN_HOME": str(home)},
        )
        assert result.exit_code == 0
        assert str(home) in result.output
        assert "would: rm -rf" in result.output
        # Dry-run must not have deleted anything.
        assert store.exists()
        assert (store / ".kg").exists()


def test_non_dry_run_removes_store():
    """Without --dry-run, the store is actually removed."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "fake_home"
        store = home / "cafef00dcafef00d"
        (store / ".knowledge").mkdir(parents=True)
        (store / ".kg").write_bytes(b"\x00")

        result = runner.invoke(
            main,
            ["uninstall", "--graph-only", "-y"],
            env={"CAIRN_HOME": str(home)},
        )
        assert result.exit_code == 0
        assert "done" in result.output
        assert not store.exists()


def test_full_and_only_flags_are_mutually_exclusive():
    """--full combined with any --*-only must be rejected (exit code 2).

    Previously --full silently overrode --*-only on this destructive command,
    running all four steps regardless of the --*-only intent.
    """
    runner = CliRunner()
    for only_flag in ("--agents-only", "--hooks-only", "--graph-only", "--package-only"):
        result = runner.invoke(
            main,
            ["uninstall", "--full", only_flag, "-y"],
        )
        assert result.exit_code == 2, f"--full {only_flag} should be rejected"
        assert "cannot be combined" in result.output.lower()
