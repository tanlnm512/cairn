"""Tests for semantic-path telemetry events (task T10, spec §6.4).

``semantic_search`` must emit one ``semantic_backend`` event per call on its
return path (``backend`` / ``fusion`` / ``rerank`` / ``ms`` bucket /
``n_results`` bucket) and an ``empty_result`` event when it returns 0 results.
Cardinality is bounded to enums + fixed buckets (spec §6.4); these tests assert
the tags are valid members of those sets, not free text or paths.

Strategy: the sink buffer is module-global, so an autouse fixture clears it
around every test (no flush / events-table machinery needed -- the flush path is
already covered by ``tests/test_telemetry.py``; here we only assert the emission
*content*). The branching functions (``is_hash_fallback`` /
``ann_backend_enabled`` / ``ann_query`` / ``rerank_enabled``) are monkeypatched
per-test so each backend tag is exercised deterministically, independent of
whether sqlite-vec or sentence-transformers happen to be installed. One test
runs the real brute path end-to-end with no branch monkeypatching.
"""

from __future__ import annotations

import json

import pytest

_VALID_BACKENDS = {"ann", "brute", "hash"}
_VALID_MS = {"0-10ms", "10-100ms", "100-1000ms", ">1000ms"}
_VALID_N = {"0", "1-5", "6-10", "11-50", ">50"}

# Force the dep-free hash embedder for the whole module (mirrors
# tests/test_ann_index.py) so no sentence-transformers model download is needed.
pytestmark = pytest.mark.usefixtures("hash_backend")


@pytest.fixture(autouse=True)
def _reset_sink_and_env(monkeypatch):
    """Clear the telemetry buffer + env knobs around each test.

    ``CAIRN_ANN_BACKEND=off`` is the module default so brute/hash/empty tests
    deterministically hit the cosine-scan branch without depending on sqlite-vec
    being installed; the ANN-path test overrides it by patching the function.

    The reranker is also stubbed off by default: a persistent ``rerank_enabled``
    marker (or installed cross-encoder) would otherwise load a model + hit the HF
    Hub mid-test, making these telemetry tests slow and network-dependent.
    ``test_rerank_on_reports_rerank_one`` re-enables it to validate that wiring.
    The embeddings backend cache is reset so a prior test's env doesn't leak.
    """
    from cairn.telemetry import sink
    from cairn.graph import embeddings as emb
    from cairn.graph import reranker as rrk

    with sink._LOCK:
        sink._BUFFER.clear()
    monkeypatch.delenv("CAIRN_TELEMETRY", raising=False)
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    monkeypatch.setattr(rrk, "rerank_enabled", lambda: False)
    monkeypatch.setattr(
        rrk, "rerank", lambda query, candidates, limit: (candidates[:limit], False)
    )
    emb.reset_backend_cache()
    yield
    with sink._LOCK:
        sink._BUFFER.clear()
    emb.reset_backend_cache()


def _buffered_events():
    """Return ``[(name, attrs_dict)]`` currently queued in the sink buffer."""
    from cairn.telemetry import sink

    return [
        (name, json.loads(attrs_json) if attrs_json else {})
        for _ts, name, _sid, attrs_json in list(sink._BUFFER)
    ]


def _seed_symbols(conn) -> None:
    """Two symbols with docstrings so BM25 fusion + cosine both see matches."""
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES (1, 'test', '/tmp/test/Api.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES (1, 1, 'safeApiCall', 'function', 'xyz.safeApiCall', "
        "'Retries a network call with backoff.', 1, 10)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES (2, 1, 'formatDate', 'function', 'xyz.formatDate', "
        "'Formats a date for display.', 12, 20)"
    )
    conn.commit()


def _semantic_attrs():
    """The attrs dict of the (single) semantic_backend event, or fail."""
    sem = [a for n, a in _buffered_events() if n == "semantic_backend"]
    assert len(sem) == 1, f"expected exactly one semantic_backend event, got {len(sem)}"
    return sem[0]


