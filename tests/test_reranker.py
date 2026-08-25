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
        # The proactive cache guard (added with auto-enable) checks the model
        # is cached before loading; stub it True so the fake-model path runs
        # in CI where no real model is downloaded. (Locally a real model may
        # be cached, which masked this — the test was environment-dependent.)
        monkeypatch.setattr(rrk, "reranker_model_is_cached", lambda name=None: True)

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


# ---------------------------------------------------------------------------
# T016 (FR-004, D-005): structured rerank pairs, pinned truncation, sigmoid
# ---------------------------------------------------------------------------


class _PairRecorder:
    """Fake CrossEncoder that records every (query, text) pair it scores."""

    def __init__(self, scores=None, tokenizer=None):
        self.pairs = []
        self._scores = scores
        if tokenizer is not None:
            self.tokenizer = tokenizer

    def predict(self, pairs):
        self.pairs.extend(pairs)
        if self._scores is not None:
            return list(self._scores[: len(pairs)])
        return [0.0] * len(pairs)


class _CharTokenizer:
    """Deterministic test tokenizer: exactly one token per character."""

    def __call__(self, text, add_special_tokens=True):
        return {"input_ids": [ord(ch) for ch in text]}

    def decode(self, ids, **kwargs):
        return "".join(chr(i) for i in ids)


_VARIANT_B_CHUNK = (
    "File: /tmp/test/Api.kt\n"
    "Enclosing Scope: xyz.Api\n"
    "function xyz.safeApiCall\n"
    "Signature: suspend fun safeApiCall(url: String): Result\n"
    "Parameters: url: String\n"
    "Return Type: Result\n"
    "Docstring: Retries a network call with backoff."
)


def _install_fake_model(monkeypatch, model):
    """Arm rerank() to run against `model` (availability + cache stubbed)."""
    from cairn.graph import reranker as rrk

    monkeypatch.setenv("CAIRN_RERANK", "1")
    monkeypatch.setattr(rrk, "reranker_available", lambda: True)
    monkeypatch.setattr(rrk, "reranker_model_is_cached", lambda name=None: True)
    rrk._RERANKER_CACHE[rrk.current_rerank_model()] = model


def _full_candidate():
    return {
        "id": 1,
        "name": "safeApiCall",
        "kind": "function",
        "qualified_name": "xyz.safeApiCall",
        "file_path": "/tmp/test/Api.kt",
        "repo": "test",
        "score": 0.9,
        "chunk": _VARIANT_B_CHUNK,
        "provenance": "fused(bm25+semantic)",
        "reranked": False,
    }


