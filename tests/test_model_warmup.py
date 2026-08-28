"""P0-1: boot-time warm-up of the semantic-path models.

Hermetic by construction: no test loads real weights. The cairn-side
loaders (``embeddings._get_local_model``, ``reranker._get_reranker``) are
replaced with recording stubs, the backend/gate helpers are pinned, and
``warm_models()`` (the synchronous thread body) is invoked directly so
assertions never depend on thread scheduling. The thread-level tests use
instant stubs and join with a timeout. The persistent rerank marker is
neutralized suite-wide here (same trick as test_reranker.py) because
``paths.CAIRN_HOME`` is resolved at import time and may point at a real
home where ``cairn download-reranker`` left a marker.
"""
from __future__ import annotations

import http.server
import json
import logging
import os
import socket
import threading
from pathlib import Path

import pytest

from cairn.graph import embeddings, model_warmup, reranker

# Captured at module import (before any fixture patches it) so the env-var
# contract test below can invoke the REAL _inside_pytest.
_REAL_INSIDE_PYTEST = model_warmup._inside_pytest


@pytest.fixture(autouse=True)
def _reset_warmup_state(monkeypatch):
    """Warm-up state is once-per-process; clear it around every test.

    Also patches _inside_pytest() to False so these tests exercise the
    production code path: warm_models_in_background() hard refuses to start
    its background thread inside a pytest test (a seconds-long load leaking
    across test boundaries flaked test_server_robustness.TestModelCacheRace;
    see the module docstrings). Patching the helper -- rather than deleting
    PYTEST_CURRENT_TEST -- is deterministic because pytest re-sets that env
    var at every test phase boundary. The guard itself has a dedicated test
    below, which patches the helper back to True.
    """
    monkeypatch.setattr(model_warmup, "_inside_pytest", lambda: False)
    model_warmup._reset_warmup_state()
    yield
    model_warmup._reset_warmup_state()


@pytest.fixture(autouse=True)
def _neutralize_rerank_marker(monkeypatch):
    """Pretend no persistent rerank marker exists so the default (disabled)
    is deterministic even on machines that ran `cairn download-reranker`.
    Marker tests below override this patch."""
    monkeypatch.setattr(
        reranker, "_rerank_marker_path", lambda: Path("/nonexistent/cairn-warmup-no-marker")
    )


@pytest.fixture
def embed_calls(monkeypatch):
    """Recording stub for the local-model loader.

    Each entry captures the offline env state *at load time* so the
    HF_HUB_OFFLINE set/restore behavior is observable without touching the
    real sentence_transformers loader.
    """
    calls: list[dict] = []

    def _fake_get_local_model(model_name=None):
        calls.append({"HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE")})
        return object()

    monkeypatch.setattr(embeddings, "_get_local_model", _fake_get_local_model)
    return calls


@pytest.fixture
def local_backend(monkeypatch):
    """Pin the effective embed backend to 'local' with cache-verified weights.

    Stubbed rather than set via env so the tests are hermetic even where
    sentence-transformers is (or isn't) installed.
    """
    monkeypatch.setattr(embeddings, "_effective_backend", lambda: "local")
    monkeypatch.setattr(embeddings, "model_is_cached", lambda name=None: True)


@pytest.fixture
def rerank_calls(monkeypatch):
    """Recording stub for the reranker loader, with the query-path gates
    (availability, cache presence) stubbed True so the enabled path runs
    without sentence-transformers or a downloaded model."""
    calls: list[dict] = []

    def _fake_get_reranker():
        calls.append({"HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE")})
        return object()

    monkeypatch.setattr(reranker, "_get_reranker", _fake_get_reranker)
    monkeypatch.setattr(reranker, "reranker_available", lambda: True)
    monkeypatch.setattr(reranker, "reranker_model_is_cached", lambda name=None: True)
    return calls


