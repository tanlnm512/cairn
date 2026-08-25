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


# --- download_model fetches in a quiet child interpreter -------------------
#
# Regression guard: the fetch used to construct SentenceTransformer
# in-process, so HuggingFace printed one tqdm bar per repo file (plus
# transformers warnings) straight into the terminal -- a wall of lines for
# an ~836 MB multi-file model. The fetch now runs in a child interpreter
# behind the shared quiet progress helper, sharing the parent's HF cache.
# Faked at the _run_subprocess_with_progress seam, same as the
# ensure_semantic_deps tests (never patch subprocess.Popen globally).

def test_download_model_fetches_in_quiet_subprocess(monkeypatch, capsys):
    import os
    import sys

    from cairn.paths import shared_lib_path

    seen = {}

    def fake_run(cmd, description, env=None):
        seen["cmd"] = cmd
        seen["env"] = env
        return ""

    monkeypatch.setattr(emb, "_run_subprocess_with_progress", fake_run)
    monkeypatch.setattr(emb, "model_is_cached", lambda m=None: False)

    assert emb.download_model("BAAI/bge-m3") is True

    # The fetch is a child interpreter constructing the model (which is
    # what downloads the weights into the shared HF cache)...
    assert seen["cmd"][:2] == [sys.executable, "-c"]
    assert "SentenceTransformer" in seen["cmd"][2]
    assert "BAAI/bge-m3" in seen["cmd"][2]
    # ...with the shared lib dirs first on PYTHONPATH so the child resolves
    # the semantic stack exactly where the parent would.
    pythonpath = (seen["env"] or {}).get("PYTHONPATH", "")
    assert pythonpath.split(os.pathsep)[0] == str(shared_lib_path())
    out = capsys.readouterr().out
    assert "Downloading 'BAAI/bge-m3'" in out
    assert "downloaded successfully" in out


def test_download_model_cached_skips_subprocess(monkeypatch):
    calls = []

    monkeypatch.setattr(
        emb, "_run_subprocess_with_progress",
        lambda *a, **k: calls.append(a),
    )
    monkeypatch.setattr(emb, "model_is_cached", lambda m=None: True)

    assert emb.download_model("BAAI/bge-m3") is True
    assert calls == []


def test_download_model_failure_surfaces_child_output(monkeypatch, capsys):
    import subprocess

    def fake_run(cmd, description, env=None):
        print("Connection error: couldn't reach huggingface.co")
        raise subprocess.CalledProcessError(1, cmd, "conn")

    monkeypatch.setattr(emb, "_run_subprocess_with_progress", fake_run)
    monkeypatch.setattr(emb, "model_is_cached", lambda m=None: False)

    assert emb.download_model("BAAI/bge-m3") is False

    out = capsys.readouterr().out
    # The child's actual error is surfaced (quiet must never mean opaque)...
    assert "huggingface.co" in out
    # ...along with the failure line.
    assert "Failed to download model 'BAAI/bge-m3'" in out
