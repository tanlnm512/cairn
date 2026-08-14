"""F4: `semantic_unavailable` -- durable signal for semantic-off degrades.

explore() and search_knowledge()'s semantic stage both degrade to lexical-only
results when the semantic backend can't contribute; those degrades previously
emitted nothing at all (explore: a debug log; knowledge/search._semantic_search:
a bare `return []` with no log). Now each surface records one bounded
`semantic_unavailable` event (surface: explore|knowledge, reason:
unavailable|no_embeddings|error) via ``note_semantic_unavailable``.

Asserted here:
  * each surface emits exactly once per process on repeated degrades;
  * the three reason enum values are reachable on the right branch;
  * the two surfaces warn/emit independently of each other;
  * CAIRN_TELEMETRY=off silences both the event and the WARNING;
  * out-of-enum surface/reason inputs are defensively coerced (the bounded
    domain can't be escaped by a future caller passing a wrong literal);
  * no attr value contains a path separator (the universal guard).
"""
from __future__ import annotations

import json
import logging

import pytest


@pytest.fixture(autouse=True)
def _reset_telemetry_state(monkeypatch):
    """Clear the sink buffer + once-guard set + gating env around each test."""
    from cairn.telemetry import events, sink

    with sink._LOCK:
        sink._BUFFER.clear()
    with events._WARN_LOCK:
        events._WARNED.clear()
    monkeypatch.delenv("CAIRN_TELEMETRY", raising=False)
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    yield
    with sink._LOCK:
        sink._BUFFER.clear()


def _buffered(name):
    from cairn.telemetry import sink

    return [
        json.loads(a) if a else {}
        for _ts, n, _sid, a in list(sink._BUFFER)
        if n == name
    ]


def _seed_symbol(conn) -> None:
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
    conn.commit()


# ---------------------------------------------------------------------------
# explore surface
# ---------------------------------------------------------------------------


def test_explore_emits_once_when_backend_unavailable(fresh_db, monkeypatch):
    """embeddings_available() False -> surface=explore, reason=unavailable,
    exactly once across repeated explores (once-guard per surface)."""
    from cairn.graph import embeddings as emb
    from cairn.graph.explore import explore

    _seed_symbol(fresh_db)
    monkeypatch.setattr(emb, "embeddings_available", lambda: False)

    explore(fresh_db, "safeApiCall", max_nodes=5)
    explore(fresh_db, "safeApiCall", max_nodes=5)

    events = _buffered("semantic_unavailable")
    assert events == [{"surface": "explore", "reason": "unavailable"}]


def test_explore_emits_no_embeddings_when_corpus_empty(fresh_db, monkeypatch):
    """Backend importable but zero stored embeddings -> reason=no_embeddings
    (a distinct, actionable state: run `cairn embed`)."""
    from cairn.graph import embeddings as emb
    from cairn.graph.explore import explore

    _seed_symbol(fresh_db)
    monkeypatch.setattr(emb, "embeddings_available", lambda: True)
    monkeypatch.setattr(emb, "embed_count", lambda conn: 0)

    explore(fresh_db, "safeApiCall", max_nodes=5)

    assert _buffered("semantic_unavailable") == [
        {"surface": "explore", "reason": "no_embeddings"}
    ]


def test_explore_emits_error_when_semantic_raises(fresh_db, monkeypatch):
    """An unexpected exception in the semantic expansion -> reason=error (the
    debug breadcrumb stays; the durable event makes it visible at all)."""
    from cairn.graph import embeddings as emb
    from cairn.graph.explore import explore

    _seed_symbol(fresh_db)
    monkeypatch.setattr(emb, "embeddings_available", lambda: True)
    monkeypatch.setattr(emb, "embed_count", lambda conn: 10)

    def _boom(*_a, **_k):
        raise RuntimeError("simulated semantic failure")

    monkeypatch.setattr(emb, "warn_hash_fallback_once", _boom)

    result = explore(fresh_db, "safeApiCall", max_nodes=5)
    assert result["seeds"], "explore still returns its FTS5 seeds"

    assert _buffered("semantic_unavailable") == [
        {"surface": "explore", "reason": "error"}
    ]


def test_explore_semantic_off_by_config_emits_nothing(fresh_db, monkeypatch):
    """CAIRN_FUSION=0 with >= 3 seeds skips the semantic block entirely -- an
    informed choice, exactly like CAIRN_ANN_BACKEND=off; no event may fire.
    search_symbols is stubbed to return 3 seeds so the `len(seeds) < 3`
    disjunct can't drag the block back in."""
    import importlib

    # cairn.graph.explore re-exports the function under the same name, so the
    # module must be fetched via importlib to monkeypatch its globals.
    explore_mod = importlib.import_module("cairn.graph.explore")

    monkeypatch.setenv("CAIRN_FUSION", "0")
    monkeypatch.setattr(
        explore_mod,
        "search_symbols",
        lambda conn, query, limit=100: [
            {"id": i, "name": f"sym{i}", "kind": "function",
             "qualified_name": f"x.sym{i}", "line_start": 1,
             "file_path": "/tmp/test/Api.kt", "repo": "test"}
            for i in range(3)
        ],
    )

    explore_mod.explore(fresh_db, "sym", max_nodes=5)

    assert _buffered("semantic_unavailable") == []


