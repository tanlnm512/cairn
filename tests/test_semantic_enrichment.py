"""Phase 4: optional graph enrichment (include_callers) on semantic_search.

Verifies the opt-in 1-hop caller/callee attachment, and that it's a true
no-op (no "callers"/"callees" keys at all) when not requested -- existing
callers of semantic_search must see zero shape change by default.
"""
from __future__ import annotations

import sqlite3

import pytest

# Apply the shared hash-backend fixture to every test in this module
# (the local autouse copy used to live here; see tests/conftest.py).
pytestmark = pytest.mark.usefixtures("hash_backend")


def _seed_call_graph(conn: sqlite3.Connection) -> None:
    """UserRepo.run() calls Profile.displayName() -- a real 1-hop edge to enrich with."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/Profile.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (2, 'test', '/tmp/test/UserRepo.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES (1, 1, 'displayName', 'method', 'Profile.displayName', 'Returns display name.', 1, 3)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (2, 2, 'run', 'method', 'UserRepo.run', 1, 5)"
    )
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line) "
        "VALUES ('e1', '2', '1', 'displayName', 'call', 3)"
    )
    conn.commit()


def _conn_with_call_graph(fresh_db) -> sqlite3.Connection:
    _seed_call_graph(fresh_db)
    return fresh_db


def test_include_callers_false_by_default_has_no_extra_keys(fresh_db):
    from cairn.graph import embeddings as emb
    from cairn.graph.queries import semantic_search

    conn = _conn_with_call_graph(fresh_db)
    emb.embed_all(conn)

    results = semantic_search(conn, "displayName", limit=5, threshold=-1.0)
    assert results
    assert "callers" not in results[0]
    assert "callees" not in results[0]


def test_include_callers_true_attaches_1hop_neighbors(fresh_db):
    from cairn.graph import embeddings as emb
    from cairn.graph.queries import semantic_search

    conn = _conn_with_call_graph(fresh_db)
    emb.embed_all(conn)

    results = semantic_search(
        conn, "displayName", limit=5, threshold=-1.0, include_callers=True
    )
    assert results
    hit = next(r for r in results if r["name"] == "displayName")
    assert "callers" in hit and "callees" in hit
    caller_names = [c["name"] for c in hit["callers"]]
    assert "run" in caller_names, f"expected UserRepo.run as a caller, got {caller_names}"


def test_include_callers_degrades_to_empty_lists_for_symbol_with_no_edges(fresh_db):
    from cairn.graph import embeddings as emb
    from cairn.graph.queries import semantic_search

    conn = _conn_with_call_graph(fresh_db)
    emb.embed_all(conn)

    results = semantic_search(
        conn, "run", limit=5, threshold=-1.0, include_callers=True
    )
    hit = next(r for r in results if r["name"] == "run")
    assert hit["callers"] == []  # nothing calls UserRepo.run in this fixture
