"""Standing guard: a full pass over every dashboard route leaves the
database file untouched (FR-010 / TC-021), reads stay clean while a
concurrent writer appends tool_metrics rows (TC-025), and the candidates
and neighbors JSON endpoints sit inside the same guard (FR-006 / TC-006)."""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from cairn.graph.schema import _apply_schema

# Every route on the app, including the query variants that hit distinct
# read paths: graph scope changes (repo / symbol+depth / unknown fallback),
# the graph JSON endpoints (candidates: exact / absent-from-store / absent
# / whitespace name; neighbors: repeatable names + depth, whitespace-only,
# absent, unknown name + bogus depth), history tool+session filters
# (separate and combined), tasks status filter, and the static assets.
ROUTES = [
    "/",
    "/projects",
    "/graph",
    "/graph?scope=module&focus=src/alpha",
    "/graph?scope=repo&repo=alpha",
    "/graph?scope=symbol&focus=alpha_main&depth=1",
    "/graph?scope=bogus",
    "/graph/candidates?name=alpha_main",
    "/graph/candidates?name=no_such_symbol",
    "/graph/candidates",
    "/graph/candidates?name=%20%20",
    "/graph/neighbors?name=alpha_main&name=beta_main&depth=2",
    "/graph/neighbors?name=%20%20",
    "/graph/neighbors",
    "/graph/neighbors?name=no_such_symbol&depth=bogus",
    "/health",
    "/memory",
    "/tasks",
    "/tasks?status=pending",
    "/tasks?status=done",
    "/history",
    "/history?tool=explore",
    "/history?session=sess-a",
    "/history?tool=explore&session=sess-b",
    "/tokens",
    "/chains",
    "/static/app.css",
    "/static/app.js",
]

_METRIC_ROWS = [
    # (id, tool, session, invoked_at, duration_ms, status, req, resp)
    (2, "explore", "sess-a", 1755500000.0, 12.5, "ok", 400, 1600),
    (4, "get_callers", "sess-b", 1755500060.5, 40.0, "ok", 80, 3200),
    (1, "explore", "sess-b", 1755500120.25, 55.5, "ok", 200, 800),
    (3, "get_callers", "sess-a", 1755500180.0, 7.0, "error", 80, 0),
]


def _seed_populated_db(tmp_path, name="guard.db") -> str:
    """A rollback-journal (non-WAL) schema'd DB file populated for every
    panel: repos/files/symbols/edges/embeddings (the alpha/beta/gamma world),
    two build_runs, and four tool_metrics rows."""
    db_path = str(tmp_path / name)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
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
    conn.executemany(
        "INSERT INTO build_runs (kind, started_at) VALUES ('full', ?)",
        [("2026-08-18T08:00:00Z",), ("2026-08-20T07:00:00Z",)],
    )
    conn.executemany(
        "INSERT INTO tool_metrics (id, tool_name, session_id, invoked_at, "
        "duration_ms, status, error_message, req_chars, resp_chars) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            )
            for rid, tool, sess, ts, dur, status, req, resp in _METRIC_ROWS
        ],
    )
    conn.commit()
    conn.close()
    return db_path


def _seed_knowledge(tmp_path) -> str:
    """Memories across types plus tasks in all three queue states."""
    from cairn.llm.tasks import claim_task, create_task
    from cairn.memory.store import create_memory, store_memory
    from cairn.okf.bundle import OKFBundle

    kdir = tmp_path / "knowledge"
    bundle = OKFBundle(str(kdir))
    for ts, mtype, title in [
        ("2026-08-18T10:00:00Z", "decision", "Use RRF fusion by default"),
        ("2026-08-19T11:00:00Z", "mistake", "Skipped the fuzzy retry"),
        ("2026-08-20T09:00:00Z", "pattern", "Seeded-DB test convention"),
    ]:
        concept = create_memory(type_=mtype, title=title, body=title)
        concept.timestamp = ts
        store_memory(concept, bundle, tier="tribal")
    claimed = create_task(bundle, "wiki", "wiki/dashboard")
    assert claim_task(bundle, claimed.id) is not None
    done = create_task(bundle, "flow-synthesize", "trace_flow")
    concept = bundle.read_concept(done.concept_id)
    concept.status = "done"
    bundle.write_concept(concept)
    create_task(bundle, "compass-synthesize", "src/cairn/viz")
    return str(kdir)


