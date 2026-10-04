"""Symlinked files stay out of the scan: their content lives outside the repo root."""
from __future__ import annotations

from pathlib import Path

from cairn.graph.scanner import iter_files_and_skips


def test_symlinked_file_is_not_indexed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("TOKEN = 'leak'\n", encoding="utf-8")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "real.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "link.py").symlink_to(outside / "secret.py")

    files, _skips = iter_files_and_skips(repo)
    assert [Path(f.path).name for f in files] == ["real.py"]


def test_symlinked_directory_contents_are_not_indexed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "lib.py").write_text("y = 2\n", encoding="utf-8")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "real.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "vendored").symlink_to(outside, target_is_directory=True)

    files, _skips = iter_files_and_skips(repo)
    assert [Path(f.path).name for f in files] == ["real.py"]
