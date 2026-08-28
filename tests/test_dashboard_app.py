"""Dashboard app spine: lazy server-stack imports, read-only connection
factory, landing/workspaces/projects/graph/history/tokens/chains/health/
memory/tasks routes + static assets."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from html.parser import HTMLParser
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


def test_suggest_route_returns_prefix_matches_as_json(tmp_path):
    """/graph/suggest is application/json carrying the typeahead contract:
    prefix matches (case-insensitive, mid-string fragments excluded),
    shortest-first with file/kind context, empty-never-error."""
    client = _panel_client(
        tmp_path, _candidates_db_file(tmp_path, seed=True), str(tmp_path / "missing")
    )

    resp = client.get("/graph/suggest", params={"name": "dup"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert [m["name"] for m in body["matches"]] == ["dup_name", "dup_name"]
    assert body["matches"][0]["kind"] == "class"
    assert body["truncated"] is False

    upper = client.get("/graph/suggest", params={"name": "SOLO"})
    assert [m["name"] for m in upper.json()["matches"]] == ["solo_name"]

    # a mid-string fragment and a blank prefix are the empty contract
    for url in ("/graph/suggest?name=olo", "/graph/suggest?name=%20", "/graph/suggest"):
        mid = client.get(url)
        assert mid.status_code == 200
        assert mid.json() == {"matches": [], "truncated": False}


def test_graph_page_carries_typeahead_search_markup(tmp_path):
    """The symbol search is a combobox wired to a live suggestion listbox
    (#symbol-suggest) -- options render while typing instead of demanding
    a full name first."""
    client = _panel_client(
        tmp_path, _candidates_db_file(tmp_path, seed=True), str(tmp_path / "missing")
    )

    resp = client.get("/graph")
    assert resp.status_code == 200
    for marker in (
        'id="symbol-search"',
        'role="combobox"',
        'aria-controls="symbol-suggest"',
        'id="symbol-suggest"',
        'role="listbox"',
        "suggest-wrap",
    ):
        assert marker in resp.text


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


def _await_prewarmed_probes(timeout_s: float = 30.0) -> dict:
    """Block until the probe cache's first population lands, then return it.

    A /health request that overlaps the probes' one-time imports is
    delivery-delayed far past FR-001's budget by GIL contention alone -- the
    handler's own work is bounded by the warm window, the wall clock is not
    -- so timing and verdict assertions wait for the prewarm to publish
    first. The cache is process-global; callers reset it
    (``reset_probe_cache``) before building the app so the wait covers this
    environment's own probes, not a previous test's.
    """
    import cairn.dashboard.data as dashboard_data

    deadline = time.monotonic() + timeout_s
    with dashboard_data._probe_cond:
        while dashboard_data._probe_cache is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pytest.fail("health probe prewarm never populated the cache")
            dashboard_data._probe_cond.wait(min(remaining, 0.01))
        return dashboard_data._probe_cache


def test_first_health_render_on_fresh_app_is_under_budget(tmp_path):
    """TC-001 / FR-001 / SC-1: on a fresh app instance (probe cache reset,
    startup prewarm armed) the first /health the app serves renders in under
    200ms server-side -- the request reads the warmed cache and pays neither
    the probe imports nor the warm-window wait."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app
    from cairn.dashboard.data import reset_probe_cache

    reset_probe_cache()
    db_path = _health_db_file(tmp_path, seed=True)
    client = TestClient(
        create_app(db_path=db_path, knowledge_dir=str(tmp_path / "knowledge"))
    )
    _await_prewarmed_probes()

    t0 = time.perf_counter()
    resp = client.get("/health")
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200
    assert "Database" in resp.text  # a real render, not an error page
    assert elapsed < 0.2, f"first /health took {elapsed:.3f}s (budget 0.2s)"


def test_health_route_shows_size_freshness_backend_and_reranker(tmp_path):
    """FR-008: one-glance panel carrying the DB size (human-readable), index
    freshness, backend mode, and reranker status from the seeded DB.

    The probe verdicts are asserted against the cache the request served,
    never against freshly recomputed live probes: with the prewarm design a
    /health request serves cached probe values, so live recomputation can
    legitimately disagree with what rendered (machines with the semantic
    extra installed report a different reranker verdict mid-warmup than the
    one the request served)."""
    from cairn.dashboard.data import reset_probe_cache

    reset_probe_cache()
    db_path = _health_db_file(tmp_path, seed=True)
    client = _panel_client(tmp_path, db_path, str(tmp_path / "knowledge"))
    _await_prewarmed_probes()

    resp = client.get("/health")
    assert resp.status_code == 200

    for label in (
        "Database",
        "Index freshness",
        "Embedding backend",
        "Vector index",
        "Reranker",
        "Retention",
    ):
        assert label in resp.text

    from cairn.dashboard.app import _human_size

    assert _human_size(os.stat(db_path).st_size) in resp.text
    assert "2026-08-20T07:00:00Z" in resp.text
    assert re.search(r"just now|\b\d+[smhd] old\b", resp.text)

    # conftest clears CAIRN_EMBED_BACKEND, so the local default is active.
    assert ">local<" in resp.text
    # The retention card renders the default policy (conftest clears the
    # CAIRN_TOOL_METRICS_* knobs, so both render from the documented
    # defaults, independent of the machine's probe verdicts).
    assert "tool_metrics cap 50000 rows" in resp.text
    assert "no age bound" in resp.text

    # What the request served is the published cache; the rendered verdicts
    # must match it on every machine class.
    import cairn.dashboard.data as dashboard_data

    served = dashboard_data._probe_cache
    assert served is not None
    assert ("hash fallback" in resp.text) == bool(served["hash_fallback"])
    expected_rerank = "available" if served["reranker_available"] else "unavailable"
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
# Live refresh, server half (live-updates FR-001 / FR-006, TC-001 / TC-007):
# the poll loop re-fetches the same URL and swaps #refresh-region, so the
# server contract beneath the client is pinned here -- the region + chrome
# render on /history, a row landed after one fetch is served by the next
# (TC-001's auto half), and consecutive fetches of the same filtered URL
# re-render the region byte-identically with each row exactly once
# (TC-007's server half: idempotent refresh, never duplicate rows). A
# non-traffic page carries no region, so the loop stays inert there. The
# DOMParser swap itself is app.js's half and has no JS runtime here.
# ---------------------------------------------------------------------------


class _RegionExtractor(HTMLParser):
    """Collects the verbatim inner HTML of the first div carrying the
    wanted id, balancing nested divs -- the exact content a poll cycle's
    fragment swap would replace. Entities are reassembled raw
    (convert_charrefs=False) so extraction is byte-faithful for equality
    checks."""

    def __init__(self, element_id: str):
        super().__init__(convert_charrefs=False)
        self._wanted = element_id
        self._depth = 0
        self.found = False
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self._depth == 0:
            if tag == "div" and dict(attrs).get("id") == self._wanted:
                self._depth = 1
                self.found = True
            return  # outside the region: not collected
        self.chunks.append(self.get_starttag_text())
        if tag == "div":
            self._depth += 1

    def handle_endtag(self, tag):
        if self._depth == 0:
            return
        if tag == "div":
            self._depth -= 1
            if self._depth == 0:
                return  # the region's own closing tag: never inner HTML
        self.chunks.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        if self._depth > 0:
            self.chunks.append(self.get_starttag_text())

    def handle_data(self, data):
        if self._depth > 0:
            self.chunks.append(data)

    def handle_entityref(self, name):
        if self._depth > 0:
            self.chunks.append(f"&{name};")

    def handle_charref(self, name):
        if self._depth > 0:
            self.chunks.append(f"&#{name};")


def _refresh_region(html: str) -> str | None:
    """Inner HTML of the page's #refresh-region, or None when the page
    carries none (the poll loop's guard-skip case)."""
    extractor = _RegionExtractor("refresh-region")
    extractor.feed(html)
    extractor.close()
    return "".join(extractor.chunks) if extractor.found else None


def test_history_wraps_table_in_refresh_region_with_live_chrome(tmp_path):
    """FR-001: /history renders exactly one #refresh-region wrapping the
    table -- the element the poll loop swaps -- and the shared
    #live-controls chrome server-renders its initial state (data-state
    "running", hidden until the loop takes over, with the state slot and
    pause control)."""
    resp = _history_client(tmp_path, seed=True).get("/history")
    assert resp.status_code == 200

    assert resp.text.count('id="refresh-region"') == 1
    region = _refresh_region(resp.text)
    assert region is not None
    assert '<table class="data-table">' in region  # rows live in the swap target
    assert "ask_compass" in region  # a seeded row, not just any table

    assert (
        '<div id="live-controls" class="muted" data-state="running" hidden>'
        in resp.text
    )
    assert 'id="live-state"' in resp.text
    assert 'id="live-pause"' in resp.text


def test_history_refetch_serves_row_landed_after_first_fetch(tmp_path):
    """TC-001 auto half (FR-001 / US1-AC1): a call that lands in the store
    after one fetch is served by the very next fetch of the same URL -- so
    the poll cycle's next tick renders it, newest-first at the top."""
    db_path = _history_db_file(tmp_path, seed=True)
    client = _panel_client(tmp_path, db_path, str(tmp_path / "missing"))

    first = client.get("/history")
    assert first.status_code == 200
    region = _refresh_region(first.text)
    assert region is not None
    assert "live_new_call" not in region

    # The newer call lands in the store (the sink's flush made it visible).
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status, req_chars, resp_chars) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("live_new_call", "sess-alpha", 1755650400.0, 90.0, "ok", 200, 400),
        )
        conn.commit()
    finally:
        conn.close()

    second = client.get("/history")  # the poll's next cycle re-fetches
    assert second.status_code == 200
    refetched = _refresh_region(second.text)
    assert refetched is not None
    assert "live_new_call" in refetched  # the new row is served
    assert "2025-08-20 00:40:00 UTC" in refetched  # its rendered identity
    # US1-AC1's top: newest-first places it above the previously-newest row.
    assert refetched.index("live_new_call") < refetched.index(
        "2025-08-20 00:25:00 UTC"
    )


def test_history_refetch_of_same_filtered_url_is_idempotent(tmp_path):
    """TC-007 server half (FR-006 / SC-2): two consecutive fetches of the
    same filtered URL re-render #refresh-region byte-identically -- the
    same rows, each exactly once (no duplicates, no ordering drift) -- so
    a swap-per-cycle can never accumulate repeated rows."""
    client = _history_client(tmp_path, seed=True)

    first = client.get("/history", params={"tool": "explore"})
    second = client.get("/history", params={"tool": "explore"})
    assert first.status_code == 200
    assert second.status_code == 200

    region_a = _refresh_region(first.text)
    region_b = _refresh_region(second.text)
    assert region_a is not None
    assert region_b is not None

    # Byte-identical re-render of the same slice: idempotent refresh.
    assert region_a == region_b

    # The same rows, each rendered exactly once -- never duplicated.
    for ts in ("2025-08-20 00:20:00 UTC", "2025-08-20 00:00:00 UTC"):
        assert region_a.count(ts) == 1
    assert "ask_compass" not in region_a  # the filter held across fetches