# ---------------------------------------------------------------------------
# Real end-to-end brute path (no branch monkeypatching)
# ---------------------------------------------------------------------------


def test_semantic_backend_fires_on_real_brute_path(hash_backend, fresh_db):
    """End-to-end: explicit hash backend (is_hash_fallback False) + ANN off ->
    brute cosine scan. Exactly one semantic_backend fires with correctly-tagged,
    bounded-cardinality attrs and no empty_result (results non-empty)."""
    _seed_symbols(fresh_db)
    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import semantic_search

    emb.embed_all(fresh_db)

    # threshold=-1.0: the hash embedder can emit sub-zero cosine between
    # unrelated chunks (mirrors tests/test_ann_index.py); -1.0 guarantees both
    # symbols pass so we're testing the telemetry plumbing, not embedder quality.
    results = semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)
    assert results, "precondition: brute path returned hits"

    attrs = _semantic_attrs()
    assert attrs["backend"] == "brute", "explicit hash backend => is_hash_fallback False"
    assert attrs["fusion"] == 1, "CAIRN_FUSION defaults on and ran to completion"
    assert attrs["rerank"] == 0, "rerank stubbed off for hermeticism"
    # F3: execution-truth markers are always present (0/1) so the attr key
    # set is stable per event (the cardinality guard checks exact key sets).
    assert attrs["fusion_degraded"] == 0
    assert attrs["rerank_degraded"] == 0
    assert attrs["ms"] in _VALID_MS
    assert attrs["n_results"] in _VALID_N and attrs["n_results"] != "0"

    assert not any(n == "empty_result" for n, _ in _buffered_events())


# ---------------------------------------------------------------------------
# empty_result
# ---------------------------------------------------------------------------


def test_empty_result_event_when_no_embeddings(fresh_db):
    """Symbols present but no embeddings rows -> brute scan finds nothing ->
    early return [] fires BOTH semantic_backend (n_results='0') and empty_result
    with query_kind='semantic_search'."""
    _seed_symbols(fresh_db)
    from cairn.graph.semantic import semantic_search

    results = semantic_search(fresh_db, "safeApiCall", limit=5)
    assert results == []

    attrs = _semantic_attrs()
    assert attrs["n_results"] == "0"
    assert attrs["backend"] in _VALID_BACKENDS

    empty = [a for n, a in _buffered_events() if n == "empty_result"]
    assert len(empty) == 1
    assert empty[0]["query_kind"] == "semantic_search"


# ---------------------------------------------------------------------------
# backend tag matrix (monkeypatched for determinism)
# ---------------------------------------------------------------------------


def test_hash_backend_tagged_when_hash_fallback(fresh_db, monkeypatch):
    """is_hash_fallback True -> backend='hash' even though retrieval was the
    brute scan (hash precedence: it's the worst degradation)."""
    _seed_symbols(fresh_db)
    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import semantic_search

    emb.embed_all(fresh_db)
    monkeypatch.setattr(emb, "is_hash_fallback", lambda: True)

    semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)

    assert _semantic_attrs()["backend"] == "hash"


def test_ann_backend_tagged_when_ann_path_used(fresh_db, monkeypatch):
    """ANN enabled + ann_query returns hits + is_hash_fallback False ->
    backend='ann' (the native vec0 path produced this call's candidates)."""
    _seed_symbols(fresh_db)
    from cairn.graph import embeddings as emb
    from cairn.graph import ann_index as ann
    from cairn.graph.semantic import semantic_search

    emb.embed_all(fresh_db)
    monkeypatch.setattr(emb, "is_hash_fallback", lambda: False)
    monkeypatch.setattr(ann, "ann_backend_enabled", lambda: True)
    # Synthetic hits referencing the seeded integer symbol ids; scores clear the
    # 0.3 threshold so _candidates_from_ann_hits keeps both.
    monkeypatch.setattr(
        ann, "ann_query", lambda conn, model, q_blob, k: [(1, 0.9), (2, 0.8)]
    )

    results = semantic_search(fresh_db, "safeApiCall", limit=5, threshold=0.3)
    assert results, "precondition: ANN path returned hits"

    assert _semantic_attrs()["backend"] == "ann"


