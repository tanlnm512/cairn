"""Unified cosine-scan core: the single shared vector-similarity implementation.

``cosine_scan`` is called by all three retrieval paths (``graph/semantic.py``
symbols, ``knowledge/search.py`` docs, ``memory/promotion.py`` concepts).
Callers pass already-fetched ``(vec_blob, dim, payload)`` rows plus a query
vector; it returns ``[(score, payload), ...]`` ranked descending.

NumPy is preferred when available (fast): all eligible rows are stacked into
one ``(|rows|, dim)`` float32 matrix and scored with a single matrix-vector
product instead of a per-row Python loop. Falls back to pure Python using
``vector_math.l2norm``/``dot`` when numpy is missing.
"""
from __future__ import annotations

import struct
from typing import List, Sequence, Tuple, TypeVar

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
        returned unchanged alongside each score (same objects, same order).
    :param threshold: minimum cosine score to keep (default 0.0 = keep all).
    :return: ``[(score, payload), ...]`` sorted by score descending. Rows whose
        dimensionality mismatches ``q_dim``, whose blob is empty/malformed, or
        whose norm is zero are skipped.

    Performance: the numpy path is batched. Instead of decoding and scoring
    one row at a time (``frombuffer`` + ``norm`` + ``dot`` per row, ~78 us of
    Python overhead each), all eligible blobs are concatenated once into a
    ``(|rows|, dim)`` float32 matrix and scored with a single matrix-vector
    product against the unit query.

    Ordering / precision contract (callers -- ``graph/semantic.py``,
    ``knowledge/search.py``, ``memory/promotion.py`` -- rely on this):

    * **Skips**: ``dim != q_dim`` and zero-norm rows are dropped exactly as
      the per-row loop did. Blobs whose byte length is not ``dim * 4`` are
      malformed and skipped too: an empty blob used to fall out via the
      zero-norm check, and a ragged blob would have raised inside
      ``np.dot`` -- worse, it would silently corrupt a stacked reshape.
    * **Threshold**: ``score >= threshold`` compares the float64 widening of
      the float32 score -- bit-identical to the old
      ``float(np.dot(...)) >= threshold`` comparison.
    * **Ties**: the sort is descending and *stable*: rows with exactly equal
      scores keep their original input order
      (``np.argsort(-scores, kind="stable")`` mirrors the old stable
      ``sort(key=lambda x: -x[0])``).
    * **Precision**: scores are computed in float32 as before, but a batched
      gemv accumulates in a different order than the old per-row ``np.dot``,
      so individual scores may differ at float-epsilon level (~1e-7). All
      callers treat scores as ranking keys rather than exact values, so this
      is safe; the differential tests pin the difference to <= 1e-6.
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

        # One stacked decode + one matrix-vector product instead of n
        # per-row frombuffer/norm/dot round-trips.
        mat = np.frombuffer(
            b"".join(blob for blob, _ in gathered), dtype="<f4"
        ).reshape(len(gathered), q_dim)
        norms = np.linalg.norm(mat, axis=1)

        keep = norms > 0.0  # zero-norm rows were skipped one at a time before
        dots = (mat @ q_unit)[keep]
        kept_norms = norms[keep]
        payloads = [payload for (_, payload), k in zip(gathered, keep) if k]
        if dots.size == 0:
            return []

        # Widen to float64 before comparing/sorting: the old loop compared
        # ``float(np.dot(...)) >= threshold`` (a float64 of the same value).
        scores = (dots / kept_norms).astype(np.float64)
        sel = scores >= threshold
        scores = scores[sel]
        payloads = [p for p, s in zip(payloads, sel) if s]

        # Descending, stable: exactly-equal scores keep original row order.
        order = np.argsort(-scores, kind="stable")
        return [(float(scores[i]), payloads[i]) for i in order]
    except ImportError:
        # Pure-Python fallback using the shared vector_math helpers.
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
