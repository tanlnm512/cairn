"""Dashboard view-data assembly: projects, graph scopes, health, memories,
the task queue, and the tool-use history."""
from __future__ import annotations

import os
import re
import sqlite3

import pytest

from cairn.graph.schema import _apply_schema


def _seed(conn):
    """Three projects whose counts are known by construction.

    alpha: 2 files / 3 symbols / 2 edges, all 3 symbols embedded with
    'all-MiniLM-L6-v2'. beta: 1 file / 2 symbols / 1 edge, 1 of 2 embedded
    with 'hash-embed-v1'. gamma: 1 file / 1 symbol / 0 edges, file rows
    carry no indexed_at (exercises the repos.indexed_at fallback), no
    embeddings.
    """
    conn.executemany(
        "INSERT INTO repos (id, name, path, language, indexed_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("alpha", "alpha", "clients/alpha", "python", "2026-08-18T08:00:00"),
            ("beta", "beta", "clients/beta", "kotlin", "2026-08-19T09:00:00"),
            ("gamma", "gamma", "tools/gamma", "rust", "2026-08-20T07:30:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO files (id, repo_id, path, language, indexed_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("f_a1", "alpha", "src/alpha/core.py", "python", "2026-08-20T10:00:00"),
            ("f_a2", "alpha", "src/alpha/util.py", "python", "2026-08-20T11:00:00"),
            ("f_b1", "beta", "beta/lib/b1.kt", "kotlin", "2026-08-19T09:30:00"),
            ("f_g1", "gamma", "gamma/src/g1.rs", "rust", None),
        ],
    )
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?, ?)",
        [
            ("s_a1", "f_a1", "alpha_main", "alpha.core.alpha_main", "function"),
            ("s_a2", "f_a1", "alpha_helper", "alpha.core.alpha_helper", "function"),
            ("s_a3", "f_a2", "alpha_util", "alpha.util.alpha_util", "function"),
            ("s_b1", "f_b1", "beta_main", "beta.beta_main", "function"),
            ("s_b2", "f_b1", "beta_aux", "beta.beta_aux", "function"),
            ("s_g1", "f_g1", "gamma_main", "gamma.gamma_main", "function"),
        ],
    )
    conn.executemany(
        "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, ?)",
        [
            ("e1", "s_a1", "s_a2", "calls"),
            ("e2", "s_a2", "s_a3", "calls"),
            ("e3", "s_b1", "s_b2", "calls"),
        ],
    )
    conn.executemany(
        "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk, embedded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("s_a1", "all-MiniLM-L6-v2", 8, b"", "alpha_main", "2026-08-20T10:05:00"),
            ("s_a2", "all-MiniLM-L6-v2", 8, b"", "alpha_helper", "2026-08-20T10:05:00"),
            ("s_a3", "all-MiniLM-L6-v2", 8, b"", "alpha_util", "2026-08-20T10:06:00"),
            ("s_b1", "hash-embed-v1", 8, b"", "beta_main", "2026-08-19T09:35:00"),
        ],
    )
    conn.commit()


def test_list_projects_counts_and_freshness(fresh_db):
    from cairn.dashboard.data import list_projects

    _seed(fresh_db)
    projects = list_projects(fresh_db)

    assert [p["id"] for p in projects] == ["alpha", "beta", "gamma"]
    by_id = {p["id"]: p for p in projects}

    alpha = by_id["alpha"]
    assert (alpha["file_count"], alpha["symbol_count"], alpha["edge_count"]) == (2, 3, 2)
    # MAX over alpha's two file timestamps, not the earliest.
    assert alpha["last_indexed"] == "2026-08-20T11:00:00"
    assert alpha["path"] == "clients/alpha"  # workspace-relative, verbatim

    beta = by_id["beta"]
    assert (beta["file_count"], beta["symbol_count"], beta["edge_count"]) == (1, 2, 1)
    assert beta["last_indexed"] == "2026-08-19T09:30:00"

    gamma = by_id["gamma"]
    assert (gamma["file_count"], gamma["symbol_count"], gamma["edge_count"]) == (1, 1, 0)
    # No file timestamps recorded: falls back to repos.indexed_at.
    assert gamma["last_indexed"] == "2026-08-20T07:30:00"


