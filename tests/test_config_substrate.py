"""Persistent config substrate (FR-010): $CAIRN_HOME/config.json.

Covers the D-008 env > file > default resolution at the embeddings read
sites, mtime-triggered re-read (running processes pick up edits without
restart), atomic writes via set_config_values, corruption degradation to
defaults-with-warning, and the reset_backend_cache() invalidation contract.

CONFIG_FILE is bound at import time from CAIRN_HOME (paths.py), so tests
monkeypatch the module-level attribute -- never the env var after import
(survey S10 pit).
"""
from __future__ import annotations

import io
import json
import logging
import os

import pytest

from cairn import paths
from cairn.graph import embeddings as emb


def _semantic_extra_installed() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


_NEEDS_SEMANTIC = pytest.mark.skipif(
    not _semantic_extra_installed(),
    reason="requires the [semantic] extra (sentence-transformers)",
)


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Point CONFIG_FILE at a sandbox file; start and end cache-clean."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(paths, "CONFIG_FILE", path)
    paths.reset_config_cache()
    emb.reset_backend_cache()
    yield path
    paths.reset_config_cache()
    emb.reset_backend_cache()


def _write(path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


_T0 = 1_000_000_000_000_000_000  # fixed ns timestamp for os.utime(ns=...)
_T1 = 2_000_000_000_000_000_000


# ---------------------------------------------------------------------------
# (a) Precedence: env > file > default.
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_default_without_env_or_file(self, config_file):
        assert not config_file.exists()
        assert emb._backend_name() == "local"
        assert emb._server_model() == "bge-m3"

    def test_file_beats_default(self, config_file):
        _write(config_file, {
            "CAIRN_EMBED_BACKEND": "omlx",
            "CAIRN_EMBED_SERVER_MODEL": "file-model",
        })
        assert emb._backend_name() == "omlx"
        assert emb._server_model() == "file-model"

    def test_env_beats_file(self, config_file, monkeypatch):
        _write(config_file, {
            "CAIRN_EMBED_BACKEND": "omlx",
            "CAIRN_EMBED_SERVER_MODEL": "file-model",
        })
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "ollama")
        monkeypatch.setenv("CAIRN_EMBED_SERVER_MODEL", "env-model")
        assert emb._backend_name() == "ollama"
        assert emb._server_model() == "env-model"

    def test_blank_env_value_falls_through_to_file(self, config_file, monkeypatch):
        _write(config_file, {"CAIRN_EMBED_SERVER_MODEL": "file-model"})
        monkeypatch.setenv("CAIRN_EMBED_SERVER_MODEL", "   ")
        assert emb._server_model() == "file-model"


# ---------------------------------------------------------------------------
# (b) File-only backend values resolve through the family machinery.
# ---------------------------------------------------------------------------


class TestFileOnlyBackend:
    def test_file_backend_resolves_to_server_family(self, config_file):
        _write(config_file, {"CAIRN_EMBED_BACKEND": "ollama"})
        assert emb._effective_backend() == "server"
        assert emb._server_base_url() == "http://127.0.0.1:11434/v1"

    def test_file_base_url_serves_bare_server(self, config_file):
        _write(config_file, {
            "CAIRN_EMBED_BACKEND": "server",
            "CAIRN_EMBED_BASE_URL": "http://example:9000/v1",
        })
        assert emb._server_base_url() == "http://example:9000/v1"

    def test_bare_server_without_any_base_url_still_raises(self, config_file):
        _write(config_file, {"CAIRN_EMBED_BACKEND": "server"})
        with pytest.raises(RuntimeError):
            emb._server_base_url()


# ---------------------------------------------------------------------------
# (c) Corrupt JSON: one warning, empty config, embed calls keep working.
# ---------------------------------------------------------------------------