class TestStructuredPairConstruction:
    def test_pair_text_is_importance_ordered(self, monkeypatch):
        """Head order is exactly D-005's: kind+qname, path, signature,
        docstring — then the full stored chunk as the truncatable tail."""
        from cairn.graph import reranker as rrk

        model = _PairRecorder()
        _install_fake_model(monkeypatch, model)
        try:
            rrk.rerank("retry logic", [_full_candidate()], limit=1, structured=True)
        finally:
            rrk._RERANKER_CACHE.clear()

        assert len(model.pairs) == 1
        query, text = model.pairs[0]
        assert query == "retry logic"  # query side passes through verbatim
        lines = text.splitlines()
        assert lines[0] == "function xyz.safeApiCall"
        assert lines[1] == "File: /tmp/test/Api.kt"
        assert lines[2] == (
            "Signature: suspend fun safeApiCall(url: String): Result"
        )
        assert lines[3] == "Docstring: Retries a network call with backoff."
        # The chunk itself is appended intact after the head, so extraction
        # can never lose the context fields (scope, imports, parameters).
        assert text.endswith(_VARIANT_B_CHUNK)

    def test_bm25_only_candidate_reranks_against_identity_not_empty(self, monkeypatch):
        """BM25-only candidates carry an empty chunk (semantic.py's fusion
        builds them with chunk=""): today they rerank against the EMPTY
        string. The structured head gives them kind+qname+path."""
        from cairn.graph import reranker as rrk

        model = _PairRecorder()
        _install_fake_model(monkeypatch, model)
        candidate = {
            "id": 2,
            "name": "formatDate",
            "kind": "function",
            "qualified_name": "xyz.formatDate",
            "file_path": "/tmp/test/Api.kt",
            "chunk": "",
        }
        try:
            rrk.rerank("format a date", [candidate], limit=1, structured=True)
        finally:
            rrk._RERANKER_CACHE.clear()

        _, text = model.pairs[0]
        assert text == "function xyz.formatDate\nFile: /tmp/test/Api.kt"

    def test_missing_fields_degrade_to_absence(self, monkeypatch):
        """Every head field is optional; absent ones skip their line instead
        of emitting empty labels, and a fully-empty candidate degrades to the
        empty string exactly like the legacy flat format."""
        from cairn.graph import reranker as rrk

        model = _PairRecorder()
        _install_fake_model(monkeypatch, model)
        candidates = [
            {"name": "bare"},  # no kind/path/chunk: name fallback for qname
            {},  # nothing at all
            {"kind": "class"},  # kind without a name is useless alone
        ]
        try:
            out, reranked = rrk.rerank("q", candidates, limit=3, structured=True)
        finally:
            rrk._RERANKER_CACHE.clear()

        assert reranked is True
        assert [text for _, text in model.pairs] == ["bare", "", "class"]
        assert len(out) == 3

    def test_signature_extraction_survives_multiline_sections(self, monkeypatch):
        """Signature/Docstring sections can span lines; extraction reads to
        the next labeled section, not to the next line."""
        from cairn.graph import reranker as rrk

        model = _PairRecorder()
        _install_fake_model(monkeypatch, model)
        chunk = (
            "File: /a.kt\n"
            "function multi\n"
            "Signature: fun multi(\n"
            "    a: Int,\n"
            "    b: Int\n"
            ")\n"
            "Docstring: First line\n"
            "    second line"
        )
        candidate = {
            "kind": "function",
            "qualified_name": "multi",
            "file_path": "/a.kt",
            "chunk": chunk,
        }
        try:
            rrk.rerank("q", [candidate], limit=1, structured=True)
        finally:
            rrk._RERANKER_CACHE.clear()

        _, text = model.pairs[0]
        lines = text.splitlines()
        assert lines[2] == "Signature: fun multi("
        assert lines[3] == "    a: Int,"
        assert lines[4] == "    b: Int"
        assert lines[5] == ")"
        assert lines[6] == "Docstring: First line"
        assert lines[7] == "    second line"

    def test_flat_format_still_reachable_for_ab(self, monkeypatch):
        """structured=False reproduces the legacy pair byte-for-byte (raw
        chunk, no structured head, no pre-truncation) so T017's A/B measures
        the pair format alone."""
        from cairn.graph import reranker as rrk

        model = _PairRecorder(scores=[1.0])
        _install_fake_model(monkeypatch, model)
        candidates = [_full_candidate()]
        try:
            out, reranked = rrk.rerank("retry logic", candidates, limit=1, structured=False)
        finally:
            rrk._RERANKER_CACHE.clear()

        assert reranked is True
        assert model.pairs == [("retry logic", _VARIANT_B_CHUNK)]
        assert out[0]["rerank_score"] == 1.0


class TestMaxLengthPin:
    def test_crossencoder_constructed_with_explicit_max_length(self, monkeypatch):
        """The encoder is built with max_length=512 explicitly (D-005 pin),
        asserted on the constructed object via an injected factory stub —
        never by loading the real model."""
        import sys
        import types

        from cairn.graph import reranker as rrk

        recorded = {}

        class RecordingCrossEncoder:
            def __init__(self, model_name, **kwargs):
                recorded["model_name"] = model_name
                recorded["kwargs"] = kwargs
                self.max_length = kwargs.get("max_length")

        fake_module = types.ModuleType("sentence_transformers")
        fake_module.CrossEncoder = RecordingCrossEncoder
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
        rrk._RERANKER_CACHE.clear()
        try:
            model = rrk._get_reranker()
        finally:
            rrk._RERANKER_CACHE.clear()

        assert recorded["model_name"] == rrk.current_rerank_model()
        assert recorded["kwargs"].get("max_length") == 512
        assert rrk.RERANK_MAX_LENGTH == 512
        assert model.max_length == 512


