"""Unit tests for the FR-005 parity sampler and the FR-012 availability
ladder (graph/embed_ladder.py).

Parity tests: no HTTP, embed_fn is always a stub, every BLOB handcrafted
float32-LE; sampling is deterministic (first-N by rowid), so stubs return
blobs in rowid order. Ladder tests: HTTP only against a loopback stub on an
ephemeral port (or monkeypatched fetch/embed seams) — never the network.
"""
from __future__ import annotations

import http.server
import json
import logging
import math
import os
import struct
import threading
from unittest import mock
from urllib.parse import urlsplit

import pytest

from cairn.graph import embed_ladder
from cairn.graph import embeddings as emb

DIM = 8


# ---------------------------------------------------------------------------
# Vector/blob helpers (pure python; float32-LE like the storage layer).
# ---------------------------------------------------------------------------


def _blob(vec):
    return struct.pack(f"<{len(vec)}f", *vec)


def _unit(vec):
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def _orthogonal(vec):
    """Unit vector orthogonal to ``vec`` (Gram-Schmidt of e_0 against vec)."""
    v = _unit(vec)
    e = [1.0] + [0.0] * (len(vec) - 1)
    dot = sum(a * b for a, b in zip(e, v))
    u = [a - dot * b for a, b in zip(e, v)]
    norm = math.sqrt(sum(x * x for x in u))
    if norm < 1e-9:  # vec parallel to e_0 -- fall back to e_1
        e = [0.0, 1.0] + [0.0] * (len(vec) - 2)
        dot = sum(a * b for a, b in zip(e, v))
        u = [a - dot * b for a, b in zip(e, v)]
        norm = math.sqrt(sum(x * x for x in u))
    return [x / norm for x in u]


def _insert(conn, symbol_id, stamp, vec):
    conn.execute(
        "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk, content_hash) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            symbol_id,
            stamp,
            len(vec),
            _blob(vec),
            f"chunk text for {symbol_id}",
            f"hash-{symbol_id}",
        ),
    )


def _counting_stub(blobs, dim):
    """Stub embed_fn returning the given blobs in input order; records calls."""
    calls = []

    def stub(texts):
        calls.append(list(texts))
        return list(blobs), dim

    stub.calls = calls
    return stub


# ---------------------------------------------------------------------------
# Gate constants (D-007: named module constant, no env override).
# ---------------------------------------------------------------------------


def test_gate_constants():
    assert embed_ladder.PARITY_GATE == 0.98
    assert embed_ladder.SAMPLE_LIMIT == 16


# ---------------------------------------------------------------------------
# (a) Pass case: identical blobs -> cosine 1.0.
# ---------------------------------------------------------------------------


def test_pass_identical_blobs(fresh_db):
    stamp = "server/127.0.0.1:8000/bge-m3"
    vecs = [_unit([1.0 + i, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]) for i in range(3)]
    for i, vec in enumerate(vecs):
        _insert(fresh_db, f"s{i}", stamp, vec)
    stub = _counting_stub([_blob(v) for v in vecs], DIM)

    result = embed_ladder.check_parity(fresh_db, stamp, embed_fn=stub)

    assert result.passed is True
    assert result.dim_match is True
    assert result.sampled == 3
    assert result.mean_cosine == pytest.approx(1.0)
    assert result.reason == "parity_ok"
    # one batched call carrying every sampled chunk
    assert len(stub.calls) == 1
    assert stub.calls[0] == [f"chunk text for s{i}" for i in range(3)]


# ---------------------------------------------------------------------------
# (b) Truncation-divergence case: served vectors at cosine ~0.991 vs stored
# (D-004 worst case) stay ABOVE the 0.98 gate.
# ---------------------------------------------------------------------------


