"""Blast diff reader tolerates non-UTF-8 payload bytes.

Git sniffs only the first 8KB of a blob for binary detection, so a diff can
carry content that decodes as text for a while and then hits an invalid
UTF-8 byte (a protobuf fixture committed as a "text" diff is the real-world
case). The reader must decode lossily, never raise.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from cairn.graph.blast import _git


def _init_repo(repo: Path) -> None:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    (repo / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-qm", "init"], cwd=repo, check=True, env=env
    )


def test_git_reader_survives_diff_with_invalid_utf8_tail(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)

    # Benign text prefix past git's 8KB binary-sniff window, then a byte
    # (0xf5) that strict UTF-8 decoding rejects.
    blob = b"a" * 9000 + b"\xf5\xf5\xf5"
    (repo / "index.scip").write_bytes(blob)
    subprocess.run(["git", "add", "index.scip"], cwd=repo, check=True)

    out = _git(repo, ["diff", "--unified=0", "HEAD"])
    assert "index.scip" in out
