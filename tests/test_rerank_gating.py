"""Confidence gating of the rerank stage in semantic_search (P0-2).

Steady-state profiling showed the optional CrossEncoder rerank is ~95% of a
`semantic_search` call's wall time. When the fused (RRF) ranking is already
decisive -- a wide normalized margin over the RRF scores AND the #1 is an
exact-name hit -- the stage is skipped (``CAIRN_RERANK_MIN_MARGIN``, default
0.45, plus a ``rerank`` per-call override: None=auto / True=force / False=off).

These tests are hermetic (no models): the hash embedder provides deterministic
vectors, and the gate's hash-vector detector is pinned off so the gate is armed
exactly as under a real embed backend (the gate refuses to fire under
token-overlap vectors -- see test_hash_vectors_disable_gate). `rrk.rerank` is
replaced by a recorder so "did the gate skip" is directly observable. Skip
events are asserted on the telemetry sink buffer (the test_semantic_events.py
pattern).
"""
from __future__ import annotations

import json

import pytest

# Dep-free hash vectors for the whole module; the embedder is deterministic so
# margins are reproducible. (CAIRN_ANN_BACKEND=off below keeps the cosine scan
# on the brute path regardless of whether sqlite-vec is installed.)
pytestmark = pytest.mark.usefixtures("hash_backend")


@pytest.fixture(autouse=True)
def _gate_env(monkeypatch):
    """Deterministic gate arming + telemetry capture around every test.

    * rerank marker neutralized (machine-independent enablement, mirroring
      tests/test_reranker.py);
    * the gate's hash-vector detector pinned off: the vectors ARE still
      hash-generated (hermetic embedder), but the gate refuses to fire under
      token-overlap vectors and ``CAIRN_EMBED_BACKEND=hash`` (set by the
      module's hash_backend fixture) counts as that. The test exercising the
      disable-behavior re-arms it;
    * brute scan forced (CAIRN_ANN_BACKEND=off);
    * sink buffer cleared on entry/exit so skip-event assertions see only
      this test's emissions.
    """
    from cairn.graph import embeddings as emb
    from cairn.graph import reranker as rrk
    from cairn.graph import semantic as semantic_mod
    from cairn.telemetry import sink

    monkeypatch.setattr(rrk, "_rerank_marker_path", lambda: _no_marker_path())
    # is_hash_fallback pinned False so provenance strings read "semantic" /
    # "fused(bm25+semantic)" (production wording); the helper patch below is
    # what actually arms the gate.
    monkeypatch.setattr(emb, "is_hash_fallback", lambda: False)
    monkeypatch.setattr(
        semantic_mod, "_vectors_carry_token_overlap_only", lambda flag: False
    )
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    monkeypatch.delenv("CAIRN_RERANK", raising=False)
    monkeypatch.delenv("CAIRN_RERANK_MIN_MARGIN", raising=False)
    with sink._LOCK:
        sink._BUFFER.clear()
    yield
    with sink._LOCK:
        sink._BUFFER.clear()


# Import-time snapshot of the real detector: the _gate_env fixture patches
# the module attribute for every test, so unit-testing the detector's own
# logic needs the pristine function object captured before any fixture runs.
from cairn.graph.semantic import _vectors_carry_token_overlap_only as _real_detector


def _no_marker_path():
    from pathlib import Path

    return Path("/nonexistent/cairn-test-marker-does-not-exist")


class _RerankRecorder:
    """Stand-in for rrk.rerank that records calls and pretends to rerank.

    Returning ``(candidates[:limit], True)`` with a fake ``rerank_score``
    mirrors the real success contract, so the post-rerank bookkeeping in
    semantic_search is exercised on the "gate did NOT skip" paths.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, query, candidates, limit):
        self.calls.append({"query": query, "ids": [c.get("id") for c in candidates], "limit": limit})
        out = []
        for c in candidates[:limit]:
            item = dict(c)
            item["rerank_score"] = 0.5
            out.append(item)
        return out, True


def _seed_decisive(conn) -> None:
    """One clearly-matching symbol + unrelated ones: querying ``safeApiCall``
    yields a wide fused margin (it is the only token match for BM25 and the
    closest chunk for the vectors) plus an exact-name #1 -- the gate's skip
    shape."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/Api.kt', 'kotlin')"
    )
    rows = [
        (1, "safeApiCall", "xyz.safeApiCall", "Retries a network call with backoff."),
        (2, "formatDate", "xyz.formatDate", "Formats a date for display."),
        (3, "drawBorder", "xyz.drawBorder", "Draws a border around a view."),
        (4, "parseJsonConfig", "xyz.parseJsonConfig", "Parses the JSON config file."),
        (5, "sortItems", "xyz.sortItems", "Sorts items by their priority."),
        (6, "validateInput", "xyz.validateInput", "Validates user input fields."),
    ]
    for sid, name, qual, doc in rows:
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
            "VALUES (?, 1, ?, 'function', ?, ?, 1, 10)",
            (sid, name, qual, doc),
        )
    conn.commit()


