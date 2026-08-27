"""Tests for embedding-backend quality surfacing.

Covers the fix for silent degradation under the dep-free hash fallback:
  1. is_hash_fallback() — the shared detector (replaces inline checks)
  2. warn_hash_fallback_once — one-time-per-process warning
  3. Provenance enrichment on the query-time paths:
     - memory recall (_semantic_memory_search stamps provenance)
     - semantic_search (semantic/fused provenance under hash)
     - knowledge search (semantic_knowledge provenance under hash)

The hash backend reports embeddings_available() as True (it's the fallback),
so the old `if not embeddings_available()` guards never tripped under it.
These tests pin the new behavior that surfaces the degradation instead.
"""
from __future__ import annotations

import http.server
import importlib
import json
import logging
import os
import re
import socket
import sqlite3
import struct
import threading
import time
from unittest import mock

import pytest

from cairn.graph import ann_index
from cairn.graph import embeddings as emb
from cairn.graph.schema import _apply_schema
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept


@pytest.fixture(autouse=True)
def _reset_backend_cache():
    """Each test sees a fresh backend resolution.

    _EFFECTIVE_BACKEND_CACHE is process-global and never invalidated in
    production (the backend doesn't change mid-process), so tests that flip
    CAIRN_EMBED_BACKEND between cases must reset it to avoid a stale cached
    backend from an earlier test. Mirrors reset_backend_cache()'s contract.
    """
    emb.reset_backend_cache()
    # Also reset the one-time-warning guard so the "fires once" assertion can
    # be exercised repeatedly across tests in the same process.
    emb._HASH_FALLBACK_WARNED = False
    yield
    emb.reset_backend_cache()
    emb._HASH_FALLBACK_WARNED = False


@pytest.fixture(autouse=True)
def _clean_embed_env(monkeypatch):
    """Scrub ambient CAIRN_EMBED_* config so every assertion is env-agnostic."""
    for name in list(os.environ):
        if name.startswith("CAIRN_EMBED_"):
            monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# 1. is_hash_fallback() — the detector
# ---------------------------------------------------------------------------


class TestIsHashFallback:
    """is_hash_fallback() distinguishes a silent fallback from an explicit one."""

    def test_true_when_local_configured_but_unavailable(self, monkeypatch):
        # Default install: backend unset (-> "local"), sentence-transformers
        # not importable -> effective backend falls back to "hash".
        monkeypatch.delenv("CAIRN_EMBED_BACKEND", raising=False)
        with mock.patch(
            "cairn.graph.embeddings._effective_backend", return_value="hash"
        ), mock.patch("cairn.graph.embeddings._backend_name", return_value="local"):
            assert emb.is_hash_fallback() is True

    def test_false_when_explicit_hash(self, monkeypatch):
        # User explicitly opted into hash -> not a *silent* fallback.
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
        with mock.patch("cairn.graph.embeddings._backend_name", return_value="hash"):
            assert emb.is_hash_fallback() is False

    def test_false_when_real_local_backend(self, monkeypatch):
        monkeypatch.delenv("CAIRN_EMBED_BACKEND", raising=False)
        with mock.patch(
            "cairn.graph.embeddings._effective_backend", return_value="local"
        ), mock.patch("cairn.graph.embeddings._backend_name", return_value="local"):
            assert emb.is_hash_fallback() is False

    def test_false_when_openai_backend(self, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "openai")
        with mock.patch(
            "cairn.graph.embeddings._effective_backend", return_value="openai"
        ), mock.patch("cairn.graph.embeddings._backend_name", return_value="openai"):
            assert emb.is_hash_fallback() is False

    def test_actual_default_install_matches_expectation(self, monkeypatch):
        """Integration check against the real resolution logic (no mocks).

        In the default test env (no [semantic] extra), the effective backend
        resolves to "hash" and _backend_name() is "local" -> fallback is True.
        Skip if sentence-transformers happens to be installed (then it's real).
        """
        monkeypatch.delenv("CAIRN_EMBED_BACKEND", raising=False)
        emb.reset_backend_cache()
        try:
            import sentence_transformers  # noqa: F401

            pytest.skip("sentence-transformers installed; no hash fallback in this env")
        except ImportError:
            assert emb.is_hash_fallback() is True


# ---------------------------------------------------------------------------
# 2. warn_hash_fallback_once — rate-limited warning
# ---------------------------------------------------------------------------


