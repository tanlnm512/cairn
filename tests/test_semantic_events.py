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
    assert attrs["fusion"] == 1, "CAIRN_FUSION defaults on"
    assert attrs["rerank"] == 0, "rerank stubbed off for hermeticism"
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
    assert empty[0]["backend"] == attrs["backend"]


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

    assert _semantic_attrs()["fusion"] == 0


def test_rerank_on_reports_rerank_one(fresh_db, monkeypatch):
    """rerank_enabled True -> rerank attr is 1, even though the cross-encoder
    itself degrades (no model loaded). The attr reflects the query's config."""
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

    semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)

    assert _semantic_attrs()["rerank"] == 1


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def test_telemetry_off_suppresses_semantic_events(fresh_db, monkeypatch):
    """CAIRN_TELEMETRY=off -> no semantic_backend / empty_result buffered at all."""
    monkeypatch.setenv("CAIRN_TELEMETRY", "off")
    from cairn.graph.semantic import semantic_search

    semantic_search(fresh_db, "safeApiCall", limit=5)  # empty -> would normally fire both

    assert _buffered_events() == [], "telemetry off -> zero events buffered"