# ---------------------------------------------------------------------------
# fusion / rerank flags
# ---------------------------------------------------------------------------


def test_fusion_off_reports_fusion_zero(fresh_db, monkeypatch):
    """CAIRN_FUSION=0 -> fusion attr is 0 (read the same way the code does)."""
    _seed_symbols(fresh_db)
    monkeypatch.setenv("CAIRN_FUSION", "0")
    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import semantic_search

    emb.embed_all(fresh_db)

    semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)

    attrs = _semantic_attrs()
    assert attrs["fusion"] == 0
    # Config-off is an informed choice, not a stage failure.
    assert attrs["fusion_degraded"] == 0


def test_fusion_degrade_reports_zero_and_degraded_flag(fresh_db, monkeypatch):
    """F3: RRF fusion configured ON but the rrf_fuse call raises -> the event
    must report execution (fusion=0) plus the durable degraded marker, not the
    config value it previously reported."""
    _seed_symbols(fresh_db)
    from cairn.graph import embeddings as emb
    from cairn.graph import fusion as fusion_mod
    from cairn.graph.semantic import semantic_search

    emb.embed_all(fresh_db)

    def _boom(*_a, **_k):
        raise RuntimeError("simulated RRF failure")

    monkeypatch.setattr(fusion_mod, "rrf_fuse", _boom)

    results = semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)
    assert results, "search still succeeds on the vector-only degrade"

    attrs = _semantic_attrs()
    assert attrs["fusion"] == 0, "a degraded stage must not report 1"
    assert attrs["fusion_degraded"] == 1, "the degradation is durable in the event"


def test_rerank_degrade_reports_zero_and_degraded_flag(fresh_db, monkeypatch):
    """F3: rerank configured ON but the cross-encoder degrades (its documented
    fallback returns reranked=False) -> rerank=0 + rerank_degraded=1. The attr
    set previously reported the config value 1, hiding the degrade."""
    _seed_symbols(fresh_db)
    from cairn.graph import embeddings as emb
    from cairn.graph import reranker as rrk
    from cairn.graph.semantic import semantic_search

    emb.embed_all(fresh_db)
    monkeypatch.setattr(rrk, "rerank_enabled", lambda: True)
    # Stub the actual rerank to its documented fallback so no model is loaded.
    monkeypatch.setattr(
        rrk, "rerank", lambda query, candidates, limit: (candidates[:limit], False)
    )

    results = semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)
    assert results, "search still succeeds on the hybrid-order fallback"

    attrs = _semantic_attrs()
    assert attrs["rerank"] == 0, "a degraded stage must not report 1"
    assert attrs["rerank_degraded"] == 1


def test_rerank_success_reports_one_and_no_degradation(fresh_db, monkeypatch):
    """The execution-truth flip cuts both ways: a rerank stage that genuinely
    applied re-scoring reports rerank=1 / rerank_degraded=0."""
    _seed_symbols(fresh_db)
    from cairn.graph import embeddings as emb
    from cairn.graph import reranker as rrk
    from cairn.graph.semantic import semantic_search

    emb.embed_all(fresh_db)
    monkeypatch.setattr(rrk, "rerank_enabled", lambda: True)
    monkeypatch.setattr(
        rrk, "rerank", lambda query, candidates, limit: (candidates[:limit], True)
    )

    semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)

    attrs = _semantic_attrs()
    assert attrs["rerank"] == 1
    assert attrs["rerank_degraded"] == 0


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def test_telemetry_off_suppresses_semantic_events(fresh_db, monkeypatch):
    """CAIRN_TELEMETRY=off -> no semantic_backend / empty_result buffered at all."""
    monkeypatch.setenv("CAIRN_TELEMETRY", "off")
    from cairn.graph.semantic import semantic_search

    semantic_search(fresh_db, "safeApiCall", limit=5)  # empty -> would normally fire both

    assert _buffered_events() == [], "telemetry off -> zero events buffered"