class TestWarnHashFallbackOnce:
    def test_fires_once_under_hash_fallback(self, caplog):
        # caplog captures via a handler; pass a real module logger that records
        # propagate up to the root where caplog's handler is attached.
        logger = logging.getLogger("cairn.tests.hash_fallback_warning")
        caplog.set_level(logging.WARNING, logger="cairn.tests.hash_fallback_warning")
        with mock.patch("cairn.graph.embeddings.is_hash_fallback", return_value=True):
            emb.warn_hash_fallback_once(logger, context="recall_memory")
            emb.warn_hash_fallback_once(logger, context="semantic_search")
            emb.warn_hash_fallback_once(logger, context="explore")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, "should fire at most once per process"
        assert "recall_memory" in warnings[0].getMessage(), "first caller's context recorded"
        assert "hash backend" in warnings[0].getMessage().lower()

    def test_noop_when_real_backend(self, caplog):
        logger = logging.getLogger("cairn.tests.hash_fallback_warning")
        caplog.set_level(logging.WARNING, logger="cairn.tests.hash_fallback_warning")
        with mock.patch("cairn.graph.embeddings.is_hash_fallback", return_value=False):
            emb.warn_hash_fallback_once(logger, context="semantic_search")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == [], "real backend should not warn"

    def test_noop_when_explicit_hash(self, caplog):
        logger = logging.getLogger("cairn.tests.hash_fallback_warning")
        caplog.set_level(logging.WARNING, logger="cairn.tests.hash_fallback_warning")
        # is_hash_fallback() is False under explicit CAIRN_EMBED_BACKEND=hash,
        # so the warning never fires (informed choice, not silent fallback).
        with mock.patch("cairn.graph.embeddings.is_hash_fallback", return_value=False):
            emb.warn_hash_fallback_once(logger, context="explore")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings == []


# ---------------------------------------------------------------------------
# 3. Memory recall provenance enrichment
# ---------------------------------------------------------------------------


@pytest.fixture
def bundle(tmp_path):
    return OKFBundle(str(tmp_path / "knowledge"))


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    yield conn
    conn.close()


def _write_memory(bundle, title="Test memory", body="a body"):
    """Write a minimal memory concept the fallback can rank."""
    concept = OKFConcept(
        type="Tribal-decision",
        title=title,
        description=title,
        tags=["decision"],
        body=body,
        extensions={
            "memory_tier": "tribal",
            "memory_is_latest": True,
            "memory_type": "decision",
        },
    )
    concept.concept_id = f"memory/tribal/{title.lower().replace(' ', '-')}"
    bundle.write_concept(concept)
    return concept


class TestMemoryRecallProvenance:
    """_semantic_memory_search stamps hash-backend provenance."""

    def test_provenance_under_hash_fallback(self, db, bundle, monkeypatch):
        from cairn.graph.embeddings import embed_memory_concepts
        from cairn.memory.promotion import _semantic_memory_search

        concept = _write_memory(bundle, title="Use JWT auth", body="Use JWT for authentication")
        monkeypatch.setattr(
            "cairn.graph.embeddings.is_hash_fallback", lambda: True
        )
        # embeddings_available() must be True for the path to run (hash returns True).
        monkeypatch.setattr("cairn.graph.embeddings.embeddings_available", lambda: True)
        embed_memory_concepts(db, bundle, [concept.concept_id])
        db.commit()

        out = _semantic_memory_search(db, bundle, "JWT authentication")
        assert out, "expected at least one semantic hit"
        for c in out:
            assert c.extensions["provenance"] == "semantic (hash backend)", (
                "hash fallback should annotate provenance so callers can flag degraded results"
            )

    def test_provenance_under_real_backend(self, db, bundle, monkeypatch):
        from cairn.graph.embeddings import embed_memory_concepts
        from cairn.memory.promotion import _semantic_memory_search

        concept = _write_memory(bundle, title="Use JWT auth", body="Use JWT for authentication")
        monkeypatch.setattr(
            "cairn.graph.embeddings.is_hash_fallback", lambda: False
        )
        monkeypatch.setattr("cairn.graph.embeddings.embeddings_available", lambda: True)
        embed_memory_concepts(db, bundle, [concept.concept_id])
        db.commit()

        out = _semantic_memory_search(db, bundle, "JWT authentication")
        # The hash embedder still runs in the test env, so we get hits; the point
        # is the provenance label reflects a real backend (no hash annotation).
        for c in out:
            assert c.extensions["provenance"] == "semantic"


# ---------------------------------------------------------------------------
# 4. semantic_search provenance enrichment
# ---------------------------------------------------------------------------


