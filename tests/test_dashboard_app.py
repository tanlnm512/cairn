"""Dashboard app spine: lazy server-stack imports, read-only connection
factory, landing/projects/graph/history/tokens/chains/health/memory/tasks
routes + static assets."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _vis_asset() -> Path:
    import cairn.dashboard

    return (
        Path(cairn.dashboard.__file__).resolve().parent
        / "static"
        / "vis-network.min.js"
    )


def _templates_dir() -> Path:
    import cairn.dashboard

    return Path(cairn.dashboard.__file__).resolve().parent / "templates"


# Source checkouts without the vendored vis-network build must not red CI.
requires_vis_network = pytest.mark.skipif(
    not _vis_asset().exists(), reason="vendored vis-network.min.js not present"
)


def test_importing_dashboard_never_loads_server_stack():
    """starlette/uvicorn/jinja2 are transitive deps of mcp and must only be
    imported inside the factory/data functions — a bare `import
    cairn.dashboard` (e.g. via any core CLI path) must not pull them in."""
    code = (
        "import sys; import cairn.dashboard; "
        "print(any(m in sys.modules "
        "for m in ('starlette', 'uvicorn', 'jinja2')))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert proc.stdout.strip() == "False"


def test_get_read_only_db_reads_but_never_writes(tmp_path):
    """The dashboard's connection factory must open mode=ro: reads succeed,
    writes raise (the dashboard can never contend with writer processes)."""
    from cairn.dashboard.data import get_read_only_db

    db_path = str(tmp_path / "ro.db")
    seed = sqlite3.connect(db_path)
    seed.execute("CREATE TABLE t (id TEXT)")
    seed.commit()
    seed.close()

    conn = get_read_only_db(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO t (id) VALUES ('x')")
    finally:
        conn.close()


def test_create_app_serves_landing_and_static(tmp_path):
    """The factory builds a constructible app whose landing route renders
    the Jinja2 template and whose /static mount serves the CSS asset."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import DEFAULT_PORT, create_app

    assert DEFAULT_PORT == 8765  # distinct from the SSE daemon's 9876

    db_path = str(tmp_path / "ro.db")
    seed = sqlite3.connect(db_path)
    seed.execute("CREATE TABLE t (id TEXT)")
    seed.commit()
    seed.close()

    client = TestClient(create_app(db_path=db_path))

    landing = client.get("/")
    assert landing.status_code == 200
    assert "Cairn Dashboard" in landing.text
    assert db_path in landing.text

    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]


def _graph_db_file(tmp_path, seed: bool) -> str:
    """A graph-schema DB file; seeded with one small project when seed=True.

    demo: 2 files / 3 symbols / 2 edges, all 3 symbols embedded with
    'all-MiniLM-L6-v2' — every projects-row field has a known value.
    """
    from cairn.graph.schema import _apply_schema

    db_path = str(tmp_path / "dash.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    if seed:
        conn.executemany(
            "INSERT INTO repos (id, name, path, language, indexed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [("demo", "demo", "clients/demo", "python", "2026-08-20T08:00:00")],
        )
        conn.executemany(
            "INSERT INTO files (id, repo_id, path, language, indexed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("f1", "demo", "src/demo/core.py", "python", "2026-08-20T10:00:00"),
                ("f2", "demo", "src/demo/util.py", "python", "2026-08-20T11:00:00"),
            ],
        )
        conn.executemany(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("s1", "f1", "demo_main", "demo.core.demo_main", "function"),
                ("s2", "f1", "demo_helper", "demo.core.demo_helper", "function"),
                ("s3", "f2", "demo_util", "demo.util.demo_util", "function"),
            ],
        )
        conn.executemany(
            "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, ?)",
            [
                ("e1", "s1", "s2", "calls"),
                ("e2", "s2", "s3", "calls"),
            ],
        )
        conn.executemany(
            "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk, embedded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("s1", "all-MiniLM-L6-v2", 8, b"", "demo_main", "2026-08-20T10:05:00"),
                ("s2", "all-MiniLM-L6-v2", 8, b"", "demo_helper", "2026-08-20T10:05:00"),
                ("s3", "all-MiniLM-L6-v2", 8, b"", "demo_util", "2026-08-20T10:06:00"),
            ],
        )
    conn.commit()
    conn.close()
    return db_path


def _client(tmp_path, seed: bool):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app

    return TestClient(create_app(db_path=_graph_db_file(tmp_path, seed)))


_GRAPH_JSON_RE = re.compile(
    r'<script id="graph-data" type="application/json">(.*?)</script>', re.S
)


def _embedded_graph(html: str) -> dict:
    match = _GRAPH_JSON_RE.search(html)
    assert match, "graph-data JSON block missing from /graph HTML"
    return json.loads(match.group(1))


def test_projects_route_lists_counts_and_embedding_status(tmp_path):
    """FR-002: every project row carries counts, freshness, and the
    embedded/not/partial status with the model where recorded."""
    client = _client(tmp_path, seed=True)
    resp = client.get("/projects")
    assert resp.status_code == 200
    assert "demo" in resp.text
    assert "clients/demo" in resp.text  # workspace-relative path, verbatim
    for header in ("Files", "Symbols", "Edges", "Last indexed", "Embeddings"):
        assert header in resp.text
    assert '<td class="num">3</td>' in resp.text  # symbol count
    assert "2026-08-20T11:00:00" in resp.text  # MAX(files.indexed_at)
    assert "embedded" in resp.text
    assert "all-MiniLM-L6-v2" in resp.text
    assert "/graph?scope=repo" in resp.text and "repo=demo" in resp.text


def test_projects_route_empty_db_renders_empty_state(tmp_path):
    client = _client(tmp_path, seed=False)
    resp = client.get("/projects")
    assert resp.status_code == 200
    assert "No projects indexed" in resp.text


@requires_vis_network
def test_graph_route_embeds_graph_json_and_assets(tmp_path):
    """FR-003: /graph renders the serialized {nodes, edges, metadata} into
    the page and references the vendored vis-network asset plus app.js."""
    client = _client(tmp_path, seed=True)
    resp = client.get("/graph", params={"scope": "module", "focus": "src/demo"})
    assert resp.status_code == 200
    assert "vis-network.min.js" in resp.text
    assert "app.js" in resp.text
    payload = _embedded_graph(resp.text)
    assert {n["id"] for n in payload["nodes"]} >= {
        "demo_main",
        "demo_helper",
        "demo_util",
    }
    assert {(e["source"], e["target"]) for e in payload["edges"]} == {
        ("demo_main", "demo_helper"),
        ("demo_helper", "demo_util"),
    }
    assert payload["metadata"]["scope"] == "module"
    assert payload["metadata"]["node_count"] == 3


