"""Standing guard: a full pass over every dashboard route leaves the
database file untouched (FR-010 / TC-021), reads stay clean while a
concurrent writer appends tool_metrics rows (TC-025), the candidates
and neighbors JSON endpoints sit inside the same guard (FR-006 / TC-006),
and the workspace launcher's overview browsing + store switching leave
every store under CAIRN_HOME byte-identical, sidecars included
(workspace-launcher FR-004 / TC-005)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from cairn.graph.schema import _apply_schema, get_db

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


# ---------------------------------------------------------------------------
# Workspace launcher guard (workspace-launcher FR-004 / TC-005): the
# overview stats every store under CAIRN_HOME and mode=ro-opens each
# populated one, and ?store=<key> serves the data views against that
# store -- none of it may write anything anywhere, visited or merely
# listed. The fixture seeds five states side by side and the guard
# tree-hashes ALL of CAIRN_HOME (the registry included) before/after the
# full interaction set, so a -wal/-shm sidecar, a registry rewrite, or a
# created directory anywhere fails the test. The handler resolves
# paths.CAIRN_HOME per request (attribute lookup), so patching the module
# attribute is the seam -- the app suite's workspaces-fixture convention.
# ---------------------------------------------------------------------------

# 16-hex store keys (the paths.store_key layout): one per fixture state,
# each independently addressable via ?store=<key>.
_WS_KEY_A = "aa00000000000001"  # populated, visited by the sweep
_WS_KEY_B = "bb00000000000002"  # populated, visited by the sweep
_WS_KEY_EMPTY = "cc00000000000003"  # key dir with no .kg: empty state
_WS_KEY_CORRUPT = "dd00000000000004"  # junk-byte .kg: probes unreadable
_WS_KEY_MISSING = "ee00000000000005"  # registered, dir gone: missing state
_WS_KEY_UNKNOWN = "ff00000000000006"  # names nothing on disk or registry


def _seed_ws_store(
    home: Path, key: str, repo_id: str, tool_name: str, build_at: str
) -> Path:
    """A real schema store at ``<home>/<key>/.kg`` (the launcher layout),
    get_db-seeded then left in rollback-journal mode -- the suite's own
    guard-store convention. WAL stores would muddy the sidecar guard:
    SQLite's FIRST mode=ro visit to a WAL store materializes a 0-byte
    ``.kg-wal`` plus a zeroed ``.kg-shm`` (the wal-index) and never
    removes them -- any reader does this, it is not a dashboard write.
    Rollback-journal stores keep the guard strict: ANY file that appears
    next to a store is the dashboard's doing. Rows distinguish the store
    from every other in the fixture: one repo, a file with two symbols
    plus the edge between them (so /graph has a neighborhood),
    tool_metrics rows of ``tool_name`` in one session, and a build_run
    stamped ``build_at``."""
    kg = home / key / ".kg"
    kg.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(str(kg))
    try:
        conn.execute(
            "INSERT INTO repos (id, name, path, language, indexed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (repo_id, repo_id, f"clients/{repo_id}", "python",
             "2026-08-20T08:00:00"),
        )
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language, indexed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"{repo_id}-f1", repo_id, f"src/{repo_id}/core.py", "python",
             "2026-08-20T10:00:00"),
        )
        conn.executemany(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (f"{repo_id}-s1", f"{repo_id}-f1", f"{repo_id}_main",
                 f"{repo_id}.core.{repo_id}_main", "function"),
                (f"{repo_id}-s2", f"{repo_id}-f1", f"{repo_id}_helper",
                 f"{repo_id}.core.{repo_id}_helper", "function"),
            ],
        )
        conn.execute(
            "INSERT INTO edges (id, source_id, target_id, kind) "
            "VALUES (?, ?, ?, ?)",
            (f"{repo_id}-e1", f"{repo_id}-s1", f"{repo_id}-s2", "calls"),
        )
        conn.executemany(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status) VALUES (?, ?, ?, ?, ?)",
            [
                (tool_name, f"sess-{repo_id}", 1755648000.0 + i, 50.0, "ok")
                for i in range(2)
            ],
        )
        conn.execute(
            "INSERT INTO build_runs (kind, started_at) VALUES ('full', ?)",
            (build_at,),
        )
        conn.commit()
        mode = conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
        assert mode == "delete", mode
    finally:
        conn.close()
    return kg


def _launcher_home(tmp_path) -> Path:
    """The multi-store CAIRN_HOME the TC-005 guard runs against: two
    populated stores with distinct data (the visited ones), an empty key
    dir, a corrupt-.kg store, and a registered key whose dir is gone (the
    merely-listed ones), plus the registry itself."""
    home = tmp_path / "cairn-home"
    home.mkdir()
    _seed_ws_store(home, _WS_KEY_A, "guard_ws_a", "ws_a_tool",
                   "2026-08-20T07:00:00Z")
    _seed_ws_store(home, _WS_KEY_B, "guard_ws_b", "ws_b_tool",
                   "2026-08-20T09:00:00Z")
    (home / _WS_KEY_EMPTY).mkdir()
    (home / _WS_KEY_CORRUPT / ".kg").parent.mkdir(parents=True)
    (home / _WS_KEY_CORRUPT / ".kg").write_bytes(
        b"definitely not a sqlite database\n" * 8
    )
    (home / "workspaces.json").write_text(
        json.dumps(
            {
                str(tmp_path / "ws" / "alpha"): _WS_KEY_A,
                str(tmp_path / "ws" / "beta"): _WS_KEY_B,
                str(tmp_path / "ws" / "gone"): _WS_KEY_MISSING,
            }
        ),
        encoding="utf-8",
    )
    return home


def _tree_digest(root: Path) -> tuple:
    """``(files, dirs)`` under ``root``: every file's relative path ->
    sha256 of its bytes, plus every directory's relative path. The
    before/after pair catches content changes, added or removed files
    (-wal/-shm sidecars, registry rewrites), and created directories
    alike -- _digest's convention applied to the whole tree."""
    files = {}
    dirs = []
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_dir():
            dirs.append(rel)
        else:
            files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files, frozenset(dirs)