class TestWarmModels:
    def test_local_backend_loads_embed_model_only(self, local_backend, embed_calls, rerank_calls):
        # CAIRN_RERANK unset + no marker (neutralized) -> reranker disabled.
        model_warmup.warm_models()
        assert len(embed_calls) == 1
        assert rerank_calls == []

    def test_hash_backend_skips_embed_model(self, monkeypatch, embed_calls, rerank_calls):
        monkeypatch.setattr(embeddings, "_effective_backend", lambda: "hash")
        model_warmup.warm_models()
        assert embed_calls == []

    def test_hash_backend_env_skips_embed_model(self, hash_backend, embed_calls, rerank_calls):
        """The real resolution path (CAIRN_EMBED_BACKEND=hash via env), not a
        stub of _effective_backend -- proves the gate reads the resolver."""
        model_warmup.warm_models()
        assert embed_calls == []

    def test_openai_backend_skips_embed_model(self, monkeypatch, embed_calls, rerank_calls):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "openai")
        embeddings.reset_backend_cache()
        try:
            model_warmup.warm_models()
        finally:
            embeddings.reset_backend_cache()
        assert embed_calls == []

    def test_rerank_enabled_via_env_loads_both(self, monkeypatch, local_backend, embed_calls, rerank_calls):
        monkeypatch.setenv("CAIRN_RERANK", "1")
        model_warmup.warm_models()
        assert len(embed_calls) == 1
        assert len(rerank_calls) == 1
        # The reranker gate guarantees cached weights -> its load runs offline.
        assert rerank_calls[0]["HF_HUB_OFFLINE"] == "1"

    def test_rerank_enabled_via_marker(self, monkeypatch, tmp_path, local_backend, embed_calls, rerank_calls):
        marker = tmp_path / "rerank_enabled"
        marker.write_text("BAAI/bge-reranker-base\n")
        monkeypatch.setattr(reranker, "_rerank_marker_path", lambda: marker)
        monkeypatch.delenv("CAIRN_RERANK", raising=False)
        model_warmup.warm_models()
        assert len(rerank_calls) == 1

    def test_rerank_env_off_beats_marker(self, monkeypatch, tmp_path, local_backend, embed_calls, rerank_calls):
        """CAIRN_RERANK=0 is a hard kill switch: wins even if the marker
        exists -- warm-up must mirror that precedence, not just read env on/off."""
        marker = tmp_path / "rerank_enabled"
        marker.write_text("BAAI/bge-reranker-base\n")
        monkeypatch.setattr(reranker, "_rerank_marker_path", lambda: marker)
        monkeypatch.setenv("CAIRN_RERANK", "0")
        model_warmup.warm_models()
        assert rerank_calls == []

    def test_reranker_not_cached_never_loaded(self, monkeypatch, local_backend, embed_calls, rerank_calls):
        """Mirrors rerank()'s proactive cache guard: an uncached model is
        never force-loaded (or downloaded) by warm-up."""
        monkeypatch.setattr(reranker, "reranker_model_is_cached", lambda name=None: False)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        model_warmup.warm_models()
        assert rerank_calls == []

    def test_reranker_unavailable_never_loaded(self, monkeypatch, local_backend, embed_calls, rerank_calls):
        monkeypatch.setattr(reranker, "reranker_available", lambda: False)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        model_warmup.warm_models()
        assert rerank_calls == []


class TestOfflineEnv:
    def test_cached_load_runs_offline_and_restores_env(self, monkeypatch, local_backend, embed_calls):
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
        monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
        model_warmup.warm_models()
        assert embed_calls[0]["HF_HUB_OFFLINE"] == "1"
        assert "HF_HUB_OFFLINE" not in os.environ
        assert "TRANSFORMERS_OFFLINE" not in os.environ

    def test_preexisting_offline_values_restored(self, monkeypatch, local_backend, embed_calls):
        monkeypatch.setenv("HF_HUB_OFFLINE", "0")
        monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
        model_warmup.warm_models()
        assert embed_calls[0]["HF_HUB_OFFLINE"] == "1"
        assert os.environ["HF_HUB_OFFLINE"] == "0"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"

    def test_uncached_model_skipped_entirely(self, monkeypatch, local_backend, embed_calls):
        """First-download users must not be broken -- and boot must never
        download: when the weights aren't verifiably in the local HF cache,
        warm-up is a no-op and the first query loads exactly as before
        (this also keeps hermetic test environments network-free)."""
        monkeypatch.setattr(embeddings, "model_is_cached", lambda name=None: False)
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
        model_warmup.warm_models()
        assert embed_calls == []
        assert "HF_HUB_OFFLINE" not in os.environ

    def test_offline_failure_retries_once_online(self, monkeypatch, local_backend):
        """A false-positive cache verdict (partial snapshot) must not leave
        the model cold: the load retries once without the offline vars."""
        attempts: list[str | None] = []

        def _flaky_get_local_model(model_name=None):
            attempts.append(os.environ.get("HF_HUB_OFFLINE"))
            if os.environ.get("HF_HUB_OFFLINE") == "1":
                raise RuntimeError("simulated incomplete local snapshot")
            return object()

        monkeypatch.setattr(embeddings, "_get_local_model", _flaky_get_local_model)
        model_warmup.warm_models()  # must not raise
        assert attempts == ["1", None]
        assert "HF_HUB_OFFLINE" not in os.environ

    def test_loader_failure_swallowed_with_single_warning(self, monkeypatch, local_backend, caplog):
        """A permanently failing loader is swallowed with exactly one
        warning per step -- never raised into boot -- and the offline env
        is still restored."""
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

        def _boom(model_name=None):
            raise RuntimeError("weights unreadable")

        monkeypatch.setattr(embeddings, "_get_local_model", _boom)
        with caplog.at_level(logging.WARNING, logger="cairn"):
            model_warmup.warm_models()
        warnings = [r for r in caplog.records if "warm-up" in r.getMessage()]
        assert len(warnings) == 1  # embedder step only; reranker step stayed quiet
        assert "HF_HUB_OFFLINE" not in os.environ


