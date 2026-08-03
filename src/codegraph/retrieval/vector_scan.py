"""Unified cosine-scan core: the single shared vector-similarity implementation.

This module exposes one ``cosine_scan`` that all three retrieval paths
(``graph/semantic.py`` symbols, ``knowledge/search.py`` docs,
``memory/promotion.py`` concepts) call. It is backend agnostic: callers pass
already-fetched ``(vec_blob, dim, payload)`` rows plus a query vector; it
returns ``[(score, payload), ...]`` ranked descending. The three pipelines
differ in *which table they scan and what payload they join*, not in the
cosine math -- so the math lives once here.

NumPy is preferred when available (fast); falls back to pure Python (correct,
slower) using ``vector_math.l2norm``/``dot``.
"""
from __future__ import annotations

import struct
from typing import Callable, List, Sequence, Tuple, TypeVar

from ..graph.vector_math import l2norm as _l2norm, dot as _dot

T = TypeVar("T")


def cosine_scan(
    q_blob: bytes,
    q_dim: int,
    rows: Sequence[Tuple[bytes, int, "T"]],
    threshold: float = 0.0,
) -> List[Tuple[float, "T"]]:
    """Rank ``rows`` by cosine similarity to the query vector ``q_blob``.

    :param q_blob: query embedding as little-endian float32 bytes.
    :param q_dim: query dimensionality (used to skip stale/dim-mismatched rows).
    :param rows: sequence of ``(vec_blob, dim, payload)`` tuples. The ``payload``
        is opaque -- typically a DB row or a (doc_id, chunk) tuple -- and is
        returned unchanged alongside each score.
    :param threshold: minimum cosine score to keep (default 0.0 = keep all).
    :return: ``[(score, payload), ...]`` sorted by score descending. Rows whose
        dimensionality mismatches ``q_dim`` or whose norm is zero are skipped.
    """
    try:
        import numpy as np

        q = np.frombuffer(q_blob, dtype="<f4")
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []
        q_unit = q / qn
        scored: List[Tuple[float, T]] = []
        for vec_blob, dim, payload in rows:
            if dim != q_dim:
                continue  # stale row from a previous model / dimensionality
            v = np.frombuffer(vec_blob, dtype="<f4")
            vn = float(np.linalg.norm(v))
            if vn == 0.0:
                continue
            score = float(np.dot(q_unit, v / vn))
            if score >= threshold:
                scored.append((score, payload))
        scored.sort(key=lambda x: -x[0])
        return scored
    except ImportError:
        # Pure-Python fallback -- struct unpack is ~10x faster than array for
        # this shape. Uses the shared vector_math helpers so all three layers
        # agree on the math.
        q = struct.unpack(f"<{len(q_blob) // 4}f", q_blob)
        qn = _l2norm(q)
        if qn == 0.0:
            return []
        scored = []
        for vec_blob, dim, payload in rows:
            if dim != q_dim:
                continue
            v = struct.unpack(f"<{dim}f", vec_blob)
            vn = _l2norm(v)
            if vn == 0.0:
                continue
            score = _dot(q, v) / (qn * vn)
            if score >= threshold:
                scored.append((score, payload))
        scored.sort(key=lambda x: -x[0])
        return scored