def test_bare_connection_returns_semantic_results(tmp_path, monkeypatch):
    """A raw sqlite3.connect (no Row factory) must not silently degrade
    semantic_search to the FTS fallback.

    Found while minting the DS-v1 quality baseline: the brute-force scan
    reads rows by column name (r["vec"]), a bare connection yields tuples,
    and the TypeError was swallowed into retrieval degradation -- a quality
    run through a bare connection measured recall 0.0. The fix normalizes
    rows at the fetch boundary (_mapping_rows).
    """
    import sqlite3 as _sq

    from cairn.graph import embeddings as emb
    from cairn.graph.queries import semantic_search
    from cairn.graph.schema import _apply_schema

    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
    emb.reset_backend_cache()

    db = tmp_path / "bare.kg"
    conn = _sq.connect(str(db))
    conn.row_factory = _sq.Row
    _apply_schema(conn)
    conn.execute("INSERT INTO repos (id, name, path, language) VALUES ('r1','r1','/r1','python')")
    conn.execute("INSERT INTO files (id, repo_id, path, language) VALUES ('f1','r1','m.py','python')")
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, docstring) "
        "VALUES ('s1','f1','alpha_handler','alpha_handler','function','handles retries with backoff')"
    )
    conn.commit()

    # Embed through the hash backend so the corpus has real vectors.
    emb.embed_symbols(conn, ["s1"])
    conn.commit()
    model = emb.current_model()
    n = conn.execute("SELECT COUNT(*) AS c FROM embeddings WHERE model = ?", (model,)).fetchone()["c"]
    assert n == 1
    conn.close()

    # The regression: a BARE connection (tuple rows) doing the same search.
    bare = _sq.connect(str(db))  # no row_factory -- tuples
    try:
        assert isinstance(bare.execute("SELECT 1").fetchone(), tuple)
        results = semantic_search(bare, "retry backoff handler", limit=5)
        assert results, "bare connection returned nothing -- silent degradation returned"
        assert any("alpha_handler" == r["name"] for r in results), (
            f"semantic hit missing under bare connection: {[r['name'] for r in results]}"
        )
    finally:
        bare.close()


# ---------------------------------------------------------------------------
# embed_server_degraded catalog (T010, FR-007/FR-013)
#
# Catalog-only pins for the producer/consumer contract: the event constant,
# its re-export convention, and the bounded reason enum. The emission site is
# wired separately, so no emission happens (or is asserted) here.
# ---------------------------------------------------------------------------


def test_embed_server_degraded_constant_exact_value():
    """The event name exists in the catalog with its exact wire spelling."""
    from cairn.telemetry import events

    assert events.EMBED_SERVER_DEGRADED == "embed_server_degraded"


def test_embed_server_degraded_reexported_and_in_all():
    """Re-export contract: importable from cairn.telemetry AND listed in
    ``__all__`` -- the public-surface convention for catalog constants."""
    import cairn.telemetry as telemetry
    from cairn.telemetry import EMBED_SERVER_DEGRADED

    assert EMBED_SERVER_DEGRADED == "embed_server_degraded"
    assert "EMBED_SERVER_DEGRADED" in telemetry.__all__


def test_embed_server_reasons_exact_membership():
    """FR-013 reason enum: a frozenset of exactly these six reasons."""
    from cairn.telemetry import events

    assert isinstance(events.EMBED_SERVER_REASONS, frozenset)
    assert events.EMBED_SERVER_REASONS == frozenset(
        {
            "server_down",
            "model_missing",
            "parity_fail",
            "fallback_session_alias",
            "fallback_local",
            "hybrid_only",
        }
    )


def test_embed_server_reasons_snake_case_tags():
    """Cardinality discipline: reasons are lowercase snake_case tags."""
    import re

    from cairn.telemetry import events

    for reason in sorted(events.EMBED_SERVER_REASONS):
        assert re.match(r"^[a-z_]+$", reason), reason


