"""Tests for scripts/verify_no_code_change.py -- the comment-only gate.

Each test drives the script as a subprocess inside a scratch git repo, so
``_changed_files``/``git show`` run against that repo rather than cairn's own.

Covered contract points:
* a deleted .py file is a mismatch in tree mode (no FileNotFoundError crash);
* a deleted .py file is a mismatch in commit mode (no false "comment-only" OK);
* a genuinely comment/docstring-only edit still verifies clean (exit 0).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_no_code_change.py"

BASE_PY = '"""Original docstring."""\n\n\ndef f():\n    return 1\n'
COMMENT_ONLY_PY = '"""Edited docstring."""\n\n\ndef f():\n    # an added comment\n    return 1\n'


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    """A scratch repo with one committed .py file at HEAD."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "mod.py").write_text(BASE_PY, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _run_script(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_deleted_file_fails_in_tree_mode(tmp_path):
    # Dirty tree + deleted file: must FAIL, not crash with FileNotFoundError.
    repo = _init_repo(tmp_path)
    (repo / "mod.py").unlink()
    proc = _run_script(repo)
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "FileNotFoundError" not in proc.stderr
    assert "FAIL:" in proc.stderr
    assert "mod.py" in proc.stderr


def test_deleted_file_fails_in_commit_mode(tmp_path):
    # Clean tree where HEAD deletes mod.py: `verify_no_code_change.py HEAD~1`
    # must FAIL, not report the deletion as comment/docstring-only.
    repo = _init_repo(tmp_path)
    (repo / "mod.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "delete mod.py")
    proc = _run_script(repo, "HEAD~1")
    assert proc.returncode == 1
    assert "FAIL:" in proc.stderr
    assert "mod.py" in proc.stderr
    assert "comment/docstring changes only" not in proc.stdout


def test_comment_only_edit_still_verifies_clean(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "mod.py").write_text(COMMENT_ONLY_PY, encoding="utf-8")
    proc = _run_script(repo)
    assert proc.returncode == 0
    assert "OK:" in proc.stdout
    assert "comment/docstring changes only" in proc.stdout