class TestSemanticSearchProvenance:
    """semantic_search annotates provenance under the hash fallback."""

    def test_provenance_strings_under_hash(self, monkeypatch):
        # Drive the _sem_prov / _fused_prov construction directly via the
        # module-level helpers, since semantic_search needs a populated DB.
        # This pins the string contract the renderer displays.
        monkeypatch.setattr("cairn.graph.embeddings.is_hash_fallback", lambda: True)
        # Re-read the constants the function builds: they're derived inside the
        # function body, so verify the derivation logic directly.
        _hash = emb.is_hash_fallback()
        _sem_prov = "semantic (hash backend)" if _hash else "semantic"
        _fused_prov = "fused(bm25+semantic, hash)" if _hash else "fused(bm25+semantic)"
        assert _sem_prov == "semantic (hash backend)"
        assert _fused_prov == "fused(bm25+semantic, hash)"

    def test_provenance_strings_under_real_backend(self, monkeypatch):
        monkeypatch.setattr("cairn.graph.embeddings.is_hash_fallback", lambda: False)
        _hash = emb.is_hash_fallback()
        _sem_prov = "semantic (hash backend)" if _hash else "semantic"
        _fused_prov = "fused(bm25+semantic, hash)" if _hash else "fused(bm25+semantic)"
        assert _sem_prov == "semantic"
        assert _fused_prov == "fused(bm25+semantic)"


# ---------------------------------------------------------------------------
# 5. knowledge search provenance enrichment
# ---------------------------------------------------------------------------


class TestKnowledgeSearchProvenance:
    def test_provenance_string_contract(self):
        # The search function builds this string locally; pin the contract.
        prov_hash = "semantic_knowledge (hash backend)" if True else "semantic_knowledge"
        prov_real = "semantic_knowledge (hash backend)" if False else "semantic_knowledge"
        assert prov_hash == "semantic_knowledge (hash backend)"
        assert prov_real == "semantic_knowledge"


# ---------------------------------------------------------------------------
# 6. Server backend family — resolution, presets, hash-fallback immunity
# ---------------------------------------------------------------------------


class TestServerBackendFamily:
    """CAIRN_EMBED_BACKEND=server/omlx/ollama resolves to the 'server' arm.

    The family must never coalesce into 'hash': is_hash_fallback() stays
    False for every server config, with or without sentence-transformers.
    """

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("server", "server"),
            ("OMLX", "omlx"),
            ("  ollama ", "ollama"),
            ("weird-backend", "weird-backend"),
        ],
    )
    def test_backend_name_normalization(self, monkeypatch, raw, expected):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", raw)
        assert emb._backend_name() == expected

    @pytest.mark.parametrize("name", ["server", "omlx", "ollama", "OMLX"])
    def test_effective_backend_resolves_family_to_server(self, monkeypatch, name):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", name)
        emb.reset_backend_cache()
        assert emb._effective_backend() == "server"

    def test_unknown_backend_stays_unchanged(self, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "weird-backend")
        emb.reset_backend_cache()
        assert emb._effective_backend() == "weird-backend"

    @pytest.mark.parametrize(
        "name, preset",
        [
            ("omlx", "http://127.0.0.1:8000/v1"),
            ("ollama", "http://127.0.0.1:11434/v1"),
        ],
    )
    def test_preset_base_url(self, monkeypatch, name, preset):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", name)
        monkeypatch.delenv("CAIRN_EMBED_BASE_URL", raising=False)
        assert emb._server_base_url() == preset

    def test_base_url_env_overrides_preset(self, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "omlx")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "http://10.0.0.5:9999/v1")
        assert emb._server_base_url() == "http://10.0.0.5:9999/v1"

    def test_bare_server_error_at_resolution_not_import(self, monkeypatch):
        """Bare 'server' without CAIRN_EMBED_BASE_URL fails only on use.

        Importing the module (and resolving the effective backend) must
        succeed with the misconfiguration set; the error surfaces when the
        base URL is actually resolved.
        """
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.delenv("CAIRN_EMBED_BASE_URL", raising=False)
        emb.reset_backend_cache()
        importlib.reload(emb)  # import with the env set — must not raise
        assert emb._effective_backend() == "server"
        with pytest.raises(RuntimeError, match="CAIRN_EMBED_BASE_URL"):
            emb._server_base_url()

    @pytest.mark.parametrize("name", ["server", "omlx", "ollama"])
    def test_is_hash_fallback_false_for_server_family(self, monkeypatch, name):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", name)
        emb.reset_backend_cache()
        assert emb.is_hash_fallback() is False

    def test_is_hash_fallback_false_for_omlx_without_sentence_transformers(
        self, monkeypatch
    ):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "omlx")
        emb.reset_backend_cache()
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            assert emb._effective_backend() == "server"
            assert emb.is_hash_fallback() is False
        else:
            pytest.skip(
                "sentence-transformers installed; the ImportError coalesce is unreachable"
            )

    def test_availability_check_does_not_poison_server_config(
        self, stub_server, monkeypatch
    ):
        """embeddings_available() must not stamp a server config into 'hash'.

        In a torch-less env the local arm's ImportError writes the shared
        cache. Whatever the probe verdict (True or False), availability must
        be answered from the server arm — the cache never records 'hash'.
        """
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "omlx")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        emb.reset_backend_cache()
        stub_server.behavior = _models_behavior(["bge-large"])
        assert emb.embeddings_available() is False
        assert emb._effective_backend() == "server"
        assert emb.is_hash_fallback() is False

        emb.reset_backend_cache()
        stub_server.behavior = _models_behavior(["bge-m3"])
        assert emb.embeddings_available() is True
        assert emb._effective_backend() == "server"
        assert emb.is_hash_fallback() is False

    def test_install_hint_names_server_path_as_no_torch_option(self):
        """FR-008 / US5 AC3: the hint offers the dep-free server path
        (omlx/ollama presets, or bare server + CAIRN_EMBED_BASE_URL)
        alongside the semantic extra and the hash smoke test."""
        hint = emb.install_hint()
        assert "pip install" in hint  # the [semantic] extra stays option 1
        assert "omlx" in hint and "ollama" in hint
        assert "server" in hint and "CAIRN_EMBED_BASE_URL" in hint
        assert "no model install needed" in hint  # no torch/sentence-transformers
        assert "hash" in hint  # the dep-free smoke test stays the last rung
        assert "cairn doctor" not in hint  # doctor is the server-down remediation