class TestCorruptConfig:
    def test_one_warning_then_empty_config(self, config_file, caplog):
        config_file.write_text("{not json", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="cairn.paths"):
            assert paths.get_config_value("CAIRN_EMBED_BACKEND") is None
            assert emb._backend_name() == "local"
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_embed_calls_still_work(self, config_file, monkeypatch):
        config_file.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
        emb.reset_backend_cache()
        blob, dim = emb.embed_query("hello world")
        assert dim == emb.DEFAULT_DIM
        assert len(blob) == dim * 4
        assert emb.current_model() == emb.HASH_MODEL

    def test_non_object_json_is_ignored(self, config_file):
        config_file.write_text('["CAIRN_EMBED_BACKEND"]', encoding="utf-8")
        assert paths.get_config_value("CAIRN_EMBED_BACKEND") is None
        assert emb._backend_name() == "local"


# ---------------------------------------------------------------------------
# (d) mtime-triggered re-read.
# ---------------------------------------------------------------------------


class TestMtimeReread:
    def test_changed_mtime_rereads_without_restart(self, config_file):
        _write(config_file, {"CAIRN_EMBED_BACKEND": "omlx"})
        os.utime(config_file, ns=(_T0, _T0))
        assert emb._backend_name() == "omlx"
        _write(config_file, {"CAIRN_EMBED_BACKEND": "ollama"})
        os.utime(config_file, ns=(_T1, _T1))
        assert emb._backend_name() == "ollama"

    def test_unchanged_mtime_serves_cached_value(self, config_file):
        _write(config_file, {"CAIRN_EMBED_BACKEND": "omlx"})
        os.utime(config_file, ns=(_T0, _T0))
        assert emb._backend_name() == "omlx"
        # Same size, restored mtime: the stamp matches, no re-read.
        _write(config_file, {"CAIRN_EMBED_BACKEND": "hash"})
        os.utime(config_file, ns=(_T0, _T0))
        assert emb._backend_name() == "omlx"
        # Advancing the mtime releases the new value.
        os.utime(config_file, ns=(_T1, _T1))
        assert emb._backend_name() == "hash"

    def test_deleted_file_degrades_to_env_only(self, config_file, monkeypatch):
        _write(config_file, {"CAIRN_EMBED_BACKEND": "omlx"})
        assert emb._backend_name() == "omlx"
        config_file.unlink()
        assert emb._backend_name() == "local"
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
        assert emb._backend_name() == "hash"


# ---------------------------------------------------------------------------
# (e) set_config_values: atomic write, merge, round-trip, OSError handling.
# ---------------------------------------------------------------------------


class TestSetConfigValues:
    def test_atomic_write_roundtrip_and_merge(self, config_file):
        assert paths.set_config_values({"CAIRN_EMBED_BACKEND": "omlx"}) is True
        assert config_file.exists()
        assert paths.get_config_value("CAIRN_EMBED_BACKEND") == "omlx"
        assert emb._backend_name() == "omlx"
        assert paths.set_config_values({"CAIRN_EMBED_SERVER_MODEL": "m2"}) is True
        on_disk = json.loads(config_file.read_text(encoding="utf-8"))
        assert on_disk == {
            "CAIRN_EMBED_BACKEND": "omlx",
            "CAIRN_EMBED_SERVER_MODEL": "m2",
        }
        assert list(config_file.parent.glob("*.tmp")) == []

    def test_creates_missing_parent_dir(self, tmp_path, monkeypatch):
        path = tmp_path / "deep" / "nest" / "config.json"
        monkeypatch.setattr(paths, "CONFIG_FILE", path)
        paths.reset_config_cache()
        try:
            assert paths.set_config_values({"K": "v"}) is True
            assert json.loads(path.read_text(encoding="utf-8")) == {"K": "v"}
        finally:
            paths.reset_config_cache()

    def test_oserror_returns_false_no_tmp_no_clobber(
        self, config_file, monkeypatch, caplog
    ):
        _write(config_file, {"CAIRN_EMBED_BACKEND": "omlx"})

        def _boom(src, dst):
            raise OSError("no space left on device")

        monkeypatch.setattr(os, "replace", _boom)
        with caplog.at_level(logging.WARNING, logger="cairn.paths"):
            assert paths.set_config_values({"CAIRN_EMBED_SERVER_MODEL": "m2"}) is False
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert list(config_file.parent.glob("*.tmp")) == []
        assert json.loads(config_file.read_text(encoding="utf-8")) == {
            "CAIRN_EMBED_BACKEND": "omlx"
        }