@requires_vis_network
def test_graph_route_empty_db_renders_empty_state(tmp_path):
    client = _client(tmp_path, seed=False)
    resp = client.get("/graph")  # default scope=module
    assert resp.status_code == 200
    assert "No nodes for this scope" in resp.text
    assert _embedded_graph(resp.text)["nodes"] == []


@requires_vis_network
def test_graph_route_unknown_scope_falls_back_to_module(tmp_path):
    client = _client(tmp_path, seed=True)
    resp = client.get("/graph", params={"scope": "bogus"})
    assert resp.status_code == 200
    assert 'value="module" selected' in resp.text
    assert _embedded_graph(resp.text)["metadata"]["scope"] == "module"


# ---------------------------------------------------------------------------
# Layout persistence + option application (graph-nav FR-004 / US3 / TC-005):
# /graph reads ``layout`` ∈ {force, hier} -- default force, bogus → force;
# the control's anchors swap only that param (window-control link
# conventions) and the canvas data-layout attribute is app.js's
# initial-layout hook. TC-005's server-visible halves are automated below;
# the live camera-preserving toggle is not automatable here -- the manual
# procedure is the section docstring beneath this comment.
# ---------------------------------------------------------------------------

TC005_MANUAL_PROCEDURE = """\
TC-005 manual half -- layout toggle re-renders in the chosen style, focus
kept (FR-004 / US3-AC1). Run against a real store (e.g. this repo's own
graph via the dev server):

1. Open /graph and let the force-directed network settle; pan/zoom to a
   recognizable focus point (a node cluster you can find again).
2. Click "hierarchical" in the layout control -- the page must NOT
   reload: the same node/edge set re-renders top-down and the camera
   stays at the panned/zoomed focus point (no reset to the origin).
3. Click "force" back -- the same node/edge set re-renders force-directed,
   camera still preserved.
4. After each click the URL's layout param follows the choice via
   history.replaceState (no navigation): ?layout=hier, then ?layout=force.
   Refresh the page: the graph renders in the layout the URL carries --
   the persistence choice survives a reload.
"""


@requires_vis_network
def test_graph_layout_defaults_to_force_with_hier_anchor(tmp_path):
    """TC-005 auto half: default /graph renders the layout control with
    force active and hierarchical as the anchor; the canvas carries
    data-layout="force" -- the attribute app.js applies as the initial
    layout."""
    client = _client(tmp_path, seed=True)
    resp = client.get("/graph")
    assert resp.status_code == 200
    assert 'id="layout-control"' in resp.text
    assert "Layout:" in resp.text
    assert "<strong>force</strong>" in resp.text
    assert "<strong>hierarchical</strong>" not in resp.text
    # The inactive entry is an anchor swapping only the layout param.
    assert (
        '<a href="/graph?layout=hier&amp;scope=module" data-layout="hier">'
        "hierarchical</a>" in resp.text
    )
    assert '<div id="graph-canvas" data-layout="force"' in resp.text


@requires_vis_network
def test_graph_layout_hier_activates_and_link_preserves_graph_params(tmp_path):
    """TC-005 auto half: ?layout=hier marks hierarchical active and the
    canvas hook carries data-layout="hier"; the control's force link is
    one full href that swaps layout and keeps every other graph param
    (scope/focus/repo/depth), so a reload round-trips the whole view."""
    client = _client(tmp_path, seed=True)
    resp = client.get(
        "/graph",
        params={
            "layout": "hier",
            "scope": "symbol",
            "focus": "demo_main",
            "repo": "demo",
            "depth": "2",
        },
    )
    assert resp.status_code == 200
    assert "<strong>hierarchical</strong>" in resp.text
    assert "<strong>force</strong>" not in resp.text
    assert '<div id="graph-canvas" data-layout="hier"' in resp.text
    # One full href: layout swapped to force, scope/focus/repo/depth kept.
    assert (
        '<a href="/graph?layout=force&amp;scope=symbol&amp;focus=demo_main'
        '&amp;repo=demo&amp;depth=2" data-layout="force">force</a>'
        in resp.text
    )


@requires_vis_network
def test_graph_layout_bogus_value_falls_back_to_force(tmp_path):
    """TC-005 auto half: an unknown layout value degrades to force,
    matching the graph handler's scope fallback -- control and canvas both
    show force and the graph still renders; never an error."""
    client = _client(tmp_path, seed=True)
    resp = client.get("/graph", params={"layout": "circular"})
    assert resp.status_code == 200
    assert 'id="layout-control"' in resp.text
    assert "<strong>force</strong>" in resp.text
    assert "<strong>hierarchical</strong>" not in resp.text
    assert '<div id="graph-canvas" data-layout="force"' in resp.text
    # The seeded graph still rendered -- a fallback, not an error page.
    assert _embedded_graph(resp.text)["metadata"]["node_count"] == 3


# ---------------------------------------------------------------------------
# Symbol-search candidates endpoint (graph-nav FR-001/FR-002 / US1): the
# JSON the graph view's search box consumes -- the data-layer contract,
# verbatim.
# ---------------------------------------------------------------------------


def _candidates_db_file(tmp_path, seed: bool) -> str:
    """A graph-schema DB file; seeded when seed=True with TC-002's
    ambiguity: ``dup_name`` defined in two files across two repos (two
    kinds, so each match's context is distinguishable), inserted in the
    reverse of the deterministic result order, plus the unique
    ``solo_name``."""
    from cairn.graph.schema import _apply_schema

    db_path = str(tmp_path / "candidates.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    if seed:
        conn.executemany(
            "INSERT INTO repos (id, name, path, language) VALUES (?, ?, ?, ?)",
            [
                ("left", "left", "clients/left", "python"),
                ("right", "right", "clients/right", "kotlin"),
            ],
        )
        conn.executemany(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
            [
                ("cf1", "left", "src/shared.py", "python"),
                ("cf2", "right", "lib/shared.kt", "kotlin"),
            ],
        )
        conn.executemany(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("cs_kt", "cf2", "dup_name", "right.dup_name", "class"),
                ("cs_py", "cf1", "dup_name", "left.dup_name", "function"),
                ("cs_solo", "cf1", "solo_name", "left.solo_name", "function"),
            ],
        )
        conn.commit()
    conn.close()
    return db_path