def test_list_projects_embedding_status(fresh_db):
    from cairn.dashboard.data import list_projects

    _seed(fresh_db)
    by_id = {p["id"]: p for p in list_projects(fresh_db)}

    assert by_id["alpha"]["embedding_status"] == "embedded"
    assert by_id["alpha"]["embedding_models"] == ["all-MiniLM-L6-v2"]

    assert by_id["beta"]["embedding_status"] == "partial"
    assert by_id["beta"]["embedding_models"] == ["hash-embed-v1"]

    assert by_id["gamma"]["embedding_status"] == "not"
    assert by_id["gamma"]["embedding_models"] == []


def test_list_projects_empty_db_returns_empty_list(fresh_db):
    from cairn.dashboard.data import list_projects

    assert list_projects(fresh_db) == []


def test_get_graph_module_scope_is_the_default(fresh_db):
    from cairn.dashboard.data import get_graph

    _seed(fresh_db)
    graph = get_graph(fresh_db, focus="src/alpha")

    assert graph["metadata"]["scope"] == "module"
    assert graph["metadata"]["node_count"] == 3  # alpha's three symbols
    assert graph["metadata"]["edge_count"] == 2  # e1 + e2, both internal
    assert {n["id"] for n in graph["nodes"]} == {"alpha_main", "alpha_helper", "alpha_util"}
    assert {(e["source"], e["target"]) for e in graph["edges"]} == {
        ("alpha_main", "alpha_helper"),
        ("alpha_helper", "alpha_util"),
    }


def test_get_graph_repo_scope(fresh_db):
    from cairn.dashboard.data import get_graph

    _seed(fresh_db)
    graph = get_graph(fresh_db, scope="repo", repo="alpha")

    assert graph["metadata"]["scope"] == "repo"
    assert graph["metadata"]["repo"] == "alpha"
    assert graph["metadata"]["node_count"] == 2  # one bucket per top-level path
    assert graph["edges"] == []
    labels = {n["id"] for n in graph["nodes"]}
    assert labels == {"src/alpha/core.py (2)", "src/alpha/util.py (1)"}


def test_get_graph_symbol_scope(fresh_db):
    from cairn.dashboard.data import get_graph

    _seed(fresh_db)
    graph = get_graph(fresh_db, scope="symbol", focus="alpha_main", depth=1)

    assert graph["metadata"]["scope"] == "symbol"
    assert graph["metadata"]["node_count"] == 2  # focal + its one callee
    assert graph["metadata"]["edge_count"] == 1
    assert {n["id"] for n in graph["nodes"]} == {"alpha_main", "alpha_helper"}


def test_get_graph_rejects_unknown_scope(fresh_db):
    from cairn.dashboard.data import get_graph

    with pytest.raises(ValueError, match="unknown graph scope"):
        get_graph(fresh_db, scope="galaxy")


def test_projects_data_flows_through_the_read_only_factory(tmp_path):
    """The dashboard's own connection factory serves the view data, and that
    connection can never write (FR-010)."""
    from cairn.dashboard.data import get_read_only_db, list_projects

    db_path = str(tmp_path / "dash.db")
    seed = sqlite3.connect(db_path)
    seed.row_factory = sqlite3.Row
    _apply_schema(seed)
    _seed(seed)
    seed.close()

    conn = get_read_only_db(db_path)
    try:
        assert [p["id"] for p in list_projects(conn)] == ["alpha", "beta", "gamma"]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO repos (id, name, path) VALUES ('x', 'x', 'x')")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Health panel (FR-008 / US6)
# ---------------------------------------------------------------------------


def _file_db(tmp_path, name="health.db"):
    db_path = str(tmp_path / name)
    seed = sqlite3.connect(db_path)
    seed.row_factory = sqlite3.Row
    _apply_schema(seed)
    return db_path, seed


