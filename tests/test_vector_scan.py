"""Differential tests for the batched numpy path of ``cosine_scan``.

The OLD per-row loop is kept verbatim below as the oracle. The new stacked
matrix implementation must match it on:

* the kept payload set (dim-mismatch and zero-norm rows skipped),
* per-row scores (within 1e-6 -- the batched gemv accumulates in a different
  order than the per-row ``np.dot``, so float-epsilon drift is expected),
* ordering (descending, stable: exactly-equal scores keep input order).

All tests are hermetic: no DB, no models, only seeded numpy/struct data.
"""
from __future__ import annotations

import random

import pytest

# numpy is an optional [semantic]-extra dependency; CI test jobs install
# only [dev]. The differential oracle needs it (the old loop was numpy
# itself), so the whole file skips where numpy is absent -- same pattern
# as test_ann_index.py for sqlite-vec. The pure-Python fallback path stays
# covered by tests/test_retrieval.py, which runs everywhere.
np = pytest.importorskip("numpy")

from cairn.retrieval import cosine_scan

# ---------------------------------------------------------------- oracle ----


def _reference_cosine_scan(q_blob, q_dim, rows, threshold=0.0):
    """The OLD per-row numpy loop, verbatim, as the differential oracle."""
    import numpy as np

    q = np.frombuffer(q_blob, dtype="<f4")
    qn = float(np.linalg.norm(q))
    if qn == 0.0:
        return []
    q_unit = q / qn
    scored = []
    for vec_blob, dim, payload in rows:
        if dim != q_dim:
            continue
        v = np.frombuffer(vec_blob, dtype="<f4")
        vn = float(np.linalg.norm(v))
        if vn == 0.0:
            continue
        score = float(np.dot(q_unit, v / vn))
        if score >= threshold:
            scored.append((score, payload))
    scored.sort(key=lambda x: -x[0])
    return scored


# --------------------------------------------------------------- helpers ----


def _pack(vals) -> bytes:
    return np.asarray(vals, dtype="<f4").tobytes()


def _gauss_list(rng: random.Random, dim: int) -> list[float]:
    return [rng.gauss(0.0, 1.0) for _ in range(dim)]


def _make_rows(rng: random.Random, n: int, dim: int, query: list[float]):
    """Seeded fixture: ~10% dim-mismatched, ~5% zero-norm, ~10% exact
    duplicates (guaranteed exact score ties), and a mix of query-aligned and
    random vectors so both thresholds keep a non-trivial set."""
    rows = []
    made: list[bytes] = []
    for i in range(n):
        payload = ("row", i)
        r = rng.random()
        if r < 0.10:  # stale row from another dimensionality -> skipped
            other = dim + 4 if i % 2 == 0 else dim - 4
            rows.append((_pack(_gauss_list(rng, other)), other, payload))
        elif r < 0.15:  # zero vector -> skipped
            rows.append((_pack([0.0] * dim), dim, payload))
        elif r < 0.25 and made:  # exact duplicate blob -> exact score tie
            rows.append((rng.choice(made), dim, payload))
        elif rng.random() < 0.3:  # query-aligned
            blob = _pack([qc + rng.gauss(0.0, 0.35) for qc in query])
            made.append(blob)
            rows.append((blob, dim, payload))
        else:  # plain random
            blob = _pack(_gauss_list(rng, dim))
            made.append(blob)
            rows.append((blob, dim, payload))
    return rows


def _assert_equivalent(ref, new, index_of):
    """Differential contract: same kept set, scores within 1e-6, no ordering
    inversions vs the oracle, and stable exact ties in input order."""
    assert len(new) == len(ref)
    ref_by_payload = {payload: score for score, payload in ref}
    assert {p for _, p in new} == set(ref_by_payload)

    prev = None
    for score, payload in new:
        assert isinstance(score, float), "scores must be Python floats"
        assert abs(score - ref_by_payload[payload]) <= 1e-6
        if prev is not None:
            prev_score, prev_payload = prev
            # new's own scores are non-increasing (descending sort)
            assert score <= prev_score
            # no inversion vs the oracle beyond the epsilon budget
            assert ref_by_payload[prev_payload] >= ref_by_payload[payload] - 1e-6
            # exactly-equal scores: stable tie, must keep input order
            if score == prev_score:
                assert index_of[prev_payload] < index_of[payload]
        prev = (score, payload)


# ----------------------------------------------------------------- tests ----