def test_truncation_divergence_still_passes(fresh_db):
    eps = math.sqrt(1.0 / 0.991**2 - 1.0)  # cos(v, v + eps*u) = 1/sqrt(1+eps^2)
    stored_blobs = []
    served_blobs = []
    for i in range(4):
        stored = _unit([(i + 1) * (j + 1) + 0.5 * j for j in range(DIM)])
        served = _unit(
            [x + eps * y for x, y in zip(stored, _orthogonal(stored))]
        )
        stored_blobs.append(_blob(stored))
        served_blobs.append(_blob(served))
        _insert(fresh_db, f"s{i}", "stamp", stored)
    stub = _counting_stub(served_blobs, DIM)

    result = embed_ladder.check_parity(fresh_db, "stamp", embed_fn=stub)

    assert result.passed is True
    assert result.mean_cosine is not None
    assert result.mean_cosine >= embed_ladder.PARITY_GATE
    assert result.mean_cosine < 1.0  # genuinely divergent, not identical
    assert result.mean_cosine == pytest.approx(0.991, abs=2e-3)


# ---------------------------------------------------------------------------
# (c) Fail case: orthogonal stub -> fail; the reason carries the measured mean.
# ---------------------------------------------------------------------------


def test_orthogonal_fail_carries_measured_mean(fresh_db):
    stored = _unit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    _insert(fresh_db, "s0", "stamp", stored)
    stub = _counting_stub([_blob(_orthogonal(stored))], DIM)

    result = embed_ladder.check_parity(fresh_db, "stamp", embed_fn=stub)

    assert result.passed is False
    assert result.dim_match is True
    assert result.sampled == 1
    assert result.mean_cosine == pytest.approx(0.0, abs=1e-5)
    assert f"{result.mean_cosine:.4f}" in result.reason
    assert "0.98" in result.reason


# ---------------------------------------------------------------------------
# (d) Dim mismatch -> fail naming both dims.
# ---------------------------------------------------------------------------


def test_dim_mismatch_names_both_dims(fresh_db):
    _insert(fresh_db, "s0", "stamp", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    wide = [0.5] * 16
    stub = _counting_stub([_blob(wide)], 16)

    result = embed_ladder.check_parity(fresh_db, "stamp", embed_fn=stub)

    assert result.passed is False
    assert result.dim_match is False
    assert result.mean_cosine is None
    assert "stored=8" in result.reason
    assert "server=16" in result.reason


# ---------------------------------------------------------------------------
# (e) Zero rows under the stamp -> vacuous pass; embed_fn never called.
# (Rows under a DIFFERENT stamp don't count.)
# ---------------------------------------------------------------------------


def test_zero_rows_under_stamp_is_vacuous_pass(fresh_db):
    _insert(fresh_db, "other", "other/stamp", [1.0] * DIM)

    def stub(texts):
        raise AssertionError("embed_fn must not run with zero stored rows")

    result = embed_ladder.check_parity(fresh_db, "target/stamp", embed_fn=stub)

    assert result.passed is True
    assert result.sampled == 0
    assert result.mean_cosine is None


# ---------------------------------------------------------------------------
# (f) sample_limit: >16 rows under the stamp -> at most 16 embedded.
# ---------------------------------------------------------------------------


def test_sample_limit_caps_embedded_texts_at_16(fresh_db):
    vec = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    for i in range(20):
        _insert(fresh_db, f"s{i}", "stamp", vec)
    # identical-vector stub: one blob per received text (sampler sends <=16)
    def per_text(texts):
        return [_blob(vec)] * len(texts), DIM

    stub = mock.Mock(side_effect=per_text)
    result = embed_ladder.check_parity(fresh_db, "stamp", embed_fn=stub)

    assert result.sampled == 16
    assert stub.call_count == 1
    assert len(stub.call_args[0][0]) == 16
    assert result.passed is True  # identical blobs


# ---------------------------------------------------------------------------
# Default embed_fn resolves to the T002 server client (late-bound, injectable).
# ---------------------------------------------------------------------------


def test_default_embed_fn_is_server_client(fresh_db, monkeypatch):
    vec = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    _insert(fresh_db, "s0", "stamp", vec)
    probe = mock.Mock(return_value=([_blob(vec)], DIM))
    monkeypatch.setattr(emb, "_embed_server", probe)

    result = embed_ladder.check_parity(fresh_db, "stamp")

    probe.assert_called_once_with(["chunk text for s0"])
    assert result.passed is True
    assert result.mean_cosine == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# FR-012 availability ladder.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_ladder_cache():
    """Every ladder verdict and session adoption dies with its test."""
    emb.reset_backend_cache()
    yield
    emb.reset_backend_cache()


@pytest.fixture(autouse=True)
def _clear_telemetry_buffer():
    """The telemetry sink buffer is module-global; keep emit assertions hermetic."""
    from cairn.telemetry import sink

    with sink._LOCK:
        sink._BUFFER.clear()
    yield
    with sink._LOCK:
        sink._BUFFER.clear()


class _StubLadderServer:
    """Loopback /v1 stand-in: GET /models listing + POST /embeddings echo."""

    def __init__(self, model_ids, vec_for):
        self.model_ids = list(model_ids)
        self.vec_for = vec_for
        self.requests = []
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def _send(self, payload):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                self._send(
                    json.dumps(
                        {"data": [{"id": mid} for mid in outer.model_ids]}
                    ).encode("utf-8")
                )

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                outer.requests.append(body)
                self._send(
                    json.dumps(
                        {
                            "data": [
                                {"index": i, "embedding": list(outer.vec_for(t))}
                                for i, t in enumerate(body["input"])
                            ]
                        }
                    ).encode("utf-8")
                )

            def log_message(self, *args):
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
            kwargs={"poll_interval": 0.05},
        ).start()

    @property
    def base_url(self):
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()