def test_get_health_reports_size_freshness_and_backend_keys(tmp_path):
    from cairn.dashboard.data import get_health, get_read_only_db

    db_path, seed = _file_db(tmp_path)
    seed.executemany(
        "INSERT INTO build_runs (kind, started_at) VALUES ('full', ?)",
        [("2026-08-18T08:00:00Z",), ("2026-08-20T07:00:00Z",)],
    )
    seed.commit()
    seed.close()

    conn = get_read_only_db(db_path)
    try:
        health = get_health(conn, db_path)
    finally:
        conn.close()

    assert health["db_size_bytes"] > 0
    assert health["db_size_bytes"] == os.stat(db_path).st_size

    # The newest build_run wins regardless of insertion order.
    assert health["last_build_at"] == "2026-08-20T07:00:00Z"
    assert health["last_build_age"] == "just now" or re.fullmatch(
        r"\d+[smhd] old", health["last_build_age"]
    )

    for key in (
        "db_size_bytes",
        "last_build_age",
        "embed_backend",
        "hash_fallback",
        "ann_backend_enabled",
        "reranker_available",
    ):
        assert key in health
    for key in ("hash_fallback", "ann_backend_enabled", "reranker_available"):
        assert isinstance(health[key], bool)


def test_get_health_in_memory_conn_degrades_to_zero_and_none(fresh_db):
    from cairn.dashboard.data import get_health

    health = get_health(fresh_db)

    assert health["db_size_bytes"] == 0  # no file behind the connection
    assert health["last_build_at"] is None
    assert health["last_build_age"] is None
    assert health["ann_embedding_rows"] == 0
    # No embeddings -> the vec0 probes are moot (a fresh store legitimately
    # has no index), reported as None rather than a missing index.
    assert health["ann_index_exists"] is None
    assert health["ann_index_rows"] is None


def test_get_health_agrees_with_doctor_on_the_same_db(tmp_path):
    """TC-018: the panel's conclusions must match `cairn doctor`'s.

    doctor's own check functions are run in-process against the same
    connection (instead of parsing CLI output), plus the graph-layer probes
    they call -- the panel must agree with both.
    """
    from cairn.cli.system import _check_ann, _check_embeddings
    from cairn.graph.ann_index import (
        ann_backend_enabled,
        index_exists,
        index_row_count,
    )
    from cairn.graph.embeddings import current_model, is_hash_fallback
    from cairn.graph.reranker import reranker_available
    from cairn.dashboard.data import get_health, get_read_only_db

    db_path, seed = _file_db(tmp_path, name="agree.db")
    seed.execute(
        "INSERT INTO build_runs (kind, started_at) VALUES ('full', '2026-08-20T07:00:00Z')"
    )
    # One embedding under the current model, no vec0 table built: exercises
    # the index_exists / index_row_count probes exactly like doctor's _check_ann.
    seed.execute(
        "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk, embedded_at) "
        "VALUES ('s1', ?, 8, x'', 'chunk', '2026-08-20T07:05:00')",
        (current_model(),),
    )
    seed.commit()
    seed.close()

    conn = get_read_only_db(db_path)
    try:
        health = get_health(conn, db_path)
        emb_row = _check_embeddings(conn)
        ann_row = _check_ann(conn)
        model = current_model()
        expected = {
            "hash_fallback": is_hash_fallback(),
            "ann_backend_enabled": ann_backend_enabled(),
            "ann_model": model,
            "ann_index_exists": index_exists(conn, model),
            "ann_index_rows": index_row_count(conn, model),
            "reranker_available": reranker_available(),
        }
    finally:
        conn.close()

    for key, value in expected.items():
        assert health[key] == value
    assert health["ann_embedding_rows"] == 1

    # doctor-level agreement: its checks WARN exactly where the panel sees
    # the corresponding degradation.
    assert (emb_row["status"] != "PASS") == health["hash_fallback"]
    assert ("sqlite-vec unavailable" in ann_row["detail"]) == (
        not health["ann_backend_enabled"]
    )
    if health["ann_backend_enabled"]:
        # Seeded DB has embeddings but no vec0 table: doctor's "no vec0
        # index" WARN fires exactly when the panel sees no index.
        assert ("no vec0 index" in ann_row["detail"]) == (
            health["ann_index_exists"] is False
        )
        assert health["ann_index_rows"] is None