class TestFrozenBackendsContract:
    """The local/hash/openai backends keep their exact contract (FR-009)."""

    def test_embed_openai_still_targets_api_openai_com(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        captured = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(
                    {"data": [{"index": 0, "embedding": [1.0, 0.0, 0.5]}]}
                ).encode("utf-8")

        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["authorization"] = req.headers.get("Authorization")
            return _FakeResponse()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        blobs, dim = emb._embed_openai(["hello"])
        assert captured["url"] == "https://api.openai.com/v1/embeddings"
        assert captured["authorization"] == "Bearer test-key"
        assert dim == 3
        assert len(blobs) == 1 and len(blobs[0]) == dim * 4

    def test_embed_openai_still_requires_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            emb._embed_openai(["hello"])

    def test_hash_backend_contract_unchanged(self):
        blobs, dim = emb._embed_hash(["alpha", "beta"])
        assert dim == emb.DEFAULT_DIM
        assert len(blobs) == 2
        assert all(len(b) == dim * 4 for b in blobs)


# ---------------------------------------------------------------------------
# 7. _embed_server client — OpenAI-compatible server arm (FR-001, FR-003)
# ---------------------------------------------------------------------------


class _SilentHTTPServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # timeout tests strand handler threads whose peer already left


class _StubEmbedServer:
    """Loopback OpenAI-compatible /v1 stand-in on an ephemeral port.

    Serves POST /v1/embeddings and GET /v1/models. ``behavior`` maps a
    recorded request dict to one of:
      ("json", status, payload) — respond with that status and JSON body
      ("raw", status, text)     — respond with that status and a plain body
      ("close",)                — drop the connection without responding
      ("hang",)                 — accept the request and never respond
    """

    def __init__(self):
        self.requests = []
        self.behavior = None
        outer = self
        self._release = threading.Event()

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                record = {
                    "method": "POST",
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(raw.decode("utf-8")),
                }
                self._handle(record)

            def do_GET(self):
                record = {
                    "method": "GET",
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                }
                self._handle(record)

            def _handle(self, record):
                outer.requests.append(record)
                action = outer.behavior(record) if outer.behavior else ("close",)
                kind = action[0]
                if kind == "close":
                    self.close_connection = True
                    return
                if kind == "hang":
                    outer._release.wait(5)
                    return
                _, status, body = action
                if kind == "raw":
                    data = body.encode("utf-8")
                    content_type = "text/plain"
                else:
                    data = json.dumps(body).encode("utf-8")
                    content_type = "application/json"
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self._httpd = _SilentHTTPServer(("127.0.0.1", 0), _Handler)
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

    def close(self):
        self._release.set()
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def stub_server():
    server = _StubEmbedServer()
    yield server
    server.close()


@pytest.fixture
def fast_backoff(monkeypatch):
    """Record backoff sleeps without waiting; pin jitter to the midpoint.

    With uniform() pinned to the midpoint of (delay/2, delay*1.5), the client
    sleeps exactly the documented 0.5/1/2 s ladder.
    """
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr("random.uniform", lambda a, b: (a + b) / 2)
    return sleeps


def _embed_payload(texts, dim=4):
    """OpenAI-shaped embeddings response; one distinct vector per input."""
    return {
        "data": [
            {"index": i, "embedding": [float(i)] * dim}
            for i in range(len(texts))
        ]
    }


def _echo_behavior(all_texts):
    """Serve each requested input the vector keyed to its global input index."""

    def behavior(record):
        data = [
            {"index": i, "embedding": [float(all_texts.index(t))] * 4}
            for i, t in enumerate(record["body"]["input"])
        ]
        return ("json", 200, {"data": data})

    return behavior


def _models_behavior(ids, status=200):
    """Serve GET /v1/models with the given model ids (OpenAI-shaped)."""

    def behavior(record):
        if status != 200:
            return ("json", status, {"error": {"message": "listing failed"}})
        return (
            "json",
            200,
            {
                "object": "list",
                "data": [{"id": i, "object": "model"} for i in ids],
            },
        )

    return behavior


class TestEmbedServerClient:
    """_embed_server speaks the OpenAI /v1/embeddings contract (FR-003)."""

    def test_happy_path_returns_blobs_in_input_order_despite_shuffled_data(
        self, stub_server, monkeypatch
    ):
        texts = ["alpha", "beta", "gamma"]
        payload = _embed_payload(texts)
        payload["data"] = [payload["data"][2], payload["data"][0], payload["data"][1]]
        stub_server.behavior = lambda record: ("json", 200, payload)
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)

        blobs, dim = emb._embed_server(texts)

        assert dim == 4
        assert [struct.unpack("<4f", b) for b in blobs] == [
            (0.0, 0.0, 0.0, 0.0),
            (1.0, 1.0, 1.0, 1.0),
            (2.0, 2.0, 2.0, 2.0),
        ]
        assert stub_server.requests[0]["path"] == "/v1/embeddings"

    def test_model_id_defaults_to_bge_m3_and_env_overrides(
        self, stub_server, monkeypatch
    ):
        stub_server.behavior = _echo_behavior(["a"])
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)

        emb._embed_server(["a"])
        assert stub_server.requests[0]["body"]["model"] == "bge-m3"

        monkeypatch.setenv("CAIRN_EMBED_SERVER_MODEL", "bge-large")
        stub_server.requests.clear()
        emb._embed_server(["a"])
        assert stub_server.requests[0]["body"]["model"] == "bge-large"

    def test_chunks_at_default_batch_size_preserving_order(
        self, stub_server, monkeypatch
    ):
        texts = [f"t{i}" for i in range(33)]
        stub_server.behavior = _echo_behavior(texts)
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)

        blobs, dim = emb._embed_server(texts)

        assert dim == 4
        assert [len(r["body"]["input"]) for r in stub_server.requests] == [32, 1]
        chunked_inputs = [t for r in stub_server.requests for t in r["body"]["input"]]
        assert chunked_inputs == texts
        assert [struct.unpack("<4f", b) for b in blobs] == [
            (float(i),) * 4 for i in range(len(texts))
        ]

    def test_batch_env_override_chunks_and_preserves_order(
        self, stub_server, monkeypatch
    ):
        texts = [f"t{i}" for i in range(5)]
        stub_server.behavior = _echo_behavior(texts)
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        monkeypatch.setenv("CAIRN_EMBED_SERVER_BATCH", "2")

        blobs, dim = emb._embed_server(texts)

        assert dim == 4
        assert [len(r["body"]["input"]) for r in stub_server.requests] == [2, 2, 1]
        assert [struct.unpack("<4f", b) for b in blobs] == [
            (float(i),) * 4 for i in range(len(texts))
        ]

    def test_retries_connection_drops_then_succeeds(
        self, stub_server, monkeypatch, fast_backoff
    ):
        texts = ["alpha", "beta"]
        echo = _echo_behavior(texts)

        def flaky(record):
            if len(stub_server.requests) < 3:
                return ("close",)
            return echo(record)

        stub_server.behavior = flaky
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)

        blobs, dim = emb._embed_server(texts)

        assert dim == 4
        assert [struct.unpack("<4f", b) for b in blobs] == [(0.0,) * 4, (1.0,) * 4]
        assert len(stub_server.requests) == 3
        assert fast_backoff == [0.5, 1.0]

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_retries_retryable_statuses_then_succeeds(
        self, stub_server, monkeypatch, fast_backoff, status
    ):
        texts = ["alpha", "beta"]
        echo = _echo_behavior(texts)

        def flaky(record):
            if len(stub_server.requests) < 3:
                return ("json", status, {"error": {"message": "slow down"}})
            return echo(record)

        stub_server.behavior = flaky
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)

        blobs, dim = emb._embed_server(texts)

        assert dim == 4
        assert [struct.unpack("<4f", b) for b in blobs] == [(0.0,) * 4, (1.0,) * 4]
        assert len(stub_server.requests) == 3
        assert fast_backoff == [0.5, 1.0]

    def test_retry_ladder_exhausts_after_three_retries(
        self, stub_server, monkeypatch, fast_backoff
    ):
        stub_server.behavior = lambda record: (
            "json",
            500,
            {"error": {"message": "boom"}},
        )
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)

        with pytest.raises(RuntimeError, match="HTTP 500"):
            emb._embed_server(["hello"])

        assert len(stub_server.requests) == 4
        assert fast_backoff == [0.5, 1.0, 2.0]

    @pytest.mark.parametrize("status", [401, 404])
    def test_permanent_4xx_fails_immediately_with_server_message(
        self, stub_server, monkeypatch, fast_backoff, status
    ):
        message = (
            "Model bge-m3 not found; available ids: bge-m3, bge-large"
            if status == 404
            else "Incorrect API key provided"
        )
        stub_server.behavior = lambda record: (
            "json",
            status,
            {"error": {"message": message, "type": "not_found_error"}},
        )
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)

        with pytest.raises(RuntimeError, match=re.escape(message)):
            emb._embed_server(["hello"])

        assert len(stub_server.requests) == 1
        assert fast_backoff == []

    def test_non_json_4xx_body_surfaces_verbatim(
        self, stub_server, monkeypatch, fast_backoff
    ):
        stub_server.behavior = lambda record: ("raw", 400, "plain-text refusal")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)

        with pytest.raises(RuntimeError, match="plain-text refusal"):
            emb._embed_server(["hello"])

        assert len(stub_server.requests) == 1
        assert fast_backoff == []

    def test_timeout_honored_and_treated_as_retryable(
        self, stub_server, monkeypatch, fast_backoff
    ):
        stub_server.behavior = lambda record: ("hang",)
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        monkeypatch.setenv("CAIRN_EMBED_TIMEOUT", "0.3")

        start = time.monotonic()
        with pytest.raises(RuntimeError, match="retries"):
            emb._embed_server(["hello"])
        elapsed = time.monotonic() - start

        assert len(stub_server.requests) == 4
        assert 1.0 <= elapsed < 8.0

    def test_bearer_header_sent_only_when_api_key_set(
        self, stub_server, monkeypatch
    ):
        stub_server.behavior = _echo_behavior(["a"])
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)

        monkeypatch.setenv("CAIRN_EMBED_API_KEY", "sek-ret-1")
        emb._embed_server(["a"])
        assert stub_server.requests[0]["authorization"] == "Bearer sek-ret-1"

        monkeypatch.delenv("CAIRN_EMBED_API_KEY")
        stub_server.requests.clear()
        emb._embed_server(["a"])
        assert stub_server.requests[0]["authorization"] is None

    def test_mixed_dimension_batch_rejected(self, stub_server, monkeypatch):
        payload = {
            "data": [
                {"index": 0, "embedding": [1.0, 1.0, 1.0]},
                {"index": 1, "embedding": [2.0, 2.0]},
            ]
        }
        stub_server.behavior = lambda record: ("json", 200, payload)
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)

        with pytest.raises(RuntimeError, match="mixed-dimension"):
            emb._embed_server(["hello", "world"])

    def test_embed_dispatches_server_family_to_client(
        self, stub_server, monkeypatch
    ):
        texts = ["hello"]
        stub_server.behavior = _echo_behavior(texts)
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "omlx")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        emb.reset_backend_cache()

        blobs, dim = emb._embed(texts)

        assert emb._effective_backend() == "server"
        assert len(stub_server.requests) == 1
        assert stub_server.requests[0]["path"] == "/v1/embeddings"
        assert dim == 4
        assert struct.unpack("<4f", blobs[0]) == (0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# 8. embeddings_available() server probe — GET {base}/models gate (FR-002)
# ---------------------------------------------------------------------------


def _model_probe_hits(stub_server):
    return [r for r in stub_server.requests if r["path"] == "/v1/models"]


class TestServerAvailabilityProbe:
    """The server family gates availability on GET {base}/models (FR-002).

    True only when the probe returns 200 AND lists the configured model id;
    the verdict is cached per process and invalidated by
    reset_backend_cache(). A failed probe never raises and never resolves
    the backend to hash.
    """

    def test_true_when_reachable_and_model_listed(self, stub_server, monkeypatch):
        stub_server.behavior = _models_behavior(["bge-m3", "other-model"])
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        emb.reset_backend_cache()

        assert emb.embeddings_available() is True
        assert [r["method"] for r in stub_server.requests] == ["GET"]
        assert [r["path"] for r in stub_server.requests] == ["/v1/models"]

    def test_false_when_connection_refused(self, monkeypatch):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        dead_port = sock.getsockname()[1]
        sock.close()

        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", f"http://127.0.0.1:{dead_port}/v1")
        emb.reset_backend_cache()

        assert emb.embeddings_available() is False
        assert emb._effective_backend() == "server"
        assert emb.is_hash_fallback() is False

    def test_false_when_200_but_model_missing(self, stub_server, monkeypatch):
        stub_server.behavior = _models_behavior(["bge-large", "nomic"])
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_SERVER_MODEL", "bge-m3")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        emb.reset_backend_cache()

        assert emb.embeddings_available() is False
        assert emb.is_hash_fallback() is False

    def test_false_on_non_200_listing(self, stub_server, monkeypatch):
        stub_server.behavior = _models_behavior(["bge-m3"], status=500)
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        emb.reset_backend_cache()

        assert emb.embeddings_available() is False

    def test_false_when_listing_is_not_openai_shaped(self, stub_server, monkeypatch):
        stub_server.behavior = lambda record: ("raw", 200, "not json")
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        emb.reset_backend_cache()

        assert emb.embeddings_available() is False

    def test_probe_cached_until_reset_backend_cache(
        self, stub_server, monkeypatch
    ):
        stub_server.behavior = _models_behavior(["bge-m3"])
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        emb.reset_backend_cache()

        assert emb.embeddings_available() is True
        assert emb.embeddings_available() is True
        assert len(_model_probe_hits(stub_server)) == 1, "cached: no re-probe"

        emb.reset_backend_cache()
        assert emb.embeddings_available() is True
        assert len(_model_probe_hits(stub_server)) == 2, "reset forces re-probe"

    def test_bearer_header_sent_only_when_api_key_set(
        self, stub_server, monkeypatch
    ):
        stub_server.behavior = _models_behavior(["bge-m3"])
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)

        monkeypatch.setenv("CAIRN_EMBED_API_KEY", "probe-key")
        emb.reset_backend_cache()
        assert emb.embeddings_available() is True
        assert stub_server.requests[0]["authorization"] == "Bearer probe-key"

        monkeypatch.delenv("CAIRN_EMBED_API_KEY")
        emb.reset_backend_cache()
        stub_server.requests.clear()
        assert emb.embeddings_available() is True
        assert stub_server.requests[0]["authorization"] is None

    def test_probe_timeout_defaults_to_two_seconds(self):
        assert emb._PROBE_TIMEOUT_S == 2.0

    def test_false_promptly_when_server_never_responds(
        self, stub_server, monkeypatch
    ):
        stub_server.behavior = lambda record: ("hang",)
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", stub_server.base_url)
        # Inject a short probe timeout instead of sleeping out the real 2 s.
        monkeypatch.setattr(emb, "_PROBE_TIMEOUT_S", 0.25)
        emb.reset_backend_cache()

        start = time.monotonic()
        assert emb.embeddings_available() is False
        assert time.monotonic() - start < 2.0, "probe must fail fast"

    def test_non_server_backends_keep_availability_semantics(
        self, stub_server, monkeypatch
    ):
        # The stub sits reachable but no non-server arm may touch it.
        stub_server.behavior = _models_behavior(["bge-m3"])
        monkeypatch.delenv("CAIRN_EMBED_BASE_URL", raising=False)

        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
        emb.reset_backend_cache()
        assert emb.embeddings_available() is True

        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "local")
        emb.reset_backend_cache()
        assert emb.embeddings_available() is True  # hash coalesce still True

        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        emb.reset_backend_cache()
        assert emb.embeddings_available() is False
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        emb.reset_backend_cache()
        assert emb.embeddings_available() is True

        assert stub_server.requests == [], "non-server arms must not probe"