def _dead_base_url():
    """A loopback URL whose port is (transiently) guaranteed refusing."""
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    host, port = httpd.server_address[:2]
    httpd.server_close()
    return f"http://{host}:{port}/v1"


def _server_env(monkeypatch, base_url, model="gone-model"):
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", base_url)
    monkeypatch.setenv("CAIRN_EMBED_SERVER_MODEL", model)
    emb.reset_backend_cache()


def _seed_corpus(conn, stamp, vec, n=3):
    for i in range(n):
        _insert(conn, f"s{i}", stamp, vec)


def _stamp_for(base_url, model):
    """The derived server stamp for a stub base URL + request model id."""
    return f"server/{urlsplit(base_url).netloc}/{model}"


def _stub_local_fallback(monkeypatch, parity_pass=True, cached=True):
    """Make rung 2 deterministic: importable, cached, parity per flag."""
    monkeypatch.setattr(embed_ladder, "_sentence_transformers_available", lambda: True)
    monkeypatch.setattr(emb, "model_is_cached", lambda name=None: cached)
    stored = _unit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])

    def fake_local(model_name):
        vec = stored if parity_pass else _orthogonal(stored)
        return lambda texts: ([_blob(vec)] * len(texts), DIM)

    monkeypatch.setattr(embed_ladder, "_local_embed_fn", fake_local)


# (a) rung 1: probe-down trigger (configured model absent from the listing)
# + parity-passing candidate -> session alias adoption.


def test_rung1_adopts_parity_passing_candidate(fresh_db, monkeypatch):
    stored = _unit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    server = _StubLadderServer(["cand-a"], vec_for=lambda t: stored)
    try:
        _server_env(monkeypatch, server.base_url)
        stamp = _stamp_for(server.base_url, "gone-model")
        _seed_corpus(fresh_db, stamp, stored)
        writes_before = fresh_db.total_changes

        state = embed_ladder.evaluate_ladder(conn=fresh_db)

        assert state is not None
        assert state.rung == 1
        assert state.reason == "fallback_session_alias"
        assert state.adopted_model == "cand-a"
        assert state.active is True
        assert "--adopt-server-model" in state.detail
        assert "cand-a" in state.detail
        assert embed_ladder.ladder_state() is state
        # alias binding: reads/writes stay on the stored stamp (zero re-embed)
        # while requests go through the adopted candidate
        assert emb.current_model() == stamp
        assert emb._server_model() == "cand-a"
        # zero DB writes during evaluation
        assert fresh_db.total_changes == writes_before
        # embeds/queries ride the candidate; availability is restored
        blob, dim = emb.embed_query("chunk text for s0")
        assert dim == DIM
        assert server.requests[-1]["model"] == "cand-a"
        assert emb.embeddings_available() is True
    finally:
        server.close()