# ---------------------------------------------------------------------------
# knowledge surface
# ---------------------------------------------------------------------------


def test_knowledge_emits_once_when_backend_unavailable(fresh_db, monkeypatch):
    """The knowledge surface records its own event, independently of explore."""
    from cairn.graph import embeddings as emb
    from cairn.knowledge.search import _semantic_search

    monkeypatch.setattr(emb, "embeddings_available", lambda: False)

    assert _semantic_search(fresh_db, None, "query", 5, 0.3) == []
    assert _semantic_search(fresh_db, None, "query", 5, 0.3) == []

    assert _buffered("semantic_unavailable") == [
        {"surface": "knowledge", "reason": "unavailable"}
    ]


def test_knowledge_error_reason(fresh_db, monkeypatch):
    """A mid-search failure (embed_query raising) -> reason=error, [] returned."""
    from cairn.graph import embeddings as emb
    from cairn.knowledge.search import _semantic_search

    monkeypatch.setattr(emb, "embeddings_available", lambda: True)
    monkeypatch.setattr(emb, "embed_knowledge_count", lambda conn: 7)

    def _boom(query):
        raise RuntimeError("simulated embed failure")

    monkeypatch.setattr(emb, "embed_query", _boom)

    assert _semantic_search(fresh_db, None, "query", 5, 0.3) == []
    assert _buffered("semantic_unavailable") == [
        {"surface": "knowledge", "reason": "error"}
    ]


def test_explore_and_knowledge_surface_guards_independent(fresh_db, monkeypatch):
    """One event per surface: two surfaces degrading in one process -> two
    events total (the bounded 2-row worst case), each with its own reason."""
    from cairn.graph import embeddings as emb
    from cairn.graph.explore import explore
    from cairn.knowledge.search import _semantic_search

    _seed_symbol(fresh_db)
    monkeypatch.setattr(emb, "embeddings_available", lambda: False)

    explore(fresh_db, "safeApiCall", max_nodes=5)
    _semantic_search(fresh_db, None, "query", 5, 0.3)

    events = _buffered("semantic_unavailable")
    assert len(events) == 2
    assert {e["surface"] for e in events} == {"explore", "knowledge"}


# ---------------------------------------------------------------------------
# The helper itself: coercion, warning, gates
# ---------------------------------------------------------------------------


def test_note_semantic_unavailable_coerces_out_of_enum_inputs():
    """A wrong literal can't escape the bounded domain (defensive coercion)."""
    from cairn.telemetry import note_semantic_unavailable

    note_semantic_unavailable("src/cairn/explore.py", "totally broken")

    events = _buffered("semantic_unavailable")
    assert events == [{"surface": "explore", "reason": "error"}]


def test_note_semantic_unavailable_warns_once(caplog):
    from cairn.telemetry import note_semantic_unavailable

    caplog.set_level(logging.WARNING, logger="cairn.telemetry.events")
    note_semantic_unavailable("knowledge", "no_embeddings")
    note_semantic_unavailable("knowledge", "error")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "first degrade wins; per-surface once-guard"
    assert "lexical-only" in warnings[0].getMessage()
    assert "cairn embed" in warnings[0].getMessage()


def test_note_semantic_unavailable_silent_when_telemetry_off(monkeypatch, caplog):
    """The master switch silences BOTH the event and the warning (a quality
    signal, unlike note_contention's operational warning)."""
    monkeypatch.setenv("CAIRN_TELEMETRY", "off")
    caplog.set_level(logging.WARNING, logger="cairn.telemetry.events")

    from cairn.telemetry import note_semantic_unavailable

    note_semantic_unavailable("explore", "unavailable")

    assert _buffered("semantic_unavailable") == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == []


def test_note_semantic_unavailable_never_raises():
    """Best-effort contract: even a nonsense input returns cleanly."""
    from cairn.telemetry import note_semantic_unavailable

    note_semantic_unavailable("explore", "unavailable")
    note_semantic_unavailable("", "")  # coerced to the same surface -> once-guarded away

    assert _buffered("semantic_unavailable") == [
        {"surface": "explore", "reason": "unavailable"}
    ]


def test_no_attr_value_contains_a_path_separator():
    """Universal guard: emitted attr values never contain '/' or '\\'."""
    from cairn.telemetry import note_semantic_unavailable

    note_semantic_unavailable("explore", "unavailable")
    for attrs in _buffered("semantic_unavailable"):
        for value in attrs.values():
            assert "/" not in str(value) and "\\" not in str(value)