# ---------------------------------------------------------------------------
# Memory + task-queue panels (FR-009 / US7)
# ---------------------------------------------------------------------------


def _seed_memories(knowledge_dir):
    from cairn.memory.store import create_memory, store_memory
    from cairn.okf.bundle import OKFBundle

    bundle = OKFBundle(str(knowledge_dir))
    for ts, mtype, title in [
        ("2026-08-18T10:00:00Z", "decision", "Use RRF fusion by default"),
        ("2026-08-19T11:00:00Z", "mistake", "Skipped the fuzzy retry"),
        ("2026-08-20T09:00:00Z", "pattern", "Seeded-DB test convention"),
    ]:
        concept = create_memory(type_=mtype, title=title, body=title)
        concept.timestamp = ts
        store_memory(concept, bundle, tier="tribal")


def test_get_recent_memories_newest_first_with_type_and_title(tmp_path):
    from cairn.dashboard.data import get_recent_memories

    kdir = tmp_path / "knowledge"
    _seed_memories(kdir)

    entries = get_recent_memories(str(kdir))

    assert [e["title"] for e in entries] == [
        "Seeded-DB test convention",
        "Skipped the fuzzy retry",
        "Use RRF fusion by default",
    ]
    assert [e["type"] for e in entries] == ["pattern", "mistake", "decision"]
    assert all(e["id"].startswith("memory/") for e in entries)
    assert all(e["tier"] == "tribal" for e in entries)


def test_get_recent_memories_limit_keeps_newest(tmp_path):
    from cairn.dashboard.data import get_recent_memories

    kdir = tmp_path / "knowledge"
    _seed_memories(kdir)

    entries = get_recent_memories(str(kdir), limit=2)

    assert [e["title"] for e in entries] == [
        "Seeded-DB test convention",
        "Skipped the fuzzy retry",
    ]


def test_get_recent_memories_missing_dir_returns_empty(tmp_path):
    from cairn.dashboard.data import get_recent_memories

    assert get_recent_memories(str(tmp_path / "nope")) == []


def _seed_tasks(knowledge_dir):
    from cairn.llm.tasks import claim_task, create_task
    from cairn.okf.bundle import OKFBundle

    bundle = OKFBundle(str(knowledge_dir))
    pending = create_task(bundle, "compass-synthesize", "src/cairn/viz")
    claimed = create_task(bundle, "wiki", "wiki/dashboard")
    assert claim_task(bundle, claimed.id) is not None
    done = create_task(bundle, "flow-synthesize", "trace_flow")
    concept = bundle.read_concept(done.concept_id)
    concept.status = "done"
    bundle.write_concept(concept)
    return pending, claimed, done


def test_get_task_queue_lists_and_filters_by_status(tmp_path):
    from cairn.dashboard.data import get_task_queue

    kdir = str(tmp_path / "knowledge")
    pending, claimed, done = _seed_tasks(tmp_path / "knowledge")

    entries = get_task_queue(kdir)
    by_status = {e["status"]: e for e in entries}
    assert set(by_status) == {"pending", "in-progress", "done"}
    assert by_status["pending"]["id"] == pending.id
    assert by_status["pending"]["kind"] == "compass-synthesize"
    assert by_status["pending"]["resource"] == "src/cairn/viz"
    assert by_status["in-progress"]["id"] == claimed.id
    assert by_status["in-progress"]["claimed_at"]
    assert by_status["done"]["id"] == done.id

    assert [e["id"] for e in get_task_queue(kdir, status="pending")] == [pending.id]
    assert [e["id"] for e in get_task_queue(kdir, status="in-progress")] == [
        claimed.id
    ]
    assert [e["id"] for e in get_task_queue(kdir, status="done")] == [done.id]
    assert get_task_queue(kdir, status="failed") == []


def test_get_task_queue_missing_dir_returns_empty(tmp_path):
    from cairn.dashboard.data import get_task_queue

    assert get_task_queue(str(tmp_path / "nope")) == []


# ---------------------------------------------------------------------------
# Tool-use history (FR-005 / US3)
# ---------------------------------------------------------------------------