# (b) candidate parity-fails -> falls through to rung 2 (local).


def test_rung1_parity_fail_falls_through_to_rung2_local(fresh_db, monkeypatch):
    stored = _unit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    server = _StubLadderServer(["cand-a"], vec_for=lambda t: _orthogonal(stored))
    try:
        _server_env(monkeypatch, server.base_url)
        stamp = _stamp_for(server.base_url, "gone-model")
        _seed_corpus(fresh_db, stamp, stored)
        _stub_local_fallback(monkeypatch, parity_pass=True)

        state = embed_ladder.evaluate_ladder(conn=fresh_db)

        assert state.rung == 2
        assert state.reason == "fallback_local"
        assert state.adopted_model == emb.DEFAULT_LOCAL_MODEL
        assert state.active is True
        # session backend switch is in-process only; env untouched
        assert emb._effective_backend() == "local"
        assert emb.current_model() == emb.DEFAULT_LOCAL_MODEL
        assert emb.is_hash_fallback() is False
        assert emb._backend_name() == "server"
    finally:
        server.close()


# (b2) no conn -> parity can prove nothing -> rung 1 and rung 2 decline.


def test_no_conn_declines_candidate_and_local_lands_rung3(fresh_db, monkeypatch):
    stored = _unit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    server = _StubLadderServer(["cand-a"], vec_for=lambda t: stored)
    try:
        _server_env(monkeypatch, server.base_url)
        _stub_local_fallback(monkeypatch)  # rung 2 would pass, given a conn

        state = embed_ladder.evaluate_ladder()

        assert state.rung == 3
        assert state.reason == "model_missing"
        assert state.adopted_model is None
        assert emb._effective_backend() == "server"
    finally:
        server.close()


# (c) rung 2: dead server -> cached local model + parity pass -> local.


def test_server_down_falls_to_local_rung2(fresh_db, monkeypatch):
    dead = _dead_base_url()
    _server_env(monkeypatch, dead)
    _seed_corpus(fresh_db, _stamp_for(dead, "gone-model"), _unit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]))
    _stub_local_fallback(monkeypatch, parity_pass=True)

    state = embed_ladder.evaluate_ladder(conn=fresh_db)

    assert state.rung == 2
    assert state.reason == "fallback_local"
    assert emb._effective_backend() == "local"
    assert emb.current_model() == emb.DEFAULT_LOCAL_MODEL
    assert emb.embeddings_available() is True
    assert os.environ["CAIRN_EMBED_BACKEND"] == "server"  # env untouched


# (d) rung 3: dead server, no local fallback -> terminal state naming the
# trigger.


def test_server_down_without_local_lands_rung3_server_down(fresh_db, monkeypatch):
    dead = _dead_base_url()
    _server_env(monkeypatch, dead)
    _seed_corpus(fresh_db, _stamp_for(dead, "gone-model"), _unit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]))
    _stub_local_fallback(monkeypatch, cached=False)

    state = embed_ladder.evaluate_ladder(conn=fresh_db)

    assert state.rung == 3
    assert state.reason == "server_down"
    assert state.adopted_model is None
    assert state.active is True
    assert emb._effective_backend() == "server"
    assert emb.is_hash_fallback() is False


# (e) verdict cached per process; force re-evaluates; reset_backend_cache()
# clears the state AND the session overrides.