class TestQueryPriorityTruncation:
    def test_default_is_flat_per_t017(self, monkeypatch):
        """T017 measured structured pairs at -10.4pp MRR vs flat on identical
        pools (cross-session-anchored); the default is FLAT, structured
        reachable for re-measurement."""
        from cairn.graph import reranker as rrk

        model = _PairRecorder()
        _install_fake_model(monkeypatch, model)
        try:
            rrk.rerank("retry logic", [_full_candidate()], limit=1)
        finally:
            rrk._RERANKER_CACHE.clear()
        assert model.pairs, "rerank ran"
        assert model.pairs[0][1] == _VARIANT_B_CHUNK
    def _long_candidate(self):
        head = "function xyz.huge\nFile: /tmp/test/H.kt\n"
        body = "x" * 600
        tail_sentinel = "SENTINEL_TAIL_LOSES_FIRST"
        return {
            "kind": "function",
            "qualified_name": "xyz.huge",
            "file_path": "/tmp/test/H.kt",
            "chunk": head + body + tail_sentinel,
        }

    def test_candidate_truncated_from_tail_query_verbatim(self, monkeypatch):
        """A pair that would exceed 512 tokens: the query reaches the model
        verbatim; the candidate loses exactly the tokens left after the
        query (plus the 3 pair special tokens), cut from its TAIL."""
        from cairn.graph import reranker as rrk

        model = _PairRecorder(tokenizer=_CharTokenizer())
        _install_fake_model(monkeypatch, model)
        query = "find the huge function"  # 22 chars == 22 tokens
        candidate = self._long_candidate()
        try:
            rrk.rerank(query, [candidate], limit=1, structured=True)
        finally:
            rrk._RERANKER_CACHE.clear()

        assert len(model.pairs) == 1
        scored_query, text = model.pairs[0]
        assert scored_query == query  # never truncated
        expected_budget = 512 - len(query) - 3  # == 487 char-tokens
        assert len(text) == expected_budget
        # The head survived intact; the tail sentinel did not.
        assert text.startswith("function xyz.huge\nFile: /tmp/test/H.kt\n")
        assert "SENTINEL_TAIL_LOSES_FIRST" not in text

    def test_short_candidate_passes_through_untruncated(self, monkeypatch):
        from cairn.graph import reranker as rrk

        model = _PairRecorder(tokenizer=_CharTokenizer())
        _install_fake_model(monkeypatch, model)
        candidate = _full_candidate()
        try:
            rrk.rerank("retry logic", [candidate], limit=1, structured=True)
        finally:
            rrk._RERANKER_CACHE.clear()

        _, text = model.pairs[0]
        # Structured text with the chunk appended, nothing cut.
        assert text.endswith(_VARIANT_B_CHUNK)
        assert text.startswith("function xyz.safeApiCall\n")

    def test_oversized_query_degrades_to_untruncated_text(self, monkeypatch):
        """A query that alone fills the window leaves no budget to protect;
        the text is returned as-is (the SDK decides) rather than emptied."""
        from cairn.graph import reranker as rrk

        model = _PairRecorder(tokenizer=_CharTokenizer())
        _install_fake_model(monkeypatch, model)
        huge_query = "q" * 600
        candidate = {"kind": "function", "qualified_name": "x.y", "chunk": "abc"}
        try:
            rrk.rerank(huge_query, [candidate], limit=1, structured=True)
        finally:
            rrk._RERANKER_CACHE.clear()

        _, text = model.pairs[0]
        assert model.pairs[0][0] == huge_query  # oversized query still verbatim
        assert text.endswith("abc")

    def test_char_fallback_when_no_tokenizer(self, monkeypatch):
        """Stub models without a .tokenizer fall back to the ~4 chars/token
        approximation: query still verbatim, candidate cut from the tail."""
        from cairn.graph import reranker as rrk

        model = _PairRecorder()  # no tokenizer attribute
        _install_fake_model(monkeypatch, model)
        query = "find the huge function"  # 22 chars
        # Long enough to exceed the ~4 chars/token fallback budget of
        # (512-3)*4 - len(query) chars.
        chunk = "function xyz.huge\n" + "y" * 2500 + "SENTINEL_TAIL_LOSES_FIRST"
        candidate = {"kind": "function", "qualified_name": "xyz.huge", "chunk": chunk}
        try:
            rrk.rerank(query, [candidate], limit=1, structured=True)
        finally:
            rrk._RERANKER_CACHE.clear()

        assert model.pairs[0][0] == query
        _, text = model.pairs[0]
        expected_chars = (512 - 3) * 4 - len(query)
        assert len(text) == expected_chars
        assert "SENTINEL_TAIL_LOSES_FIRST" not in text
        assert text.startswith("function xyz.huge")