# Known-by-construction calls across 2 tools / 2 sessions. Ids deliberately
# do not sort with time: the view's ORDER BY invoked_at DESC must yield
# [3, 1, 4, 2], which is neither id-ascending nor id-descending (id is the
# rowid, so rowid order cannot be what produces the result).
_METRIC_ROWS = [
    # (id, tool, session, invoked_at, duration_ms, status, req, resp)
    (2, "explore", "sess-a", 1755500000.0, 12.5, "ok", 400, 1600),  # oldest
    (4, "get_callers", "sess-b", 1755500060.5, 40.0, "ok", 80, 3200),
    (1, "explore", "sess-b", 1755500120.25, 55.5, "ok", 200, 800),
    (3, "get_callers", "sess-a", 1755500180.0, 7.0, "error", 80, 0),  # newest
]


def _seed_metrics(conn, rows=None):
    rows = rows if rows is not None else _METRIC_ROWS
    conn.executemany(
        "INSERT INTO tool_metrics (id, tool_name, session_id, invoked_at, "
        "duration_ms, status, error_message, req_chars, resp_chars, args_summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                rid,
                tool,
                sess,
                ts,
                dur,
                status,
                "boom" if status == "error" else None,
                req,
                resp,
                '{"query": "alpha"}',
            )
            for rid, tool, sess, ts, dur, status, req, resp in rows
        ],
    )
    conn.commit()
    return [row[0] for row in rows]


def test_list_history_newest_first_with_full_columns(fresh_db):
    from cairn.dashboard.data import list_history

    _seed_metrics(fresh_db)

    history = list_history(fresh_db)

    # invoked_at descending; not id order ([1, 2, 3, 4]) nor its reverse.
    assert [h["id"] for h in history] == [3, 1, 4, 2]
    newest = history[0]
    assert newest["tool_name"] == "get_callers"
    assert newest["session_id"] == "sess-a"
    assert newest["invoked_at"] == 1755500180.0  # epoch float, verbatim
    assert newest["duration_ms"] == 7.0
    assert newest["status"] == "error"
    assert newest["error_message"] == "boom"
    # Per-row estimated tokens (US4-AC2): chars // CHARS_PER_TOKEN.
    oldest = history[-1]
    assert (oldest["est_req_tokens"], oldest["est_resp_tokens"]) == (100, 400)
    assert all("args_summary" in h for h in history)


def test_list_history_filters_tool_session_combined_and_nonsense(fresh_db):
    from cairn.dashboard.data import list_history

    _seed_metrics(fresh_db)

    by_tool = list_history(fresh_db, tool_name="explore")
    assert [h["id"] for h in by_tool] == [1, 2]

    by_session = list_history(fresh_db, session_id="sess-b")
    assert [h["id"] for h in by_session] == [1, 4]

    combined = list_history(fresh_db, tool_name="explore", session_id="sess-b")
    assert [h["id"] for h in combined] == [1]

    # Nonsense filters are empty lists, never errors.
    assert list_history(fresh_db, tool_name="no_such_tool") == []
    assert list_history(fresh_db, session_id="no-such-session") == []
    assert list_history(fresh_db, tool_name="no_such_tool", session_id="x") == []


def test_list_history_pre_migration_null_sizes_stay_unknown(fresh_db):
    from cairn.dashboard.data import list_history

    # A row recorded before the payload-size migrations: no size columns set.
    fresh_db.execute(
        "INSERT INTO tool_metrics (id, tool_name, session_id, invoked_at, "
        "duration_ms, status) VALUES (1, 'explore', 'sess-a', 1755500000.0, 5.0, 'ok')"
    )
    fresh_db.commit()

    (row,) = list_history(fresh_db)

    assert row["req_chars"] is None
    assert row["resp_chars"] is None
    # Unknown, not zero-vs-value confusion: None, never 0.
    assert row["est_req_tokens"] is None
    assert row["est_resp_tokens"] is None


def test_list_history_fresh_db_returns_empty_list(fresh_db):
    from cairn.dashboard.data import list_history

    assert list_history(fresh_db) == []
    assert list_history(fresh_db, tool_name="explore") == []


