"""Phase 3: cross-encoder reranking for semantic_search.

sentence-transformers isn't installed in this environment (no [semantic]
extra), so these tests exercise the *disabled* and *graceful-fallback* paths
end-to-end, plus reranker.rerank()'s pure-Python contract directly with a
fake model substituted in for the real CrossEncoder. That combination proves
the wiring without needing a model download.
"""
from __future__ import annotations

import sqlite3

import pytest

# Apply the shared hash-backend fixture to every test in this module
# (the local autouse copy used to live here; see tests/conftest.py).
pytestmark = pytest.mark.usefixtures("hash_backend")


@pytest.fixture(autouse=True)
def _neutralize_rerank_marker(monkeypatch):
    """Default: pretend no persistent rerank marker exists, so tests are
    deterministic regardless of whether `cairn download-reranker` was run on
    this machine. Tests that exercise the marker override this patch."""
    from cairn.graph import reranker as rrk
    monkeypatch.setattr(rrk, "_rerank_marker_path", lambda: _no_marker_path())


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


def _no_marker_path():
    """A path guaranteed not to exist — used to neutralize the real persistent
    rerank marker so tests are deterministic on machines where
    `cairn download-reranker` has been run."""
    from pathlib import Path
    return Path("/nonexistent/cairn-test-marker-does-not-exist")


class TestRerankEnabled:
    def test_disabled_by_default(self, monkeypatch):
        # No env var AND no persistent marker → off.
        monkeypatch.delenv("CAIRN_RERANK", raising=False)
        from cairn.graph import reranker as rrk
        assert rrk.rerank_enabled() is False

    def test_enabled_via_env_var(self, monkeypatch):
        monkeypatch.setenv("CAIRN_RERANK", "1")
        from cairn.graph import reranker as rrk
        assert rrk.rerank_enabled() is True

    def test_enabled_via_download_marker(self, monkeypatch, tmp_path):
        """A successful download-reranker writes a marker; rerank_enabled()
        honors it even when CAIRN_RERANK is unset."""
        from cairn.graph import reranker as rrk
        marker = tmp_path / "rerank_enabled"
        marker.write_text("BAAI/bge-reranker-base\n")
        monkeypatch.delenv("CAIRN_RERANK", raising=False)
        monkeypatch.setattr(rrk, "_rerank_marker_path", lambda: marker)

        assert rrk.rerank_enabled() is True

    def test_env_off_overrides_marker(self, monkeypatch, tmp_path):
        """CAIRN_RERANK=0 is a hard kill switch — wins even if the marker exists."""
        from cairn.graph import reranker as rrk
        marker = tmp_path / "rerank_enabled"
        marker.write_text("BAAI/bge-reranker-base\n")
        monkeypatch.setenv("CAIRN_RERANK", "0")
        monkeypatch.setattr(rrk, "_rerank_marker_path", lambda: marker)

        assert rrk.rerank_enabled() is False

    def test_env_on_overrides_missing_marker(self, monkeypatch):
        """CAIRN_RERANK=1 enables even without the marker (env is explicit)."""
        from cairn.graph import reranker as rrk
        monkeypatch.setenv("CAIRN_RERANK", "1")
        assert rrk.rerank_enabled() is True