class TestSigmoidNormalization:
    def test_norm_field_accompanies_raw_score(self, monkeypatch):
        """rerank_score stays the RAW unbounded logit (compat: existing
        consumers order by it); rerank_score_norm is its sigmoid image in
        [0, 1] for any future thresholding and T017's analysis."""
        import math

        from cairn.graph import reranker as rrk

        model = _PairRecorder(scores=[8.0, 0.0, -8.0])
        _install_fake_model(monkeypatch, model)
        candidates = [
            {"kind": "function", "qualified_name": f"x.f{i}", "chunk": "c"}
            for i in range(3)
        ]
        try:
            out, reranked = rrk.rerank("q", candidates, limit=3, structured=True)
        finally:
            rrk._RERANKER_CACHE.clear()

        assert reranked is True
        # Descending raw order preserved; raw logits exposed unchanged.
        assert [c["rerank_score"] for c in out] == [8.0, 0.0, -8.0]
        assert out[0]["rerank_score_norm"] == pytest.approx(1.0 / (1.0 + math.exp(-8.0)))
        assert out[1]["rerank_score_norm"] == pytest.approx(0.5)
        assert out[2]["rerank_score_norm"] == pytest.approx(1.0 / (1.0 + math.exp(8.0)))
        for c in out:
            assert 0.0 <= c["rerank_score_norm"] <= 1.0
        # Monotone: raw order == norm order.
        norms = [c["rerank_score_norm"] for c in out]
        assert norms == sorted(norms, reverse=True)

    def test_extreme_logits_do_not_overflow(self):
        """The naive sigmoid overflows at |x| > ~709; the two-branch form
        must stay finite (bge logits are unbounded)."""
        from cairn.graph.reranker import _sigmoid

        assert _sigmoid(-1000.0) == pytest.approx(0.0)
        assert _sigmoid(1000.0) == pytest.approx(1.0)
        assert _sigmoid(-710.0) >= 0.0  # would OverflowError naively
        assert _sigmoid(710.0) <= 1.0


