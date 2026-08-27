"""Tests for the `cairn embed` server-down path (D-003: loud failure).

A server-family backend whose availability probe fails must exit 1 with a
server-specific remediation (base URL, the /v1/models check, `cairn doctor`)
-- never the sentence-transformers install text, and never a silent hash
fallback (FR-002). Non-server backends keep the existing install-hint exit.
"""
from __future__ import annotations

import socket

import pytest
from click.testing import CliRunner

from cairn.cli import main as cli_main
from cairn.graph import embeddings as emb


@pytest.fixture(autouse=True)
def _fresh_backend_cache():
    """The probe/effective-backend verdicts are process-cached (FR-002);
    reset around every test so each one probes its own env."""
    emb.reset_backend_cache()
    yield
    emb.reset_backend_cache()


def _closed_port_base_url() -> str:
    """A loopback /v1 base URL whose port refuses connections."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1"


def _invoke(*args):
    return CliRunner().invoke(cli_main, ["embed", *args], catch_exceptions=False)


def _flat(result) -> str:
    # rich wraps at console width; collapse whitespace so substring
    # assertions never straddle a wrap boundary.
    return " ".join(result.output.split())


def test_server_down_exits_1_with_server_remediation(monkeypatch, tmp_path):
    base = _closed_port_base_url()
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "omlx")
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", base)

    result = _invoke("--db", str(tmp_path / "db.sqlite"))

    assert result.exit_code == 1
    out = _flat(result)
    assert base in out  # names the base URL the probe hit
    assert "/v1/models" in out  # the check the server must pass
    assert "cairn doctor" in out  # the diagnostic next step
    # D-003: the torch-install text is wrong for a server user.
    assert "pip install" not in out
    assert "sentence-transformers" not in out
    assert "--install-deps" not in out


def test_server_down_download_model_path_gets_server_remediation(
    monkeypatch, tmp_path
):
    base = _closed_port_base_url()
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "omlx")
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", base)

    result = _invoke("--db", str(tmp_path / "db.sqlite"), "--download-model")

    assert result.exit_code == 1
    out = _flat(result)
    assert "Loading the semantic backend" in out  # pre-check liveness line
    assert base in out
    assert "cairn doctor" in out
    assert "pip install" not in out
    assert "--install-deps" not in out


def test_bare_server_without_base_url_names_the_missing_knob(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.delenv("CAIRN_EMBED_BASE_URL", raising=False)

    result = _invoke("--db", str(tmp_path / "db.sqlite"))

    assert result.exit_code == 1
    out = _flat(result)
    assert "CAIRN_EMBED_BASE_URL" in out  # the unresolvable knob
    assert "cairn doctor" in out
    assert "pip install" not in out


def test_local_unavailable_keeps_install_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "local")
    monkeypatch.setattr(emb, "embeddings_available", lambda: False)

    result = _invoke("--db", str(tmp_path / "db.sqlite"))

    assert result.exit_code == 1
    out = _flat(result)
    assert "Semantic dependencies unavailable" in out
    assert "pip install" in out  # the install text, not a server hint
    assert "--install-deps" in out
    assert "cairn doctor" not in out  # no server remediation leaked in


def test_install_deps_flag_path_untouched(monkeypatch, tmp_path):
    # Even under a server backend, --install-deps remains the explicit
    # sentence-transformers install path with its own hint.
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "omlx")
    monkeypatch.setattr(emb, "ensure_semantic_deps", lambda auto_install=True: False)

    result = _invoke("--db", str(tmp_path / "db.sqlite"), "--install-deps")

    assert result.exit_code == 1
    out = _flat(result)
    assert "Semantic dependencies unavailable" in out
    assert "pip install" in out  # install-hint path, unchanged by T005