def test_candidates_route_returns_the_data_layer_contract(tmp_path):
    """FR-001/FR-002: /graph/candidates is application/json carrying the
    data-layer result verbatim -- the ambiguous name lists both matches
    with file and kind (TC-002), the unique name exactly one (TC-001's
    auto half)."""
    db_path = _candidates_db_file(tmp_path, seed=True)
    client = _panel_client(tmp_path, db_path, str(tmp_path / "missing"))

    resp = client.get("/graph/candidates", params={"name": "dup_name"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {
        "matches": [
            {
                "name": "dup_name",
                "kind": "class",
                "file": "lib/shared.kt",
                "repo_id": "right",
            },
            {
                "name": "dup_name",
                "kind": "function",
                "file": "src/shared.py",
                "repo_id": "left",
            },
        ],
        "truncated": False,
    }

    # Verbatim passthrough: the same store through the data layer equals the
    # route body, key for key.
    from cairn.dashboard.data import get_read_only_db, symbol_candidates

    conn = get_read_only_db(db_path)
    try:
        assert resp.json() == symbol_candidates(conn, "dup_name")
    finally:
        conn.close()

    exact = client.get("/graph/candidates", params={"name": "solo_name"})
    assert exact.status_code == 200
    assert exact.json() == {
        "matches": [
            {
                "name": "solo_name",
                "kind": "function",
                "file": "src/shared.py",
                "repo_id": "left",
            }
        ],
        "truncated": False,
    }


def test_candidates_route_whitespace_and_absent_name_are_empty_json(tmp_path):
    """A whitespace-only or absent name is the empty-matches contract as
    JSON -- HTTP 200, never an error (the search box's blank-submit
    boundary)."""
    client = _panel_client(
        tmp_path, _candidates_db_file(tmp_path, seed=True), str(tmp_path / "missing")
    )
    empty = {"matches": [], "truncated": False}

    for url in ("/graph/candidates?name=%20%20", "/graph/candidates"):
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json() == empty


# ---------------------------------------------------------------------------
# Node-expansion neighbors endpoint (graph-nav FR-003/FR-005 / US2): the
# node/edge JSON the graph view's expand action fetches and merges.
# ---------------------------------------------------------------------------


def _neighbors_db_file(tmp_path, seed: bool) -> str:
    """A graph-schema DB file; seeded when seed=True with TC-003's hub:
    ``expand_main`` with one caller (``expand_caller``) and one callee
    (``expand_helper``) -- the exact node/edge set an expansion fetches."""
    from cairn.graph.schema import _apply_schema

    db_path = str(tmp_path / "neighbors.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    if seed:
        conn.executemany(
            "INSERT INTO repos (id, name, path, language) VALUES (?, ?, ?, ?)",
            [("demo", "demo", "clients/demo", "python")],
        )
        conn.executemany(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
            [
                ("nf1", "demo", "src/demo/core.py", "python"),
                ("nf2", "demo", "src/demo/util.py", "python"),
            ],
        )
        conn.executemany(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("ns_main", "nf1", "expand_main", "demo.expand_main", "function"),
                ("ns_helper", "nf1", "expand_helper", "demo.expand_helper", "function"),
                ("ns_caller", "nf2", "expand_caller", "demo.expand_caller", "function"),
            ],
        )
        conn.executemany(
            "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, ?)",
            [
                ("ne1", "ns_main", "ns_helper", "calls"),
                ("ne2", "ns_caller", "ns_main", "calls"),
            ],
        )
        conn.commit()
    conn.close()
    return db_path


def test_neighbors_route_serves_the_expansion_contract(tmp_path):
    """FR-003/FR-005: /graph/neighbors is application/json carrying the
    viz-layer result verbatim for the requested name -- TC-003's expected
    node/edge set (focal + caller + callee, both edges) -- and repeat/blank
    ``name`` params collapse to the single-name call's JSON."""
    db_path = _neighbors_db_file(tmp_path, seed=True)
    client = _panel_client(tmp_path, db_path, str(tmp_path / "missing"))

    resp = client.get("/graph/neighbors", params={"name": "expand_main"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["metadata"]["scope"] == "neighbors"
    assert body["metadata"]["requested"] == ["expand_main"]
    assert {n["id"] for n in body["nodes"]} == {
        "expand_main",
        "expand_helper",  # the focal's callee
        "expand_caller",  # the focal's caller
    }
    assert {(e["source"], e["target"], e["kind"]) for e in body["edges"]} == {
        ("expand_main", "expand_helper", "calls"),
        ("expand_caller", "expand_main", "calls"),
    }

    # Verbatim passthrough: the same store through the viz layer equals the
    # route body, key for key.
    from cairn.dashboard.data import get_read_only_db
    from cairn.viz.query import get_symbol_neighbors

    conn = get_read_only_db(db_path)
    try:
        assert body == get_symbol_neighbors(conn, ["expand_main"])
    finally:
        conn.close()

    # Repeat + blank params dedupe/clean to the same JSON as the one call.
    noisy = client.get("/graph/neighbors?name=expand_main&name=expand_main&name=")
    assert noisy.status_code == 200
    assert noisy.json() == body


def test_neighbors_route_absent_name_and_bogus_depth_never_error(tmp_path):
    """An absent name is the empty neighbors contract as JSON (HTTP 200),
    and a bogus depth falls back to the default behavior -- the route's
    cleaning boundaries never surface an error."""
    client = _panel_client(
        tmp_path, _neighbors_db_file(tmp_path, seed=True), str(tmp_path / "missing")
    )

    absent = client.get("/graph/neighbors")
    assert absent.status_code == 200
    assert absent.headers["content-type"].startswith("application/json")
    assert absent.json() == {
        "nodes": [],
        "edges": [],
        "metadata": {
            "scope": "neighbors",
            "requested": [],
            "node_count": 0,
            "edge_count": 0,
            "truncated": False,
        },
    }

    single = client.get("/graph/neighbors", params={"name": "expand_main"})
    bogus = client.get(
        "/graph/neighbors", params=[("name", "expand_main"), ("depth", "bogus")]
    )
    assert bogus.status_code == 200
    assert bogus.json() == single.json()


# ---------------------------------------------------------------------------
# Health / memory / task-queue panels (FR-008, FR-009)
# ---------------------------------------------------------------------------


def _health_db_file(tmp_path, seed: bool) -> str:
    """A graph-schema DB file; seeded with one build_run when seed=True."""
    from cairn.graph.schema import _apply_schema

    db_path = str(tmp_path / "health.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    if seed:
        conn.execute(
            "INSERT INTO build_runs (kind, started_at) "
            "VALUES ('full', '2026-08-20T07:00:00Z')"
        )
        conn.commit()
    conn.close()
    return db_path


def _panel_client(tmp_path, db_file: str, knowledge_dir: str):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app

    return TestClient(create_app(db_path=db_file, knowledge_dir=knowledge_dir))


def test_health_route_shows_size_freshness_backend_and_reranker(tmp_path):
    """FR-008: one-glance panel carrying the DB size (human-readable), index
    freshness, backend mode, and reranker status from the seeded DB."""
    db_path = _health_db_file(tmp_path, seed=True)
    client = _panel_client(tmp_path, db_path, str(tmp_path / "knowledge"))
    resp = client.get("/health")
    assert resp.status_code == 200

    for label in (
        "Database",
        "Index freshness",
        "Embedding backend",
        "Vector index",
        "Reranker",
    ):
        assert label in resp.text

    from cairn.dashboard.app import _human_size

    assert _human_size(os.stat(db_path).st_size) in resp.text
    assert "2026-08-20T07:00:00Z" in resp.text
    assert re.search(r"just now|\b\d+[smhd] old\b", resp.text)

    # conftest clears CAIRN_EMBED_BACKEND, so the local default is active.
    assert ">local<" in resp.text
    from cairn.graph.embeddings import is_hash_fallback
    from cairn.graph.reranker import reranker_available

    assert ("hash fallback" in resp.text) == is_hash_fallback()
    expected_rerank = "available" if reranker_available() else "unavailable"
    assert f">{expected_rerank}<" in resp.text


def test_health_route_empty_db_renders_empty_state(tmp_path):
    client = _panel_client(
        tmp_path, _health_db_file(tmp_path, seed=False), str(tmp_path / "nope")
    )
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "no build_runs recorded" in resp.text
    assert "no embeddings to index yet" in resp.text


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


def test_memory_route_lists_memories_newest_first_with_type(tmp_path):
    """FR-009: recent memories newest-first, each with type badge + title."""
    kdir = tmp_path / "knowledge"
    _seed_memories(kdir)
    client = _panel_client(tmp_path, _graph_db_file(tmp_path, seed=False), str(kdir))
    resp = client.get("/memory")
    assert resp.status_code == 200

    newest_first = [
        "Seeded-DB test convention",
        "Skipped the fuzzy retry",
        "Use RRF fusion by default",
    ]
    positions = [resp.text.index(title) for title in newest_first]
    assert positions == sorted(positions)
    for mtype in ("pattern", "mistake", "decision"):
        assert f">{mtype}<" in resp.text


def test_memory_route_empty_knowledge_dir_renders_empty_state(tmp_path):
    client = _panel_client(
        tmp_path,
        _graph_db_file(tmp_path, seed=False),
        str(tmp_path / "missing"),
    )
    resp = client.get("/memory")
    assert resp.status_code == 200
    assert "No memories recorded yet" in resp.text


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


def test_tasks_route_lists_entries_and_honors_status_filter(tmp_path):
    """FR-009: queue entries listed with status, and ?status= filters them."""
    pending, claimed, done = _seed_tasks(tmp_path / "knowledge")
    kdir = str(tmp_path / "knowledge")
    client = _panel_client(tmp_path, _graph_db_file(tmp_path, seed=False), kdir)

    resp = client.get("/tasks")
    assert resp.status_code == 200
    for tid in (pending.id, claimed.id, done.id):
        assert tid in resp.text
    for badge in ("pending", "in-progress", "done"):
        assert f">{badge}<" in resp.text
    assert 'value="all" selected' in resp.text

    filtered = client.get("/tasks", params={"status": "pending"})
    assert filtered.status_code == 200
    assert 'value="pending" selected' in filtered.text
    assert pending.id in filtered.text
    assert claimed.id not in filtered.text
    assert done.id not in filtered.text

    bogus = client.get("/tasks", params={"status": "bogus"})
    assert bogus.status_code == 200
    assert 'value="all" selected' in bogus.text
    assert pending.id in bogus.text and claimed.id in bogus.text


def test_tasks_route_empty_queue_renders_empty_state(tmp_path):
    client = _panel_client(
        tmp_path,
        _graph_db_file(tmp_path, seed=False),
        str(tmp_path / "missing"),
    )
    resp = client.get("/tasks")
    assert resp.status_code == 200
    assert "No tasks" in resp.text

    filtered = client.get("/tasks", params={"status": "done"})
    assert filtered.status_code == 200
    assert "No tasks" in filtered.text
    assert "done" in filtered.text


# ---------------------------------------------------------------------------
# Tool-call history (FR-005, US3)
# ---------------------------------------------------------------------------

# Distinctive head/tail of a payload whose args_summary was truncated at the
# write chokepoint: the head renders, the tail must never reach the page.
_TC024_PAYLOAD = "HEAD" + "z" * 296 + "TC024-TAIL-NEVER-RENDER"
_TC024_SUMMARY = _TC024_PAYLOAD[:200]


def _history_db_file(tmp_path, seed: bool) -> str:
    """A graph-schema DB file; seeded with four tool_metrics rows when
    seed=True, written newest-last so rendering order is not insert order:

    ask_compass  / sess-alpha @ 00:25 — error, 1750 ms, ~100/~200 tokens
    explore      / sess-beta  @ 00:20 — ok, 250 ms, ~300/~1200 tokens,
                                        truncated args summary (TC-024)
    explore      / sess-alpha @ 00:00 — ok, 60 ms, NULL sizes (pre-migration)
    legacy_tool  / unknown    @ 2025-08-19 23:43:20 — ok, 30 ms, NULL sizes;
                                        the literal 'unknown' session every
                                        pre-session-id row shares
                                        (cross-links TC-002's legacy shape)
    """
    from cairn.graph.schema import _apply_schema

    db_path = str(tmp_path / "history.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    if seed:
        conn.executemany(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status, error_message, req_chars, resp_chars, "
            "args_summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("legacy_tool", "unknown", 1755647000.0, 30.0, "ok", None,
                 None, None, None),
                ("explore", "sess-alpha", 1755648000.0, 60.0, "ok", None,
                 None, None, None),
                ("explore", "sess-beta", 1755649200.0, 250.25, "ok", None,
                 1200, 4800, _TC024_SUMMARY),
                ("ask_compass", "sess-alpha", 1755649500.0, 1750.0, "error",
                 "connection refused", 400, 800,
                 '{"query": "backoff retry policy"}'),
            ],
        )
        conn.commit()
    conn.close()
    return db_path


def _history_client(tmp_path, seed: bool):
    return _panel_client(
        tmp_path, _history_db_file(tmp_path, seed), str(tmp_path / "missing")
    )


def test_history_route_lists_calls_newest_first_with_all_columns(tmp_path):
    """FR-005 / US3-AC1: newest-first rows carrying tool name, timestamp,
    duration, status, and session, plus per-call token estimates (US4-AC2)
    and 'unknown' for pre-migration rows with NULL sizes."""
    resp = _history_client(tmp_path, seed=True).get("/history")
    assert resp.status_code == 200

    for header in ("Tool", "Timestamp", "Duration", "Status", "Session"):
        assert header in resp.text

    newest_first = [
        "2025-08-20 00:25:00 UTC",  # ask_compass / sess-alpha
        "2025-08-20 00:20:00 UTC",  # explore / sess-beta
        "2025-08-20 00:00:00 UTC",  # explore / sess-alpha
    ]
    positions = [resp.text.index(ts) for ts in newest_first]
    assert positions == sorted(positions)

    assert "ask_compass" in resp.text
    assert "sess-beta" in resp.text and "sess-alpha" in resp.text
    assert "1.8 s" in resp.text and "250 ms" in resp.text and "60 ms" in resp.text
    assert "badge-ok" in resp.text and "badge-error" in resp.text
    assert "~100 / ~200" in resp.text  # 400//4, 800//4 chars per token
    assert "~300 / ~1200" in resp.text  # 1200//4, 4800//4
    assert "unknown" in resp.text  # NULL sizes: unknown, not 0, no error


def test_history_route_filters_by_tool_and_session(tmp_path):
    """FR-005 / US3-AC2: exact-match tool and session filters, combinable,
    each preserving the other; a nonsense filter is a 200 empty state."""
    client = _history_client(tmp_path, seed=True)

    by_tool = client.get("/history", params={"tool": "explore"})
    assert by_tool.status_code == 200
    assert "2025-08-20 00:20:00 UTC" in by_tool.text
    assert "2025-08-20 00:00:00 UTC" in by_tool.text
    assert "ask_compass" not in by_tool.text
    assert 'value="explore"' in by_tool.text  # form keeps the other filter
    assert 'name="session"' in by_tool.text

    by_session = client.get("/history", params={"session": "sess-alpha"})
    assert by_session.status_code == 200
    assert "2025-08-20 00:25:00 UTC" in by_session.text
    assert "2025-08-20 00:00:00 UTC" in by_session.text
    assert "sess-beta" not in by_session.text
    assert 'value="sess-alpha"' in by_session.text

    both = client.get(
        "/history", params={"tool": "explore", "session": "sess-beta"}
    )
    assert both.status_code == 200
    assert "2025-08-20 00:20:00 UTC" in both.text
    assert "ask_compass" not in both.text
    assert "sess-alpha" not in both.text

    for nonsense in ({"tool": "no-such-tool"}, {"session": "no-such-session"}):
        empty = client.get("/history", params=nonsense)
        assert empty.status_code == 200
        assert "No matching calls" in empty.text


def test_history_route_fresh_db_renders_empty_state(tmp_path):
    """TC-013: no recorded calls yet — empty state, HTTP 200, no error."""
    resp = _history_client(tmp_path, seed=False).get("/history")
    assert resp.status_code == 200
    assert "No matching calls" in resp.text


def test_history_route_shows_arg_summary_never_full_payload(tmp_path):
    """TC-024: the truncated args_summary renders; the payload's distinctive
    tail never reaches the page."""
    resp = _history_client(tmp_path, seed=True).get("/history")
    assert resp.status_code == 200
    assert "backoff retry policy" in resp.text  # summary text, rendered
    assert _TC024_SUMMARY in resp.text
    assert "TC024-TAIL-NEVER-RENDER" not in resp.text


# ---------------------------------------------------------------------------
# Tokens + chains views (FR-006, FR-007 / US4, US5)
# ---------------------------------------------------------------------------

_TC_BASE = 1755648000.0  # 2025-08-20 00:00:00 UTC, like the history fixture


def _tokens_chains_db_file(tmp_path, seed: bool) -> str:
    """A graph-schema DB file; seeded when seed=True with:

    tokens (TC-014): tool_heavy — 2 calls, 1600+2400 req / 3200+4800 resp
    chars -> ~1000 + ~2000 = ~3000 total, ~1500 mean — clearly above
    tool_light — 1 call, 400+800 chars -> ~100 + ~200 = ~300 total.
    chains (TC-016 / TC-017): sess-multi — 3 calls a minute apart;
    sess-single — 1 call; sess-gapped — 3 calls a minute apart, then 2 more
    six hours later under the same session id (two chains).
    """
    from cairn.graph.schema import _apply_schema

    db_path = str(tmp_path / "tokens-chains.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    if seed:
        six_hours = 6 * 3600
        conn.executemany(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status, req_chars, resp_chars) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("tool_heavy", "sess-tok", _TC_BASE, 100.0, "ok", 1600, 3200),
                ("tool_heavy", "sess-tok", _TC_BASE + 60, 120.0, "ok", 2400, 4800),
                ("tool_light", "sess-tok", _TC_BASE + 120, 50.0, "ok", 400, 800),
                ("seq_alpha", "sess-multi", _TC_BASE + 600, 10.0, "ok", 10, 10),
                ("seq_beta", "sess-multi", _TC_BASE + 660, 20.0, "ok", 10, 10),
                ("seq_gamma", "sess-multi", _TC_BASE + 720, 30.0, "ok", 10, 10),
                ("solo_call", "sess-single", _TC_BASE + 1200, 2500.0, "ok", 10, 10),
                ("pair1_a", "sess-gapped", _TC_BASE + 1800, 5.0, "ok", 10, 10),
                ("pair1_b", "sess-gapped", _TC_BASE + 1860, 5.0, "ok", 10, 10),
                ("pair1_c", "sess-gapped", _TC_BASE + 1920, 5.0, "error", 10, 10),
                ("pair2_a", "sess-gapped", _TC_BASE + 1800 + six_hours, 5.0, "ok", 10, 10),
                ("pair2_b", "sess-gapped", _TC_BASE + 1860 + six_hours, 5.0, "ok", 10, 10),
            ],
        )
        conn.commit()
    conn.close()
    return db_path


def _tokens_chains_client(tmp_path, seed: bool):
    return _panel_client(
        tmp_path, _tokens_chains_db_file(tmp_path, seed), str(tmp_path / "missing")
    )


def _chain_blocks(html: str):
    """Rendered content of each chain block, in page order."""
    return html.split('<div class="chain"')[1:]


def test_tokens_route_ranks_aggregates_with_bigger_payload_tool_first(tmp_path):
    """FR-006 / US4-AC1: calls / total / mean columns, ranked by total
    descending — the bigger-payload tool renders above the smaller."""
    resp = _tokens_chains_client(tmp_path, seed=True).get("/tokens")
    assert resp.status_code == 200

    for header in ("Tool", "Calls", "Total est. tokens", "Mean est. tokens"):
        assert header in resp.text

    # Ranked by total: tool_heavy (~3000) above tool_light (~300).
    assert resp.text.index("tool_heavy") < resp.text.index("tool_light")
    assert '<td class="num">2</td>' in resp.text  # heavy's call count
    assert '<td class="num">~1000</td>' in resp.text  # heavy est. req
    assert '<td class="num">~2000</td>' in resp.text  # heavy est. resp
    assert '<td class="num">~3000</td>' in resp.text  # heavy total
    assert '<td class="num">~1500</td>' in resp.text  # heavy mean (3000/2)
    assert '<td class="num">~300</td>' in resp.text  # light total


def test_chains_route_lists_sessions_as_ordered_chains(tmp_path):
    """FR-007 / US5-AC1: each session is its own visually connected chain
    with calls in chronological order and human-readable timestamps and
    durations; a single-call session still renders as its own chain."""
    resp = _tokens_chains_client(tmp_path, seed=True).get("/chains")
    assert resp.status_code == 200

    blocks = _chain_blocks(resp.text)
    # sess-tok / sess-multi / sess-single: one chain each; sess-gapped: two.
    assert len(blocks) == 5
    assert resp.text.count('data-session="sess-gapped"') == 2
    assert resp.text.count('data-session="sess-single"') == 1

    # Newest session's activity leads the page; chains within a session
    # stay chronological, so the gapped session's first burst is block 0.
    assert "sess-gapped" in blocks[0] and "pair1_a" in blocks[0]
    assert "pair2_b" in blocks[1]

    multi = next(b for b in blocks if "sess-multi" in b)
    order = [multi.index(t) for t in ("seq_alpha", "seq_beta", "seq_gamma")]
    assert order == sorted(order)  # chronological, not alphabetical
    assert "3 calls" in multi
    assert "2025-08-20 00:10:00 UTC" in multi  # seq_alpha, epoch like /history
    assert "span 2m" in multi  # 600s -> 720s
    assert "+2m" in multi  # seq_gamma's offset from chain start
    assert "badge-error" in resp.text  # pair1_c's error call keeps its badge

    single = next(b for b in blocks if "sess-single" in b)
    assert "solo_call" in single and "seq_alpha" not in single
    assert "+0s" in single  # its only call, at chain start
    assert "2.5 s" in single  # solo_call's 2500 ms, human like /history


def test_chains_route_gap_pair_renders_as_two_chains(tmp_path):
    """FR-007 / US5-AC2 / TC-017: two bursts six hours apart under one
    session id render as two separate chains, each with only its own calls."""
    resp = _tokens_chains_client(tmp_path, seed=True).get("/chains")
    assert resp.status_code == 200

    bursts = [b for b in _chain_blocks(resp.text) if "sess-gapped" in b]
    assert len(bursts) == 2
    first, second = bursts  # chains within a session stay chronological
    assert "pair1_a" in first and "pair1_b" in first and "pair1_c" in first
    assert "pair2_a" not in first and "pair2_b" not in first
    assert "pair2_a" in second and "pair2_b" in second
    assert "pair1_a" not in second and "pair1_c" not in second


def test_chains_route_session_param_filters_to_one_session(tmp_path):
    """FR-002: ``session`` narrows /chains to that session's chains only;
    a no-match session renders the empty state (HTTP 200, never an error);
    without the param every session renders as before."""
    client = _tokens_chains_client(tmp_path, seed=True)

    resp = client.get("/chains", params={"session": "sess-gapped"})
    assert resp.status_code == 200
    blocks = _chain_blocks(resp.text)
    assert len(blocks) == 2  # sess-gapped's two chains, nothing else
    assert all("sess-gapped" in b for b in blocks)
    assert "seq_alpha" not in resp.text  # sess-multi is filtered out

    # A no-match session: the empty state, never an error.
    empty = client.get("/chains", params={"session": "no-such-session"})
    assert empty.status_code == 200
    assert _chain_blocks(empty.text) == []
    assert "No tool calls recorded yet." in empty.text

    # Without the param: every session renders (5 chains as above).
    plain = client.get("/chains")
    assert len(_chain_blocks(plain.text)) == 5


def test_tokens_and_chains_routes_empty_db_render_empty_states(tmp_path):
    """Empty-input boundary: no recorded calls — both views are HTTP 200
    with an empty state, no error."""
    client = _tokens_chains_client(tmp_path, seed=False)
    for path in ("/tokens", "/chains"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert "No tool calls recorded yet" in resp.text
        assert '<table class="data-table">' not in resp.text


# ---------------------------------------------------------------------------
# Time-window control on the traffic routes (FR-002): the data layer owns
# the semantics (tests/test_dashboard_data.py); these pin only what is
# route-visible — the control, the bogus-value fallback, and the Older
# link carrying the window. Seeded timestamps anchor to time.time() with
# fixed offsets because the cutoff is computed at request time.
# ---------------------------------------------------------------------------


def _window_db_file(tmp_path, bulk: bool) -> str:
    """A graph-schema DB file; ``recent_tool`` ran a minute ago (inside any
    24h window), ``ancient_tool`` three days ago (outside 24h, inside 30d).
    ``bulk=True`` adds one page plus ten more recent explore rows so a 24h
    window still paginates."""
    from cairn.dashboard.data import HISTORY_PAGE_SIZE
    from cairn.graph.schema import _apply_schema

    now = time.time()
    rows = [
        ("recent_tool", "sess-recent", now - 60, 10.0, "ok", 40, 80),
        ("ancient_tool", "sess-ancient", now - 3 * 86400, 20.0, "ok", 400, 800),
    ]
    if bulk:
        rows += [
            ("explore", "sess-bulk", now - 120 - i, 5.0, "ok", 10, 10)
            for i in range(HISTORY_PAGE_SIZE + 10)
        ]

    db_path = str(tmp_path / "window.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.executemany(
        "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
        "duration_ms, status, req_chars, resp_chars) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def _window_client(tmp_path, bulk: bool):
    return _panel_client(
        tmp_path, _window_db_file(tmp_path, bulk), str(tmp_path / "missing")
    )


def test_window_control_renders_on_history_tokens_and_chains(tmp_path):
    """FR-002: all three traffic views carry the shared window control with
    the 24h/7d/30d/all presets, 'all' active by default."""
    client = _window_client(tmp_path, bulk=False)
    for path in ("/history", "/tokens", "/chains"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "Window:" in resp.text, path
        for preset in ("24h", "7d", "30d", "all"):
            assert preset in resp.text, path
        assert "<strong>all</strong>" in resp.text, path


def test_window_bogus_value_falls_back_to_all(tmp_path):
    """An unknown window value degrades to all-time, matching the graph
    handler's scope fallback: never an error, 'all' marked active, and the
    out-of-24h rows stay on the page."""
    client = _window_client(tmp_path, bulk=False)

    windowed = client.get("/history", params={"window": "24h"})
    assert windowed.status_code == 200
    assert "<strong>24h</strong>" in windowed.text
    assert "recent_tool" in windowed.text
    assert "ancient_tool" not in windowed.text  # outside rows excluded

    bogus = client.get("/history", params={"window": "bogus"})
    assert bogus.status_code == 200
    assert "<strong>all</strong>" in bogus.text
    assert "recent_tool" in bogus.text
    assert "ancient_tool" in bogus.text  # same rows as all-time


def test_history_older_link_carries_the_active_window(tmp_path):
    """FR-006: paging composes with the window — the Older link keeps the
    24h param so the next page stays in-window instead of resuming all-time
    pagination past the window's edge."""
    client = _window_client(tmp_path, bulk=True)
    resp = client.get("/history", params={"window": "24h"})
    assert resp.status_code == 200

    older = re.search(r'<a href="(/history\?before=[^"]*)">Older</a>', resp.text)
    assert older, "Older link missing from a windowed multi-page history"
    assert "window=24h" in older.group(1)

    page2 = client.get(older.group(1).replace("&amp;", "&"))
    assert page2.status_code == 200
    # Dropping the window here would resume all-time pagination and reach
    # the ancient row; carrying it keeps every older page in-window.
    assert "ancient_tool" not in page2.text


# ---------------------------------------------------------------------------
# Cross-view links (cross-links FR-001/FR-002/FR-003/FR-004/FR-006 /
# US1-US4): tokens rows anchor to tool-filtered history, history rows
# anchor to session-focused chains (the literal 'unknown' legacy session
# included), the shipped projects->graph anchor stays pinned, a graph
# node's inspect action anchors into its symbol neighborhood, and /graph
# is reachable from both navs. The session-filter ROUTE half of TC-002
# lives above in test_chains_route_session_param_filters_to_one_session;
# TC-004's AUTO halves (the placeholder span the selectNode JS builds on
# + the inspect target URL) are in the FR-004 tests below, with the live
# JS half as TC004_MANUAL_PROCEDURE; TC-005's BUILDER half (the
# view_link macro's window/urlencode edge cases) lives below in
# test_view_link_macro_carries_window_and_encodes_value.
# ---------------------------------------------------------------------------


def test_tokens_rows_anchor_to_tool_filtered_history(tmp_path):
    """FR-001 / US1-AC1 / TC-001: each tokens row's tool name anchors to
    the history route pre-filtered to that tool, and following the anchor
    lists only that tool's calls. FR-005's carry half: a non-'all' window
    rides the anchor; default and explicit 'all' omit it."""
    client = _tokens_chains_client(tmp_path, seed=True)
    resp = client.get("/tokens")
    assert resp.status_code == 200
    assert '<a href="/history?tool=tool_heavy">tool_heavy</a>' in resp.text
    assert '<a href="/history?tool=tool_light">tool_light</a>' in resp.text

    # Explicit 'all' is the default: the anchor still carries no window.
    explicit_all = client.get("/tokens", params={"window": "all"})
    assert '<a href="/history?tool=tool_heavy">tool_heavy</a>' in (
        explicit_all.text
    )

    # Following the anchor: history pre-filtered to that tool only
    # (TC-001's pass condition).
    drilled = client.get("/history", params={"tool": "tool_heavy"})
    assert drilled.status_code == 200
    assert "2025-08-20 00:01:00 UTC" in drilled.text  # heavy's 2 calls
    assert "2025-08-20 00:00:00 UTC" in drilled.text
    assert "tool_light" not in drilled.text

    # A 24h window rides the anchor; the aggregate fixture is anchored to
    # a 2025 epoch (outside any 24h window), so the window client's fresh
    # timestamps carry this half.
    wclient = _window_client(tmp_path, bulk=False)
    windowed = wclient.get("/tokens", params={"window": "24h"})
    assert windowed.status_code == 200
    anchor = re.search(
        r'<a href="(/history\?tool=recent_tool[^"]*)">recent_tool</a>',
        windowed.text,
    )
    assert anchor, "windowed tokens row is missing its tool anchor"
    assert "window=24h" in anchor.group(1)
    assert "ancient_tool" not in windowed.text  # outside the window's slice

    followed = wclient.get(anchor.group(1).replace("&amp;", "&"))
    assert followed.status_code == 200
    assert "recent_tool" in followed.text
    assert "ancient_tool" not in followed.text  # destination stays in-window


def test_history_rows_anchor_to_session_chains(tmp_path):
    """FR-002 / US2-AC1 / TC-002: each history row's session id anchors to
    the chains route focused on that session, and following it lists only
    that session's chains; the legacy literal ``unknown`` session (every
    pre-session-id row) anchors too — a functional link, never
    special-cased away."""
    client = _history_client(tmp_path, seed=True)
    resp = client.get("/history")
    assert resp.status_code == 200
    assert '<a href="/chains?session=sess-alpha">sess-alpha</a>' in resp.text
    assert '<a href="/chains?session=sess-beta">sess-beta</a>' in resp.text
    # The legacy shape: rows recorded before per-boot session ids exist.
    assert '<a href="/chains?session=unknown">unknown</a>' in resp.text

    # Following an anchor: that session's chain only (00:00 -> 00:25 sits
    # inside the 30-min chain gap, so one chain).
    alpha = client.get("/chains", params={"session": "sess-alpha"})
    assert alpha.status_code == 200
    assert len(_chain_blocks(alpha.text)) == 1
    assert "ask_compass" in alpha.text and "explore" in alpha.text
    assert "sess-beta" not in alpha.text

    # The legacy anchor is equally functional: /chains?session=unknown
    # renders the unknown session's chain, not an error or empty state.
    legacy = client.get("/chains", params={"session": "unknown"})
    assert legacy.status_code == 200
    assert len(_chain_blocks(legacy.text)) == 1
    assert 'data-session="unknown"' in legacy.text
    assert "legacy_tool" in legacy.text
    assert "sess-alpha" not in legacy.text and "sess-beta" not in (
        legacy.text
    )


@requires_vis_network
def test_projects_row_anchor_opens_repo_scoped_graph(tmp_path):
    """FR-003 / US3-AC1 / TC-003: regression guard on the already-shipped
    link — the seeded project row anchors to the graph route scoped to
    that repo, and following it renders that repo's graph (its module
    buckets under repo metadata), never another scope."""
    client = _client(tmp_path, seed=True)
    resp = client.get("/projects")
    assert resp.status_code == 200
    assert '<a href="/graph?scope=repo&amp;repo=demo">demo</a>' in resp.text

    target = client.get("/graph", params={"scope": "repo", "repo": "demo"})
    assert target.status_code == 200
    payload = _embedded_graph(target.text)
    assert payload["metadata"]["scope"] == "repo"
    assert payload["metadata"]["repo"] == "demo"
    # demo's graph renders its own files' buckets, symbol counts included.
    assert {n["id"] for n in payload["nodes"]} == {
        "src/demo/core.py (2)",
        "src/demo/util.py (1)",
    }


# TC-004's JS half -- the live selectNode/deselectNode swap of the
# #inspect-action hint into the inspect anchor and back -- needs a browser
# (these route tests have no JS runtime); the procedure is the constant
# beneath, mirroring TC005_MANUAL_PROCEDURE above.

TC004_MANUAL_PROCEDURE = """\
TC-004 manual half -- node inspect opens its neighborhood (FR-004 /
US4-AC1). Run against a real store (e.g. this repo's own graph via the
dev server):

1. Open /graph and let the network settle; pick a node with visible
   neighbors you can find again.
2. Single-click the node -- it becomes selected (vis default) and the
   "select a node to inspect" hint beside the layout control becomes an
   inspect '<name>' link for that node.
3. Click the link -- the browser navigates (full page load) to
   /graph?scope=symbol&focus=<name> and the focused
   symbol-neighborhood subgraph renders: the symbol plus its callers
   and callees.
4. Browser-back returns to the graph with the node still selected (the
   inspect link still showing, not the placeholder).
5. Single-click empty canvas -- the node deselects and the hint returns
   to the "select a node to inspect" placeholder.
6. Double-click the node -- it still expands in place (graph-nav's
   expand gesture): no conflict with inspect's single-click select
   (D-004's gesture split).
"""


@requires_vis_network
def test_graph_page_renders_the_inspect_placeholder_span(tmp_path):
    """FR-004 / TC-004 (auto half): /graph renders the inspect hook the
    selectNode JS builds on -- the placeholder span verbatim (id, class,
    placeholder text) before any node is selected."""
    client = _client(tmp_path, seed=True)
    resp = client.get("/graph")
    assert resp.status_code == 200
    assert (
        '<span id="inspect-action" class="muted">'
        "select a node to inspect</span>" in resp.text
    )


@requires_vis_network
def test_inspect_target_url_renders_symbol_neighborhood(tmp_path):
    """FR-004 / US4-AC1 / TC-004 (URL-construction half): the URL the
    selectNode JS builds -- /graph?scope=symbol&focus=<name> -- is a real
    route: following it renders that symbol's neighborhood (the focal
    plus its 1-hop caller and callee), never another scope."""
    client = _client(tmp_path, seed=True)

    # The exact href shape app.js builds: scope=symbol + focus=<node id>.
    target = client.get("/graph?scope=symbol&focus=demo_helper")
    assert target.status_code == 200
    payload = _embedded_graph(target.text)
    assert payload["metadata"]["scope"] == "symbol"
    assert payload["metadata"]["symbol"] == "demo_helper"
    assert {n["id"] for n in payload["nodes"]} == {
        "demo_helper",  # the focal
        "demo_main",  # its caller
        "demo_util",  # its callee
    }
    assert {(e["source"], e["target"]) for e in payload["edges"]} == {
        ("demo_main", "demo_helper"),
        ("demo_helper", "demo_util"),
    }


def test_nav_and_landing_page_each_link_to_graph(tmp_path):
    """FR-006 / US3 / TC-006: /graph is no orphan — the shared nav carries
    it on every page (base.html) and the landing page's link list repeats
    it (index.html)."""
    client = _client(tmp_path, seed=False)

    landing = client.get("/")
    assert landing.status_code == 200
    assert '<a href="/graph">Graph</a>' in landing.text  # base.html nav
    assert '<a href="/graph">Graph explorer</a>' in landing.text  # list

    # The nav entry is base.html's, not landing-specific: another page
    # renders it too, so /graph is one click from anywhere.
    projects = client.get("/projects")
    assert projects.status_code == 200
    assert '<a href="/graph">Graph</a>' in projects.text


def test_view_link_macro_carries_window_and_encodes_value():
    """FR-005 / TC-005 (builder half): the view_link macro appends the
    window param only when a real window is active -- the empty default and
    an explicit 'all' omit it, '24h' rides the href -- and a value needing
    quoting is urlencoded in the href while the anchor label stays the
    value verbatim. The route-level halves ride the page tests above."""
    pytest.importorskip("jinja2")
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(_templates_dir()), autoescape=True
    )
    view_link = env.get_template("_links.html").module.view_link

    # Window omitted when not present ('' default) and when explicitly 'all'.
    assert str(view_link("history", "tool", "tool_heavy")) == (
        '<a href="/history?tool=tool_heavy">tool_heavy</a>'
    )
    assert str(view_link("history", "tool", "tool_heavy", window="all")) == (
        '<a href="/history?tool=tool_heavy">tool_heavy</a>'
    )

    # A real window is appended as the second query param (HTML-escaped &).
    assert str(view_link("history", "tool", "tool_heavy", window="24h")) == (
        '<a href="/history?tool=tool_heavy&amp;window=24h">tool_heavy</a>'
    )

    # A value needing quoting: urlencoded in the href, verbatim as the label.
    assert str(view_link("chains", "session", "sess alpha")) == (
        '<a href="/chains?session=sess%20alpha">sess alpha</a>'
    )


def test_missing_db_renders_friendly_state_not_500(tmp_path):
    """A nonexistent DB path is the never-indexed boundary: every DB-backed
    route must render the guidance page (200), never a 500."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app

    client = TestClient(create_app(db_path=str(tmp_path / "nope.db")))
    for path in ("/projects", "/graph", "/health", "/history", "/tokens", "/chains"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "No graph database found" in resp.text, path
        assert "cairn build" in resp.text, path