def test_projects_page_carries_no_refresh_region(tmp_path):
    """FR-001's guard-skip half: /projects is not a traffic view -- no
    #refresh-region renders there, so the poll loop stays inert on it."""
    resp = _client(tmp_path, seed=True).get("/projects")
    assert resp.status_code == 200
    assert _refresh_region(resp.text) is None


# ---------------------------------------------------------------------------
# Chains + tokens fragment growth, server half (live-updates FR-002 / US2,
# TC-003 / TC-004 auto halves): T008 wrapped both views' content in
# #refresh-region, so the existing poll loop re-fetches them too -- pinned
# here is what the poll's next cycle would render: the region wraps each
# view's content, a call landing in an open session grows that session's
# rendered chain by exactly one with no duplicate identities (TC-003), a
# new session's first call adds its chain at the top of the fragment, and
# a call with real payload sizes shifts the tokens aggregates the next
# fetch serves (TC-004) -- expected totals from the data layer on the same
# store plus the CHARS_PER_TOKEN arithmetic the view renders.
# ---------------------------------------------------------------------------


def _tokens_row(region: str, tool: str) -> str:
    """The rendered <tr> of ``tool`` inside a tokens region -- growth
    assertions scoped to the one tool they are about."""
    for row in region.split("<tr>")[1:]:
        if f">{tool}</a>" in row:  # the tool cell anchors to /history
            return row
    raise AssertionError(f"tokens row for {tool!r} missing from region")


def test_tokens_and_chains_wrap_their_content_in_refresh_region(tmp_path):
    """FR-002's region half: /tokens and /chains each render exactly one
    #refresh-region wrapping the content the poll loop swaps -- the tokens
    table and the chain list with seeded content inside the swap target,
    and app.js loaded exactly once so the loop is armed on these views."""
    client = _tokens_chains_client(tmp_path, seed=True)
    for path, structure, seeded in (
        ("/tokens", '<table class="data-table">', "tool_heavy"),
        ("/chains", '<div class="chain-list">', "sess-multi"),
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.text.count('id="refresh-region"') == 1, path
        region = _refresh_region(resp.text)
        assert region is not None, path
        assert structure in region, path  # the view lives in the swap target
        assert seeded in region, path  # seeded content, not empty markup
        assert (
            len(re.findall(r'<script[^>]*\ssrc="[^"]*app\.js[?"]', resp.text))
            == 1
        ), path  # the loop module loads once, never twice


def test_chains_refetch_grows_open_session_chain_by_exactly_one(tmp_path):
    """TC-003 auto half (FR-002 / US2-AC1): a call that lands in an open
    session after one fetch is served by the next -- that session's chain
    grows by exactly one call inside the swapped region, every rendered
    call identity exactly once, and still one chain for the session."""
    db_path = _tokens_chains_db_file(tmp_path, seed=True)
    client = _panel_client(tmp_path, db_path, str(tmp_path / "missing"))

    first = client.get("/chains")
    assert first.status_code == 200
    region = _refresh_region(first.text)
    assert region is not None
    assert len(_chain_blocks(region)) == 5  # the seeded page, unchanged
    multi = next(b for b in _chain_blocks(region) if "sess-multi" in b)
    assert "3 calls" in multi
    assert "seq_delta" not in region

    # The newer call lands in the SAME session, a minute after its last
    # one -- well inside the 30-min chain gap, so the chain grows.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status, req_chars, resp_chars) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("seq_delta", "sess-multi", _TC_BASE + 780, 40.0, "ok", 10, 10),
        )
        conn.commit()
    finally:
        conn.close()

    second = client.get("/chains")  # the poll's next cycle re-fetches
    refetched = _refresh_region(second.text)
    assert refetched is not None
    assert len(_chain_blocks(refetched)) == 5  # grew a chain, split none
    grown = next(b for b in _chain_blocks(refetched) if "sess-multi" in b)
    assert "4 calls" in grown  # exactly one more than the first fetch
    assert grown.index("seq_delta") > grown.index("seq_gamma")  # appended
    # No duplicate identities: each of the session's calls renders once.
    for tool in ("seq_alpha", "seq_beta", "seq_gamma", "seq_delta"):
        assert grown.count(tool) == 1, tool


def test_chains_refetch_new_session_chain_lands_at_fragment_top(tmp_path):
    """FR-002 / US2-AC1: the first call of a NEW session that lands after
    one fetch is served by the next as its own chain at the top of the
    fragment -- newest activity leads the chains page, so the swapped
    region gains a leading chain instead of disturbing the old ones."""
    db_path = _tokens_chains_db_file(tmp_path, seed=True)
    client = _panel_client(tmp_path, db_path, str(tmp_path / "missing"))

    first = client.get("/chains")
    assert first.status_code == 200
    region = _refresh_region(first.text)
    assert region is not None
    assert len(_chain_blocks(region)) == 5
    assert "sess-fresh" not in region

    # A new session's first call, newer than every seeded activity.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status, req_chars, resp_chars) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "fresh_call",
                "sess-fresh",
                _TC_BASE + 6 * 3600 + 3600.0,
                70.0,
                "ok",
                10,
                10,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    second = client.get("/chains")  # the poll's next cycle re-fetches
    refetched = _refresh_region(second.text)
    assert refetched is not None
    blocks = _chain_blocks(refetched)
    assert len(blocks) == 6  # the new chain, nothing merged away
    assert "sess-fresh" in blocks[0]  # the new chain leads the fragment
    assert "fresh_call" in blocks[0]
    assert "1 call" in blocks[0]  # singular: its only call so far
    # The seeded chains follow intact, the previous leader one slot down.
    assert "sess-gapped" in blocks[1] and "pair1_a" in blocks[1]
    assert "sess-gapped" in blocks[2] and "pair2_b" in blocks[2]


def test_tokens_refetch_shifts_call_count_and_displayed_totals(tmp_path):
    """TC-004 auto half (FR-002 / US2-AC2): a call with real payload sizes
    that lands for an existing tool after one fetch is served by the next
    -- the tool's calls count grows by one and its displayed token totals
    shift by exactly the new call's contribution, the expected aggregates
    from the data layer on the same store plus the CHARS_PER_TOKEN
    arithmetic the view renders."""
    from cairn.bench.agent_suite import CHARS_PER_TOKEN
    from cairn.dashboard.data import get_read_only_db, get_tool_tokens

    db_path = _tokens_chains_db_file(tmp_path, seed=True)
    client = _panel_client(tmp_path, db_path, str(tmp_path / "missing"))

    def store_row() -> dict:
        conn = get_read_only_db(db_path)
        try:
            return {
                e["tool_name"]: e for e in get_tool_tokens(conn)
            }["tool_heavy"]
        finally:
            conn.close()

    first = client.get("/tokens")
    assert first.status_code == 200
    region = _refresh_region(first.text)
    assert region is not None
    before = store_row()
    served = _tokens_row(region, "tool_heavy")
    assert f'<td class="num">{before["calls"]}</td>' in served
    assert f'<td class="num">~{before["total_tokens"]}</td>' in served

    # The new call lands for the existing tool with non-null sizes; the
    # fixture's heavy sums are whole tokens, so the per-total deltas are
    # exact under the SUM // CHARS_PER_TOKEN floor.
    new_req_chars, new_resp_chars = 800, 1600
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status, req_chars, resp_chars) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "tool_heavy",
                "sess-tok",
                _TC_BASE + 240,
                140.0,
                "ok",
                new_req_chars,
                new_resp_chars,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    second = client.get("/tokens")  # the poll's next cycle re-fetches
    refetched = _refresh_region(second.text)
    assert refetched is not None

    # The aggregate the next cycle must render: calls +1, each displayed
    # total shifted by the new call's CHARS_PER_TOKEN contribution.
    after = store_row()
    req_gain = new_req_chars // CHARS_PER_TOKEN
    resp_gain = new_resp_chars // CHARS_PER_TOKEN
    assert after["calls"] == before["calls"] + 1
    assert after["est_req_tokens"] == before["est_req_tokens"] + req_gain
    assert after["est_resp_tokens"] == before["est_resp_tokens"] + resp_gain
    assert after["total_tokens"] == before["total_tokens"] + req_gain + resp_gain

    grown = _tokens_row(refetched, "tool_heavy")
    assert f'<td class="num">{after["calls"]}</td>' in grown
    assert f'<td class="num">~{after["est_req_tokens"]}</td>' in grown
    assert f'<td class="num">~{after["est_resp_tokens"]}</td>' in grown
    assert f'<td class="num">~{after["total_tokens"]}</td>' in grown
    # The displayed total shifted, not accumulated beside the old one.
    assert f'<td class="num">~{before["total_tokens"]}</td>' not in grown


# ---------------------------------------------------------------------------
# Loop-module state machine (live-updates FR-004 / FR-005, TC-005 /
# TC-006): this repo has no JS test harness -- pytest only, and app.js is
# browser-global IIFEs, not importable modules -- so the loop's state
# behavior is pinned in its server-visible and structural halves: the
# chrome contract the loop reads and writes (the rendered state hooks plus
# exactly one app.js load, so the loop can never double-arm), and static
# analysis of the live-refresh IIFE's source asserting the control-flow
# ordering that makes paused-issues-no-fetch and rejected-then-resolved
# hold (the paused guard ahead of the visibility guard and the fetch, the
# setState words, the arm pattern). Every assertion anchors on those
# stable tokens only -- never exact surrounding strings -- so concurrent
# banner/styling work inside app.js cannot break them. The interactive
# halves (a real click, a really-dead server) are the LIVE_TC005 /
# LIVE_TC006 manual procedures at the section's end; those constants carry
# a LIVE_ prefix because this file already defines graph-nav's
# TC005_MANUAL_PROCEDURE -- each spec numbers its test cases
# independently, and ruff's F811 forbids the bare redefinition.
# ---------------------------------------------------------------------------


def _app_js_source() -> str:
    """app.js source, read from the installed dashboard package -- the
    file the /static route serves."""
    import cairn.dashboard

    return (
        Path(cairn.dashboard.__file__).resolve().parent / "static" / "app.js"
    ).read_text(encoding="utf-8")


_IIFE_OPEN_RE = re.compile(r"(?<![.\w])\(\s*function\s*\(\s*\)\s*\{")
_LIVE_REGION_RE = re.compile(r'getElementById\(\s*["\']refresh-region["\']')