def test_launcher_interactions_leave_every_store_byte_identical(
    tmp_path, monkeypatch
):
    """TC-005 / FR-004: the overview (which stats every store and
    mode=ro-opens each populated one) plus a full selected-store sweep
    across the data views leave EVERY file under CAIRN_HOME
    byte-identical -- the two visited stores, the merely-listed
    empty/corrupt/missing entries, and the registry -- with no new files
    (no sidecars, no registry writes) and no created directories. The
    sweep is non-vacuous: the selected store's own data renders on its
    views, so stores were really served, not just 200ed."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn import paths
    from cairn.dashboard.app import create_app

    home = _launcher_home(tmp_path)
    monkeypatch.setattr(paths, "CAIRN_HOME", home)
    # The launch db lives OUTSIDE home (this interaction set never opens
    # it) so the tree hash covers launcher state only.
    client = TestClient(create_app(db_path=str(tmp_path / "dash.db")))

    before_files, before_dirs = _tree_digest(home)
    # Fixture sanity: the hashed tree really covers both stores, the
    # corrupt .kg, and the registry; seeding left no sidecars behind.
    assert "workspaces.json" in before_files
    assert f"{_WS_KEY_A}/.kg" in before_files
    assert f"{_WS_KEY_B}/.kg" in before_files
    assert f"{_WS_KEY_CORRUPT}/.kg" in before_files
    assert not [p for p in before_files if p.endswith(("-wal", "-shm"))]

    # Overview browsing: the plain overview, then its store-echo variant
    # for every key -- populated, empty, corrupt, and missing alike.
    _fetch(client, "/workspaces")
    for key in (
        _WS_KEY_A,
        _WS_KEY_B,
        _WS_KEY_EMPTY,
        _WS_KEY_CORRUPT,
        _WS_KEY_MISSING,
    ):
        _fetch(client, f"/workspaces?store={key}")

    # Non-vacuous serving: each populated store's own data renders on the
    # views and the other store's does not (the selection is real).
    projects_a = _fetch(client, f"/projects?store={_WS_KEY_A}")
    assert "guard_ws_a" in projects_a.text
    assert "guard_ws_b" not in projects_a.text
    history_b = _fetch(client, f"/history?store={_WS_KEY_B}")
    assert "ws_b_tool" in history_b.text
    assert "ws_a_tool" not in history_b.text
    health_a = _fetch(client, f"/health?store={_WS_KEY_A}")
    assert str(home / _WS_KEY_A / ".kg") in health_a.text
    assert "2026-08-20T07:00:00Z" in health_a.text

    # The selected-store sweep: every data view against each populated
    # store.
    for key in (_WS_KEY_A, _WS_KEY_B):
        for view in (
            "/projects",
            "/history",
            "/tokens",
            "/chains",
            "/health",
            "/graph",
        ):
            _fetch(client, f"{view}?store={key}")

    # Unknown and non-populated keys render the friendly missing page
    # (200, naming the key) -- never an error, never a store "repaired"
    # into existence.
    for key in (_WS_KEY_UNKNOWN, _WS_KEY_EMPTY, _WS_KEY_MISSING):
        missing = _fetch(client, f"/projects?store={key}")
        assert "No graph database found" in missing.text, key
        assert key in missing.text, key

    # The guard itself: byte-identical files (no touched store, no
    # registry rewrite), no new files (no -wal/-shm sidecars), no new
    # directories.
    after_files, after_dirs = _tree_digest(home)
    assert after_files == before_files
    assert after_dirs == before_dirs
    sidecars = sorted(
        p for p in after_files if p.endswith(("-wal", "-shm"))
    )
    assert sidecars == []


# ---------------------------------------------------------------------------
# Retention display guard (ui-dashboard-polish FR-007 / TC-009): the health
# panel now renders the retention policy and the current store size, and
# the dashboard serves traffic views over stores that are OVER those
# bounds. Aging runs solely in the recording sink's flush -- the dashboard
# process must still never age anything, no matter how far past the bounds
# the store it reads sits or which bounds its env pins.
# ---------------------------------------------------------------------------


def test_views_over_an_over_cap_store_never_age_it(tmp_path, monkeypatch):
    """TC-009: a store past BOTH bounds -- row count over the pinned cap and
    every bulk row older than the pinned age window -- serves the full route
    sweep (health displaying the policy in force and the over-cap size
    included) with row counts and the file digest unchanged and no sidecars:
    no route, /health included, deletes a single row."""
    monkeypatch.setenv("CAIRN_TOOL_METRICS_MAX_ROWS", "5000")
    monkeypatch.setenv("CAIRN_TOOL_METRICS_MAX_AGE_SECONDS", "60")

    db_path = _seed_populated_db(tmp_path, name="over-cap.db")
    over_cap = 5200
    ancient = 1_700_000_000.0  # far older than the 60s age window
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
        "duration_ms, status, req_chars, resp_chars) "
        "VALUES ('bulk_tool', 'sess-bulk', ?, 5.0, 'ok', 10, 20)",
        [(ancient + i,) for i in range(over_cap)],
    )
    conn.commit()
    conn.close()
    total_rows = 4 + over_cap

    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app

    client = TestClient(
        create_app(db_path=db_path, knowledge_dir=_seed_knowledge(tmp_path))
    )

    before_digest = _digest(db_path)
    assert _metric_rows(db_path) == total_rows

    for path in ROUTES:
        _fetch(client, path)

    # Non-vacuous: /health really served the over-cap store WITH the
    # pinned policy visible -- the display half of the retention seam.
    health = _fetch(client, "/health")
    assert "tool_metrics cap 5000 rows" in health.text
    assert f"{total_rows} rows" in health.text
    assert "over cap" in health.text
    # The age knob parses as a float, so the card renders "60.0s".
    assert "age bound 60.0s" in health.text
    assert "events ≤ 5000" in health.text

    # The guard itself: nothing aged, nothing written.
    assert _metric_rows(db_path) == total_rows
    assert _digest(db_path) == before_digest
    sidecars = sorted(
        p.name
        for p in Path(db_path).parent.iterdir()
        if p.name.endswith(("-wal", "-shm"))
    )
    assert sidecars == []