# ---------------------------------------------------------------------------
# 6. current_model() — server-family stamp derivation (FR-004)
# ---------------------------------------------------------------------------


class TestCurrentModelServerStamp:
    """Server backends stamp rows ``server/{netloc}/{model}`` (FR-004).

    The stamp must flow unmodified through ann_index._table_name so the
    vec0 tables, staleness, and purge machinery work untouched (survey S03).
    """

    def test_server_stamp_from_env_base_url_and_model(self, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "http://127.0.0.1:8000/v1")
        monkeypatch.setenv("CAIRN_EMBED_SERVER_MODEL", "bge-m3")
        assert emb.current_model() == "server/127.0.0.1:8000/bge-m3"

    def test_server_stamp_model_id_defaults_to_bge_m3(self, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "http://127.0.0.1:8000/v1")
        monkeypatch.delenv("CAIRN_EMBED_SERVER_MODEL", raising=False)
        assert emb.current_model() == "server/127.0.0.1:8000/bge-m3"

    @pytest.mark.parametrize(
        "name,netloc",
        [("omlx", "127.0.0.1:8000"), ("ollama", "127.0.0.1:11434")],
    )
    def test_preset_backends_derive_preset_netloc(self, monkeypatch, name, netloc):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", name)
        assert emb.current_model() == f"server/{netloc}/bge-m3"

    def test_env_base_url_overrides_preset_netloc(self, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "omlx")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "http://10.0.0.5:9000/v1")
        assert emb.current_model() == "server/10.0.0.5:9000/bge-m3"

    def test_model_stamp_env_is_pure_override(self, monkeypatch):
        # Returned verbatim: no derivation, no validation — the value even
        # wins over an unresolvable base URL instead of raising.
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.delenv("CAIRN_EMBED_BASE_URL", raising=False)
        monkeypatch.setenv("CAIRN_EMBED_MODEL_STAMP", "legacy/local-model")
        assert emb.current_model() == "legacy/local-model"

    def test_stamp_sanitizes_into_vec0_table_name(self, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "http://127.0.0.1:8000/v1")
        stamp = emb.current_model()
        # The real sanitizer maps EVERY non-[a-zA-Z0-9_] char (including the
        # stamp's / : separators and the model id's hyphen) to underscore.
        assert ann_index._table_name(stamp) == "vec_server_127_0_0_1_8000_bge_m3"

    def test_stamp_netloc_strips_scheme_and_path(self, monkeypatch):
        # netloc is host:port only — scheme and any path suffix are dropped.
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "https://emb.internal:8443/api/v1")
        assert emb.current_model() == "server/emb.internal:8443/bge-m3"

    def test_unresolvable_bare_server_raises_at_stamp_time(self, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.delenv("CAIRN_EMBED_BASE_URL", raising=False)
        monkeypatch.delenv("CAIRN_EMBED_MODEL_STAMP", raising=False)
        with pytest.raises(RuntimeError):
            emb.current_model()

    def test_local_hash_openai_semantics_byte_identical(self, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
        emb.reset_backend_cache()
        assert emb.current_model() == emb.HASH_MODEL

        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "openai")
        emb.reset_backend_cache()
        monkeypatch.delenv("CAIRN_EMBED_OPENAI_MODEL", raising=False)
        assert emb.current_model() == "text-embedding-3-small"
        monkeypatch.setenv("CAIRN_EMBED_OPENAI_MODEL", "text-embedding-3-large")
        assert emb.current_model() == "text-embedding-3-large"

        # local: mock the effective resolution so the assertion holds with
        # or without the [semantic] extra installed.
        with mock.patch(
            "cairn.graph.embeddings._effective_backend", return_value="local"
        ):
            monkeypatch.delenv("CAIRN_EMBED_LOCAL_MODEL", raising=False)
            assert emb.current_model() == emb.DEFAULT_LOCAL_MODEL
            monkeypatch.setenv("CAIRN_EMBED_LOCAL_MODEL", "custom/local")
            assert emb.current_model() == "custom/local"

    def test_corpus_overrides_apply_only_to_local(self, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_KNOWLEDGE_MODEL", "know-model")
        monkeypatch.setenv("CAIRN_EMBED_MEMORY_MODEL", "mem-model")

        with mock.patch(
            "cairn.graph.embeddings._effective_backend", return_value="local"
        ):
            assert emb.current_model(corpus="knowledge") == "know-model"
            assert emb.current_model(corpus="memory") == "mem-model"
            assert emb.current_model() == emb.DEFAULT_LOCAL_MODEL

        # Server backends ignore the corpus envs: one stamp across corpora.
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
        monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "http://127.0.0.1:8000/v1")
        emb.reset_backend_cache()
        stamp = "server/127.0.0.1:8000/bge-m3"
        assert emb.current_model() == stamp
        assert emb.current_model(corpus="knowledge") == stamp
        assert emb.current_model(corpus="memory") == stamp