# ---------------------------------------------------------------------------
# FR-012 dense-leg guard (T013, D-011): embed failures never raise out of
# semantic_search
#
# The dense embed call is guarded for ALL backends: a hard failure evaluates
# the fallback ladder at most once per search, contributes ZERO dense
# candidates (the existing bm25+RRF fusion path, provenance="bm25" -- no new
# short-circuit), and -- only when an active ladder rung exists AND this
# search's dense leg actually fell to it -- tags every result additively with
# degraded="embedding-backend" plus a remediation hint. Healthy searches keep
# today's exact shape.
# ---------------------------------------------------------------------------


def _server_env(monkeypatch):
    """Point the effective backend at a dead local server with a fixed stamp."""
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("CAIRN_EMBED_MODEL_STAMP", "server/test/stamp-t013")


def _rung3_ladder(monkeypatch):
    """Pin the ladder to a deterministic terminal rung-3 (server_down).

    No network: the model listing fails outright and rung 2 is unavailable,
    so ``evaluate_ladder`` lands on the terminal bm25-hybrid rung.
    """
    from cairn.graph import embed_ladder

    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", lambda: None)
    monkeypatch.setattr(
        embed_ladder, "_sentence_transformers_available", lambda: False
    )
    return embed_ladder


def _counting_ladder(monkeypatch, embed_ladder):
    """Wrap evaluate_ladder with a call-counting spy (still delegates)."""
    calls = []
    real = embed_ladder.evaluate_ladder

    def spy(conn=None, force=False):
        calls.append(1)
        return real(conn, force=force)

    monkeypatch.setattr(embed_ladder, "evaluate_ladder", spy)
    return calls


def test_embed_failure_rides_bm25_with_degraded_keys(fresh_db, monkeypatch):
    """A server embed failure mid-search returns bm25-provenanced results
    tagged degraded="embedding-backend" with a non-empty hint; nothing
    raises out of semantic_search."""
    _seed_symbols(fresh_db)
    _server_env(monkeypatch)
    ladder = _rung3_ladder(monkeypatch)

    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import semantic_search

    def _boom(text):
        raise RuntimeError("simulated server embed failure")

    monkeypatch.setattr(emb, "embed_query", _boom)

    results = semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)

    assert results, "bm25 hybrid results must survive the dense failure"
    assert all(r["provenance"] == "bm25" for r in results)
    assert all(r["degraded"] == "embedding-backend" for r in results)
    hints = {r["hint"] for r in results}
    assert len(hints) == 1, "one shared remediation hint across the result set"
    hint = hints.pop()
    assert hint and "rung 3" in hint
    assert ladder.degradation_active()


def test_embed_failure_with_zero_bm25_hits_returns_empty(fresh_db, monkeypatch):
    """The same failure with a query matching nothing -> empty result, no
    raise (the empty_result telemetry path stays the only observer)."""
    _server_env(monkeypatch)
    _rung3_ladder(monkeypatch)

    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import semantic_search

    def _boom(text):
        raise RuntimeError("simulated server embed failure")

    monkeypatch.setattr(emb, "embed_query", _boom)

    assert semantic_search(fresh_db, "zzqq_no_such_symbol_qqxx", limit=5) == []


def test_healthy_backends_carry_no_degraded_keys(fresh_db, monkeypatch):
    """Healthy hash / local / server searches: results keep today's exact
    shape -- no degraded key, no hint."""
    _seed_symbols(fresh_db)
    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import semantic_search

    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
    emb.reset_backend_cache()
    emb.embed_all(fresh_db)
    results = semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)
    assert results
    assert all("degraded" not in r and "hint" not in r for r in results)

    # local (whatever it resolves to in this env -- real model or hash
    # fallback): the _embed seam is pinned to the dep-free embedder so the
    # case never downloads weights or depends on torch.
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "local")
    emb.reset_backend_cache()
    monkeypatch.setattr(emb, "_embed", emb._embed_hash)
    emb.embed_all(fresh_db)
    results = semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)
    assert results
    assert all("degraded" not in r and "hint" not in r for r in results)

    # healthy server: embeds work, so the ladder is never even engaged.
    _server_env(monkeypatch)
    emb.reset_backend_cache()
    monkeypatch.setattr(emb, "_embed", emb._embed_hash)
    monkeypatch.setattr(emb, "_alias_preflight", lambda conn: None)
    emb.embed_all(fresh_db)
    results = semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)
    assert results
    assert all("degraded" not in r and "hint" not in r for r in results)


