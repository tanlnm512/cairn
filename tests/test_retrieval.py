"""Tests for the retrieval pipeline (Phase 1.1).

Guards three things:
1. The protocol trio (Retriever/Reranker/Fusion) is satisfied by the default
   concrete providers and the protocols are runtime-checkable.
2. ``cosine_scan`` -- the shared core that collapsed three duplicated scan
   loops -- produces correct cosine rankings in both the numpy and pure-Python
   paths, and matches the original inline math.
3. The three call sites (symbols / knowledge / memory) actually route through
   ``cosine_scan`` rather than re-rolling their own scan (drift guard).
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

from cairn.retrieval import (
    Candidate,
    Reranker,
    Fusion,
    cosine_scan,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _f32(*vals: float) -> bytes:
    """Pack floats as little-endian f32 bytes."""
    return struct.pack(f"<{len(vals)}f", *vals)


class TestProtocols:
    def test_protocols_are_runtime_checkable(self):
        """The protocol trio is runtime-checkable and structurally enforced.

        The concrete providers were removed (fusion/rerank live directly in
        ``graph.semantic``), so verify the protocols themselves still work
        against minimal conforming and non-conforming objects.
        """

        class _GoodFusion:
            def fuse(self, rankings, *, k=60, weights=None):
                return []

        class _GoodReranker:
            def rerank(self, query, candidates, limit):
                return [], False

        class _NoMethods:
            pass

        assert isinstance(_GoodFusion(), Fusion)
        assert isinstance(_GoodReranker(), Reranker)
        # A class lacking the required methods does not satisfy the protocol.
        assert not isinstance(_NoMethods(), Fusion)
        assert not isinstance(_NoMethods(), Reranker)

    def test_candidate_defaults(self):
        c = Candidate(id="x")
        assert c.score == 0.0
        assert c.payload == {}
        assert c.provenance == "semantic"
        assert c.reranked is False


class TestCosineScan:
    def test_identical_vectors_score_one(self):
        v = _f32(1.0, 0.0, 0.0)
        out = cosine_scan(v, 3, [(v, 3, "a")])
        assert len(out) == 1
        assert out[0][0] == 1.0
        assert out[0][1] == "a"

    def test_orthogonal_vectors_below_threshold(self):
        q = _f32(1.0, 0.0)
        ortho = _f32(0.0, 1.0)  # cosine = 0
        out = cosine_scan(q, 2, [(ortho, 2, "orthogonal")], threshold=0.5)
        assert out == []

    def test_ranking_is_descending(self):
        q = _f32(1.0, 0.0)
        rows = [
            (_f32(0.5, 0.5), 2, "angled"),    # cosine ~0.707
            (_f32(1.0, 0.0), 2, "identical"),  # cosine 1.0
            (_f32(0.0, 1.0), 2, "ortho"),      # cosine 0.0
        ]
        # Default threshold is 0.0 (inclusive), so the orthogonal vector at
        # cosine 0.0 survives but ranks last.
        out = cosine_scan(q, 2, rows)
        ids = [payload for _, payload in out]
        assert ids == ["identical", "angled", "ortho"]
        # With a positive threshold the orthogonal vector is filtered out.
        out_strict = cosine_scan(q, 2, rows, threshold=0.1)
        assert [payload for _, payload in out_strict] == ["identical", "angled"]

    def test_dimensionality_mismatch_rows_skipped(self):
        q = _f32(1.0, 0.0)
        out = cosine_scan(q, 2, [(_f32(1.0, 0.0, 0.0), 3, "wrong-dim")])
        assert out == []

    def test_zero_norm_query_returns_empty(self):
        out = cosine_scan(_f32(0.0, 0.0), 2, [(_f32(1.0, 0.0), 2, "a")])
        assert out == []

    def test_matches_reference_math(self):
        """The shared scan must match the cosine formula it replaced."""
        q = _f32(1.0, 2.0, 3.0)
        v = _f32(4.0, 5.0, 6.0)
        ql = [1.0, 2.0, 3.0]
        vl = [4.0, 5.0, 6.0]
        expected = sum(a * b for a, b in zip(ql, vl)) / (
            math.sqrt(sum(x * x for x in ql)) * math.sqrt(sum(x * x for x in vl))
        )
        out = cosine_scan(q, 3, [(v, 3, "v")])
        assert abs(out[0][0] - expected) < 1e-6


class TestNoScanDuplication:
    """Drift guard: the three pipelines must route through cosine_scan, not
    re-roll their own numpy/pure-Python scan loop.
    """

    @staticmethod
    def _source(layer: str, fname: str) -> str:
        return (REPO_ROOT / "src" / "cairn" / layer / fname).read_text(
            encoding="utf-8"
        )

    def test_memory_promotion_uses_shared_scan(self):
        src = self._source("memory", "promotion.py")
        assert "cosine_scan" in src, "memory/promotion.py must use the shared cosine_scan"
        assert "def _vec_norm" not in src, (
            "memory/promotion.py should no longer define its own _vec_norm"
        )

    def test_knowledge_search_uses_shared_scan(self):
        src = self._source("knowledge", "search.py")
        assert "cosine_scan" in src, "knowledge/search.py must use the shared cosine_scan"
        # The old pure-python fallback imported l2norm/dot directly for its own loop.
        assert "import numpy as np" not in src, (
            "knowledge/search.py should no longer roll its own numpy scan path"
        )

    def test_graph_semantic_uses_shared_scan(self):
        src = self._source("graph", "semantic.py")
        assert "cosine_scan" in src, "graph/semantic.py must use the shared cosine_scan"
