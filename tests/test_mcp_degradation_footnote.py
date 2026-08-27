"""Tests for the FR-013 MCP degradation footnote (T016).

Query tools whose quality depends on the dense leg (semantic_search, explore,
ask_compass, recall_memory) append ``embed_ladder.degradation_footnote()`` as
one trailing line naming the rung, reason, and remediation; healthy state (or
an inactive cached verdict) renders byte-identical output, and write-only
tools (record_memory) never carry the footnote.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from cairn.graph import embed_ladder
from cairn.graph.schema import _apply_schema

FOOTNOTE = "degraded: rung 3 (server_down): check the embedding server"


def _activate(detail: str = "check the embedding server") -> None:
    """Cache an active rung-3 verdict (the documented test seam)."""
    embed_ladder._LADDER_CACHE["state"] = embed_ladder.LadderState(
        rung=3, reason="server_down", detail=detail, adopted_model=None, active=True,
    )


@pytest.fixture(autouse=True)
def _fresh_ladder():
    """Each test starts from a never-evaluated (healthy) ladder cache."""
    embed_ladder.reset_cache()
    yield
    embed_ladder.reset_cache()


@pytest.fixture
def graph_env(tmp_path, monkeypatch):
    """A schema'd file DB + knowledge dir wired through CAIRN_DB/CAIRN_KNOWLEDGE."""
    db_path = tmp_path / "graph.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '.')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'r1', 'src/auth.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (1, 1, 'login', 'function', 'auth.login', 1, 10)"
    )
    conn.commit()
    conn.close()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setenv("CAIRN_DB", str(db_path))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(knowledge))
    return knowledge


def _sem_row(name: str, score: float, symbol_id: int = 1) -> dict:
    """One fake dense-search row shaped like queries.semantic_search output."""
    return {
        "id": symbol_id,
        "kind": "function",
        "name": name,
        "qualified_name": f"auth.{name}",
        "file_path": f"src/{name}.py",
        "repo": "r1",
        "score": score,
        "provenance": "semantic",
        "reranked": False,
        "rerank_score": None,
        "chunk": f"def {name}()",
        "callers": [],
        "callees": [],
    }


# ---------------------------------------------------------------------------
# The shared MCP-layer helper
# ---------------------------------------------------------------------------


def test_footnote_helper_empty_when_healthy():
    from cairn.mcp_server import _server_core

    assert _server_core._embed_degradation_footnote() == ""
    assert _server_core._append_embed_degradation_footnote("body") == "body"


def test_footnote_helper_empty_when_state_inactive():
    from cairn.mcp_server import _server_core

    embed_ladder._LADDER_CACHE["state"] = embed_ladder.LadderState(
        rung=3, reason="server_down", detail="d", adopted_model=None, active=False,
    )
    assert _server_core._embed_degradation_footnote() == ""
    assert _server_core._append_embed_degradation_footnote("body") == "body"


def test_footnote_names_rung_reason_remediation():
    from cairn.mcp_server import _server_core

    _activate()
    assert _server_core._embed_degradation_footnote() == FOOTNOTE


# ---------------------------------------------------------------------------
# semantic_search (prose path)
# ---------------------------------------------------------------------------


def _stub_dense(monkeypatch, rows):
    from cairn.graph import embeddings as emb
    from cairn.graph import semantic as semantic_mod

    monkeypatch.setattr(emb, "embeddings_available", lambda: True)
    monkeypatch.setattr(emb, "embed_count", lambda conn: 3)
    # Patch the SOURCE module: setattr on queries would permanently plant the
    # attribute in its __dict__ (its __getattr__ re-resolves per access) and
    # defeat the lazy resolution other suites' spies rely on.
    monkeypatch.setattr(semantic_mod, "semantic_search", lambda conn, q, **kw: rows)


def test_semantic_search_carries_footnote_once(graph_env, monkeypatch):
    from cairn.mcp_server import tools_graph

    _stub_dense(monkeypatch, [_sem_row("retry_with_backoff", 0.9), _sem_row("backoff_helper", 0.8)])
    _activate()
    out = tools_graph.semantic_search("retry backoff")
    assert out.count("degraded: rung 3 (server_down)") == 1
    assert out.splitlines()[-1] == FOOTNOTE