def test_ladder_evaluated_at_most_once_per_search(fresh_db, monkeypatch):
    """One embed failure in one search evaluates the ladder exactly once
    (the ladder self-caches per process; the search bounds its own call)."""
    _seed_symbols(fresh_db)
    _server_env(monkeypatch)
    ladder = _rung3_ladder(monkeypatch)
    calls = _counting_ladder(monkeypatch, ladder)

    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import semantic_search

    def _boom(text):
        raise RuntimeError("simulated server embed failure")

    monkeypatch.setattr(emb, "embed_query", _boom)

    results = semantic_search(fresh_db, "safeApiCall", limit=5)

    assert calls == [1], "exactly one ladder evaluation per failing search"
    assert results, "the bm25 ride still answers"


def test_hash_backend_stays_untouched_by_the_ladder(fresh_db, monkeypatch):
    """Hash backend: a healthy search never consults the ladder, and even a
    failing embed under hash leaves no ladder state and no degraded keys."""
    _seed_symbols(fresh_db)
    from cairn.graph import embed_ladder
    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import semantic_search

    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
    emb.reset_backend_cache()
    emb.embed_all(fresh_db)
    calls = _counting_ladder(monkeypatch, embed_ladder)

    results = semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)
    assert results
    assert calls == [], "a healthy hash search never engages the ladder"
    assert all("degraded" not in r and "hint" not in r for r in results)

    def _boom(text):
        raise RuntimeError("simulated embed failure")

    monkeypatch.setattr(emb, "embed_query", _boom)
    results = semantic_search(fresh_db, "safeApiCall", limit=5)
    assert results, "the failure still rides bm25 instead of raising"
    assert embed_ladder.ladder_state() is None, "hash never produces a rung"
    assert all("degraded" not in r and "hint" not in r for r in results)


def test_active_rung1_adoption_carries_the_dense_leg(fresh_db, monkeypatch):
    """An embed failure with a rung-1 adoption reachable: the ladder adopts
    session-scoped, the single retry rides the adopted model, the dense leg
    contributes, and NO degraded keys land."""
    _seed_symbols(fresh_db)
    _server_env(monkeypatch)

    from cairn.graph import embed_ladder
    from cairn.graph import embeddings as emb
    from cairn.graph.embed_ladder import ParityResult
    from cairn.graph.semantic import semantic_search

    # The embed seam serves deterministic dep-free vectors: the retry after
    # the rung-1 adoption succeeds instead of POSTing the dead server URL.
    monkeypatch.setattr(emb, "_embed", emb._embed_hash)

    # Corpus rows under the server stamp (same vector space as the retry).
    stamp = emb.current_model()
    for sid, chunk in (
        (1, "Retries a network call with backoff."),
        (2, "Formats a date for display."),
    ):
        blob, dim = emb._embed_hash([chunk])
        fresh_db.execute(
            "INSERT INTO embeddings "
            "(symbol_id, model, dim, vec, chunk, content_hash, embedded_at) "
            "VALUES (?, ?, ?, ?, ?, 't013', '2026-01-01T00:00:00+00:00')",
            (sid, stamp, dim, blob[0], chunk),
        )
    fresh_db.commit()

    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", lambda: ["candidate-1"])
    monkeypatch.setattr(
        embed_ladder,
        "check_parity",
        lambda conn, stamp, embed_fn=None, sample_limit=16: ParityResult(
            1, 1.0, True, True, "parity_ok"
        ),
    )

    real_embed_query = emb.embed_query
    calls = {"n": 0}

    def flaky(text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated configured-model failure")
        return real_embed_query(text)

    monkeypatch.setattr(emb, "embed_query", flaky)

    results = semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)

    assert calls["n"] == 2, "one failure, then one retry through the adoption"
    assert results
    assert all("degraded" not in r and "hint" not in r for r in results), (
        "a working rung-1 adoption leaves the results untagged"
    )
    assert any("semantic" in r["provenance"] for r in results), (
        "the dense leg must contribute through the adopted session model"
    )