# ---------------------------------------------------------------------------
# (f) reset_backend_cache() invalidates the config cache.
# ---------------------------------------------------------------------------


class TestResetHook:
    def test_reset_backend_cache_clears_config_cache(self, config_file):
        _write(config_file, {"CAIRN_EMBED_BACKEND": "omlx"})
        os.utime(config_file, ns=(_T0, _T0))
        assert emb._backend_name() == "omlx"
        # Same size + restored mtime: only a reset exposes the rewrite.
        _write(config_file, {"CAIRN_EMBED_BACKEND": "hash"})
        os.utime(config_file, ns=(_T0, _T0))
        emb.reset_backend_cache()
        assert emb._backend_name() == "hash"


# ---------------------------------------------------------------------------
# (g) No config file / untouched arms: openai stays env-only (FR-009).
# ---------------------------------------------------------------------------


class TestUnchangedArms:
    def test_openai_model_and_key_stay_env_only(self, config_file, monkeypatch):
        _write(config_file, {
            "CAIRN_EMBED_BACKEND": "openai",
            "CAIRN_EMBED_OPENAI_MODEL": "from-file",
            "OPENAI_API_KEY": "file-key",
        })
        # The backend knob itself is config-aware; the openai arm's model
        # and key semantics are not (FR-009).
        assert emb._backend_name() == "openai"
        assert emb.current_model() == "text-embedding-3-small"
        assert emb.embeddings_available() is False
        with pytest.raises(RuntimeError):
            emb._embed_openai(["x"])

    @_NEEDS_SEMANTIC
    def test_local_arm_still_env_driven_without_file(self, config_file, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_LOCAL_MODEL", "env-local")
        monkeypatch.setenv("CAIRN_EMBED_KNOWLEDGE_MODEL", "env-knowledge")
        assert emb.current_model() == "env-local"
        assert emb.current_model(corpus="knowledge") == "env-knowledge"
        assert emb.current_model(corpus="memory") == "env-local"

    def test_server_knobs_resolve_from_file(
        self, config_file, monkeypatch
    ):
        _write(config_file, {
            "CAIRN_EMBED_BACKEND": "omlx",
            "CAIRN_EMBED_SERVER_MODEL": "file-model",
            "CAIRN_EMBED_TIMEOUT": "7.5",
            "CAIRN_EMBED_SERVER_BATCH": "2",
            "CAIRN_EMBED_API_KEY": "file-key",
        })
        calls = []

        def _fake_urlopen(req, timeout=None):
            payload = json.loads(req.data.decode("utf-8"))
            calls.append(
                {
                    "timeout": timeout,
                    "auth": req.headers.get("Authorization"),
                    "model": payload["model"],
                    "inputs": len(payload["input"]),
                }
            )
            body = json.dumps(
                {
                    "data": [
                        {"index": i, "embedding": [0.5, 0.5, 0.5, 0.5]}
                        for i in range(len(payload["input"]))
                    ]
                }
            ).encode("utf-8")
            return io.BytesIO(body)

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        blobs, dim = emb._embed_server(["a", "b", "c"])
        assert dim == 4
        assert len(blobs) == 3
        # Batch=2 from the file chunks 3 texts into POSTs of 2 then 1.
        assert [c["inputs"] for c in calls] == [2, 1]
        assert calls[0]["timeout"] == 7.5
        assert calls[0]["auth"] == "Bearer file-key"
        assert calls[0]["model"] == "file-model"


# ---------------------------------------------------------------------------
# (h) Empty JSON object behaves exactly like an absent file.
# ---------------------------------------------------------------------------


class TestEmptyObject:
    def test_empty_object_equals_absent(self, config_file, monkeypatch):
        _write(config_file, {})
        assert emb._backend_name() == "local"
        assert emb._server_model() == "bge-m3"
        assert paths.get_config_value("anything", "d") == "d"
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
        emb.reset_backend_cache()
        blob, dim = emb.embed_query("x")
        assert dim == emb.DEFAULT_DIM
