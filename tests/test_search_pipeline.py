"""Failure-path contracts for the semantic search pipeline stages.

Injection tests over the pipeline's own adapter seam: a PRF second pass
that fails (raises or returns None) must degrade to the first-pass
candidates, never wipe them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cairn.graph.search_pipeline import (
    SearchAdapters,
    SearchContext,
    query_stage,
    rerank_stage,
    run_search,
)

FIRST_PASS = [
    {
        "id": "s1",
        "name": "gizmo_polish",
        "kind": "function",
        "qualified_name": "mod.gizmo_polish",
        "file_path": "m.py",
        "repo": "t",
        "score": 0.9,
        "chunk": "",
    }
]

SPARSE_HIT = [
    {
        "id": "s2",
        "name": "bm25_only",
        "kind": "function",
        "qualified_name": "mod.bm25_only",
        "file_path": "m.py",
        "repo": "t",
        "score": 0.7,
        "chunk": "",
    }
]


class _ScriptedPrfExpander:
    """Fixed non-empty expansion: triggers the PRF second pass."""

    terms = ("polish",)
    dense_query = "gizmo polish"

    def __call__(self, query, feedback_docs, *, df_lookup=None,
                 fb_terms=10, fb_lambda=0.5):
        return self


class _SecondPassFails:
    """First dense call returns the pool; the PRF second call fails."""

    def __init__(self, mode: str):
        self.mode = mode
        self.calls = 0

    def __call__(self, context):
        self.calls += 1
        if self.calls == 1:
            return [dict(c) for c in FIRST_PASS]
        if self.mode == "raise":
            raise RuntimeError("second dense pass down")
        return None


class _DenseAlwaysFails:
    """Every dense call raises, simulating a broken dense leg."""

    def __call__(self, context):
        raise RuntimeError("dense leg down")


class _Recorder:
    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1


def _context(fresh_db, mode: str) -> tuple[SearchContext, _Recorder, _Recorder]:
    dense_failure = _Recorder()
    apply_degradation = _Recorder()
    adapters = SearchAdapters(
        dense_retrieve=_SecondPassFails(mode),
        sparse_retrieve=lambda context, terms: [],
        query_enricher=lambda *args, **kwargs: None,
        prf_expander=_ScriptedPrfExpander(),
        build_fused_candidates=lambda context, bm25_map, dense_map, fused_rank: [
            dict(c) for c in FIRST_PASS
        ],
        confidence_gate=lambda context: False,
        enrich_results=lambda context: None,
        dense_failure=dense_failure,
        apply_degradation=apply_degradation,
        df_lookup_factory=None,
    )
    params = SimpleNamespace(
        prf=True,
        prf_docs=None,
        prf_terms=None,
        prf_lambda=None,
        rrf_k=None,
        rrf_weights=None,
        sparse_top_n=None,
        rerank=None,
        enrich=False,
        enrich_idf=False,
        rerank_pool=None,
        dense_threshold=None,
        multivector=False,
        dense_pool=None,
    )
    context = SearchContext(
        conn=fresh_db,
        query="gizmo",
        limit=10,
        threshold=0.0,
        rerank_override=None,
        params=params,
        adapters=adapters,
    )
    return context, dense_failure, apply_degradation


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    monkeypatch.delenv("CAIRN_RERANK", raising=False)
    monkeypatch.delenv("CAIRN_RERANK_MIN_MARGIN", raising=False)
    from cairn.telemetry import sink

    with sink._LOCK:
        sink._BUFFER.clear()


@pytest.mark.parametrize("mode", ["raise", "none"])
def test_prf_second_pass_failure_keeps_first_pass_candidates(
    fresh_db, mode
):
    context, dense_failure, apply_degradation = _context(fresh_db, mode)

    results = run_search(context)

    names = [r["name"] for r in results]
    assert names == ["gizmo_polish"]
    assert context.dense_lost is True
    assert apply_degradation.calls == 1
    # The recovery adapter is exception-scoped: only a raised failure invokes it.
    assert dense_failure.calls == (1 if mode == "raise" else 0)


def _rescue_context(fresh_db) -> tuple[SearchContext, _Recorder, _Recorder]:
    dense_failure = _Recorder()
    apply_degradation = _Recorder()
    adapters = SearchAdapters(
        dense_retrieve=_DenseAlwaysFails(),
        sparse_retrieve=lambda context, terms: [dict(c) for c in SPARSE_HIT],
        query_enricher=lambda *args, **kwargs: None,
        prf_expander=_ScriptedPrfExpander(),
        build_fused_candidates=lambda context, bm25_map, dense_map, fused_rank: [
            dict(c) for c in bm25_map.values()
        ],
        confidence_gate=lambda context: False,
        enrich_results=lambda context: None,
        dense_failure=dense_failure,
        apply_degradation=apply_degradation,
        df_lookup_factory=None,
    )
    context = SearchContext(
        conn=fresh_db,
        query="gizmo",
        limit=10,
        threshold=0.0,
        rerank_override=None,
        params=SimpleNamespace(prf=False),
        adapters=adapters,
    )
    return context, dense_failure, apply_degradation


def test_dense_exception_still_runs_sparse_fusion_rescue(fresh_db):
    """A raised dense leg degrades like an empty one: the sparse leg and RRF
    still run instead of returning zero hits."""
    context, dense_failure, _apply_degradation = _rescue_context(fresh_db)

    results = run_search(context)

    assert [r["name"] for r in results] == ["bm25_only"]
    assert context.dense_lost is True
    assert context.fusion_used is True
    assert dense_failure.calls == 1


def _gate_context(fresh_db, override) -> SearchContext:
    adapters = SearchAdapters(
        dense_retrieve=lambda context: [],
        sparse_retrieve=lambda context, terms: [],
        query_enricher=lambda *args, **kwargs: None,
        prf_expander=_ScriptedPrfExpander(),
        build_fused_candidates=lambda context, bm25_map, dense_map, fused_rank: [],
        confidence_gate=lambda context: False,
        enrich_results=lambda context: None,
        dense_failure=lambda context: None,
        apply_degradation=lambda context: None,
        df_lookup_factory=None,
    )
    return SearchContext(
        conn=fresh_db,
        query="q",
        limit=10,
        threshold=0.0,
        rerank_override=override,
        params=SimpleNamespace(enrich=False, enrich_idf=False, prf=False),
        adapters=adapters,
    )


def test_explicit_rerank_true_forces_on_when_model_importable(
    fresh_db, monkeypatch
):
    """Override True forces reranking even with the env/marker gate off."""
    from pathlib import Path

    from cairn.graph import reranker as rrk

    monkeypatch.delenv("CAIRN_RERANK", raising=False)
    monkeypatch.setattr(rrk, "rerank_enabled", lambda: False)
    monkeypatch.setattr(rrk, "reranker_available", lambda: True)
    monkeypatch.setattr(
        rrk, "_rerank_marker_path", lambda: Path("/nonexistent/rerank_enabled")
    )
    context = _gate_context(fresh_db, True)

    query_stage(context)

    assert context.rerank_on is True
    assert context.reranker_available is True


def test_explicit_rerank_false_forces_off(fresh_db, monkeypatch):
    from cairn.graph import reranker as rrk

    monkeypatch.setenv("CAIRN_RERANK", "1")
    monkeypatch.setattr(rrk, "rerank_enabled", lambda: True)
    context = _gate_context(fresh_db, False)

    query_stage(context)

    assert context.rerank_on is False
    assert context.reranker_available is False


def test_rerank_stage_passes_force_for_explicit_override(fresh_db, monkeypatch):
    from cairn.graph import reranker as rrk

    captured = {}

    def fake_rerank(query, candidates, limit, structured=False, force=False):
        captured["force"] = force
        return candidates[:limit], True

    monkeypatch.setattr(rrk, "rerank", fake_rerank)
    context = _gate_context(fresh_db, True)
    context.rerank_on = True
    context.candidates = [{"id": "s1", "name": "gizmo_polish"}]

    rerank_stage(context)

    assert captured["force"] is True
    assert context.rerank_used is True