def test_list_history_args_summary_truncated_never_expanded(fresh_db):
    from cairn.mcp_server.metric_buffering import MAX_ARGS_SUMMARY_CHARS

    from cairn.dashboard.data import list_history

    tail_marker = "DISTINCTIVE_TAIL_" + "x" * 300
    payload = '{"query": "' + "y" * 300 + tail_marker + '"}'
    # Stored exactly as the write chokepoint (T004) leaves it: redacted and
    # sliced to MAX_ARGS_SUMMARY_CHARS, with req_chars the FULL payload size.
    _seed_metrics(
        fresh_db,
        rows=[(1, "explore", "sess-a", 1755500000.0, 5.0, "ok", len(payload), 0)],
    )
    fresh_db.execute(
        "UPDATE tool_metrics SET args_summary = ? WHERE id = 1",
        (payload[:MAX_ARGS_SUMMARY_CHARS],),
    )
    fresh_db.commit()

    (row,) = list_history(fresh_db)

    assert row["args_summary"] == payload[:MAX_ARGS_SUMMARY_CHARS]
    assert len(row["args_summary"]) <= 200
    assert "DISTINCTIVE_TAIL_" not in row["args_summary"]  # TC-024
    # The full-payload size still drives the token estimate.
    assert row["est_req_tokens"] == len(payload) // 4


# ---------------------------------------------------------------------------
# Token aggregates (FR-006 / US4) + call chains (FR-007 / US5)
# ---------------------------------------------------------------------------


def test_session_gap_s_constant():
    from cairn.dashboard import data

    assert data.SESSION_GAP_S == 1800


def test_get_tool_tokens_aggregates_ranked_by_total_desc(fresh_db):
    from cairn.dashboard.data import get_tool_tokens

    _seed_metrics(fresh_db)

    tokens = get_tool_tokens(fresh_db)

    # get_callers: (80+3200) + (80+0) chars -> 40 + 800 = 840 tokens;
    # explore: (400+1600) + (200+800) chars -> 150 + 600 = 750 tokens.
    assert [t["tool_name"] for t in tokens] == ["get_callers", "explore"]

    by_tool = {t["tool_name"]: t for t in tokens}
    assert by_tool["get_callers"]["calls"] == 2
    assert by_tool["get_callers"]["total_tokens"] == 840
    assert by_tool["explore"]["calls"] == 2
    assert by_tool["explore"]["total_tokens"] == 750
    # Every row internally consistent: req + resp == total, mean * calls
    # == total within rounding (TC-014).
    for t in tokens:
        assert t["est_req_tokens"] + t["est_resp_tokens"] == t["total_tokens"]
        assert t["mean_tokens"] * t["calls"] == pytest.approx(t["total_tokens"])


def test_get_tool_tokens_seeded_400_800_is_100_plus_200(fresh_db):
    from cairn.dashboard.data import get_tool_tokens

    _seed_metrics(
        fresh_db,
        rows=[(1, "explore", "sess-a", 1755500000.0, 5.0, "ok", 400, 800)],
    )

    (row,) = get_tool_tokens(fresh_db)

    assert (row["est_req_tokens"], row["est_resp_tokens"]) == (100, 200)
    assert row["total_tokens"] == 300
    assert row["calls"] == 1
    assert row["mean_tokens"] == 300.0


def test_get_tool_tokens_null_sizes_count_as_calls_zero_tokens(fresh_db):
    from cairn.dashboard.data import get_tool_tokens

    # One pre-migration row (no size columns) plus one sized row.
    fresh_db.execute(
        "INSERT INTO tool_metrics (id, tool_name, session_id, invoked_at, "
        "duration_ms, status) VALUES (1, 'explore', 'sess-a', 1755500000.0, 5.0, 'ok')"
    )
    _seed_metrics(
        fresh_db,
        rows=[(2, "explore", "sess-a", 1755500060.0, 5.0, "ok", 400, 800)],
    )

    (row,) = get_tool_tokens(fresh_db)

    assert row["calls"] == 2  # the NULL-size row still counts as a call
    assert row["total_tokens"] == 300  # only the sized row's 100 + 200
    assert row["mean_tokens"] == 150.0


