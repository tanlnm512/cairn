"""Shared pytest fixtures: every test runs on a clean machine by default."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path):
    """HOME and CAIRN_HOME point into the tmp sandbox and no CAIRN_* env leaks between tests.

    Opt out with @pytest.mark.real_env -- justify in a comment."""
    home = tmp_path / "_home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda *a, **k: home)
    monkeypatch.setenv("HOME", str(home))
    for var in [v for v in os.environ if v.startswith("CAIRN_")]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CAIRN_HOME", str(tmp_path / "_cairn_home"))
    yield