def test_evaluation_cached_until_reset_or_force(fresh_db, monkeypatch):
    _server_env(monkeypatch, "http://127.0.0.1:1/v1")
    monkeypatch.setattr(embed_ladder, "_sentence_transformers_available", lambda: False)
    fetch = mock.Mock(return_value=[])
    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", fetch)

    first = embed_ladder.evaluate_ladder(conn=fresh_db)
    assert first.rung == 3
    assert fetch.call_count == 1
    assert embed_ladder.evaluate_ladder(conn=fresh_db) is first
    assert fetch.call_count == 1  # cached, listing not re-fetched
    again = embed_ladder.evaluate_ladder(conn=fresh_db, force=True)
    assert fetch.call_count == 2
    assert again.rung == 3
    assert again is not first


def test_reset_backend_cache_clears_state_and_session_overrides(fresh_db, monkeypatch):
    stored = _unit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    _server_env(monkeypatch, "http://127.0.0.1:1/v1")
    stamp = "server/127.0.0.1:1/gone-model"
    _seed_corpus(fresh_db, stamp, stored)
    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", lambda: ["cand-a"])

    def fake_embed(texts, model_id):
        return [_blob(stored)] * len(texts), DIM

    monkeypatch.setattr(embed_ladder, "_embed_with_model", fake_embed)

    state = embed_ladder.evaluate_ladder(conn=fresh_db)
    assert state.rung == 1
    assert emb._SESSION_STAMP_OVERRIDE == stamp
    assert emb._SESSION_SERVER_MODEL == "cand-a"

    emb.reset_backend_cache()

    assert embed_ladder.ladder_state() is None
    assert emb._SESSION_STAMP_OVERRIDE is None
    assert emb._SESSION_SERVER_MODEL is None
    assert emb._SESSION_BACKEND_OVERRIDE is None
    assert emb.current_model() == stamp  # derived again, not pinned


# (f) explicit env alias beats the session adoption.


def test_env_alias_beats_session_adoption(fresh_db, monkeypatch):
    stored = _unit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    _server_env(monkeypatch, "http://127.0.0.1:1/v1")
    _seed_corpus(fresh_db, "server/127.0.0.1:1/gone-model", stored)
    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", lambda: ["cand-a"])

    def fake_embed(texts, model_id):
        return [_blob(stored)] * len(texts), DIM

    monkeypatch.setattr(embed_ladder, "_embed_with_model", fake_embed)

    state = embed_ladder.evaluate_ladder(conn=fresh_db)
    assert state.rung == 1  # session adoption happened...
    monkeypatch.setenv("CAIRN_EMBED_MODEL_STAMP", "explicit/user-alias")
    # ...but explicit user intent wins over the session stamp
    assert emb.current_model() == "explicit/user-alias"


# (g) non-server backend: evaluate_ladder no-ops, state stays None.


def test_non_server_backend_nop(fresh_db, monkeypatch):
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "local")
    emb.reset_backend_cache()
    fetch = mock.Mock(return_value=["cand-a"])
    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", fetch)

    assert embed_ladder.evaluate_ladder(conn=fresh_db) is None
    assert embed_ladder.ladder_state() is None
    fetch.assert_not_called()


# (h) hash is never a rung: no candidates + no sentence-transformers ->
# rung 3, never the hash backend (D-003).


def test_no_candidates_and_no_st_does_not_reach_hash(fresh_db, monkeypatch):
    _server_env(monkeypatch, "http://127.0.0.1:1/v1")
    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", lambda: [])
    monkeypatch.setattr(embed_ladder, "_sentence_transformers_available", lambda: False)

    state = embed_ladder.evaluate_ladder(conn=fresh_db)

    assert state.rung == 3
    assert state.reason == "model_missing"
    assert emb.is_hash_fallback() is False
    assert emb._effective_backend() == "server"


# Bare 'server' without a base URL: fetch and stamp resolution fail safe ->
# rung 3, and evaluate_ladder never raises into the caller.


