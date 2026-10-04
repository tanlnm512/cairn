"""Unified cosine-scan core: the single shared vector-similarity implementation."""
from __future__ import annotations

import math
import operator
import struct
from typing import List, Sequence, Tuple, TypeVar

from ..graph.vector_math import l2norm as _l2norm

T = TypeVar("T")
_MUL = operator.mul


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
        returned unchanged alongside each score (same objects, same order).
    :param threshold: minimum cosine score to keep (default 0.0 = keep all).
    :return: ``[(score, payload), ...]`` sorted by score descending. Rows whose
        dimensionality mismatches ``q_dim``, whose blob byte length is not
        ``dim * 4`` (empty or malformed), or whose norm is zero are skipped.

    Performance: the numpy path is batched -- all eligible blobs are
    concatenated once into a ``(|rows|, dim)`` float32 matrix and scored
    with a single matrix-vector product against the unit query.

    Ordering / precision contract (callers -- ``graph/semantic.py``,
    ``knowledge/search.py``, ``memory/promotion.py`` -- rely on this):

    * **Skips**: rows with ``dim != q_dim``, malformed blob length, or
      zero norm are dropped.
    * **Threshold**: ``score >= threshold`` compares the float64 widening
      of the float32 score.
    * **Ties**: the sort is descending and *stable*: rows with exactly
      equal scores keep their original input order.
    * **Precision**: scores are computed in float32; the batched gemv
      accumulates in a different order than a per-row dot, so individual
      scores may differ at float-epsilon level (~1e-7) and the two paths'
      scores agree to float epsilon. All callers treat scores as ranking
      keys rather than exact values.
    """
    try:
        import numpy as np

        q = np.frombuffer(q_blob, dtype="<f4")
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []
        q_unit = q / qn  # float32, unit length

        # Gather eligible rows in input order: dimensionality must match the
        # query (stale rows from a previous model are skipped) and the blob
        # must carry exactly ``dim`` little-endian f32 values.
        blob_size = q_dim * 4
        gathered: List[Tuple[bytes, T]] = [
            (vec_blob, payload)
            for vec_blob, dim, payload in rows
            if dim == q_dim and len(vec_blob) == blob_size
        ]
        if not gathered:
            return []

        # One stacked decode + one matrix-vector product.
        mat = np.frombuffer(
            b"".join(blob for blob, _ in gathered), dtype="<f4"
        ).reshape(len(gathered), q_dim)
        norms = np.linalg.norm(mat, axis=1)

        keep = norms > 0.0  # zero-norm rows are skipped
        dots = (mat @ q_unit)[keep]
        kept_norms = norms[keep]
        payloads = [payload for (_, payload), k in zip(gathered, keep) if k]
        if dots.size == 0:
            return []

        # Widen to float64 before comparing/sorting.
        scores = (dots / kept_norms).astype(np.float64)
        sel = scores >= threshold
        scores = scores[sel]
        payloads = [p for p, s in zip(payloads, sel) if s]

        # Descending, stable: exactly-equal scores keep original row order.
        order = np.argsort(-scores, kind="stable")
        return [(float(scores[i]), payloads[i]) for i in order]
    except ImportError:
        # Pure-Python fallback (numpy absent: the default install is
        # torch-free and numpy rides only in the [semantic] extra).
        # Same math as the numpy path: dot with the precomputed unit query,
        # divided by the row norm.
        q_vec = struct.unpack(f"<{len(q_blob) // 4}f", q_blob)
        q_norm = _l2norm(q_vec)
        if q_norm == 0.0:
            return []
        q_unit_seq = [x / q_norm for x in q_vec]
        scored: List[Tuple[float, T]] = []
        blob_size = q_dim * 4
        for vec_blob, dim, payload in rows:
            if dim != q_dim or len(vec_blob) != blob_size:
                continue  # stale dimensionality / malformed blob, like numpy
            v = struct.unpack(f"<{dim}f", vec_blob)
            vn = math.sqrt(sum(map(_MUL, v, v)))
            if vn == 0.0:
                continue
            score = sum(map(_MUL, q_unit_seq, v)) / vn
            if score >= threshold:
                scored.append((score, payload))
        scored.sort(key=lambda x: -x[0])
        return scored
