"""Tests for memory-triggered build hints (Phase 3.2).

After `cairn update` reindexes changed files, warn when a tribal memory cites
a file/symbol that no longer fully resolves (refs_verified < 1.0) -- the graph
just changed, so surface memories that may have drifted. Warning, not a block;
only considers explicit backtick refs (never loose prose mentions).

These tests build a tiny repo, record a memory, edit the source to remove the
cited symbol, and run `cairn update` end-to-end via CliRunner.
"""
from __future__ import annotations

import os
from pathlib import Path

from click.testing import CliRunner

from cairn.cli import main


def _make_repo(root: Path) -> None:
    """A one-file git repo with a single function `greet`."""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / "hello.py").write_text("def greet():\n    return 'hi'\n")


def test_update_warns_when_memory_cites_removed_symbol(tmp_path):
    """Editing a file so a memory's cited symbol is gone → warning on update."""
    runner = CliRunner()
    repo = tmp_path / "repo"
    _make_repo(repo)
    db = tmp_path / "test.kg"
    knowledge = tmp_path / "knowledge"

    env = {**os.environ}

    # 1. Build the graph.
    build = runner.invoke(
        main, ["build", "--db", str(db), "--workspace", str(tmp_path)],
        env=env, catch_exceptions=False,
    )
    assert build.exit_code == 0, build.output

    # 2. Record a tribal memory backtick-citing `greet`.
    rec = runner.invoke(
        main,
        ["memory", "record", "decision", "greet returns hi",
         "--body", "`greet()` returns the greeting string. Why: entry point.",
         "--db", str(db), "--knowledge", str(knowledge)],
        env=env, catch_exceptions=False,
    )
    assert rec.exit_code == 0, rec.output
    # Capture the recorded memory id so we can assert the warning names THIS
    # memory specifically (not just any "memory/tribal/" substring).
    import re
    mem_match = re.search(r"(memory/tribal/\S+)", rec.output)
    assert mem_match, f"could not parse memory id from record output: {rec.output}"
    recorded_id = mem_match.group(1)

    # 3. Edit the source: remove `greet` (so the memory's cited symbol is gone).
    (repo / "hello.py").write_text("# greet was removed\n")

    # Make the edit visible to git-diff-based change detection. The incremental
    # updater uses git diff OR stat fallback; initialize git + commit to be safe.
    import subprocess
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=False,
                   env=env, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=False,
                   env=env, capture_output=True)
    # Now make the working-tree edit (the removal above) a diff vs HEAD.
    (repo / "hello.py").write_text("# greet was removed\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=False,
                   env=env, capture_output=True)

    # 4. Run cairn update — should reindex hello.py and warn about the memory.
    upd = runner.invoke(
        main, ["update", "--db", str(db), "--workspace", str(tmp_path),
               "--knowledge", str(knowledge)],
        env=env, catch_exceptions=False,
    )
    # update exits 0 (the memory hint is a warning, not a failure).
    assert upd.exit_code == 0, upd.output
    # The warning names the now-stale memory BY ITS FULL ID (not just a loose
    # "memory/tribal/" prefix that could match anything).
    assert "no longer fully resolve" in upd.output, upd.output
    assert recorded_id in upd.output, (
        f"warning should name the stale memory {recorded_id!r}; got:\n{upd.output}"
    )


def test_update_no_warning_when_memory_refs_still_valid(tmp_path):
    """A memory whose refs are still valid → no staleness warning on update.

    This is the NON-vacuous negative case: the memory is in the scan scope
    (--knowledge passed to update so the bundle is loaded), an edit happens so
    the scan runs, but the cited symbol still exists → no warning. This is what
    catches an 'always warn' mutation (which the positive test alone cannot).
    """
    runner = CliRunner()
    repo = tmp_path / "repo"
    _make_repo(repo)
    db = tmp_path / "test.kg"
    knowledge = tmp_path / "knowledge"
    env = {**os.environ}

    runner.invoke(main, ["build", "--db", str(db), "--workspace", str(tmp_path)],
                  env=env, catch_exceptions=False)
    runner.invoke(
        main,
        ["memory", "record", "decision", "greet returns hi",
         "--body", "`greet()` returns the greeting. Why: entry point.",
         "--db", str(db), "--knowledge", str(knowledge)],
        env=env, catch_exceptions=False,
    )

    # Edit the file but KEEP greet (add a harmless line) so the symbol still
    # exists after reindex -- the memory's ref stays valid.
    (repo / "hello.py").write_text("def greet():\n    return 'hi'\n# extra\n")
    import subprocess
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=False,
                   env=env, capture_output=True)

    # update WITH --knowledge so the memory is actually in scan scope.
    upd = runner.invoke(
        main, ["update", "--db", str(db), "--workspace", str(tmp_path),
               "--knowledge", str(knowledge)],
        env=env, catch_exceptions=False,
    )
    assert upd.exit_code == 0, upd.output
    assert "no longer fully resolve" not in upd.output, upd.output
