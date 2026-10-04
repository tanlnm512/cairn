"""Unit tests for cairn.cli._helpers."""
from __future__ import annotations

from pathlib import Path

import cairn.paths as paths_mod
import cairn.cli._helpers as helpers


def _pin_workspace(monkeypatch, ws: str) -> None:
    monkeypatch.setattr(paths_mod, "resolve_workspace",
                        lambda *a, **k: Path(ws))


def test_shorten_strips_only_at_path_boundary(monkeypatch):
    """A sibling directory sharing a prefix must not be mangled into a
    false-relative path."""
    _pin_workspace(monkeypatch, "/a/b")
    assert helpers._shorten("/a/b/d.py") == "d.py"
    assert helpers._shorten("/a/bc/d.py") == "/a/bc/d.py"
    assert helpers._shorten("/a/b") == "/a/b"


def test_shorten_leaves_paths_outside_the_workspace_alone(monkeypatch):
    _pin_workspace(monkeypatch, "/a/b")
    assert helpers._shorten("/elsewhere/x.py") == "/elsewhere/x.py"