class _WarmupStubServer:
    """Loopback OpenAI-compatible /v1 stand-in on an ephemeral port.

    Records every request (method, path, JSON body) and serves GET
    /v1/models listing ``model_ids`` (OpenAI-shaped) plus POST
    /v1/embeddings with one unit vector per input.
    """

    def __init__(self, model_ids=("bge-m3",)):
        self.requests = []
        self._model_ids = list(model_ids)
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append({"method": "GET", "path": self.path})
                body = json.dumps(
                    {
                        "object": "list",
                        "data": [
                            {"id": i, "object": "model"} for i in outer._model_ids
                        ],
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                payload = json.loads(raw.decode("utf-8"))
                outer.requests.append(
                    {"method": "POST", "path": self.path, "body": payload}
                )
                body = json.dumps(
                    {
                        "data": [
                            {"index": i, "embedding": [0.0]}
                            for i in range(len(payload["input"]))
                        ]
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def hits(self, path: str) -> list[dict]:
        return [r for r in self.requests if r["path"] == path]

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()


def _closed_loopback_port() -> int:
    """An ephemeral port that is free right now: bound, then released."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestServerBackendWarmup:
    """FR-006: a healthy server backend is warmed with one tiny
    /v1/embeddings POST through the shared T003 probe cache; every guard
    from the local arm still applies."""

    @pytest.fixture
    def server_backend(self, monkeypatch):
        """CAIRN_EMBED_BACKEND=server with fresh backend/probe caches.

        Tests point CAIRN_EMBED_BASE_URL at their own loopback stub (or a
        closed port for the unhealthy probe)."""
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        embeddings.reset_backend_cache()
        yield
        embeddings.reset_backend_cache()

    @pytest.fixture
    def stub_server(self):
        server = _WarmupStubServer()
        yield server
        server.close()

    def test_healthy_cached_probe_fires_one_tiny_post_without_reprobing(
        self, server_backend, stub_server, monkeypatch
    ):
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        # The boot path already gated on availability: the probe verdict is
        # cached, so warm-up must consume it -- no second /v1/models hit.
        assert embeddings.embeddings_available()
        model_warmup.warm_models()
        assert len(stub_server.hits("/v1/models")) == 1
        posts = stub_server.hits("/v1/embeddings")
        assert len(posts) == 1
        assert posts[0]["body"]["input"] == ["warmup"]  # tiny single-text input
        assert posts[0]["body"]["model"] == "bge-m3"

    def test_cold_probe_cache_probes_once_then_posts_once(
        self, server_backend, stub_server, monkeypatch
    ):
        """With nothing pre-probed, the warm step itself populates the
        shared probe cache (one GET) and then fires its one POST."""
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        model_warmup.warm_models()
        assert [r["path"] for r in stub_server.requests] == [
            "/v1/models",
            "/v1/embeddings",
        ]

    def test_unreachable_server_warns_once_and_never_raises(
        self, server_backend, monkeypatch, caplog
    ):
        monkeypatch.setenv(
            "CAIRN_EMBED_BASE_URL",
            f"http://127.0.0.1:{_closed_loopback_port()}/v1",
        )
        with caplog.at_level(logging.WARNING, logger="cairn"):
            model_warmup.warm_models()  # must not raise into boot
        warnings = [r for r in caplog.records if "warm-up" in r.getMessage()]
        assert len(warnings) == 1

    def test_model_missing_from_listing_never_posts(
        self, server_backend, monkeypatch, caplog
    ):
        """A reachable server whose /v1/models omits the configured model
        fails the FR-002 probe: observable here as zero /embeddings hits,
        not just an unconnectable port."""
        stub = _WarmupStubServer(model_ids=["some-other-model"])
        try:
            monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub.base_url)
            with caplog.at_level(logging.WARNING, logger="cairn"):
                model_warmup.warm_models()
            assert [r["path"] for r in stub.requests] == ["/v1/models"]
            warnings = [r for r in caplog.records if "warm-up" in r.getMessage()]
            assert len(warnings) == 1
        finally:
            stub.close()

    def test_pytest_guard_blocks_background_server_warmup(
        self, server_backend, stub_server, monkeypatch
    ):
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        monkeypatch.setattr(model_warmup, "_inside_pytest", lambda: True)
        assert model_warmup.warm_models_in_background() is None
        assert model_warmup._WARM_THREAD is None
        assert stub_server.requests == []  # no probe, no POST

    def test_server_arm_never_sets_offline_env(
        self, server_backend, stub_server, monkeypatch
    ):
        """The offline vars are a local-HF-cache concern; the server arm
        makes no HF calls, so the env must be untouched at POST time."""
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
        monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
        observed = []
        real_embed_server = embeddings._embed_server

        def _spy(texts):
            observed.append(
                (
                    os.environ.get("HF_HUB_OFFLINE"),
                    os.environ.get("TRANSFORMERS_OFFLINE"),
                )
            )
            return real_embed_server(texts)

        monkeypatch.setattr(embeddings, "_embed_server", _spy)
        model_warmup.warm_models()
        assert observed == [(None, None)]
        assert "HF_HUB_OFFLINE" not in os.environ
        assert "TRANSFORMERS_OFFLINE" not in os.environ


class TestBackgroundThread:
    def test_returns_daemon_thread_and_loads(self, local_backend, embed_calls):
        thread = model_warmup.warm_models_in_background()
        assert isinstance(thread, threading.Thread)
        assert thread.daemon is True  # never blocks process exit
        assert thread.name == "cairn-model-warmup"
        thread.join(timeout=5)
        assert len(embed_calls) == 1

    def test_enabled_by_default(self, monkeypatch, local_backend, embed_calls):
        monkeypatch.delenv("CAIRN_WARM_MODELS", raising=False)
        thread = model_warmup.warm_models_in_background()
        assert thread is not None
        thread.join(timeout=5)
        assert len(embed_calls) == 1

    @pytest.mark.parametrize("off", ["0", "false", "no"])
    def test_kill_switch_disables_entirely(self, off, monkeypatch, local_backend, embed_calls, rerank_calls):
        monkeypatch.setenv("CAIRN_WARM_MODELS", off)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        assert model_warmup.warm_models_in_background() is None
        assert model_warmup._WARM_THREAD is None
        assert embed_calls == []
        assert rerank_calls == []

    def test_idempotent_single_load_sequence(self, monkeypatch, local_backend, embed_calls, rerank_calls):
        """Two calls -> one thread, one load of each model. The second call
        returns the already-started thread rather than spawning a duplicate
        load that would race the first over the model caches."""
        monkeypatch.setenv("CAIRN_RERANK", "1")
        first = model_warmup.warm_models_in_background()
        second = model_warmup.warm_models_in_background()
        assert second is first
        first.join(timeout=5)
        assert len(embed_calls) == 1
        assert len(rerank_calls) == 1

    def test_repeat_call_after_finish_does_not_reload(self, local_backend, embed_calls):
        """Warm-up is once-per-process even after the thread finishes: a
        long-lived server must not restart weight loads on a later call."""
        first = model_warmup.warm_models_in_background()
        first.join(timeout=5)
        again = model_warmup.warm_models_in_background()
        assert again is first
        assert len(embed_calls) == 1

    def test_never_starts_inside_pytest_test(self, monkeypatch, local_backend, embed_calls, rerank_calls):
        """In-process test boots (server.run() called from a test) must not
        start the background load: the thread outlives the test and collides
        with later tests' monkeypatched loaders."""
        monkeypatch.setattr(model_warmup, "_inside_pytest", lambda: True)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        assert model_warmup.warm_models_in_background() is None
        assert model_warmup._WARM_THREAD is None
        assert embed_calls == []
        assert rerank_calls == []

    def test_inside_pytest_reads_pytest_env_marker(self, monkeypatch):
        """The production check is the PYTEST_CURRENT_TEST env var: set
        during every pytest test, never set in a real server process."""
        monkeypatch.setattr(model_warmup, "_inside_pytest", _REAL_INSIDE_PYTEST)
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_fake.py::test_fake (call)")
        assert model_warmup._inside_pytest() is True
        monkeypatch.delenv("PYTEST_CURRENT_TEST")
        assert model_warmup._inside_pytest() is False