def test_unresolvable_base_url_lands_rung3_without_raising(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.delenv("CAIRN_EMBED_BASE_URL", raising=False)
    emb.reset_backend_cache()

    state = embed_ladder.evaluate_ladder()

    assert state.rung == 3
    assert state.reason == "server_down"


# Candidates exist, parity ran and failed, local unavailable -> the rung-3
# reason records parity_fail (the deepest observed cause).


def test_parity_fail_reason_when_no_replacement_verified(fresh_db, monkeypatch):
    stored = _unit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    _server_env(monkeypatch, "http://127.0.0.1:1/v1")
    _seed_corpus(fresh_db, "server/127.0.0.1:1/gone-model", stored)
    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", lambda: ["cand-a"])

    def orthogonal_embed(texts, model_id):
        return [_blob(_orthogonal(stored))] * len(texts), DIM

    monkeypatch.setattr(embed_ladder, "_embed_with_model", orthogonal_embed)
    monkeypatch.setattr(embed_ladder, "_sentence_transformers_available", lambda: False)

    state = embed_ladder.evaluate_ladder(conn=fresh_db)

    assert state.rung == 3
    assert state.reason == "parity_fail"


# A healthy forced re-evaluation supersedes the active degradation.


def test_healthy_evaluation_supersedes_active_state(fresh_db, monkeypatch):
    _server_env(monkeypatch, "http://127.0.0.1:1/v1")
    monkeypatch.setattr(embed_ladder, "_sentence_transformers_available", lambda: False)
    fetch = mock.Mock(return_value=[])
    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", fetch)

    degraded = embed_ladder.evaluate_ladder(conn=fresh_db)
    assert degraded.rung == 3 and degraded.active is True

    fetch.return_value = ["gone-model"]  # configured model is served again
    assert embed_ladder.evaluate_ladder(force=True) is None
    assert embed_ladder.ladder_state() is None
    assert degraded.active is False


# Every reason the ladder can record stays inside the FR-013 enum.


def test_ladder_reasons_within_telemetry_enum():
    from cairn.telemetry.events import EMBED_SERVER_REASONS

    assert set(embed_ladder._RUNG3_DETAIL) <= EMBED_SERVER_REASONS
    assert {"fallback_session_alias", "fallback_local"} <= EMBED_SERVER_REASONS


# ---------------------------------------------------------------------------
# FR-013 notification fan-out (T012): notify_degradation + accessors.
# The logger line is the user-facing surface and must NEVER be silenced by
# telemetry-off (US3 AC3); the event is telemetry-gated and carries
# host+model only (spec A2.6).
# ---------------------------------------------------------------------------


def _cairn_warnings(caplog):
    """The warning records logged on the shared 'cairn' logger."""
    return [r for r in caplog.records if r.name == "cairn"]


def _degraded_events():
    """The attrs dicts of buffered embed_server_degraded events."""
    from cairn.telemetry import sink

    return [
        json.loads(attrs_json) if attrs_json else {}
        for _ts, name, _sid, attrs_json in list(sink._BUFFER)
        if name == "embed_server_degraded"
    ]


# (a) warn-once per reason: same reason silent, different reason fires.


def test_notify_warns_once_per_reason(caplog, monkeypatch):
    _server_env(monkeypatch, "http://127.0.0.1:1/v1")
    with caplog.at_level(logging.WARNING, logger="cairn"):
        embed_ladder.notify_degradation("server_down", "server unreachable")
        embed_ladder.notify_degradation("server_down", "server unreachable")
        embed_ladder.notify_degradation("model_missing", "model not served")

    lines = _cairn_warnings(caplog)
    assert len(lines) == 2
    assert "server_down" in lines[0].message
    assert "server unreachable" in lines[0].message
    assert "model_missing" in lines[1].message
    assert "model not served" in lines[1].message


# (b) telemetry off suppresses the EVENT, never the logger line (US3 AC3).


def test_notify_line_not_silenced_by_telemetry_off(caplog, monkeypatch):
    _server_env(monkeypatch, "http://127.0.0.1:1/v1")
    monkeypatch.setenv("CAIRN_TELEMETRY", "off")
    with caplog.at_level(logging.WARNING, logger="cairn"):
        embed_ladder.notify_degradation("server_down", "server unreachable")

    lines = _cairn_warnings(caplog)
    assert len(lines) == 1
    assert "server_down" in lines[0].message
    assert _degraded_events() == []


# (c) one emit per process per reason: reason enum + host/model-only payload.


def test_notify_emits_once_with_reason_host_model(monkeypatch):
    from cairn.telemetry.events import EMBED_SERVER_REASONS

    _server_env(monkeypatch, "http://127.0.0.1:1/v1")
    embed_ladder.notify_degradation("server_down", "down")
    embed_ladder.notify_degradation("server_down", "down")

    events = _degraded_events()
    assert len(events) == 1
    attrs = events[0]
    assert set(attrs) == {"reason", "host", "model"}  # never request bodies
    assert attrs["reason"] in EMBED_SERVER_REASONS
    assert attrs["host"] == "127.0.0.1:1"
    assert attrs["model"] == "gone-model"


def test_notify_host_unresolved_when_base_url_missing(monkeypatch):
    from cairn.telemetry.events import EMBED_SERVER_REASONS

    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.delenv("CAIRN_EMBED_BASE_URL", raising=False)
    embed_ladder.notify_degradation("server_down", "down")

    attrs = _degraded_events()[0]
    assert attrs["reason"] in EMBED_SERVER_REASONS
    assert attrs["host"] == "unresolved"


# (d) rung-1 adoption via evaluate_ladder auto-notifies with the permanence
# command in the message and host+model (the adopted id) in the payload.


def test_rung1_adoption_auto_notifies(caplog, fresh_db, monkeypatch):
    stored = _unit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    server = _StubLadderServer(["cand-a"], vec_for=lambda t: stored)
    try:
        _server_env(monkeypatch, server.base_url)
        stamp = _stamp_for(server.base_url, "gone-model")
        _seed_corpus(fresh_db, stamp, stored)
        with caplog.at_level(logging.WARNING, logger="cairn"):
            state = embed_ladder.evaluate_ladder(conn=fresh_db)

        assert state.rung == 1
        lines = _cairn_warnings(caplog)
        assert len(lines) == 1
        assert "--adopt-server-model" in lines[0].message
        assert "cand-a" in lines[0].message
        events = _degraded_events()
        assert len(events) == 1
        assert events[0] == {
            "reason": "fallback_session_alias",
            "host": urlsplit(server.base_url).netloc,
            "model": "cand-a",
        }
    finally:
        server.close()


# (e) accessors: "" when healthy; rung + reason + remediation when active.


def test_degradation_accessors_empty_when_healthy():
    assert embed_ladder.degradation_active() is False
    assert embed_ladder.degradation_footnote() == ""
    assert embed_ladder.degradation_banner() == ""


def test_degradation_accessors_name_rung_reason_remediation(monkeypatch):
    _server_env(monkeypatch, "http://127.0.0.1:1/v1")
    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", lambda: [])
    monkeypatch.setattr(embed_ladder, "_sentence_transformers_available", lambda: False)

    state = embed_ladder.evaluate_ladder(conn=None)
    assert state.rung == 3 and state.reason == "model_missing"
    assert embed_ladder.degradation_active() is True

    footnote = embed_ladder.degradation_footnote()
    banner = embed_ladder.degradation_banner()
    for text in (footnote, banner):
        assert "rung 3" in text
        assert "model_missing" in text
        assert "CAIRN_EMBED_SERVER_MODEL" in text  # the remediation
    assert footnote != banner  # distinct wordings per surface


# (f) embeddings.reset_backend_cache() re-arms the once-set through the
# embed_ladder.reset_cache() hook: the next notify logs again.


def test_reset_backend_cache_rearms_notify_once_set(caplog, monkeypatch):
    _server_env(monkeypatch, "http://127.0.0.1:1/v1")
    with caplog.at_level(logging.WARNING, logger="cairn"):
        embed_ladder.notify_degradation("server_down", "down")
        emb.reset_backend_cache()
        embed_ladder.notify_degradation("server_down", "down")

    assert len(_cairn_warnings(caplog)) == 2