def _live_loop_js() -> str:
    """The source following the live-refresh IIFE's opener -- the poll
    loop's home. The opener pattern excludes zero-arg promise handlers
    (.catch(function () {...})), which are not IIFEs; the segment is then
    identified by its getElementById("refresh-region") CODE call, not the
    region name in prose -- a neighboring IIFE's comments mention the
    region too."""
    for segment in _IIFE_OPEN_RE.split(_app_js_source())[1:]:
        if _LIVE_REGION_RE.search(segment):
            return segment
    raise AssertionError("app.js carries no #refresh-region poll loop")


def test_history_live_chrome_hooks_render_and_app_js_loads_once(tmp_path):
    """TC-005/TC-006 chrome contract (FR-004 / FR-005): /history renders
    the three stable hooks the loop's state machine reads and writes --
    #live-controls server-rendering its initial data-state="running", the
    #live-state word slot, the #live-pause toggle -- and loads app.js
    exactly once: a second copy of the loop would arm a second timer and
    double every fetch."""
    resp = _history_client(tmp_path, seed=True).get("/history")
    assert resp.status_code == 200

    controls = re.search(r'<div id="live-controls"[^>]*>', resp.text)
    assert controls, "#live-controls chrome missing from /history"
    assert 'data-state="running"' in controls.group(0)  # initial state
    assert 'id="live-state"' in resp.text  # the visible state-word slot
    assert 'id="live-pause"' in resp.text  # the pause/resume toggle

    loads = re.findall(r'<script[^>]*\ssrc="[^"]*app\.js[?"]', resp.text)
    assert len(loads) == 1  # the loop module loads once, never twice


def test_loop_tick_paused_guard_precedes_hidden_guard_and_fetch():
    """TC-005 auto half (FR-004 / US3-AC1): inside tick the paused guard
    leads -- ahead of the document.hidden guard and ahead of the fetch --
    so a paused loop issues no fetch regardless of tab state; and unlike
    a hidden tab it does not re-arm, so only the user's resume restarts
    the loop (no arm() between the paused guard and the hidden one)."""
    loop = _live_loop_js()
    tick = re.search(r"function\s+tick\s*\(\s*\)\s*\{", loop)
    assert tick, "tick function missing from the poll loop"
    body = loop[tick.end():]

    paused_guard = re.search(r"if\s*\(\s*paused\s*\)", body)
    hidden_guard = re.search(r"if\s*\(\s*document\.hidden\s*\)", body)
    fetch_call = re.search(r"\bfetch\s*\(", body)
    assert paused_guard, "tick lacks the paused guard"
    assert hidden_guard, "tick lacks the document.hidden guard"
    assert fetch_call, "tick never fetches"
    assert paused_guard.start() < hidden_guard.start() < fetch_call.start()

    # A paused tick must not re-arm -- that is what distinguishes it from
    # the hidden-tab skip just below it, which re-arms and stays alive.
    between = body[paused_guard.end():hidden_guard.start()]
    assert not re.search(r"\barm\s*\(", between)


def test_loop_pause_clears_timer_resume_restores_running_and_rearms():
    """TC-005 auto half (FR-004 / US3-AC1): the pause toggle's click
    handler is the state machine's pause half -- pausing clears the
    pending timer (so no already-armed tick can fetch) and lands the
    'paused' state word; the resume half restores 'running' and re-arms,
    so the loop returns on the normal schedule rather than never."""
    loop = _live_loop_js()

    toggle = re.search(r"addEventListener\(\s*[\"']click[\"']\s*,", loop)
    assert toggle, "the pause control's click handler is missing"

    set_paused = re.search(r"setState\(\s*[\"']paused[\"']\s*\)", loop)
    assert set_paused, "the loop never sets the 'paused' state"

    clear = re.search(r"clearTimeout\s*\(", loop[: set_paused.start()])
    assert clear, "pausing without clearing the timer -- a tick could fetch"
    assert toggle.start() < clear.start() < set_paused.start()

    after = loop[set_paused.end():]
    set_running = re.search(r"setState\(\s*[\"']running[\"']\s*\)", after)
    assert set_running, "the resume path never restores the 'running' state"
    running_at = set_paused.end() + set_running.start()
    assert re.search(r"\barm\s*\(", loop[running_at:]), (
        "the resume path does not re-arm -- the loop would never resume"
    )


def test_loop_failure_sets_disconnected_success_restores_running_live():
    """TC-006 auto half (FR-005 / US3-AC2): the loop's rejected-then-
    resolved transitions -- the fetch chain's rejection handler sets the
    distinct 'disconnected' state and still re-arms (self-healing: the
    next cycle retries), while the success handler restores 'running',
    whose visible word is 'live' per the STATE_WORDS table the state slot
    renders from."""
    loop = _live_loop_js()

    # The visible vocabulary: running shows as "live"; the disconnected
    # and paused words are their own states.
    words = re.search(r"STATE_WORDS\s*=\s*\{(.*?)\}", loop, re.S)
    assert words, "the loop's STATE_WORDS table is missing"
    for key, word in (
        ("running", "live"),
        ("disconnected", "disconnected"),
        ("paused", "paused"),
    ):
        assert re.search(rf"{key}\s*:\s*[\"']{word}[\"']", words.group(1)), (
            f"STATE_WORDS lost the {key} -> {word!r} mapping"
        )

    # Rejected: downstream of the fetch, the catch handler sets
    # 'disconnected' -- and re-arms, so recovery needs no reload.
    fetch = re.search(r"\bfetch\s*\(", loop)
    assert fetch, "the loop never fetches"
    catch = re.search(r"\.catch\s*\(", loop)
    assert catch, "the fetch chain has no rejection handler"
    set_disconnected = re.search(
        r"setState\(\s*[\"']disconnected[\"']\s*\)", loop
    )
    assert set_disconnected, "a failed cycle never sets 'disconnected'"
    assert fetch.start() < catch.start() < set_disconnected.start()
    assert re.search(r"\barm\s*\(", loop[set_disconnected.end():]), (
        "the disconnected path does not re-arm -- no self-healing recovery"
    )

    # Resolved: the success handler restores 'running' (the 'live' word),
    # ahead of the rejection handler in the chain's source order.
    set_running = re.search(
        r"setState\(\s*[\"']running[\"']\s*\)", loop[fetch.end():]
    )
    assert set_running, "a successful cycle never restores 'running'"
    running_at = fetch.end() + set_running.start()
    assert running_at < catch.start()


# TC-005/TC-006 interactive halves -- a real click on a live page, and a
# server that is really dead -- cannot run here (these route tests have no
# JS runtime); the procedures below mirror TC004_MANUAL_PROCEDURE and
# graph-nav's TC005_MANUAL_PROCEDURE above.

LIVE_TC005_MANUAL_PROCEDURE = """\
TC-005 manual half -- pause stops updates and is indicated (FR-004 /
US3-AC1). Run against a live dashboard (cairn serve) with traffic landing
(an agent session querying cairn, or any store that keeps growing):

1. Open /history with calls landing; rows appear on their own each
   refresh cycle (~5s) and the state word beside Pause reads "live".
2. Click Pause -- the state word flips to "paused" and the button label
   becomes Resume.
3. Leave the page open past at least two refresh cycles while traffic
   keeps landing -- the table must NOT change (no swap happens) and the
   "paused" word stays indicated the whole time.
4. Click Resume -- the label returns to Pause, the word returns to
   "live", and within one cycle every row that landed while paused
   appears (the loop resumed on its normal schedule, not never).
"""

LIVE_TC006_MANUAL_PROCEDURE = """\
TC-006 manual half -- disconnected state on an unreachable server,
self-healing on return (FR-005 / US3-AC2). Run against a live dashboard:

1. Start the dashboard (cairn serve) and open /history; the state word
   reads "live".
2. Stop the dashboard server process while keeping the page open.
3. Within one refresh cycle (~5s) the state word flips to "disconnected"
   and the disconnected indication appears (state/banner styling); the
   page itself stays rendered and usable -- no blank region, no crash.
4. Leave the page open across several cycles -- it stays disconnected
   and visibly so, never silently failing.
5. Restart the server -- on the first cycle after it is reachable again
   the disconnected indication clears, the word returns to "live", and
   fresh content resumes with no manual reload.
"""


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
    it (index.html). The sidebar nav anchors carry an inline svg icon, so
    the label rides a <span> inside the anchor."""
    client = _client(tmp_path, seed=False)

    def nav_anchor(html):
        # base.html nav: the /graph anchor with its spanned label
        return re.search(
            r'<a href="/graph"[^>]*>.*?>Graph</span>', html, re.S
        )

    landing = client.get("/")
    assert landing.status_code == 200
    assert nav_anchor(landing.text)
    # Landing launcher card: the anchor carries an svg + spanned title.
    assert re.search(
        r'<a class="launcher-card" href="/graph"[^>]*>.*?>Graph explorer'
        r"</span>",
        landing.text,
        re.S,
    )

    # The nav entry is base.html's, not landing-specific: another page
    # renders it too, so /graph is one click from anywhere.
    projects = client.get("/projects")
    assert projects.status_code == 200
    assert nav_anchor(projects.text)


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


# ---------------------------------------------------------------------------
# Workspaces overview (workspace-launcher FR-001 / FR-002, TC-001 / TC-002):
# /workspaces lists every local store (registry ∪ store dirs under
# CAIRN_HOME) with size, last-modified, and recorded call count; the four
# divergent states render with their state, never an error. The handler
# reads paths.CAIRN_HOME at request time (attribute lookup), so tests patch
# the module attribute -- the autouse env scrub never reaches it. The probe
# cap (FR-005's visibility half) rides probe_stores' module attribute
# patched BEFORE create_app binds it into the handler: the route's default
# cap is frozen into the def-time parameter, so this is the one seam.
# ---------------------------------------------------------------------------

# Store keys are 16 hex chars by layout (paths.store_key); literal keys keep
# the four states independently addressable and the capped pair ordered.
_WSK_POPULATED = "0123456789abcdef"
_WSK_ORPHAN = "2468ace02468ace0"
_WSK_UNREADABLE = "deadbeefdeadbeef"
_WSK_MISSING = "ffffffffffffffff"


def _seed_store_db(home: Path, key: str, calls: int) -> Path:
    """A real schema store at ``<home>/<key>/.kg`` with ``calls``
    tool_metrics rows -- the seeded-call-count convention of the history
    fixtures, applied to a store-dir layout the probe can open read-only."""
    from cairn.graph.schema import get_db

    kg = home / key / ".kg"
    kg.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(str(kg))
    try:
        conn.executemany(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status) VALUES (?, ?, ?, ?, ?)",
            [
                ("explore", "ws-sess", 1755648000.0 + i, 50.0, "ok")
                for i in range(calls)
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return kg


def _four_state_home(tmp_path) -> dict:
    """A CAIRN_HOME fixture covering TC-002's four states:

    populated -- real schema .kg, 2 recorded calls, registered workspace
    empty     -- orphan key dir with no .kg (unregistered marker expected)
    missing   -- registered key whose store dir does not exist
    unreadable -- junk-byte .kg: enumerates populated, probes unreadable
    """
    home = tmp_path / "cairn-home"
    home.mkdir()
    kg = _seed_store_db(home, _WSK_POPULATED, calls=2)
    (home / _WSK_ORPHAN).mkdir()
    junk = home / _WSK_UNREADABLE / ".kg"
    junk.parent.mkdir(parents=True)
    junk.write_bytes(b"definitely not a sqlite database\n" * 8)
    (home / "workspaces.json").write_text(
        json.dumps(
            {
                str(tmp_path / "ws" / "alpha"): _WSK_POPULATED,
                str(tmp_path / "ws" / "gone"): _WSK_MISSING,
            }
        ),
        encoding="utf-8",
    )
    return {"home": home, "kg": kg}


def _workspaces_client(tmp_path, monkeypatch, home: Path):
    """A TestClient whose /workspaces probes ``home``: the handler resolves
    paths.CAIRN_HOME per request, so patching the attribute is the seam
    (the launch db_path is never opened by this route)."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn import paths
    from cairn.dashboard.app import create_app

    monkeypatch.setattr(paths, "CAIRN_HOME", home)
    return TestClient(create_app(db_path=str(tmp_path / "dash.db")))