class TestBatchedMatchesReference:
    def test_randomized_differential(self):
        """Seeded sweep: dims 8-64 x thresholds {0.0, 0.5}, 250 rows each."""
        rng = random.Random(20240815)
        for dim in (8, 16, 33, 64):
            for threshold in (0.0, 0.5):
                query = _gauss_list(rng, dim)
                rows = _make_rows(rng, n=250, dim=dim, query=query)
                index_of = {payload: i for i, (_, _, payload) in enumerate(rows)}
                q_blob = _pack(query)
                ref = _reference_cosine_scan(q_blob, dim, rows, threshold)
                new = cosine_scan(q_blob, dim, rows, threshold)
                assert ref, "fixture must keep rows at both thresholds"
                _assert_equivalent(ref, new, index_of)

    def test_empty_rows_list(self):
        q = _pack([1.0, 0.0])
        assert cosine_scan(q, 2, []) == []
        assert _reference_cosine_scan(q, 2, []) == []

    def test_single_row(self):
        q = _pack([1.0, 2.0, 3.0])
        row = (_pack([4.0, 5.0, 6.0]), 3, ("only", 0))
        index_of = {p: 0 for _, _, p in [row]}
        ref = _reference_cosine_scan(q, 3, [row])
        new = cosine_scan(q, 3, [row])
        _assert_equivalent(ref, new, index_of)
        assert new[0][1] is row[2]

    def test_all_zero_norm_rows(self):
        q = _pack([1.0, 0.0])
        rows = [(_pack([0.0, 0.0]), 2, ("z", i)) for i in range(5)]
        assert cosine_scan(q, 2, rows) == []
        assert _reference_cosine_scan(q, 2, rows) == []

    def test_all_dim_mismatched(self):
        q = _pack([1.0, 0.0])
        rows = [(_pack([1.0, 0.0, 0.0]), 3, ("m", i)) for i in range(5)]
        assert cosine_scan(q, 2, rows) == []
        assert _reference_cosine_scan(q, 2, rows) == []

    def test_zero_norm_query_returns_empty(self):
        rows = [(_pack([1.0, 0.0]), 2, ("a", 0))]
        assert cosine_scan(_pack([0.0, 0.0]), 2, rows) == []
        assert _reference_cosine_scan(_pack([0.0, 0.0]), 2, rows) == []

    def test_payload_objects_returned_unchanged(self):
        """The ORIGINAL payload objects (identity, not copies) come back."""
        q = _pack([1.0, 0.0])
        payloads = [("p", i) for i in range(6)]
        rows = [
            (_pack([1.0, 0.0]), 2, payloads[0]),
            (_pack([0.5, 0.5]), 2, payloads[1]),
            (_pack([1.0, 0.0, 0.0]), 3, payloads[2]),  # skipped
            (_pack([0.0, 0.0]), 2, payloads[3]),  # skipped
            (_pack([0.9, 0.1]), 2, payloads[4]),
            (_pack([0.0, 1.0]), 2, payloads[5]),
        ]
        out = cosine_scan(q, 2, rows)
        assert [p for _, p in out] == [payloads[0], payloads[4], payloads[1], payloads[5]]
        assert all(p is orig for (_, p), orig in zip(out, [payloads[0], payloads[4], payloads[1], payloads[5]]))

    def test_malformed_blob_skipped(self):
        """A blob whose byte length != dim*4 is skipped instead of scoring
        garbage (the old per-row loop raised ValueError on ragged blobs; a
        stacked reshape must never see them)."""
        q = _pack([1.0, 0.0])
        rows = [
            (b"", 2, ("empty", 0)),
            (_pack([1.0])[:3], 2, ("short", 1)),  # 3 bytes, not dim*4
            (_pack([0.6, 0.8]), 2, ("good", 2)),
        ]
        out = cosine_scan(q, 2, rows)
        assert [p for _, p in out] == [("good", 2)]
        assert abs(out[0][0] - 0.6) < 1e-6

    def test_exact_ties_preserve_input_order(self):
        """Stable tie contract: identical vectors (exact equal scores) come
        back in original input order, interleaved duplicates included."""
        q = _pack([1.0, 0.0, 0.0])
        a = _pack([1.0, 0.0, 0.0])  # cosine 1.0
        b = _pack([0.0, 1.0, 0.0])  # cosine 0.0
        rows = [
            (a, 3, ("a", 0)),
            (b, 3, ("b", 1)),
            (a, 3, ("a", 2)),
            (a, 3, ("a", 3)),
            (b, 3, ("b", 4)),
            (a, 3, ("a", 5)),
        ]
        new = cosine_scan(q, 3, rows)
        ref = _reference_cosine_scan(q, 3, rows)
        expected = [("a", 0), ("a", 2), ("a", 3), ("a", 5), ("b", 1), ("b", 4)]
        assert [p for _, p in new] == expected
        assert [p for _, p in ref] == expected
        # all four 'a' scores are exactly equal (and exactly 1.0)
        a_scores = [s for s, p in new if p[0] == "a"]
        assert a_scores[0] == 1.0
        assert len(set(a_scores)) == 1