def test_semantic_search_healthy_output_is_footnote_free(graph_env, monkeypatch):
    from cairn.mcp_server import tools_graph

    _stub_dense(monkeypatch, [_sem_row("retry_with_backoff", 0.9), _sem_row("backoff_helper", 0.8)])
    healthy = tools_graph.semantic_search("retry backoff")
    assert "degraded: rung" not in healthy

    # The degraded render is the healthy render plus exactly the footnote line.
    _activate()
    degraded = tools_graph.semantic_search("retry backoff")
    assert degraded == f"{healthy}\n{FOOTNOTE}"


# ---------------------------------------------------------------------------
# explore (fusion leg)
# ---------------------------------------------------------------------------


def _stub_explore_fusion(monkeypatch, rows):
    from cairn.graph import embeddings as emb
    import cairn.graph.semantic as semantic_mod

    monkeypatch.setattr(emb, "embeddings_available", lambda: True)
    monkeypatch.setattr(emb, "embed_count", lambda conn: 3)
    monkeypatch.setattr(semantic_mod, "semantic_search", lambda conn, q, **kw: rows)


def test_explore_carries_footnote_once(graph_env, monkeypatch):
    from cairn.mcp_server import tools_graph

    _stub_explore_fusion(monkeypatch, [_sem_row("login", 0.9)])
    _activate()
    out = tools_graph.explore("how does login work")
    assert out.count("degraded: rung 3 (server_down)") == 1
    assert out.splitlines()[-1] == FOOTNOTE


def test_explore_healthy_output_is_footnote_free(graph_env, monkeypatch):
    from cairn.mcp_server import tools_graph

    _stub_explore_fusion(monkeypatch, [_sem_row("login", 0.9)])
    out = tools_graph.explore("how does login work")
    assert "degraded: rung" not in out
    assert out.splitlines()[-1] != FOOTNOTE


# ---------------------------------------------------------------------------
# ask_compass (router; memory layer rides the dense leg)
# ---------------------------------------------------------------------------


def test_ask_compass_carries_footnote_once(graph_env):
    from cairn.mcp_server import tools_compass

    _activate()
    out = tools_compass.ask_compass("why did we choose backoff")
    assert out.count("degraded: rung 3 (server_down)") == 1
    assert out.splitlines()[-1] == FOOTNOTE


def test_ask_compass_healthy_output_is_footnote_free(graph_env):
    from cairn.mcp_server import tools_compass

    out = tools_compass.ask_compass("why did we choose backoff")
    assert "degraded: rung" not in out


# ---------------------------------------------------------------------------
# recall_memory (semantic memory scan rides the dense leg)
# ---------------------------------------------------------------------------


def _stub_search_memory(monkeypatch, mems):
    import cairn.memory.promotion as promotion

    monkeypatch.setattr(promotion, "search_memory", lambda conn, bundle, q, **kw: mems)


def _memory(title: str) -> SimpleNamespace:
    return SimpleNamespace(
        title=title,
        description="past decision",
        body="body without refs",
        extensions={"memory_score": 0.9, "memory_tier": "tribal", "provenance": ""},
    )


def test_recall_memory_carries_footnote_once(graph_env, monkeypatch):
    from cairn.mcp_server import tools_memory

    _stub_search_memory(monkeypatch, [_memory("Backoff decision"), _memory("Retry policy")])
    _activate()
    out = tools_memory.recall_memory("backoff")
    assert out.count("degraded: rung 3 (server_down)") == 1
    assert out.splitlines()[-1] == FOOTNOTE


def test_recall_memory_healthy_output_is_footnote_free(graph_env, monkeypatch):
    from cairn.mcp_server import tools_memory

    _stub_search_memory(monkeypatch, [_memory("Backoff decision")])
    out = tools_memory.recall_memory("backoff")
    assert "degraded: rung" not in out


# ---------------------------------------------------------------------------
# Write-only tools never carry the footnote
# ---------------------------------------------------------------------------


def test_record_memory_never_carries_footnote(graph_env, monkeypatch):
    from cairn.mcp_server import embed_buffering
    from cairn.mcp_server import tools_memory

    monkeypatch.setattr(embed_buffering, "enqueue", lambda cid: None)
    _activate()
    out = tools_memory.record_memory(
        "decision", "Use exponential backoff",
        "Why: the embedding server fails under load.\n"
        "How to apply: retry with backoff before degrading.",
    )
    assert "degraded: rung" not in out
    assert out.startswith("Recorded decision")