class TestRerankFallback:
    def test_disabled_returns_candidates_unchanged(self, monkeypatch):
        monkeypatch.delenv("CAIRN_RERANK", raising=False)
        from cairn.graph import reranker as rrk

        candidates = [{"chunk": "a", "score": 0.9}, {"chunk": "b", "score": 0.5}]
        out, reranked = rrk.rerank("query", candidates, limit=1)
        assert reranked is False
        assert out == candidates[:1]

    def test_enabled_but_uninstalled_degrades_gracefully(self, monkeypatch):
        """No sentence-transformers in this env -- must fall back, not raise."""
        monkeypatch.setenv("CAIRN_RERANK", "1")
        from cairn.graph import reranker as rrk
        monkeypatch.setattr(rrk, "reranker_available", lambda: False)

        candidates = [{"chunk": "a"}, {"chunk": "b"}, {"chunk": "c"}]
        out, reranked = rrk.rerank("query", candidates, limit=2)
        assert reranked is False
        assert out == candidates[:2]

    def test_enabled_but_model_not_cached_falls_back_to_hybrid(self, monkeypatch):
        """Rerank enabled + installed, but the configured model is missing from
        the cache → fall back to the hybrid (unchanged) order, not a download
        or a crash. This is the proactive guard added so auto-enable (via the
        download marker) is safe even if the cache is later evicted."""
        monkeypatch.setenv("CAIRN_RERANK", "1")
        from cairn.graph import reranker as rrk
        monkeypatch.setattr(rrk, "reranker_available", lambda: True)
        # Simulate the model NOT being cached locally.
        monkeypatch.setattr(rrk, "reranker_model_is_cached", lambda name=None: False)

        candidates = [{"chunk": "a", "score": 0.9}, {"chunk": "b", "score": 0.5}]
        out, reranked = rrk.rerank("query", candidates, limit=2)
        assert reranked is False
        # Hybrid order preserved — no reranking applied, candidates unchanged.
        assert out == candidates

    def test_empty_candidates_short_circuits(self, monkeypatch):
        monkeypatch.setenv("CAIRN_RERANK", "1")
        from cairn.graph import reranker as rrk

        out, reranked = rrk.rerank("query", [], limit=5)
        assert out == []
        assert reranked is False


class TestRerankSuccessPath:
    def test_rerank_resorts_by_fake_model_score(self, monkeypatch):
        """Substitute a fake CrossEncoder to prove the resort/truncate logic
        without needing the real model downloaded.

        `rerank()` gates on `reranker_available()` (a CrossEncoder import
        check), which is False when the [semantic] extra isn't installed --
        so we also stub `reranker_available` to True here. Without that stub
        the fake model in the cache is never reached: the availability gate
        returns (candidates, False) first. This lets the resort/truncate
        contract run in the default (extra-free) test environment.
        """
        from cairn.graph import reranker as rrk

        monkeypatch.setenv("CAIRN_RERANK", "1")
        monkeypatch.setattr(rrk, "reranker_available", lambda: True)

        class FakeModel:
            def predict(self, pairs):
                # Score higher for candidates whose chunk contains "backoff",
                # inverting the input order to prove resorting actually happens.
                return [1.0 if "backoff" in chunk else 0.1 for _, chunk in pairs]

        rrk._RERANKER_CACHE[rrk.current_rerank_model()] = FakeModel()
        try:
            candidates = [
                {"chunk": "formats a date for display"},
                {"chunk": "retries with backoff"},
            ]
            out, reranked = rrk.rerank("retry logic", candidates, limit=2)
            assert reranked is True
            assert out[0]["chunk"] == "retries with backoff"
            assert out[0]["rerank_score"] == 1.0
        finally:
            rrk._RERANKER_CACHE.clear()


class TestSemanticSearchIntegration:
    def test_semantic_search_without_rerank_has_reranked_false(self, monkeypatch, fresh_db):
        monkeypatch.delenv("CAIRN_RERANK", raising=False)
        from cairn.graph.queries import semantic_search

        conn = _conn_with_symbols(fresh_db)
        from cairn.graph import embeddings as emb

        emb.embed_all(conn)

        results = semantic_search(conn, "safeApiCall", limit=5, threshold=0.0)
        assert results, "expected at least one hit"
        assert all(r["reranked"] is False for r in results)
        assert "rerank_score" not in results[0]

    def test_semantic_search_with_rerank_enabled_but_uninstalled_still_returns_results(
        self, monkeypatch, fresh_db
    ):
        """Enabling CAIRN_RERANK without the extra installed must degrade,
        not break semantic_search."""
        monkeypatch.setenv("CAIRN_RERANK", "1")
        monkeypatch.setattr("cairn.graph.reranker.reranker_available", lambda: False)
        from cairn.graph.queries import semantic_search

        conn = _conn_with_symbols(fresh_db)
        from cairn.graph import embeddings as emb

        emb.embed_all(conn)

        results = semantic_search(conn, "safeApiCall", limit=5, threshold=0.0)
        assert results, "expected at least one hit even with rerank stage falling back"
        assert all(r["reranked"] is False for r in results)
