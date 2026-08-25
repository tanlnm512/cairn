"""Tests for the `cairn embed --download-model` CLI path.

Regression guard for the dead-silent first-run window: the availability
check imports the semantic stack in-process (30s+ on a first run, or right
after `--install-deps`). The CLI now prints a "Loading the semantic
backend..." line BEFORE that check so the window isn't silent.
"""
from __future__ import annotations

from click.testing import CliRunner

from cairn.cli import main as cli_main
from cairn.graph import embeddings as emb


def test_download_model_prints_status_before_silent_import(monkeypatch):
    order = []

    def fake_available():
        order.append("available")
        return True

    def fake_download():
        order.append("download")
        return True

    monkeypatch.setattr(emb, "embeddings_available", fake_available)
    monkeypatch.setattr(emb, "download_model", fake_download)
    monkeypatch.setattr(emb, "is_hash_fallback", lambda: False)

    result = CliRunner().invoke(
        cli_main, ["embed", "--download-model"], catch_exceptions=False
    )

    assert result.exit_code == 0
    # The liveness line precedes the in-process import (availability check)...
    assert "Loading the semantic backend" in result.stdout
    # ...and the flow still runs availability -> download -> success.
    assert order == ["available", "download"]
    assert "Model download complete" in result.stdout


def test_download_model_without_deps_exits_with_install_hint(monkeypatch):
    monkeypatch.setattr(emb, "embeddings_available", lambda: False)
    monkeypatch.setattr(emb, "download_model", lambda: True)  # must not run

    result = CliRunner().invoke(
        cli_main, ["embed", "--download-model"], catch_exceptions=False
    )

    assert result.exit_code == 1
    assert "Semantic dependencies unavailable" in result.stdout
    assert "--install-deps" in result.stdout