def _store_row(html: str, key: str) -> str:
    """The rendered <tr> of the store keyed ``key`` -- assertions scoped to
    the one store they are about."""
    for row in html.split("<tr>")[1:]:
        if f"<code>{key}</code>" in row:
            return row
    raise AssertionError(f"workspaces row for key {key!r} missing from page")


def test_workspaces_route_lists_store_stats_columns(tmp_path, monkeypatch):
    """FR-001 / TC-001: the populated store's row carries workspace identity,
    a nonzero store size, a last-modified timestamp, and the recorded
    tool-call count -- every overview column renders per store."""
    from cairn.dashboard.app import _human_size, _human_ts

    fixture = _four_state_home(tmp_path)
    resp = _workspaces_client(
        tmp_path, monkeypatch, fixture["home"]
    ).get("/workspaces")
    assert resp.status_code == 200

    for header in (
        "Workspace",
        "Store",
        "State",
        "Size",
        "Last modified",
        "Recorded calls",
    ):
        assert header in resp.text

    row = _store_row(resp.text, _WSK_POPULATED)
    assert str(tmp_path / "ws" / "alpha") in row  # identity verbatim
    assert _human_size(os.stat(fixture["kg"]).st_size) in row  # nonzero size
    assert _human_ts(os.stat(fixture["kg"]).st_mtime) in row  # last modified
    assert '<td class="num">2</td>' in row  # the 2 seeded tool_metrics rows


def test_workspaces_route_renders_all_four_states_without_error(
    tmp_path, monkeypatch
):
    """FR-002 / TC-002: populated, empty, missing, and unreadable stores each
    render with their state; the missing row keeps its registered path, the
    orphan carries the unregistered marker, the unreadable probe degrades
    the count to an em-dash -- and the page completes (200, no error)."""
    fixture = _four_state_home(tmp_path)
    resp = _workspaces_client(
        tmp_path, monkeypatch, fixture["home"]
    ).get("/workspaces")
    assert resp.status_code == 200

    for key, state in (
        (_WSK_POPULATED, "populated"),
        (_WSK_ORPHAN, "empty"),
        (_WSK_MISSING, "missing"),
        (_WSK_UNREADABLE, "unreadable"),
    ):
        assert f"<td>{state}</td>" in _store_row(resp.text, key), state

    # The registered-but-gone store still shows its workspace path verbatim.
    assert str(tmp_path / "ws" / "gone") in _store_row(resp.text, _WSK_MISSING)
    # The orphan dir shows the unregistered marker, never a fabricated path.
    assert "— (unregistered)" in _store_row(resp.text, _WSK_ORPHAN)
    # The corrupt .kg: count unknown (em-dash), not zero and not a 500.
    assert '<td class="num">—</td>' in _store_row(resp.text, _WSK_UNREADABLE)


def test_base_nav_leads_with_the_workspaces_link(tmp_path, monkeypatch):
    """FR-001: the shared base nav (base.html) carries the overview as its
    first entry, one click from every page. Sidebar nav anchors carry an
    inline svg icon, so the label rides a <span> inside the anchor."""
    fixture = _four_state_home(tmp_path)
    resp = _workspaces_client(
        tmp_path, monkeypatch, fixture["home"]
    ).get("/workspaces")
    assert resp.status_code == 200

    def anchor_pos(href, label):
        return re.search(
            r'<a href="' + href + r'"[^>]*>.*?>' + label + r"</span>",
            resp.text,
            re.S,
        )

    workspaces = anchor_pos("/workspaces", "Workspaces")
    projects = anchor_pos("/projects", "Projects")
    assert workspaces and projects
    # First entry: the overview anchor precedes every other nav view.
    assert workspaces.start() < projects.start()


def test_probe_cap_degrades_counts_visibly(tmp_path, monkeypatch):
    """FR-005's visibility half: past the probe-open budget a populated store
    renders the em-dash call count and the page carries the muted cap note --
    the degradation stays visible, never a hang or a silent zero. The route
    freezes probe_stores' default cap into its def-time parameter, so the
    module attribute is swapped BEFORE create_app binds it, forcing the
    two-store fixture down to one budgeted open."""
    home = tmp_path / "cairn-home"
    home.mkdir()
    _seed_store_db(home, "1111111111111111", calls=1)
    _seed_store_db(home, "2222222222222222", calls=3)

    from cairn.dashboard import workspaces as ws_module

    real_probe_stores = ws_module.probe_stores
    monkeypatch.setattr(
        ws_module,
        "probe_stores",
        lambda home_dir, entries: real_probe_stores(
            home_dir, entries, max_opens=1
        ),
    )
    resp = _workspaces_client(tmp_path, monkeypatch, home).get("/workspaces")
    assert resp.status_code == 200

    # Populated-first, then by key: the 1-store still gets its budgeted open;
    # the 2-store lands past the cap.
    first = _store_row(resp.text, "1111111111111111")
    second = _store_row(resp.text, "2222222222222222")
    assert '<td class="num">1</td>' in first
    assert '<td class="num">3</td>' not in second
    assert '<td class="num">—</td>' in second  # unknown, not zero
    assert "counts unavailable for some stores (probe cap)" in resp.text


# ---------------------------------------------------------------------------
# Workspace switching (workspace-launcher FR-003, TC-003 / TC-004): two
# seeded stores with distinct repos/tool calls/build stamps under a patched
# CAIRN_HOME plus a distinct launch store, all served by ONE TestClient --
# ?store=<key> serves that store's projects/history/health, no param serves
# the launch store, the three-way A -> overview -> B sequence tracks the
# selection without a restart, and the selection rides every inter-view
# link (deep-link carry). The missing-state keys (unknown, empty-state dir)
# render the friendly page. GAP-1's halves: a selected store's /graph page
# carries the selection to app.js (data-store hook) and the two fetch
# builders append it guarded -- the fetch itself is browser-side, so the
# app.js contract is pinned at the source level, like the loop tests above.
# ---------------------------------------------------------------------------

# 16-hex store keys (the layout convention the workspaces tests use).
_SW_KEY_A = "aaaaaaaaaaaaaaaa"
_SW_KEY_B = "bbbbbbbbbbbbbbbb"
_SW_KEY_EMPTY = "cccccccccccccccc"  # store dir with no .kg: the empty state
_SW_KEY_UNKNOWN = "dddddddddddddddd"  # names nothing on disk