def _seed_tight(conn, n: int = 8) -> None:
    """n symbols with IDENTICAL names/docstrings: every retrieval signal ties,
    so the fused margin is a near-tie -- the gate's rerank shape."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/W.kt', 'kotlin')"
    )
    for i in range(n):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
            "VALUES (?, 1, 'worker', 'function', 'xyz.worker', "
            "'Process items from the queue.', 1, 10)",
            (i + 1,),
        )
    conn.commit()


def _buffered_events():
    from cairn.telemetry import sink

    with sink._LOCK:
        buf = list(sink._BUFFER)
    return [(name, json.loads(attrs) if attrs else {}) for _ts, name, _sid, attrs in buf]


def _semantic_search(conn, query, **kw):
    from cairn.graph.semantic import semantic_search

    return semantic_search(conn, query, **kw)


# ---------------------------------------------------------------------------
# Pure helpers (no DB)
# ---------------------------------------------------------------------------


class TestFusedMargin:
    def test_empty_and_single_candidate_are_trivially_decisive(self):
        from cairn.graph.semantic import _fused_margin

        assert _fused_margin([], 5) == 1.0
        assert _fused_margin([{"score": 0.01}], 5) == 1.0

    def test_ratio_over_edge_slot(self):
        from cairn.graph.semantic import _fused_margin

        cands = [{"score": 0.04}, {"score": 0.03}, {"score": 0.02}, {"score": 0.01}]
        # limit=3 -> edge index min(2, 3) = 2 -> (0.04-0.02)/0.04
        assert _fused_margin(cands, 3) == pytest.approx(0.5)
        # limit larger than the list -> edge is the last element
        assert _fused_margin(cands, 10) == pytest.approx(0.75)

    def test_non_positive_top_score_is_never_confident(self):
        from cairn.graph.semantic import _fused_margin

        assert _fused_margin([{"score": 0.0}, {"score": 0.0}], 5) == 0.0


class TestExactNameHit:
    def test_matches_bare_and_qualified_names(self):
        from cairn.graph.semantic import _exact_name_hit

        top = {"name": "ApiFactory", "qualified_name": "com.x.ApiFactory"}
        assert _exact_name_hit("ApiFactory", top)
        assert _exact_name_hit("com.x.ApiFactory", top)
        assert _exact_name_hit("apifactory", top)  # case-insensitive
        assert not _exact_name_hit("ApiFact", top)  # fragments are NOT exact
        assert not _exact_name_hit("", top)

    def test_missing_fields_do_not_raise(self):
        from cairn.graph.semantic import _exact_name_hit

        assert not _exact_name_hit("ApiFactory", {})


class TestRerankMinMargin:
    def test_default_and_env_override(self, monkeypatch):
        from cairn.graph import semantic

        assert semantic._rerank_min_margin() == semantic._DEFAULT_RERANK_MIN_MARGIN
        monkeypatch.setenv("CAIRN_RERANK_MIN_MARGIN", "0.9")
        assert semantic._rerank_min_margin() == 0.9

    def test_garbage_falls_back_to_default(self, monkeypatch):
        from cairn.graph import semantic

        monkeypatch.setenv("CAIRN_RERANK_MIN_MARGIN", "not-a-float")
        assert semantic._rerank_min_margin() == semantic._DEFAULT_RERANK_MIN_MARGIN

    def test_clamped_to_unit_interval(self, monkeypatch):
        from cairn.graph import semantic

        monkeypatch.setenv("CAIRN_RERANK_MIN_MARGIN", "1.5")
        assert semantic._rerank_min_margin() == 1.0
        monkeypatch.setenv("CAIRN_RERANK_MIN_MARGIN", "-3")
        assert semantic._rerank_min_margin() == 0.0


class TestHashVectorDetector:
    def test_both_hash_modes_count_as_token_overlap(self, monkeypatch):
        assert _real_detector(True) is True  # silent local-backend fallback
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
        assert _real_detector(False) is True  # explicit smoke-test mode
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "HASH")  # case-insensitive
        assert _real_detector(False) is True

    def test_real_backends_are_trusted(self, monkeypatch):
        monkeypatch.delenv("CAIRN_EMBED_BACKEND", raising=False)
        assert _real_detector(False) is False
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "local")
        assert _real_detector(False) is False
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "openai")
        assert _real_detector(False) is False


class TestCalibrationPin:
    def test_t018_ds_v1_recalibration_no_change(self):
        """T018 (FR-004/TC-013) re-measured the gate on the DS-v1 ground
        truth (yarl corpus, 29-query tune split, bge-m3 + bge-reranker-base,
        chunk variant B, rerank on): at margins {0.30, 0.45, 0.60, 0.75} the
        gate skips 0/29 tune queries both at the shipped config and with
        enrichment forced on -- every DS-v1 query is a natural-language
        question, so the exact-name corroboration never fires (0/29
        exact-name hits either way; the gate sees the RAW query by design)
        and the skip-rate curve is flat at zero across the whole margin
        axis. The margin cannot be re-calibrated on a population with no
        skip traffic; the original agent-style calibration (17-25% skips,
        0.94-1.00 top-1 rerank agreement at 0.45) remains the operative
        basis. The margin-only hypothetical (corroboration dropped)
        re-confirmed on DS-v1 why it must stay: 14/29 would-skip at 0.30
        with only 0.50 agreement (9/29 at 0.45, agreement 0.44). This pin
        exists so the constant cannot drift casually: changing it requires a
        new calibration table (see semantic.py's gating note), not an edit.
        """
        from cairn.graph import semantic

        assert semantic._DEFAULT_RERANK_MIN_MARGIN == 0.45


class TestFusedConfident:
    def test_requires_both_margin_and_exact_name(self, monkeypatch):
        from cairn.graph.semantic import _fused_confident

        top = {"name": "safeApiCall", "qualified_name": "xyz.safeApiCall", "score": 0.0328}
        tail = [{"score": 0.014}] * 9
        cands = [top] + tail  # margin ~0.57
        monkeypatch.delenv("CAIRN_RERANK_MIN_MARGIN", raising=False)
        assert _fused_confident("safeApiCall", cands, 10) is True
        assert _fused_confident("unrelated query", cands, 10) is False  # no exact hit
        tight = [{"score": 0.0328}, {"score": 0.031}] + [{"score": 0.0305}] * 8
        assert _fused_confident("safeApiCall", tight, 10) is False  # margin ~0.07

    def test_threshold_zero_skips_any_exact_name_hit(self, monkeypatch):
        from cairn.graph.semantic import _fused_confident

        monkeypatch.setenv("CAIRN_RERANK_MIN_MARGIN", "0")
        cands = [{"name": "worker", "qualified_name": "x.worker", "score": 0.0328},
                 {"score": 0.0327}]
        assert _fused_confident("worker", cands, 10) is True


# ---------------------------------------------------------------------------
# Gate behavior through semantic_search (recorder proves skip vs call)
# ---------------------------------------------------------------------------


class TestGateSkips:
    def test_decisive_margin_skips_rerank(self, monkeypatch, fresh_db):
        """The point of the feature: a decisive fused ranking returns the
        fused order without ever invoking the cross-encoder."""
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb

        _seed_decisive(fresh_db)
        emb.embed_all(fresh_db)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", rec)

        # threshold=0.0 keeps all six symbols in the vector pool so the skip
        # exercises a genuine multi-candidate margin (~0.53 >= 0.45), not the
        # trivial single-candidate path.
        results = _semantic_search(fresh_db, "safeApiCall", limit=5, threshold=0.0)

        assert rec.calls == [], "gate should have skipped the rerank stage"
        assert results, "fused results must still be returned"
        assert all(r["reranked"] is False for r in results)
        assert all("rerank_score" not in r for r in results)
        assert results[0]["name"] == "safeApiCall"

    def test_skip_emits_rerank_skipped_event(self, monkeypatch, fresh_db):
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb

        _seed_decisive(fresh_db)
        emb.embed_all(fresh_db)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        monkeypatch.setattr(rrk, "rerank", _RerankRecorder())

        _semantic_search(fresh_db, "safeApiCall", limit=5, threshold=0.0)

        skips = [a for n, a in _buffered_events() if n == "rerank_skipped"]
        assert len(skips) == 1
        assert skips[0] == {"reason": "confident_margin"}
        # The semantic_backend funnel reports execution truth: the stage did
        # not run AND did not degrade (a skip is neither).
        sem = [a for n, a in _buffered_events() if n == "semantic_backend"]
        assert len(sem) == 1
        assert sem[0]["rerank"] == 0
        assert sem[0]["rerank_degraded"] == 0

    def test_single_hit_is_trivially_decisive(self, monkeypatch, fresh_db):
        """One candidate leaves rerank nothing to reorder -- margin 1.0."""
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb

        conn = fresh_db
        conn.execute("INSERT INTO repos (id, name, path) VALUES ('t', 't', '/tmp/t')")
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (1, 't', '/tmp/t/A.kt', 'kotlin')"
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
            "VALUES (1, 1, 'loneSymbol', 'function', 'x.loneSymbol', 'Does one thing.', 1, 5)"
        )
        conn.commit()
        emb.embed_all(conn)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", rec)

        results = _semantic_search(conn, "loneSymbol", limit=5)
        assert rec.calls == []
        assert [r["name"] for r in results] == ["loneSymbol"]

    def test_hash_vectors_disable_gate(self, monkeypatch, fresh_db):
        """Under hash (token-overlap) vectors the fused ranking is
        untrustworthy; the gate must not fire (rerank is the only semantic
        signal left), even on a query shape that would otherwise skip."""
        from cairn.graph import reranker as rrk
        from cairn.graph import semantic as semantic_mod

        _seed_decisive(fresh_db)
        from cairn.graph import embeddings as emb

        emb.embed_all(fresh_db)
        # The real helper would return True here (the module fixture sets
        # CAIRN_EMBED_BACKEND=hash); patching the decision directly keeps the
        # test on the gate wiring. The helper's own logic is covered below in
        # TestHashVectorDetector.
        monkeypatch.setattr(
            semantic_mod, "_vectors_carry_token_overlap_only", lambda flag: True
        )
        monkeypatch.setenv("CAIRN_RERANK", "1")
        rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", rec)

        _semantic_search(fresh_db, "safeApiCall", limit=5)
        assert len(rec.calls) == 1, "hash vectors must keep the rerank stage"
        assert not [n for n, _ in _buffered_events() if n == "rerank_skipped"]


class TestGateReranks:
    def test_tight_margin_still_reranks(self, monkeypatch, fresh_db):
        """Identical candidates tie everywhere; the fused margin is a
        near-tie, so the cross-encoder must still run."""
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb

        _seed_tight(fresh_db)
        emb.embed_all(fresh_db)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", rec)

        results = _semantic_search(fresh_db, "process items from the queue",
                                   limit=5, threshold=0.0)
        assert len(rec.calls) == 1
        assert all(r["reranked"] is True for r in results)
        assert not [n for n, _ in _buffered_events() if n == "rerank_skipped"]

    def test_no_exact_name_hit_blocks_skip_even_with_margin(self, monkeypatch, fresh_db):
        """A wide margin whose #1 is not an exact reference of the query is
        not trusted (calibration: BM25/fragment queries reshape under
        rerank). The decisive corpus queried by a fragment-only phrase has a
        wide margin but no exact hit -> rerank runs."""
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb

        _seed_decisive(fresh_db)
        emb.embed_all(fresh_db)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", rec)

        _semantic_search(fresh_db, "sort priority", limit=5)
        assert len(rec.calls) == 1


class TestPerCallOverride:
    def test_rerank_false_never_calls_and_does_not_emit_skip(self, monkeypatch, fresh_db):
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb

        _seed_decisive(fresh_db)
        emb.embed_all(fresh_db)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", rec)

        results = _semantic_search(fresh_db, "safeApiCall", limit=5, rerank=False)
        assert rec.calls == []
        assert all(r["reranked"] is False for r in results)
        # An explicit opt-out is not a gate decision -- no rerank_skipped event.
        assert not [n for n, _ in _buffered_events() if n == "rerank_skipped"]

    def test_rerank_true_forces_past_gate(self, monkeypatch, fresh_db):
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb

        _seed_decisive(fresh_db)
        emb.embed_all(fresh_db)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", rec)

        results = _semantic_search(fresh_db, "safeApiCall", limit=5, rerank=True)
        assert len(rec.calls) == 1, "rerank=True must bypass the confidence gate"
        assert all(r["reranked"] is True for r in results)

    def test_rerank_true_loses_to_kill_switch(self, monkeypatch, fresh_db):
        """CAIRN_RERANK=0 is the hard off: even rerank=True cannot force the
        stage (the env kill switch must win over the per-call override)."""
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb

        _seed_decisive(fresh_db)
        emb.embed_all(fresh_db)
        monkeypatch.setenv("CAIRN_RERANK", "0")
        rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", rec)

        results = _semantic_search(fresh_db, "safeApiCall", limit=5, rerank=True)
        assert rec.calls == []
        assert all(r["reranked"] is False for r in results)

    def test_env_zero_disables_kill_switch_still_off_by_default(self, monkeypatch, fresh_db):
        """Without enablement (no env, no marker) nothing reranks regardless
        of the per-call override or the gate."""
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb

        _seed_decisive(fresh_db)
        emb.embed_all(fresh_db)
        rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", rec)

        for kwargs in ({}, {"rerank": True}):
            results = _semantic_search(fresh_db, "safeApiCall", limit=5, **kwargs)
            assert rec.calls == []
            assert all(r["reranked"] is False for r in results)


class TestEnvThresholdOverride:
    def test_high_threshold_blocks_skip_on_decisive_query(self, monkeypatch, fresh_db):
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb

        _seed_decisive(fresh_db)
        emb.embed_all(fresh_db)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        monkeypatch.setenv("CAIRN_RERANK_MIN_MARGIN", "0.99")
        rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", rec)

        # threshold=0.0 keeps every symbol in the vector pool, so the fused
        # margin is a real multi-candidate ratio (~0.53): above the 0.45
        # default (skip) but below the 0.99 override (no skip).
        _semantic_search(fresh_db, "safeApiCall", limit=5, threshold=0.0)
        assert len(rec.calls) == 1, "0.99 margin is unattainable -> gate cannot skip"


class TestRerankerUnavailableFallback:
    def test_unavailable_reranker_returns_fused_order(self, monkeypatch, fresh_db):
        """Enabled + tight margin (gate does not skip) + the reranker cannot
        load: the call degrades to the fused order, tagged degraded."""
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb

        _seed_tight(fresh_db)
        emb.embed_all(fresh_db)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        monkeypatch.setattr(rrk, "reranker_available", lambda: False)

        degraded = _semantic_search(fresh_db, "process items from the queue",
                                    limit=5, threshold=0.0)
        fused_ref = _semantic_search(fresh_db, "process items from the queue",
                                     limit=5, threshold=0.0, rerank=False)

        assert [r["id"] for r in degraded] == [r["id"] for r in fused_ref]
        assert all(r["reranked"] is False for r in degraded)
        sem = [a for n, a in _buffered_events() if n == "semantic_backend"]
        assert any(s["rerank_degraded"] == 1 for s in sem)


class TestProvenanceOnSkip:
    def test_skip_path_reports_fused_provenance_and_scores(self, monkeypatch, fresh_db):
        """Truthful provenance: the skip path must report exactly the fused
        ranking it actually used -- same ids, RRF scores, provenance strings,
        and no rerank artifacts -- byte-for-byte equal to a rerank=False
        call on the same query."""
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb

        _seed_decisive(fresh_db)
        emb.embed_all(fresh_db)
        monkeypatch.setenv("CAIRN_RERANK", "1")
        monkeypatch.setattr(rrk, "rerank", _RerankRecorder())

        skipped = _semantic_search(fresh_db, "safeApiCall", limit=5, threshold=0.0)
        fused_ref = _semantic_search(fresh_db, "safeApiCall", limit=5, threshold=0.0,
                                     rerank=False)

        assert skipped == fused_ref
        assert skipped[0]["provenance"] == "fused(bm25+semantic)"
        assert skipped[0]["score"] > 0
        # RRF rank-scores are small (~0.016-0.033), unlike cosines.
        assert skipped[0]["score"] < 0.1
        for r in skipped:
            assert r["provenance"] in ("semantic", "bm25", "fused(bm25+semantic)")
            assert r["reranked"] is False
            assert "rerank_score" not in r