def test_get_tool_tokens_empty_db_returns_empty_list(fresh_db):
    from cairn.dashboard.data import get_tool_tokens

    assert get_tool_tokens(fresh_db) == []


def test_get_session_chains_gap_splits_bursts_keeps_order(fresh_db):
    from cairn.dashboard.data import get_session_chains

    base = 1755500000.0
    later = base + 6 * 3600  # six hours on: far beyond SESSION_GAP_S
    _seed_metrics(
        fresh_db,
        rows=[
            # sess-a burst 1: three calls a minute apart (TC-017).
            (1, "explore", "sess-a", base, 5.0, "ok", 10, 10),
            (2, "get_callers", "sess-a", base + 60, 5.0, "ok", 10, 10),
            (3, "ask_compass", "sess-a", base + 120, 5.0, "ok", 10, 10),
            # sess-a burst 2: two more calls six hours later, same session.
            (4, "explore", "sess-a", later, 5.0, "ok", 10, 10),
            (5, "impact_analysis", "sess-a", later + 60, 5.0, "ok", 10, 10),
            # sess-b: a single call is still a chain (TC-016).
            (6, "explore", "sess-b", base + 30, 5.0, "ok", 10, 10),
        ],
    )

    chains = get_session_chains(fresh_db)

    # Sessions newest-activity-first (sess-a ends at later+60, sess-b at
    # base+30); chains within a session chronological.
    assert [(c["session_id"], c["call_count"]) for c in chains] == [
        ("sess-a", 3),
        ("sess-a", 2),
        ("sess-b", 1),
    ]

    burst1, burst2, single = chains
    assert [c["id"] for c in burst1["calls"]] == [1, 2, 3]  # chronological
    assert [c["tool_name"] for c in burst1["calls"]] == [
        "explore",
        "get_callers",
        "ask_compass",
    ]
    assert [c["id"] for c in burst2["calls"]] == [4, 5]
    assert (burst1["started_at"], burst1["ended_at"]) == (base, base + 120)
    assert (burst2["started_at"], burst2["ended_at"]) == (later, later + 60)
    call = burst2["calls"][0]
    assert (call["invoked_at"], call["duration_ms"], call["status"]) == (
        later,
        5.0,
        "ok",
    )
    assert single["calls"][0]["id"] == 6


def test_get_session_chains_splits_only_beyond_the_gap(fresh_db):
    from cairn.dashboard.data import SESSION_GAP_S, get_session_chains

    base = 1755500000.0
    _seed_metrics(
        fresh_db,
        rows=[
            (1, "explore", "sess-a", base, 5.0, "ok", 10, 10),
            # Exactly SESSION_GAP_S apart: still the same chain.
            (2, "explore", "sess-a", base + SESSION_GAP_S, 5.0, "ok", 10, 10),
            # One second past the gap: a new chain starts.
            (3, "explore", "sess-a", base + 2 * SESSION_GAP_S + 1, 5.0, "ok", 10, 10),
        ],
    )

    chains = get_session_chains(fresh_db)

    assert [c["call_count"] for c in chains] == [2, 1]


def test_get_session_chains_equal_timestamps_stay_one_chain(fresh_db):
    from cairn.dashboard.data import get_session_chains

    # invoked_at is NOT NULL in the schema, so NULL rows cannot occur; the
    # nearest legal edge is several calls at the same instant (zero gap).
    _seed_metrics(
        fresh_db,
        rows=[
            (1, "explore", "sess-a", 1755500000.0, 5.0, "ok", 10, 10),
            (2, "get_callers", "sess-a", 1755500000.0, 5.0, "ok", 10, 10),
            (3, "ask_compass", "sess-a", 1755500000.0, 5.0, "ok", 10, 10),
        ],
    )

    chains = get_session_chains(fresh_db)

    (chain,) = chains
    assert chain["call_count"] == 3
    assert [c["id"] for c in chain["calls"]] == [1, 2, 3]
    assert (chain["started_at"], chain["ended_at"]) == (1755500000.0, 1755500000.0)


def test_get_session_chains_empty_db_returns_empty_list(fresh_db):
    from cairn.dashboard.data import get_session_chains

    assert get_session_chains(fresh_db) == []