def _guard_world(tmp_path, name="guard.db"):
    """(client, db_path): a TestClient over a freshly seeded populated DB."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app

    return (
        TestClient(
            create_app(
                db_path=_seed_populated_db(tmp_path, name=name),
                knowledge_dir=_seed_knowledge(tmp_path),
            )
        ),
        str(tmp_path / name),
    )


def _fetch(client, path):
    """One guarded fetch: a sqlite error must fail naming the route, not
    surface as an opaque 500."""
    try:
        resp = client.get(path)
    except sqlite3.OperationalError as exc:
        pytest.fail(f"{path}: {exc}")
    assert resp.status_code == 200, path
    return resp


def _digest(db_path: str) -> str:
    return hashlib.sha256(Path(db_path).read_bytes()).hexdigest()


def _metric_rows(db_path: str) -> int:
    from cairn.dashboard.data import get_read_only_db

    conn = get_read_only_db(db_path)
    try:
        return conn.execute("SELECT count(*) FROM tool_metrics").fetchone()[0]
    finally:
        conn.close()


def test_full_route_pass_leaves_db_byte_identical(tmp_path):
    """TC-021: after exercising every view with its query variants and the
    static assets, the DB file's checksum, the tool_metrics row count, and
    the sidecar set (no -wal/-shm may appear) are all unchanged."""
    client, db_path = _guard_world(tmp_path)

    before_digest = _digest(db_path)
    before_rows = _metric_rows(db_path)
    assert before_rows == 4

    for path in ROUTES:
        _fetch(client, path)

    assert _digest(db_path) == before_digest
    assert _metric_rows(db_path) == before_rows
    sidecars = sorted(
        p.name
        for p in Path(db_path).parent.iterdir()
        if p.name.endswith(("-wal", "-shm"))
    )
    assert sidecars == []


def test_graph_json_endpoints_stay_read_only(tmp_path):
    """TC-006 / FR-006: the candidates and neighbors JSON endpoints --
    happy paths and every edge path (repeatable name params, whitespace
    name, absent name, name absent from the store, bogus depth) -- leave
    the DB byte-identical with no sidecars, and both return real content
    on the seeded store so the guard is non-vacuous."""
    client, db_path = _guard_world(tmp_path, name="graph-json.db")
    before_digest = _digest(db_path)

    # Candidates: the seeded alpha_main is unique, so an exact hit returns
    # its one disambiguating match.
    found = _fetch(client, "/graph/candidates?name=alpha_main").json()
    assert found == {
        "matches": [
            {
                "name": "alpha_main",
                "kind": "function",
                "file": "src/alpha/core.py",
                "repo_id": "alpha",
            }
        ],
        "truncated": False,
    }
    # A whitespace-padded name resolves to the same hit after stripping.
    assert _fetch(client, "/graph/candidates?name=%20alpha_main%20").json() == found
    # Edge paths: a name absent from the store, the param absent, and a
    # whitespace-only name all return the empty contract, never an error.
    for path in (
        "/graph/candidates?name=no_such_symbol",
        "/graph/candidates",
        "/graph/candidates?name=%20%20",
    ):
        assert _fetch(client, path).json() == {"matches": [], "truncated": False}

    # Neighbors: repeatable name params (with a depth) merge both focal
    # neighborhoods -- alpha_main's callee and beta_main's callee.
    neighbors = _fetch(
        client, "/graph/neighbors?name=alpha_main&name=beta_main&depth=2"
    ).json()
    node_names = {node["id"] for node in neighbors["nodes"]}
    assert {"alpha_main", "alpha_helper", "beta_main", "beta_aux"} <= node_names
    alpha_edge = {"source": "alpha_main", "target": "alpha_helper", "kind": "calls"}
    assert alpha_edge in neighbors["edges"]
    assert neighbors["metadata"]["requested"] == ["alpha_main", "beta_main"]
    # Edge paths: whitespace-only and absent names hit the empty contract
    # (200, empty graph); an unknown name resolves no nodes while a bogus
    # depth silently falls back to the default.
    for path in ("/graph/neighbors?name=%20%20", "/graph/neighbors"):
        empty = _fetch(client, path).json()
        assert empty["nodes"] == []
        assert empty["metadata"]["requested"] == []
    unknown = _fetch(client, "/graph/neighbors?name=no_such_symbol&depth=bogus").json()
    assert unknown["nodes"] == []
    assert unknown["metadata"]["requested"] == ["no_such_symbol"]

    # The guard itself: byte-identical DB, no -wal/-shm sidecars.
    assert _digest(db_path) == before_digest
    sidecars = sorted(
        p.name
        for p in Path(db_path).parent.iterdir()
        if p.name.endswith(("-wal", "-shm"))
    )
    assert sidecars == []


def test_reads_stay_clean_while_a_writer_appends_tool_metrics(tmp_path):
    """TC-025: while a concurrent writer commits tool_metrics rows (the
    server's FR-004 role, never the dashboard's), every route fetch stays
    HTTP 200 with no lock error, and the new records appear on refresh."""
    client, db_path = _guard_world(tmp_path, name="concurrent.db")
    before_rows = _metric_rows(db_path)

    writer_errors: list[str] = []
    written = 25

    def _write():
        # Short busy timeout: the writer waits out readers, never hangs.
        writer = sqlite3.connect(db_path, timeout=2.0)
        try:
            for i in range(written):
                writer.execute(
                    "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
                    "duration_ms, status, req_chars, resp_chars) "
                    "VALUES ('writer_tool', 'sess-live', ?, 5.0, 'ok', 40, 80)",
                    (1755501000.0 + i,),
                )
                writer.commit()
                time.sleep(0.03)
        except sqlite3.OperationalError as exc:
            writer_errors.append(str(exc))
        finally:
            writer.close()

    thread = threading.Thread(target=_write)
    thread.start()
    try:
        for _ in range(3):
            for path in ROUTES:
                _fetch(client, path)
    finally:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert writer_errors == []

    assert _metric_rows(db_path) == before_rows + written
    refreshed = _fetch(client, "/history?tool=writer_tool")
    assert "sess-live" in refreshed.text
