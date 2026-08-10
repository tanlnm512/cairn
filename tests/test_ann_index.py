"""Phase 2: native ANN index (sqlite-vec) for semantic_search.

sqlite-vec IS installed in this environment (spiked successfully -- v0.1.9,
cosine distance_metric supported natively), so these tests exercise the real
extension, not just the disabled/fallback path. Still uses
CAIRN_EMBED_BACKEND=hash for the vectors themselves so no model download
is needed.

Covers:
1. ANN on by default -- semantic_search behavior/results unchanged whether
   the pool comes from the ANN path or the brute-force scan.
2. rebuild_index + ann_query round-trip against a real sqlite-vec table.
3. Graceful fallback when enabled but no index has been built yet.
4. Explicit opt-out via CAIRN_ANN_BACKEND=off.
"""
from __future__ import annotations

import sqlite3

import pytest

sqlite_vec = pytest.importorskip(
    "sqlite_vec", reason="sqlite-vec not installed -- ANN tests need the real extension"
)

# Apply the shared hash-backend fixture to every test in this module
# (the local autouse copy used to live here; see tests/conftest.py).
pytestmark = pytest.mark.usefixtures("hash_backend")


def _seed_symbols(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/Api.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES (1, 1, 'safeApiCall', 'function', 'xyz.safeApiCall', 'Retries a network call with backoff.', 1, 10)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES (2, 1, 'formatDate', 'function', 'xyz.formatDate', 'Formats a date for display.', 12, 20)"
    )
    conn.commit()


def _conn_with_symbols(fresh_db) -> sqlite3.Connection:
    _seed_symbols(fresh_db)
    return fresh_db


def test_try_load_succeeds_in_this_environment():
    from cairn.graph import ann_index as ann

    conn = sqlite3.connect(":memory:")
    assert ann.try_load(conn) is True


def test_ann_backend_enabled_by_default(monkeypatch):
    monkeypatch.delenv("CAIRN_ANN_BACKEND", raising=False)
    from cairn.graph import ann_index as ann

    assert ann.ann_backend_enabled() is True


def test_ann_backend_enabled_via_env_var(monkeypatch):
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    from cairn.graph import ann_index as ann

    assert ann.ann_backend_enabled() is True


def test_ann_backend_disabled_via_explicit_opt_out(monkeypatch):
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    from cairn.graph import ann_index as ann

    assert ann.ann_backend_enabled() is False


def test_ann_query_returns_none_when_no_index_built(monkeypatch, fresh_db):
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    from cairn.graph import ann_index as ann, embeddings as emb

    conn = _conn_with_symbols(fresh_db)
    emb.embed_all(conn)
    q_blob, _ = emb.embed_query("safeApiCall")

    result = ann.ann_query(conn, emb.current_model(), q_blob, k=5)
    assert result is None, "no index built yet -- caller must fall back to brute force"


def test_rebuild_index_and_query_round_trip(monkeypatch, fresh_db):
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    from cairn.graph import ann_index as ann, embeddings as emb

    conn = _conn_with_symbols(fresh_db)
    emb.embed_all(conn)
    model = emb.current_model()

    summary = ann.rebuild_index(conn, model)
    assert summary["indexed"] == 2
    assert ann.index_exists(conn, model)

    q_blob, _ = emb.embed_query("safeApiCall")
    hits = ann.ann_query(conn, model, q_blob, k=5)
    assert hits is not None
    assert len(hits) == 2
    ids = [sid for sid, _score in hits]
    assert "1" in ids and "2" in ids


def test_semantic_search_uses_ann_path_when_index_built(monkeypatch, fresh_db):
    """End-to-end: rebuild the index, enable the backend, confirm
    semantic_search returns results via the ANN path (not just that ANN
    itself works in isolation)."""
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    from cairn.graph import ann_index as ann, embeddings as emb
    from cairn.graph.queries import semantic_search

    conn = _conn_with_symbols(fresh_db)
    emb.embed_all(conn)
    ann.rebuild_index(conn, emb.current_model())

    # threshold=-1.0: the hash embedder can produce a negative cosine score
    # between unrelated chunks, which a threshold=0.0 would legitimately
    # filter out -- not an ANN bug, just the hash embedder being low quality.
    # Use -1.0 here so both symbols are guaranteed to pass and we're testing
    # the ANN plumbing, not the hash embedder's semantic quality.
    results = semantic_search(conn, "safeApiCall", limit=5, threshold=-1.0)
    assert results, "expected hits via the ANN retrieval path"
    ids = {r["id"] for r in results}
    assert "1" in ids and "2" in ids


def test_semantic_search_falls_back_when_ann_enabled_but_no_index(monkeypatch, fresh_db):
    """ANN enabled, but rebuild_index was never called for this model --
    semantic_search must still work via the brute-force fallback."""
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    from cairn.graph import embeddings as emb
    from cairn.graph.queries import semantic_search

    conn = _conn_with_symbols(fresh_db)
    emb.embed_all(conn)  # note: no ann.rebuild_index() call

    results = semantic_search(conn, "safeApiCall", limit=5, threshold=0.0)
    assert results, "must fall back to brute-force scan, not return empty/crash"


def test_semantic_search_default_env_falls_back_without_index(monkeypatch, fresh_db):
    """Default env (ANN on, but no index built here) must match prior (ANN-off)
    results -- rebuild_index was never called, so ann_query returns None and
    semantic_search falls back to the brute-force scan either way."""
    monkeypatch.delenv("CAIRN_ANN_BACKEND", raising=False)
    from cairn.graph import embeddings as emb
    from cairn.graph.queries import semantic_search

    conn = _conn_with_symbols(fresh_db)
    emb.embed_all(conn)

    # See comment in test_semantic_search_uses_ann_path_when_index_built on
    # why this needs threshold=-1.0 rather than 0.0 with the hash embedder.
    results = semantic_search(conn, "safeApiCall", limit=5, threshold=-1.0)
    assert len(results) == 2
    # With fusion now actually running (P3 fix: the .get()-on-Row bug that
    # silently skipped RRF fusion is fixed), default provenance is the fused
    # label, not plain "semantic". Either is valid depending on whether fusion
    # produced BM25-only entries; the key contract is the results are present.
    for r in results:
        assert r["provenance"] in (
            "semantic", "fused(bm25+semantic)", "fused(bm25+semantic, hash)",
            "bm25",
        ), f"unexpected provenance: {r['provenance']!r}"