def _seed_switch_store(
    path: Path, repo_id: str, tool_name: str, build_at: str, calls: int = 1
) -> Path:
    """A schema store at ``path`` distinguishable from every other store in
    the switching fixtures: one repo (``repo_id``), ``calls`` tool_metrics
    rows of ``tool_name``, and a build_run stamped ``build_at`` -- each of
    projects/history/health has its own per-store marker to assert on."""
    from cairn.graph.schema import get_db

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(str(path))
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
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"{repo_id}-s1", f"{repo_id}-f1", f"{repo_id}_fn",
             f"{repo_id}.core.{repo_id}_fn", "function"),
        )
        conn.executemany(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status) VALUES (?, ?, ?, ?, ?)",
            [
                (tool_name, f"sess-{repo_id}", 1755648000.0 + i, 50.0, "ok")
                for i in range(calls)
            ],
        )
        conn.execute(
            "INSERT INTO build_runs (kind, started_at) VALUES ('full', ?)",
            (build_at,),
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _switch_client(tmp_path, monkeypatch) -> tuple:
    """ONE TestClient over the launch store with paths.CAIRN_HOME pointed at
    the fixture home (the workspaces tests' attribute-lookup seam) -- the
    same app instance serves every store, FR-003's no-restart seam. Store A
    seeds enough calls (a page plus overflow) that its history paginates."""
    from cairn.dashboard.data import HISTORY_PAGE_SIZE

    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn import paths
    from cairn.dashboard.app import create_app

    home = tmp_path / "cairn-home"
    home.mkdir()
    _seed_switch_store(
        home / _SW_KEY_A / ".kg", "storeA", "store_a_tool",
        "2026-08-20T07:00:00Z", calls=HISTORY_PAGE_SIZE + 5,
    )
    _seed_switch_store(
        home / _SW_KEY_B / ".kg", "storeB", "store_b_tool",
        "2026-08-20T09:00:00Z", calls=3,
    )
    (home / _SW_KEY_EMPTY).mkdir()  # empty state: a key dir with no .kg
    launch_db = _seed_switch_store(
        tmp_path / "launch" / "dash.db", "launchproj", "launch_tool",
        "2026-08-20T08:00:00Z", calls=2,
    )
    monkeypatch.setattr(paths, "CAIRN_HOME", home)
    return TestClient(create_app(db_path=str(launch_db))), home


def test_store_param_serves_the_selected_workspace(tmp_path, monkeypatch):
    """FR-003 / US2-AC1 / TC-003: two seeded stores with distinct repos and
    tool calls; ?store=<keyA> serves A's projects/history/health and
    ?store=<keyB> serves B's, while no param serves the launch store -- one
    app instance, no restart."""
    client, home = _switch_client(tmp_path, monkeypatch)

    # Projects: the selected store's repo, none of the others'.
    projects_a = client.get("/projects", params={"store": _SW_KEY_A})
    projects_b = client.get("/projects", params={"store": _SW_KEY_B})
    assert projects_a.status_code == 200 and projects_b.status_code == 200
    assert "storeA" in projects_a.text
    assert "storeB" not in projects_a.text and "launchproj" not in (
        projects_a.text
    )
    assert "storeB" in projects_b.text
    assert "storeA" not in projects_b.text and "launchproj" not in (
        projects_b.text
    )

    # History: the selected store's tool calls only.
    history_a = client.get("/history", params={"store": _SW_KEY_A})
    history_b = client.get("/history", params={"store": _SW_KEY_B})
    assert "store_a_tool" in history_a.text and "store_b_tool" not in (
        history_a.text
    ) and "launch_tool" not in history_a.text
    assert "store_b_tool" in history_b.text and "store_a_tool" not in (
        history_b.text
    )

    # Health: the served store IS the selected one (its .kg path + build).
    health_a = client.get("/health", params={"store": _SW_KEY_A})
    health_b = client.get("/health", params={"store": _SW_KEY_B})
    assert str(home / _SW_KEY_A / ".kg") in health_a.text
    assert "2026-08-20T07:00:00Z" in health_a.text
    assert str(home / _SW_KEY_B / ".kg") in health_b.text
    assert "2026-08-20T09:00:00Z" in health_b.text

    # No param: the launch store, exactly as before the seam existed.
    plain = client.get("/projects")
    assert "launchproj" in plain.text
    assert "storeA" not in plain.text and "storeB" not in plain.text
    assert "launch_tool" in client.get("/history").text


def test_three_way_switch_sequence_tracks_selection(tmp_path, monkeypatch):
    """FR-003 / US2-AC2 / TC-004: A -> overview -> B on one TestClient (no
    server restart) -- each leg serves exactly the selected store's data and
    the overview leg between them still completes."""
    client, _ = _switch_client(tmp_path, monkeypatch)

    # Leg 1: select A.
    leg_a = client.get("/projects", params={"store": _SW_KEY_A})
    assert leg_a.status_code == 200
    assert "storeA" in leg_a.text and "storeB" not in leg_a.text

    # Leg 2: return to the overview -- it lists both stores, still one app.
    overview = client.get("/workspaces")
    assert overview.status_code == 200
    assert _SW_KEY_A in overview.text and _SW_KEY_B in overview.text

    # Leg 3: pick B -- the views switch with the selection.
    leg_b = client.get("/projects", params={"store": _SW_KEY_B})
    assert "storeB" in leg_b.text and "storeA" not in leg_b.text
    history_b = client.get("/history", params={"store": _SW_KEY_B})
    assert "store_b_tool" in history_b.text and "store_a_tool" not in (
        history_b.text
    )


def test_selected_store_rides_the_inter_view_links(tmp_path, monkeypatch):
    """FR-003 carry (GAP-2): on a selected store's pages every inter-view
    href keeps the selection -- history's session anchor, the Newer/Older
    paging links (followed Older page included), the window presets, the
    tokens tool anchor, and the graph layout link."""
    client, _ = _switch_client(tmp_path, monkeypatch)

    # History (store A paginates: a page plus overflow of seeded calls).
    history = client.get("/history", params={"store": _SW_KEY_A})
    assert history.status_code == 200
    assert (
        f'<a href="/chains?session=sess-storeA&amp;store={_SW_KEY_A}">'
        "sess-storeA</a>" in history.text
    )
    older = re.search(r'<a href="(/history\?before=[^"]*)">Older</a>', history.text)
    assert older, "Older link missing from the paginated selected store"
    assert f"store={_SW_KEY_A}" in older.group(1)
    # Following the link keeps the selection: the Older page's Newer link.
    page2 = client.get(older.group(1).replace("&amp;", "&"))
    assert page2.status_code == 200
    newer = re.search(r'<a href="(/history\?after=[^"]*)">Newer</a>', page2.text)
    assert newer, "Newer link missing from the Older page"
    assert f"store={_SW_KEY_A}" in newer.group(1)
    # The shared window presets carry it too (window_control partial).
    assert f"/history?window=24h&amp;store={_SW_KEY_A}" in history.text

    # Tokens: the tool anchor into filtered history carries the store.
    tokens = client.get("/tokens", params={"store": _SW_KEY_A})
    assert tokens.status_code == 200
    assert (
        f'<a href="/history?tool=store_a_tool&amp;store={_SW_KEY_A}">'
        "store_a_tool</a>" in tokens.text
    )

    # Graph: the layout link keeps scope AND store.
    graph = client.get("/graph", params={"store": _SW_KEY_A})
    assert graph.status_code == 200
    layout = re.search(r'<a href="([^"]*)" data-layout="hier">', graph.text)
    assert layout, "layout link missing from the selected store's graph"
    assert "scope=module" in layout.group(1)
    assert f"store={_SW_KEY_A}" in layout.group(1)


def test_unknown_and_empty_state_store_keys_render_missing_page(
    tmp_path, monkeypatch
):
    """FR-003's never-an-error edge: an unknown key (nothing on disk) and an
    empty-state key (store dir, no .kg) each render the friendly missing-DB
    page (200) -- never a 500, and never the launch store's data."""
    client, _ = _switch_client(tmp_path, monkeypatch)
    for key in (_SW_KEY_UNKNOWN, _SW_KEY_EMPTY):
        resp = client.get("/projects", params={"store": key})
        assert resp.status_code == 200, key
        assert "No graph database found" in resp.text, key
        assert "launchproj" not in resp.text, key


def test_graph_page_carries_the_selection_to_app_js(tmp_path, monkeypatch):
    """GAP-1 (FR-003): on a selected store's /graph the page carries the
    selection to the JS (the data-store hook on the graph-data block) and
    app.js's two fetch builders append it only when non-empty; the endpoints
    they hit honor the same param, so browser-side search/expand stays on
    the selected store. The launch-store page renders the tag byte-identical
    (no attribute)."""
    client, _ = _switch_client(tmp_path, monkeypatch)

    selected = client.get("/graph", params={"store": _SW_KEY_A})
    assert selected.status_code == 200
    assert (
        '<script id="graph-data" type="application/json" '
        f'data-store="{_SW_KEY_A}">' in selected.text
    )
    # No selection: the tag renders exactly as before the seam.
    plain = client.get("/graph")
    assert '<script id="graph-data" type="application/json">' in plain.text

    # The endpoints the builders hit serve the selected store's symbols.
    hit = client.get(
        "/graph/candidates", params={"store": _SW_KEY_A, "name": "storeA_fn"}
    )
    assert hit.status_code == 200
    assert [m["name"] for m in hit.json()["matches"]] == ["storeA_fn"]
    miss = client.get(
        "/graph/candidates", params={"store": _SW_KEY_A, "name": "storeB_fn"}
    )
    assert miss.json()["matches"] == []  # B's symbol is not in A's store

    # app.js source contract (no JS runtime here): both fetch builders
    # append the store param, read from the data-store hook, guarded.
    src = _app_js_source()
    for endpoint in ("/graph/neighbors?name=", "/graph/candidates?name="):
        builder = re.search(re.escape(endpoint) + r".{0,160}", src, re.S)
        assert builder, f"app.js lost the {endpoint!r} fetch builder"
        assert "storeKey" in builder.group(0), endpoint
    assert src.count('getElementById("graph-data")') >= 2
    assert src.count('getAttribute("data-store")') >= 2
    # The guard: an empty or absent selection appends nothing — every
    # store-appending site (fetch builders, focusUrl, inspect anchor)
    # follows the same guarded pattern.
    assert (
        len(
            re.findall(
                r'\? "&store=" \+ encodeURIComponent\(\w+\) : ""', src
            )
        )
        >= 3
    )
    assert 'inspectStore ? "&store=" + encodeURIComponent(inspectStore)' in src


# ---------------------------------------------------------------------------
# Mixed-source usage (cli-usage-recording FR-002, TC-003 / TC-004): CLI
# invocations land in tool_metrics as source='cli' rows named 'cli:<command>'
# beside source='mcp' tool rows (cli_metrics stamps 'cli'; MCP rows ride the
# table's DEFAULT 'mcp'). Pinned here at the route level: /history displays
# each row's source and ?source= narrows to that source's rows, composing
# with the tool/session filters and riding the Older paging link; /tokens
# aggregates the cli rows under their cli:* tool_name -- expected aggregates
# from the data layer (get_tool_tokens) on the same store plus the
# CHARS_PER_TOKEN arithmetic the view renders.
# ---------------------------------------------------------------------------

_MIXED_BASE = 1755648000.0  # 2025-08-20 00:00:00 UTC, like the history fixture


def _mixed_source_db_file(tmp_path, bulk: bool) -> str:
    """A graph-schema DB file seeded with BOTH sources' row shapes:

    cli (source='cli', what cli_metrics lands — NULL resp chars, a CLI
    invocation has no response payload): 'cli:cairn build' / term:shell-B @
    00:02:00 — ok, 800 req chars; 'cli:cairn config' / term:shell-A @
    00:01:30 — ok, 1600 req chars. mcp (source='mcp', the table default):
    'explore' / sess-alpha @ 00:01:00 — ok, 400/800 chars; 'ask_compass' /
    sess-beta @ 00:00:30 — ok, 200/400 chars. ``bulk=True`` adds
    HISTORY_PAGE_SIZE + 5 older mcp explore rows so ?source=mcp paginates.
    """
    from cairn.dashboard.data import HISTORY_PAGE_SIZE
    from cairn.graph.schema import _apply_schema

    rows = [
        # (tool, session, invoked_at, duration_ms, status, req, resp, source)
        ("ask_compass", "sess-beta", _MIXED_BASE + 30, 70.0, "ok", 200, 400, "mcp"),
        ("explore", "sess-alpha", _MIXED_BASE + 60, 50.0, "ok", 400, 800, "mcp"),
        ("cli:cairn config", "term:shell-A", _MIXED_BASE + 90, 100.0, "ok",
         1600, None, "cli"),
        ("cli:cairn build", "term:shell-B", _MIXED_BASE + 120, 9000.0, "ok",
         800, None, "cli"),
    ]
    if bulk:
        rows += [
            ("explore", "sess-bulk", _MIXED_BASE - 10 - i, 5.0, "ok", 10, 10, "mcp")
            for i in range(HISTORY_PAGE_SIZE + 5)
        ]

    db_path = str(tmp_path / "mixed-source.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.executemany(
        "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
        "duration_ms, status, req_chars, resp_chars, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def _mixed_source_client(tmp_path, bulk: bool):
    return _panel_client(
        tmp_path, _mixed_source_db_file(tmp_path, bulk), str(tmp_path / "missing")
    )


def test_history_route_displays_source_column_with_mixed_source_rows(tmp_path):
    """TC-003 / US1-AC2: with both sources recorded, /history renders the
    Source column -- 'cli' and 'mcp' both visible -- plus the Source filter
    input, with rows newest-first across sources."""
    resp = _mixed_source_client(tmp_path, bulk=False).get("/history")
    assert resp.status_code == 200

    assert "<th>Source</th>" in resp.text
    assert "<td>cli</td>" in resp.text and "<td>mcp</td>" in resp.text
    assert 'name="source"' in resp.text  # the filter input, like tool/session

    # Both sources' identities on one page, newest first across sources.
    newest_first = [
        "cli:cairn build",  # 00:02:00
        "cli:cairn config",  # 00:01:30
        "explore",  # 00:01:00
        "ask_compass",  # 00:00:30
    ]
    positions = [resp.text.index(tool) for tool in newest_first]
    assert positions == sorted(positions)


def test_history_route_source_filter_narrows_and_composes(tmp_path):
    """TC-003: ?source= narrows /history to that source's rows only, composes
    with the tool/session filters, and a no-match source is the empty state
    (HTTP 200, never an error) -- the same discipline as tool/session."""
    client = _mixed_source_client(tmp_path, bulk=False)

    cli_only = client.get("/history", params={"source": "cli"})
    assert cli_only.status_code == 200
    assert "cli:cairn build" in cli_only.text
    assert "cli:cairn config" in cli_only.text
    assert "explore" not in cli_only.text
    assert "ask_compass" not in cli_only.text
    assert 'value="cli"' in cli_only.text  # the form keeps the filter

    mcp_only = client.get("/history", params={"source": "mcp"})
    assert mcp_only.status_code == 200
    assert "explore" in mcp_only.text
    assert "ask_compass" in mcp_only.text
    assert "cli:" not in mcp_only.text  # the cli rows are filtered out
    assert 'value="mcp"' in mcp_only.text

    # Source AND session: the one cli row in that shell's session.
    by_session = client.get(
        "/history", params={"source": "cli", "session": "term:shell-A"}
    )
    assert by_session.status_code == 200
    assert "cli:cairn config" in by_session.text
    assert "cli:cairn build" not in by_session.text  # term:shell-B's row
    assert "explore" not in by_session.text

    # Source AND tool: the one mcp row with that tool name.
    by_tool = client.get("/history", params={"source": "mcp", "tool": "explore"})
    assert by_tool.status_code == 200
    assert "explore" in by_tool.text
    assert "ask_compass" not in by_tool.text
    assert "cli:" not in by_tool.text

    # A no-match source: the empty state, never an error.
    empty = client.get("/history", params={"source": "no-such-source"})
    assert empty.status_code == 200
    assert "No matching calls" in empty.text


def test_history_route_older_link_carries_the_active_source(tmp_path):
    """FR-002: paging composes with the source filter -- the Older link keeps
    ?source=mcp so the next page stays in-source instead of resuming
    unfiltered pagination into the cli rows."""
    client = _mixed_source_client(tmp_path, bulk=True)
    resp = client.get("/history", params={"source": "mcp"})
    assert resp.status_code == 200

    older = re.search(r'<a href="(/history\?before=[^"]*)">Older</a>', resp.text)
    assert older, "Older link missing from a source-filtered multi-page history"
    assert "source=mcp" in older.group(1)

    page2 = client.get(older.group(1).replace("&amp;", "&"))
    assert page2.status_code == 200
    # Dropping the source here would resume unfiltered pagination and reach
    # the cli rows; carrying it keeps every older page in-source.
    assert "cli:" not in page2.text
    assert "explore" in page2.text  # page 2 still carries the mcp bulk rows


def test_tokens_route_aggregates_include_cli_rows_under_cli_tool_name(tmp_path):
    """TC-004 / US2-AC1: cli rows with payload sizes join the /tokens
    aggregates under their cli:* tool_name (the source label the view
    renders), side by side with the mcp tools -- the rendered row matches
    the data layer's get_tool_tokens on the same store, CHARS_PER_TOKEN
    arithmetic included, and the cli rows' NULL resp chars contribute zero
    resp tokens rather than an error."""
    from cairn.bench.agent_suite import CHARS_PER_TOKEN
    from cairn.dashboard.data import get_read_only_db, get_tool_tokens

    db_path = _mixed_source_db_file(tmp_path, bulk=False)
    client = _panel_client(tmp_path, db_path, str(tmp_path / "missing"))

    conn = get_read_only_db(db_path)
    try:
        aggregates = {e["tool_name"]: e for e in get_tool_tokens(conn)}
    finally:
        conn.close()

    # Data layer: the cli rows aggregate under their cli:* names -- no
    # separate bucketing, no drop -- beside the mcp tools.
    assert set(aggregates) >= {
        "cli:cairn config",
        "cli:cairn build",
        "explore",
        "ask_compass",
    }
    config = aggregates["cli:cairn config"]
    assert config["calls"] == 1
    assert config["est_req_tokens"] == 1600 // CHARS_PER_TOKEN
    assert config["est_resp_tokens"] == 0  # NULL resp chars -> zero tokens
    assert config["total_tokens"] == 1600 // CHARS_PER_TOKEN

    # Route: the same row renders under that name (the anchor's visible
    # label; its href carries the urlencoded tool name), expected cells
    # beside it.
    resp = client.get("/tokens")
    assert resp.status_code == 200
    assert (
        '<a href="/history?tool=cli%3Acairn%20config">cli:cairn config</a>'
        in resp.text
    )
    row = _tokens_row(_refresh_region(resp.text), "cli:cairn config")
    assert f'<td class="num">{config["calls"]}</td>' in row
    assert f'<td class="num">~{config["est_req_tokens"]}</td>' in row
    assert f'<td class="num">~{config["est_resp_tokens"]}</td>' in row
    assert f'<td class="num">~{config["total_tokens"]}</td>' in row

    # The label is functional: following the tool filter lists only the cli
    # row it names (the space/colon name round-trips through the param).
    drilled = client.get("/history", params={"tool": "cli:cairn config"})
    assert drilled.status_code == 200
    assert "cli:cairn config" in drilled.text
    assert "cli:cairn build" not in drilled.text
    assert "explore" not in drilled.text


# ---------------------------------------------------------------------------
# Embedding degradation banner (FR-013 / US3-AC3's dashboard surface): the
# banner context is dashboard-process observability — this process's cached
# ladder verdict plus one once-per-process uncached server probe — carried
# onto every page through base.html's shared include, which renders zero
# bytes when healthy so pages stay byte-identical.
# ---------------------------------------------------------------------------

# The include site in base.html: a healthy banner renders nothing, so the
# bytes between <main> and each view's first element stay the pre-banner
# ones (all three target views open their content block with .panel).
_BANNER_JUNCTION = '<main class="site-main">\n      \n<section class="panel">'

_BANNER_PAGES = ("/graph", "/history", "/memory")


@pytest.fixture
def _isolated_embed_state(monkeypatch):
    """Swap the embed modules' process-wide caches for throwaway dicts so a
    banner test can neither read nor leak ladder/backend verdicts (env vars
    ride monkeypatch; the swapped plain dicts are restored on teardown)."""
    from cairn.graph import embed_ladder, embeddings

    monkeypatch.setattr(embed_ladder, "_LADDER_CACHE", {"state": None})
    monkeypatch.setattr(embed_ladder, "_DEGRADATION_NOTIFIED", set())
    monkeypatch.setattr(embeddings, "_EFFECTIVE_BACKEND_CACHE", {"effective": None})
    monkeypatch.setattr(embeddings, "_SERVER_PROBE_CACHE", {"available": None})


def _server_banner_env(monkeypatch):
    """Server-family embed config whose real endpoints are never reached."""
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("CAIRN_EMBED_SERVER_MODEL", "gone-model")


def _probe_stub(monkeypatch, verdict: bool) -> list:
    """Counting stand-in for the uncached server probe (returns verdict)."""
    from cairn.graph import embeddings

    calls = []

    def probe():
        calls.append(True)
        return verdict

    monkeypatch.setattr(embeddings, "_run_server_probe", probe)
    return calls


def _banner_client(tmp_path):
    return _panel_client(
        tmp_path, _graph_db_file(tmp_path, seed=True), str(tmp_path / "missing")
    )


def test_embed_banner_absent_and_byte_identical_for_non_server_backend(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """FR-013's healthy half, non-server backend: the banner machinery never
    probes and never renders — every main page keeps the pre-banner bytes
    (the include site emits nothing between <main> and the view)."""
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "local")
    probe_calls = _probe_stub(monkeypatch, True)
    client = _banner_client(tmp_path)

    for path in _BANNER_PAGES:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "embed-banner" not in resp.text, path
        assert _BANNER_JUNCTION in resp.text, path
    assert probe_calls == []  # the backend gate precedes any probe


def test_embed_banner_healthy_server_probe_renders_nothing(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """FR-013's healthy half, server backend: one uncached probe answers for
    the whole dashboard process (never per request), and its healthy verdict
    renders no banner on any page."""
    _server_banner_env(monkeypatch)
    probe_calls = _probe_stub(monkeypatch, True)
    client = _banner_client(tmp_path)

    for path in _BANNER_PAGES:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "embed-banner" not in resp.text, path
        assert _BANNER_JUNCTION in resp.text, path
    assert len(probe_calls) == 1  # once per dashboard process


def test_embed_probe_runs_once_and_banner_serves_every_request(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """A failed first probe seeds the ladder once per dashboard process: the
    banner then serves from the cached verdict — present on every later
    request with the probe never re-run."""
    _server_banner_env(monkeypatch)
    from cairn.graph import embed_ladder

    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", lambda: [])
    probe_calls = _probe_stub(monkeypatch, False)
    client = _banner_client(tmp_path)

    for _ in range(2):
        resp = client.get("/graph")
        assert resp.status_code == 200
        assert 'class="embed-banner" role="status"' in resp.text
    assert len(probe_calls) == 1


@pytest.mark.parametrize("path", _BANNER_PAGES)
def test_embed_banner_names_rung_reason_and_remediation_on_every_page(
    tmp_path, monkeypatch, _isolated_embed_state, path
):
    """FR-013 / US3-AC3: with the server backend degraded (probe fails, no
    served replacement), every target page carries the banner naming the
    rung, the reason, and the actionable remediation."""
    _server_banner_env(monkeypatch)
    from cairn.graph import embed_ladder

    monkeypatch.setattr(embed_ladder, "_fetch_model_listing", lambda: [])
    _probe_stub(monkeypatch, False)
    resp = _banner_client(tmp_path).get(path)
    assert resp.status_code == 200
    assert 'class="embed-banner" role="status"' in resp.text
    assert "Embedding backend degraded" in resp.text
    assert "rung 3" in resp.text
    assert "model_missing" in resp.text
    assert "CAIRN_EMBED_SERVER_MODEL" in resp.text  # the remediation


def test_embed_banner_prefers_this_process_ladder_verdict(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """An active ladder verdict in this process answers the banner directly —
    the probe never runs (the degradation is already established)."""
    _server_banner_env(monkeypatch)
    from cairn.graph import embed_ladder

    monkeypatch.setattr(
        embed_ladder,
        "_LADDER_CACHE",
        {
            "state": embed_ladder.LadderState(
                1,
                "fallback_session_alias",
                "adopted server model 'cand' for this session after parity "
                "pass; make permanent: cairn embed --adopt-server-model cand",
                "cand",
                True,
            )
        },
    )
    probe_calls = _probe_stub(monkeypatch, True)
    resp = _banner_client(tmp_path).get("/history")
    assert resp.status_code == 200
    assert 'class="embed-banner" role="status"' in resp.text
    assert "rung 1" in resp.text
    assert "fallback_session_alias" in resp.text
    assert "--adopt-server-model cand" in resp.text  # the remediation
    assert probe_calls == []


# ---------------------------------------------------------------------------
# Dashboard Settings section (FR-011 / US4-AC1, AC2): the app's first POST
# routes. Save persists through paths.set_config_values into the conftest-
# sandboxed CONFIG_FILE; the base-URL change needs its explicit confirm step;
# the API key is write-only (never rendered back); env-pinned keys show the
# override marker while the file write still lands (D-008); the parity-check
# action renders the check_parity verdict and never raises into the response.
# ---------------------------------------------------------------------------


def _settings_client(tmp_path):
    return _panel_client(
        tmp_path, _graph_db_file(tmp_path, seed=False), str(tmp_path / "missing")
    )


def _saved_config() -> dict:
    """The sandboxed config file's parsed contents ({} when absent)."""
    from cairn import paths

    if not paths.CONFIG_FILE.exists():
        return {}
    return json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))


def test_settings_get_renders_effective_values_and_env_markers(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """GET /settings prefills the form with effective values and marks every
    env-pinned key as overridden: a file value stays in its input while the
    marker names the env value that actually resolves (D-008)."""
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.setenv("CAIRN_EMBED_SERVER_MODEL", "env-model")
    _probe_stub(monkeypatch, True)  # the banner's once-per-process probe
    from cairn import paths

    paths.set_config_values({"CAIRN_EMBED_SERVER_MODEL": "file-model"})
    client = _settings_client(tmp_path)

    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Settings" in resp.text
    # Backend pinned by env, nothing in the file: the form prefills the
    # effective value and the marker names the override.
    assert "overridden by environment" in resp.text
    assert '<option value="server" selected>' in resp.text
    # Model pinned by env with a file value: the input keeps the file value
    # (the form edits the file layer) and the marker shows the effective one.
    assert 'value="file-model"' in resp.text
    assert "env-model" in resp.text
    # API key is write-only: status only, never a prefilled value.
    assert ">not set<" in resp.text


def test_settings_save_persists_to_config_file_and_reflects_state(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """POST /settings/save writes the submitted knobs as strings into the
    sandboxed CONFIG_FILE and re-renders the page reflecting the saved
    state."""
    _probe_stub(monkeypatch, True)  # saving server-family backend + re-render
    client = _settings_client(tmp_path)

    resp = client.post(
        "/settings/save",
        data={
            "CAIRN_EMBED_BACKEND": "omlx",
            "CAIRN_EMBED_SERVER_MODEL": "bge-m3",
            "CAIRN_EMBED_TIMEOUT": "45",
            "CAIRN_EMBED_SERVER_BATCH": "64",
            "CAIRN_EMBED_MODEL_STAMP": "my-alias",
        },
    )
    assert resp.status_code == 200
    assert "Settings saved" in resp.text

    saved = _saved_config()
    assert saved["CAIRN_EMBED_BACKEND"] == "omlx"
    assert saved["CAIRN_EMBED_SERVER_MODEL"] == "bge-m3"
    assert saved["CAIRN_EMBED_TIMEOUT"] == "45"  # strings: what D-008 resolves
    assert saved["CAIRN_EMBED_SERVER_BATCH"] == "64"
    assert saved["CAIRN_EMBED_MODEL_STAMP"] == "my-alias"

    # The page reflects the saved state: inputs re-render the file values.
    assert 'value="bge-m3"' in resp.text
    assert 'value="my-alias"' in resp.text
    assert '<option value="omlx" selected>' in resp.text


def test_settings_base_url_change_without_confirm_is_refused(tmp_path):
    """US4-AC2: a base-URL change attempt without the explicit confirm step
    is refused with an error on the page and the config left unchanged."""
    from cairn import paths

    paths.set_config_values({"CAIRN_EMBED_BASE_URL": "http://before:8000/v1"})
    client = _settings_client(tmp_path)

    resp = client.post(
        "/settings/save",
        data={"CAIRN_EMBED_BASE_URL": "http://after:9000/v1"},
    )
    assert resp.status_code == 400
    assert "Refused" in resp.text
    assert "confirm" in resp.text.lower()
    assert _saved_config()["CAIRN_EMBED_BASE_URL"] == "http://before:8000/v1"


def test_settings_base_url_change_with_confirm_is_saved(tmp_path):
    from cairn import paths

    paths.set_config_values({"CAIRN_EMBED_BASE_URL": "http://before:8000/v1"})
    client = _settings_client(tmp_path)

    resp = client.post(
        "/settings/save",
        data={
            "CAIRN_EMBED_BASE_URL": "http://after:9000/v1",
            "confirm_base_url": "1",
        },
    )
    assert resp.status_code == 200
    assert "Settings saved" in resp.text
    assert _saved_config()["CAIRN_EMBED_BASE_URL"] == "http://after:9000/v1"


def test_settings_base_url_unchanged_value_saves_without_confirm(tmp_path):
    """The confirm gate scopes to CHANGES: submitting the base URL it already
    has (or blank over blank) needs no checkbox, so other fields stay
    savable without re-confirming a URL nobody touched."""
    from cairn import paths

    paths.set_config_values({"CAIRN_EMBED_BASE_URL": "http://keep:8000/v1"})
    client = _settings_client(tmp_path)

    resp = client.post(
        "/settings/save",
        data={
            "CAIRN_EMBED_SERVER_MODEL": "bge-m3",
            "CAIRN_EMBED_BASE_URL": "http://keep:8000/v1",
        },
    )
    assert resp.status_code == 200
    assert "Settings saved" in resp.text
    saved = _saved_config()
    assert saved["CAIRN_EMBED_BASE_URL"] == "http://keep:8000/v1"
    assert saved["CAIRN_EMBED_SERVER_MODEL"] == "bge-m3"


def test_settings_api_key_is_write_only(tmp_path):
    """US4-AC2: a submitted key is stored but never rendered back — not in
    the save response, not in a later GET; the page shows set/not-set only,
    and a blank submit leaves the stored key untouched."""
    client = _settings_client(tmp_path)

    saved_resp = client.post(
        "/settings/save",
        data={
            "CAIRN_EMBED_API_KEY": "sk-supersecret-123",
            "CAIRN_EMBED_BACKEND": "hash",
        },
    )
    assert saved_resp.status_code == 200
    assert _saved_config()["CAIRN_EMBED_API_KEY"] == "sk-supersecret-123"
    assert "sk-supersecret-123" not in saved_resp.text

    page = client.get("/settings")
    assert page.status_code == 200
    assert "sk-supersecret-123" not in page.text
    assert ">set<" in page.text
    assert ">not set<" not in page.text

    blank = client.post("/settings/save", data={"CAIRN_EMBED_API_KEY": ""})
    assert blank.status_code == 200
    assert _saved_config()["CAIRN_EMBED_API_KEY"] == "sk-supersecret-123"


def test_settings_env_pin_shows_marker_while_save_persists_file_value(
    tmp_path, monkeypatch
):
    """D-008: an env var pinning a key shows the override marker with the
    effective value, and saving still persists the file value — env wins at
    resolution time, not at write time."""
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")  # keep the banner inert
    monkeypatch.setenv("CAIRN_EMBED_TIMEOUT", "99")
    client = _settings_client(tmp_path)

    resp = client.post("/settings/save", data={"CAIRN_EMBED_TIMEOUT": "120"})
    assert resp.status_code == 200
    assert _saved_config()["CAIRN_EMBED_TIMEOUT"] == "120"

    page = client.get("/settings")
    assert "overridden by environment" in page.text
    assert "<code>99</code>" in page.text  # the effective value, in the marker
    assert 'value="120"' in page.text  # the persisted file value, in the form

    from cairn.graph import embeddings

    assert embeddings._config_or_env("CAIRN_EMBED_TIMEOUT") == "99"


def test_settings_parity_check_renders_pass_and_fail_verdicts(
    tmp_path, monkeypatch
):
    """The parity-check action renders check_parity's verdict — pass/fail
    badge, measured mean cosine, sampled count, reason — for both verdicts."""
    from cairn.graph import embed_ladder

    client = _settings_client(tmp_path)

    monkeypatch.setattr(
        embed_ladder,
        "check_parity",
        lambda conn, stamp, embed_fn=None: embed_ladder.ParityResult(
            16, 0.995, True, True, "parity_ok"
        ),
    )
    passed = client.post("/settings/parity-check")
    assert passed.status_code == 200
    assert ">pass<" in passed.text
    assert "0.9950" in passed.text
    assert "sampled 16 chunks" in passed.text
    assert "parity_ok" in passed.text

    monkeypatch.setattr(
        embed_ladder,
        "check_parity",
        lambda conn, stamp, embed_fn=None: embed_ladder.ParityResult(
            16, 0.42, True, False, "below_gate"
        ),
    )
    failed = client.post("/settings/parity-check")
    assert failed.status_code == 200
    assert ">fail<" in failed.text
    assert "0.4200" in failed.text
    assert "below_gate" in failed.text


def test_settings_parity_check_embed_error_renders_failure_text(
    tmp_path, monkeypatch
):
    """The check never raises into the response: an embed error inside
    check_parity renders as the check's failure verdict."""
    from cairn.graph import embed_ladder

    def boom(conn, stamp, embed_fn=None):
        raise RuntimeError("server down")

    monkeypatch.setattr(embed_ladder, "check_parity", boom)
    resp = _settings_client(tmp_path).post("/settings/parity-check")
    assert resp.status_code == 200
    assert ">fail<" in resp.text
    assert "RuntimeError: server down" in resp.text


def test_settings_refuses_non_numeric_timeout_without_writing(tmp_path):
    """A non-numeric timeout/batch is refused before any write: the server
    client casts these unguarded, so the form must never persist garbage."""
    client = _settings_client(tmp_path)

    resp = client.post("/settings/save", data={"CAIRN_EMBED_TIMEOUT": "soon"})
    assert resp.status_code == 400
    assert "Refused" in resp.text
    assert "CAIRN_EMBED_TIMEOUT" in resp.text
    assert _saved_config() == {}


def test_settings_save_write_failure_renders_error_and_keeps_config(
    tmp_path, monkeypatch
):
    """A failed config write (set_config_values -> False) renders the 500
    error page naming the file, and the stored config is untouched."""
    from cairn import paths

    paths.set_config_values({"CAIRN_EMBED_SERVER_MODEL": "keep-me"})
    client = _settings_client(tmp_path)
    monkeypatch.setattr(paths, "set_config_values", lambda values: False)

    resp = client.post(
        "/settings/save", data={"CAIRN_EMBED_SERVER_MODEL": "new-value"}
    )
    assert resp.status_code == 500
    assert "Could not write" in resp.text
    assert "nothing was saved" in resp.text
    assert _saved_config()["CAIRN_EMBED_SERVER_MODEL"] == "keep-me"


def test_settings_blank_submit_keeps_stored_file_value(tmp_path):
    """A blank field submit means "no change", never a write of "": the
    stored SERVER_MODEL survives a blank submit untouched (a knob is
    cleared by editing config.json, not silently blanked from the form)."""
    from cairn import paths

    paths.set_config_values({"CAIRN_EMBED_SERVER_MODEL": "file-model"})
    client = _settings_client(tmp_path)

    resp = client.post("/settings/save", data={"CAIRN_EMBED_SERVER_MODEL": ""})
    assert resp.status_code == 200
    assert _saved_config()["CAIRN_EMBED_SERVER_MODEL"] == "file-model"
    assert 'value="file-model"' in resp.text


def test_settings_routes_leave_existing_routes_get_only(tmp_path):
    """The new POST routes are POST-only and the existing GET views keep
    their read-only method surface (the suite's read-only assumption)."""
    client = _settings_client(tmp_path)

    assert client.get("/settings").status_code == 200
    assert client.post("/settings").status_code == 405
    assert client.get("/settings/save").status_code == 405
    assert client.get("/settings/parity-check").status_code == 405
    landing = client.get("/")
    assert landing.status_code == 200
    assert client.post("/").status_code == 405


# ---------------------------------------------------------------------------
# Embeddings status view (FR-011 / US4-AC3): GET /embeddings renders the
# effective backend, resolved stamp, per-corpus counts + last-embedded
# times, the dashboard-process probe verdict, and the active fallback rung
# as structured rows. The rung rows read ladder_state() — the SAME
# accessor the FR-013 banner text builds from — and the probe rides the
# banner's once-per-process seam; per-knob effective values carry the
# D-008 env-override marker.
# ---------------------------------------------------------------------------


def _embeddings_client(tmp_path):
    """A TestClient over a seeded graph store (3 code embeddings)."""
    return _panel_client(
        tmp_path, _graph_db_file(tmp_path, seed=True), str(tmp_path / "missing")
    )


def test_embeddings_status_renders_backend_stamp_counts_and_freshness(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """US4-AC3 data half: the status view renders the effective backend,
    the resolved stamp, and per-corpus row counts + last-embedded times
    from the selected store — counts are per current model (a stale-model
    row does not count), and an empty corpus renders 0 / never, not an
    error."""
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "local")
    from cairn.graph import embeddings

    db_path = _graph_db_file(tmp_path, seed=True)
    conn = sqlite3.connect(db_path)
    try:
        # Current-model rows: the fixture's 3 rows carry a stale stamp, so
        # only this one counts — the invalidation semantic, pinned.
        conn.execute(
            "INSERT INTO embeddings "
            "(symbol_id, model, dim, vec, chunk, embedded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("s1", embeddings.current_model(), 8, b"", "fresh",
             "2026-08-21T09:00:00"),
        )
        conn.executemany(
            "INSERT INTO knowledge_embeddings "
            "(doc_id, chunk_index, model, dim, vec, chunk, embedded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("k1", 0, embeddings.current_model(corpus="knowledge"), 8, b"",
                 "k", "2026-08-20T12:00:00"),
                ("k2", 0, embeddings.current_model(corpus="knowledge"), 8, b"",
                 "k", "2026-08-20T12:30:00"),
            ],
        )
        # memory stays empty: 0 rows / never — the graceful empty corpus.
        conn.commit()
    finally:
        conn.close()

    client = _panel_client(tmp_path, db_path, str(tmp_path / "missing"))
    resp = client.get("/embeddings")
    assert resp.status_code == 200
    assert (
        '<th scope="row">Effective backend</th><td><code>local</code></td>'
        in resp.text
    )
    assert embeddings.current_model() in resp.text  # resolved stamp
    assert '<td class="num">1</td>' in resp.text  # code: current model only
    assert '<td class="num">2</td>' in resp.text  # knowledge rows
    assert '<td class="num">0</td>' in resp.text  # memory: a zero, not unknown
    assert "2026-08-21T09:00:00" in resp.text  # MAX(code embedded_at)
    assert "2026-08-20T12:30:00" in resp.text  # MAX(knowledge embedded_at)
    assert "never" in resp.text  # memory has never embedded


def test_embeddings_status_healthy_server_probe_and_rung(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """US4-AC3 healthy half: a server-family backend with a healthy
    dashboard-process probe renders the probe-healthy row and the healthy
    fallback row — one probe per dashboard process, never per request."""
    _server_banner_env(monkeypatch)
    probe_calls = _probe_stub(monkeypatch, True)
    client = _embeddings_client(tmp_path)

    first = client.get("/embeddings")
    assert first.status_code == 200
    assert "server/127.0.0.1:1/gone-model" in first.text  # resolved stamp
    assert '<span class="badge badge-ok">healthy</span>' in first.text
    assert "no active fallback" in first.text
    client.get("/embeddings")
    assert len(probe_calls) == 1  # the shared once-per-process probe


def test_embeddings_status_degraded_rung_rows_from_ladder_state(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """US4-AC3 degraded half: an active ladder verdict renders structured
    rung/reason/detail rows sourced from ladder_state() — the same
    accessor the FR-013 banner text builds from — while the page carries
    the one shared banner and introduces no second banner builder."""
    _server_banner_env(monkeypatch)
    from cairn.graph import embed_ladder

    monkeypatch.setattr(
        embed_ladder,
        "_LADDER_CACHE",
        {
            "state": embed_ladder.LadderState(
                1,
                "fallback_session_alias",
                "adopted server model 'cand' for this session after parity "
                "pass; make permanent: cairn embed --adopt-server-model cand",
                "cand",
                True,
            )
        },
    )
    _probe_stub(monkeypatch, True)
    resp = _embeddings_client(tmp_path).get("/embeddings")
    assert resp.status_code == 200

    # The shared banner renders once, from the base-level partial.
    assert resp.text.count('class="embed-banner"') == 1
    # The structured rows come from ladder_state(), not a second builder.
    assert '<th scope="row">Rung detail</th>' in resp.text
    assert ">fallback_session_alias</code>" in resp.text
    assert "--adopt-server-model cand" in resp.text  # the detail row
    assert "<code>cand</code>" in resp.text  # the adopted-model row
    # No duplicate builder: the view's template never builds banner text.
    template = (_templates_dir() / "embeddings.html").read_text(encoding="utf-8")
    assert "degradation_banner" not in template


def test_embeddings_status_shows_effective_value_and_env_override_marker(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """D-008 transparency: an env-pinned knob renders its effective value
    beside the override marker, and the API key never renders a value —
    set/not-set only."""
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")  # keeps the probe inert
    monkeypatch.setenv("CAIRN_EMBED_TIMEOUT", "99")
    monkeypatch.setenv("CAIRN_EMBED_API_KEY", "sk-status-secret")
    from cairn import paths

    paths.set_config_values({"CAIRN_EMBED_TIMEOUT": "120"})
    resp = _embeddings_client(tmp_path).get("/embeddings")
    assert resp.status_code == 200
    assert "overridden by environment" in resp.text
    assert "<code>99</code>" in resp.text  # effective beats the file's 120
    assert "sk-status-secret" not in resp.text  # key: set/not-set only
    assert ">set<" in resp.text


def test_nav_carries_settings_and_embeddings_entries(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """FR-011: /settings and /embeddings are one click from every page —
    the base sidebar nav carries both entries (with spanned labels, like
    every nav anchor)."""
    client = _client(tmp_path, seed=False)
    resp = client.get("/")
    assert resp.status_code == 200
    for href, label in (("/embeddings", "Embeddings"), ("/settings", "Settings")):
        assert re.search(
            r'<a href="' + href + r'"[^>]*>.*?' + label + r"</span>",
            resp.text,
            re.S,
        ), href


def test_embeddings_status_non_server_marks_probe_na_without_probe(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """Non-server backends never probe: the probe-health row renders n/a,
    the shared probe seam is never reached, and the fallback row reads
    healthy."""
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "local")
    probe_calls = _probe_stub(monkeypatch, True)
    resp = _embeddings_client(tmp_path).get("/embeddings")
    assert resp.status_code == 200
    assert probe_calls == []
    assert "n/a" in resp.text  # probe health: not applicable
    assert "no active fallback" in resp.text  # the healthy rung row


def test_embeddings_status_core_schema_only_db_renders_unknown_rows(
    tmp_path, monkeypatch, _isolated_embed_state
):
    """A store predating the knowledge/memory embedding tables still renders
    the status view: those corpora report unknown (em-dash rows), never a
    500 — the display-degrade contract for schema drift."""
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "local")
    from cairn.graph import embeddings

    db_path = _graph_db_file(tmp_path, seed=False)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE knowledge_embeddings")
        conn.execute("DROP TABLE memory_embeddings")
        conn.commit()
    finally:
        conn.close()

    client = _panel_client(tmp_path, db_path, str(tmp_path / "missing"))
    resp = client.get("/embeddings")
    assert resp.status_code == 200
    # The core code corpus still renders (empty: 0 rows / never embedded).
    assert embeddings.current_model() in resp.text
    assert '<td class="num">0</td>' in resp.text
    assert "never" in resp.text
    # The two absent tables degrade to em-dash rows, not an error page.
    assert resp.text.count('<td class="num">—</td>') == 2


@requires_vis_network
def test_graph_route_has_include_tests_toggle_and_inspect_panel(tmp_path):
    """The graph tab ships the tests toggle in the toolbar and the empty
    side-panel shell the node-inspect fetch fills."""
    client = _client(tmp_path, seed=True)
    resp = client.get("/graph", params={"scope": "module", "focus": "src/demo"})
    assert resp.status_code == 200
    assert 'name="tests"' in resp.text
    assert 'id="graph-panel"' in resp.text


def test_graph_inspect_route_returns_json_payload(tmp_path):
    """/graph/inspect answers the panel's question in one call: identity,
    callers, callees, and the impact view with affected tests."""
    client = _client(tmp_path, seed=True)
    resp = client.get("/graph/inspect", params={"name": "demo_main"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["symbol"]["name"] == "demo_main"
    assert data["symbol"]["file"] == "src/demo/core.py"
    assert {c["name"] for c in data["callees"]} == {"demo_helper"}

    missing = client.get("/graph/inspect", params={"name": "missing_symbol"})
    assert missing.status_code == 200
    assert missing.json()["found"] is False