class TestSemanticSearchPassesEnrichedQuery:
    """T016 call-site hunk: the rerank pair's query side is the enriched
    dense query when FR-001 is on, the raw query otherwise (D-005)."""

    def test_enrich_on_routes_dense_query_to_rerank(self, monkeypatch, fresh_db):
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb
        from cairn.graph.query_enrich import enrich
        from cairn.graph.semantic import RetrievalParams
        from cairn.graph.queries import semantic_search

        conn = _conn_with_symbols(fresh_db)
        emb.embed_all(conn)
        monkeypatch.setenv("CAIRN_RERANK", "1")

        recorded = {}

        def fake_rerank(query, candidates, limit, structured=True):
            recorded["query"] = query
            recorded["structured"] = structured
            out = [dict(c) for c in candidates[:limit]]
            for item in out:
                item["rerank_score"] = 1.0
                item["rerank_score_norm"] = 0.5
            return out, True

        monkeypatch.setattr(rrk, "rerank", fake_rerank)

        raw_query = "how does safeApiCall handle backoff"
        semantic_search(
            conn, raw_query, limit=5, threshold=0.0,
            params=RetrievalParams(enrich=True),
        )

        expected = enrich(raw_query).dense_query
        assert recorded["query"] == expected
        assert expected != raw_query  # identifier actually appended
        assert recorded["structured"] is True  # default arm is structured

    def test_enrich_off_routes_raw_query_to_rerank(self, monkeypatch, fresh_db):
        from cairn.graph import reranker as rrk
        from cairn.graph import embeddings as emb
        from cairn.graph.queries import semantic_search

        conn = _conn_with_symbols(fresh_db)
        emb.embed_all(conn)
        monkeypatch.setenv("CAIRN_RERANK", "1")

        recorded = {}

        def fake_rerank(query, candidates, limit, structured=True):
            recorded["query"] = query
            return [dict(c) for c in candidates[:limit]], False

        monkeypatch.setattr(rrk, "rerank", fake_rerank)

        semantic_search(conn, "safeApiCall", limit=5, threshold=0.0)
        assert recorded["query"] == "safeApiCall"


# --- download_reranker_model fetches in a quiet child interpreter ----------
#
# Regression guard, mirroring the embeddings.download_model tests: the fetch
# used to construct CrossEncoder in-process, so HuggingFace printed one tqdm
# bar per repo file (plus transformers warnings) into the terminal for a
# ~1.1 GB model. The fetch now runs in a child interpreter behind the shared
# quiet progress helper, sharing the parent's HF cache. Faked at the
# _run_subprocess_with_progress seam (never patch subprocess.Popen globally).

def test_download_reranker_model_fetches_in_quiet_subprocess(monkeypatch, capsys):
    import sys

    from cairn.graph import embeddings as emb_mod
    from cairn.graph import reranker as rrk

    seen = {}

    def fake_run(cmd, description, env=None):
        seen["cmd"] = cmd
        seen["env"] = env
        return ""

    monkeypatch.setattr(emb_mod, "_run_subprocess_with_progress", fake_run)
    monkeypatch.setattr(rrk, "reranker_model_is_cached", lambda m=None: False)

    assert rrk.download_reranker_model("BAAI/bge-reranker-base") is True

    # A child interpreter constructs the CrossEncoder (which is what
    # downloads the weights into the shared HF cache).
    assert seen["cmd"][:2] == [sys.executable, "-c"]
    assert "CrossEncoder" in seen["cmd"][2]
    assert "BAAI/bge-reranker-base" in seen["cmd"][2]
    out = capsys.readouterr().out
    assert "Downloading reranker 'BAAI/bge-reranker-base'" in out
    assert "downloaded successfully" in out


def test_download_reranker_model_cached_skips_subprocess(monkeypatch):
    from cairn.graph import embeddings as emb_mod
    from cairn.graph import reranker as rrk

    calls = []
    monkeypatch.setattr(
        emb_mod, "_run_subprocess_with_progress", lambda *a, **k: calls.append(a)
    )
    monkeypatch.setattr(rrk, "reranker_model_is_cached", lambda m=None: True)

    assert rrk.download_reranker_model("BAAI/bge-reranker-base") is True
    assert calls == []


def test_download_reranker_model_failure_surfaces_child_output(monkeypatch, capsys):
    import subprocess as sp

    from cairn.graph import embeddings as emb_mod
    from cairn.graph import reranker as rrk

    def fake_run(cmd, description, env=None):
        print("Connection error: couldn't reach huggingface.co")
        raise sp.CalledProcessError(1, cmd, "conn")

    monkeypatch.setattr(emb_mod, "_run_subprocess_with_progress", fake_run)
    monkeypatch.setattr(rrk, "reranker_model_is_cached", lambda m=None: False)

    assert rrk.download_reranker_model("BAAI/bge-reranker-base") is False

    out = capsys.readouterr().out
    assert "huggingface.co" in out
    assert "Failed to download reranker" in out