def test_pass_level_failure_maps_to_rung_instead_of_raising(fresh_db, monkeypatch):
    """A residual hard failure from the dense pass (here: the ANN gate
    raising with a healthy embed) is caught at the _run_pass call site --
    ladder evaluated once, no raise, zero candidates."""
    _seed_symbols(fresh_db)
    _server_env(monkeypatch)
    ladder = _rung3_ladder(monkeypatch)
    calls = _counting_ladder(monkeypatch, ladder)

    from cairn.graph import ann_index as ann
    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import semantic_search

    blob, dim = emb._embed_hash(["safeApiCall"])
    monkeypatch.setattr(emb, "embed_query", lambda text: (blob[0], dim))

    def _boom():
        raise RuntimeError("simulated dense-pass failure")

    monkeypatch.setattr(ann, "ann_backend_enabled", _boom)

    results = semantic_search(fresh_db, "safeApiCall", limit=5)

    assert calls == [1], "the call-site guard maps the failure to the ladder"
    assert results == [], "the pass yields zero candidates instead of raising"
    assert ladder.degradation_active()


def test_embed_failure_with_enrich_on_rides_bm25_with_degraded_keys(
    fresh_db, monkeypatch
):
    """The enrichment path (params.enrich=True) with a dead server: the
    guarded embed maps the failure onto the ladder exactly as with raw
    queries -- term-mode bm25 results, no raise, degraded keys + hint."""
    _seed_symbols(fresh_db)
    _server_env(monkeypatch)
    ladder = _rung3_ladder(monkeypatch)

    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import RetrievalParams, semantic_search

    def _boom(text):
        raise RuntimeError("simulated server embed failure")

    monkeypatch.setattr(emb, "embed_query", _boom)

    results = semantic_search(
        fresh_db,
        "safeApiCall",
        limit=5,
        threshold=-1.0,
        params=RetrievalParams(enrich=True),
    )

    assert results, "term-mode bm25 results must survive the dense failure"
    assert all(r["provenance"] == "bm25" for r in results)
    assert all(r["degraded"] == "embedding-backend" for r in results)
    hints = {r["hint"] for r in results}
    assert len(hints) == 1, "one shared remediation hint across the result set"
    hint = hints.pop()
    assert hint and "rung 3" in hint
    assert ladder.degradation_active()


def test_embed_failure_with_multivector_on_rides_bm25_with_degraded_keys(
    fresh_db, monkeypatch
):
    """The multivector leg (params.multivector=True) with a dead server: the
    same degraded contract as the single-vector path -- bm25 results, no
    raise, degraded keys. The flag widens the dense leg only; a dead embed
    never turns it into a raise out of the search."""
    _seed_symbols(fresh_db)
    _server_env(monkeypatch)
    ladder = _rung3_ladder(monkeypatch)

    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import RetrievalParams, semantic_search

    def _boom(text):
        raise RuntimeError("simulated server embed failure")

    monkeypatch.setattr(emb, "embed_query", _boom)

    results = semantic_search(
        fresh_db,
        "safeApiCall",
        limit=5,
        threshold=-1.0,
        params=RetrievalParams(multivector=True),
    )

    assert results, "bm25 hybrid results must survive the dense failure"
    assert all(r["provenance"] == "bm25" for r in results)
    assert all(r["degraded"] == "embedding-backend" for r in results)
    hints = {r["hint"] for r in results}
    assert len(hints) == 1, "one shared remediation hint across the result set"
    hint = hints.pop()
    assert hint and "rung 3" in hint
    assert ladder.degradation_active()
