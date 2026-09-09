"""Knowledge relationship graph view (M3): /knowledge/graph + its inspect
fragment.

Covers the validation-contract behaviors VAL-KNOW-013 (one node per doc;
supersede edges drawn directional, as stored), VAL-KNOW-014's server
seam (edges carry kind, so the canvas can render inferred dashed vs
extracted solid), VAL-KNOW-015's seam (the whole edge set rides the one
JSON block — legend filtering is client-side, no fragment endpoint), and
VAL-KNOW-016's server half (the node-click inspect fragment identifies
the clicked doc), plus the VAL-CROSS-001 tuple agreement between the
graph JSON and ``related_docs``. The interactive browser halves — canvas
rendering, filter clicks, node clicks — are agent-browser checks.

Route order is part of the contract: /knowledge/graph/inspect must be
matched by its own route, never by /knowledge/{family}/{slug} (family=
graph, slug=inspect). The inspect route is inherently fragment-only (the
canvas script is the only client; no UI links the bare path) — the
documented exception to the full-page-vs-fragment seam.
"""
from __future__ import annotations

import json
import re

import pytest

# ---------------------------------------------------------------------------
# Seeding: docs through the store API + the derived index, hermetically
# ---------------------------------------------------------------------------


def _seed_graph_docs(kdir):
    """The graph corpus: a 3-ADR supersede chain, an inferred relates-to
    pair, a spec sharing the chain's tag (derived overlap), and an
    isolated doc. Mirrors the fixture: declared supersedes + critic-
    approved inferred + tag-overlap derived + one zero-edge doc."""
    from cairn.knowledge.store import add_document
    from cairn.okf.bundle import OKFBundle

    bundle = OKFBundle(str(kdir))
    adr1 = add_document(
        bundle, "ADR 0001: Postgres", "Pick Postgres for storage.",
        "decision", tags=["storage"],
    )
    adr2 = add_document(
        bundle, "ADR 0002: SQLite", "Supersedes ADR-0001: move to SQLite.",
        "decision", tags=["storage"],
        relationships=[
            {"concept_id": adr1, "relation": "supersedes", "kind": "extracted"}
        ],
    )
    adr3 = add_document(
        bundle, "ADR 0003: DuckDB", "Supersedes ADR-0002: embed the engine.",
        "decision", tags=["storage"],
        relationships=[
            {"concept_id": adr2, "relation": "supersedes", "kind": "extracted"}
        ],
    )
    spec = add_document(
        bundle, "Storage spec", "Rows live in tables.", "spec",
        tags=["storage"],
    )
    inferred_target = add_document(
        bundle, "Queue retry policy", "Retries are idempotent.",
        "spec", tags=["queues"],
    )
    inferred_source = add_document(
        bundle, "Backoff policy", "Backoff defers to the queue policy.",
        "spec", tags=["queues"],
        relationships=[
            {"concept_id": inferred_target, "relation": "relates-to",
             "kind": "inferred"}
        ],
    )
    lone = add_document(
        bundle, "Deploy gate", "Ship only on green builds.",
        "business-rule", tags=["deploys"],
    )
    return {
        "adr1": adr1,
        "adr2": adr2,
        "adr3": adr3,
        "spec": spec,
        "inferred_source": inferred_source,
        "inferred_target": inferred_target,
        "lone": lone,
    }


def _graph_client(tmp_path):
    """A client over the graph corpus with its derived index rebuilt.

    Returns ``(client, ids, db_path)`` so tests can read and mutate the
    index the view is supposed to mirror."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app
    from cairn.knowledge.index import rebuild_knowledge_index
    from cairn.graph.schema import get_db
    from cairn.okf.bundle import OKFBundle
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    kdir = tmp_path / "knowledge"
    ids = _seed_graph_docs(kdir)
    conn = get_db(db)
    try:
        rebuild_knowledge_index(conn, OKFBundle(str(kdir)))
        conn.commit()
    finally:
        conn.close()
    return (
        TestClient(create_app(db_path=db, knowledge_dir=str(kdir))),
        ids,
        db,
    )


def _empty_client(tmp_path):
    """A client whose knowledge dir has no docs (dir never written)."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app
    from tests.test_dashboard_app import _graph_db_file

    return TestClient(
        create_app(
            db_path=_graph_db_file(tmp_path, seed=False),
            knowledge_dir=str(tmp_path / "knowledge"),
        )
    )


def _graph_json(html: str) -> dict:
    """The canvas data the page serialized into #knowledge-graph-data."""
    match = re.search(
        r'<script id="knowledge-graph-data" type="application/json">'
        r'(.*?)</script>',
        html,
        re.S,
    )
    assert match, "the knowledge graph data block is missing"
    return json.loads(match.group(1))


# ---------------------------------------------------------------------------
# Data layer: get_knowledge_graph over the bundle + relationship index
# ---------------------------------------------------------------------------


def test_knowledge_graph_renders_one_node_per_doc(tmp_path):
    """VAL-KNOW-013 (data half): every stored doc is a node exactly once,
    edgeless docs included — the canvas shows the whole corpus."""
    from cairn.dashboard.data import get_knowledge_graph
    from cairn.graph.schema import get_db
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    kdir = tmp_path / "knowledge"
    ids = _seed_graph_docs(kdir)
    conn = get_db(db)
    try:
        graph = get_knowledge_graph(conn, str(kdir))
    finally:
        conn.close()

    node_ids = [n["id"] for n in graph["nodes"]]
    assert sorted(node_ids) == sorted(ids.values())
    assert len(node_ids) == len(set(node_ids))
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id[ids["adr1"]]["title"] == "ADR 0001: Postgres"
    assert by_id[ids["adr1"]]["family"] == "decision"
    assert by_id[ids["lone"]]["family"] == "business-rule"
    assert by_id[ids["lone"]]["status"] == "active"
    assert graph["metadata"]["node_count"] == len(ids)


def test_knowledge_graph_edges_carry_relation_and_kind(tmp_path):
    """VAL-KNOW-014's seam: every edge names its relation and kind, so
    the canvas can draw inferred dashed vs extracted solid and the
    legend can chip both facets."""
    from cairn.dashboard.data import get_knowledge_graph
    from cairn.knowledge.index import rebuild_knowledge_index
    from cairn.graph.schema import get_db
    from cairn.okf.bundle import OKFBundle
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    kdir = tmp_path / "knowledge"
    ids = _seed_graph_docs(kdir)
    conn = get_db(db)
    try:
        rebuild_knowledge_index(conn, OKFBundle(str(kdir)))
        conn.commit()
        graph = get_knowledge_graph(conn, str(kdir))
    finally:
        conn.close()

    for edge in graph["edges"]:
        assert set(edge) == {"source", "target", "relation", "kind"}

    def has(source, target, relation, kind):
        return any(
            e["source"] == source
            and e["target"] == target
            and e["relation"] == relation
            and e["kind"] == kind
            for e in graph["edges"]
        )

    # Declared supersedes: newer -> older, extracted.
    assert has(ids["adr2"], ids["adr1"], "supersedes", "extracted")
    assert has(ids["adr3"], ids["adr2"], "supersedes", "extracted")
    # The critic-approved inferred link.
    assert has(
        ids["inferred_source"], ids["inferred_target"], "relates-to",
        "inferred",
    )
    # Tag overlap materialized as derived rows (symmetric pair).
    assert has(ids["adr1"], ids["spec"], "relates-to", "derived")
    assert has(ids["spec"], ids["adr1"], "relates-to", "derived")
    # The isolated doc carries no edge.
    assert not any(
        ids["lone"] in (e["source"], e["target"]) for e in graph["edges"]
    )
    assert graph["metadata"]["edge_count"] == len(graph["edges"])


def test_knowledge_graph_draws_supersede_rows_as_stored(tmp_path):
    """VAL-KNOW-013: supersede edges are directional and drawn exactly as
    the index stores them — the declared supersedes row orients newer ->
    older, and the ingest pipeline's mirrored superseded-by row (the old
    doc's inverse pointer) adds the inverse directed edge rather than
    being reoriented or deduped away. The pair renders as the two-way
    link every other surface (related CLI, detail panels) reports."""
    from cairn.dashboard.data import get_knowledge_graph
    from cairn.knowledge.index import rebuild_knowledge_index
    from cairn.graph.schema import get_db
    from cairn.okf.bundle import OKFBundle
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    kdir = tmp_path / "knowledge"
    ids = _seed_graph_docs(kdir)
    conn = get_db(db)
    try:
        rebuild_knowledge_index(conn, OKFBundle(str(kdir)))
        conn.commit()
        graph = get_knowledge_graph(conn, str(kdir))
        supersede = [
            e
            for e in graph["edges"]
            if e["relation"] == "supersedes"
            and e["source"] == ids["adr2"]
        ]
        assert supersede == [
            {
                "source": ids["adr2"],
                "target": ids["adr1"],
                "relation": "supersedes",
                "kind": "extracted",
            }
        ]

        # The old doc's mirrored pointer, as the ingest pipeline promotes
        # it: one more directed row, the pair's inverse direction.
        conn.execute(
            "INSERT INTO knowledge_edges "
            "(doc_id, related_id, relation, kind, provenance, created_at) "
            "VALUES (?, ?, 'superseded-by', 'extracted', 'test', 0)",
            (ids["adr1"], ids["adr2"]),
        )
        conn.commit()
        both = get_knowledge_graph(conn, str(kdir))
    finally:
        conn.close()

    assert any(
        e["source"] == ids["adr1"]
        and e["target"] == ids["adr2"]
        and e["relation"] == "superseded-by"
        and e["kind"] == "extracted"
        for e in both["edges"]
    )
    assert both["metadata"]["edge_count"] == graph["metadata"]["edge_count"] + 1


def test_knowledge_graph_drops_dangling_endpoints(tmp_path):
    """Rows naming docs the bundle no longer resolves (stale index
    between rebuilds) draw nothing — never a phantom endpoint. (Exact
    duplicate rows cannot occur: the table's primary key is the edge
    4-tuple, and the rebuild dedupes on it.)"""
    from cairn.dashboard.data import get_knowledge_graph
    from cairn.knowledge.index import rebuild_knowledge_index
    from cairn.graph.schema import get_db
    from cairn.okf.bundle import OKFBundle
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    kdir = tmp_path / "knowledge"
    ids = _seed_graph_docs(kdir)
    conn = get_db(db)
    try:
        rebuild_knowledge_index(conn, OKFBundle(str(kdir)))
        conn.commit()
        baseline = get_knowledge_graph(conn, str(kdir))
        conn.execute(
            "INSERT INTO knowledge_edges "
            "(doc_id, related_id, relation, kind, provenance, created_at) "
            "VALUES ('knowledge/spec/ghost', ?, 'relates-to', 'derived', "
            "'test', 0)",
            (ids["spec"],),
        )
        conn.execute(
            "INSERT INTO knowledge_edges "
            "(doc_id, related_id, relation, kind, provenance, created_at) "
            "VALUES (?, 'knowledge/spec/ghost', 'relates-to', 'derived', "
            "'test', 0)",
            (ids["spec"],),
        )
        conn.commit()
        graph = get_knowledge_graph(conn, str(kdir))
    finally:
        conn.close()

    assert graph["edges"] == baseline["edges"]
    assert graph["metadata"] == baseline["metadata"]


def test_knowledge_graph_empty_corpus(tmp_path):
    """A doc-less workspace yields an empty graph (the route renders its
    empty state), never an error."""
    from cairn.dashboard.data import get_knowledge_graph
    from cairn.graph.schema import get_db
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    conn = get_db(db)
    try:
        graph = get_knowledge_graph(conn, str(tmp_path / "knowledge"))
    finally:
        conn.close()
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert graph["metadata"] == {"node_count": 0, "edge_count": 0}


def test_knowledge_graph_tuples_match_related_docs(tmp_path):
    """VAL-CROSS-001 (server half): for every doc, the (neighbor,
    relation, kind) tuples reachable through its node equal the set
    ``related_docs`` returns — the canvas can never disagree with the
    related CLI or the detail panels."""
    from cairn.graph.schema import get_db
    from cairn.knowledge.index import related_docs
    from cairn.okf.bundle import OKFBundle

    client, ids, db = _graph_client(tmp_path)
    page = client.get("/knowledge/graph")
    assert page.status_code == 200
    graph = _graph_json(page.text)

    conn = get_db(db)
    try:
        for doc_id in ids.values():
            expected = {
                (n["doc_id"], n["relation"], n["kind"])
                for n in related_docs(
                    conn, OKFBundle(str(tmp_path / "knowledge")), doc_id
                )
            }
            seen = {
                (
                    e["target"] if e["source"] == doc_id else e["source"],
                    e["relation"],
                    e["kind"],
                )
                for e in graph["edges"]
                if doc_id in (e["source"], e["target"])
            }
            assert seen == expected, doc_id
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /knowledge/graph route
# ---------------------------------------------------------------------------


def test_knowledge_graph_route_renders_canvas_machinery(tmp_path):
    """The page carries the /graph machinery's seams: the JSON data block,
    the canvas mount, the legend and counts elements the script fills,
    the inspect panel aside, and the overlay controls."""
    client, ids, _ = _graph_client(tmp_path)
    resp = client.get("/knowledge/graph")
    assert resp.status_code == 200

    graph = _graph_json(resp.text)
    assert sorted(n["id"] for n in graph["nodes"]) == sorted(ids.values())

    for element_id in (
        "knowledge-graph-canvas",
        "knowledge-graph-legend",
        "knowledge-graph-counts",
        "knowledge-graph-panel",
    ):
        assert f'id="{element_id}"' in resp.text, element_id

    counts = re.search(
        r'<p class="muted" id="knowledge-graph-counts">(.*?)</p>',
        resp.text,
        re.S,
    ).group(1)
    assert str(len(ids)) in counts  # the doc count server-renders

    assert 'data-graph-action="zoom-in"' in resp.text
    assert 'data-graph-action="fit"' in resp.text

    # The canvas scripts load vendored, cache-busted.
    assert re.search(
        r'<script src="[^"]*/static/vis-network\.min\.js\?v=', resp.text
    )
    assert re.search(
        r'<script src="[^"]*/static/knowledge-graph\.js\?v=', resp.text
    )
    assert "Traceback" not in resp.text


def test_knowledge_graph_route_empty_state(tmp_path):
    """A doc-less workspace renders the explicit empty state naming the
    ingest command — no canvas, no data block, no canvas scripts."""
    client = _empty_client(tmp_path)
    resp = client.get("/knowledge/graph")
    assert resp.status_code == 200
    assert 'class="empty-state"' in resp.text
    assert "No knowledge documents" in resp.text
    assert "cairn knowledge ingest" in resp.text
    assert 'id="knowledge-graph-data"' not in resp.text
    assert 'id="knowledge-graph-canvas"' not in resp.text
    assert "knowledge-graph.js" not in resp.text


def test_knowledge_graph_route_order_inspect_not_shadowed(tmp_path):
    """Route order: /knowledge/graph is the canvas (not a detail 404) and
    /knowledge/graph/inspect is the fragment (not the two-segment doc
    route eating it as family=graph, slug=inspect)."""
    client, ids, _ = _graph_client(tmp_path)

    canvas = client.get("/knowledge/graph")
    assert canvas.status_code == 200
    assert 'id="knowledge-graph-data"' in canvas.text
    assert "Knowledge document not found" not in canvas.text

    fragment = client.get(
        "/knowledge/graph/inspect", params={"doc": ids["adr2"]}
    )
    assert fragment.status_code == 200
    assert "ADR 0002: SQLite" in fragment.text
    assert "Knowledge document not found" not in fragment.text


# ---------------------------------------------------------------------------
# /knowledge/graph/inspect fragment (the node-click panel)
# ---------------------------------------------------------------------------


def test_knowledge_graph_inspect_fragment_identifies_the_doc(tmp_path):
    """VAL-KNOW-016 (server half): the fragment identifies the clicked
    doc — title, bare id, family and status badges — and links its full
    detail page."""
    client, ids, _ = _graph_client(tmp_path)
    resp = client.get(
        "/knowledge/graph/inspect", params={"doc": ids["adr2"]}
    )
    assert resp.status_code == 200
    # Fragment, not a page: no shell.
    assert "<html" not in resp.text
    assert "<aside" not in resp.text

    assert "ADR 0002: SQLite" in resp.text
    assert ids["adr2"] in resp.text  # the bare concept id
    assert 'class="badge badge-active"' in resp.text  # status badge
    assert f'href="/knowledge/decision/{ids["adr2"].split("/", 2)[2]}"' \
        in resp.text
    assert "Open full detail" in resp.text


def test_knowledge_graph_inspect_lists_relationships_with_kinds(tmp_path):
    """The panel's relationship section renders the same grouped rows the
    detail panel does — relation, kind badge, direction — so a node
    click answers 'how does this doc relate?' without leaving the canvas."""
    client, ids, _ = _graph_client(tmp_path)
    resp = client.get(
        "/knowledge/graph/inspect", params={"doc": ids["adr2"]}
    )
    assert 'data-relation="supersedes"' in resp.text
    assert 'data-relation="relates-to"' in resp.text
    assert "badge-kind-extracted" in resp.text
    assert "badge-kind-derived" in resp.text


def test_knowledge_graph_inspect_unknown_doc_renders_note(tmp_path):
    """An unknown doc renders the panel's not-found note at 200 — the
    /graph/inspect found=False contract, never a 500 or an empty panel."""
    client, _, _ = _graph_client(tmp_path)
    resp = client.get(
        "/knowledge/graph/inspect", params={"doc": "knowledge/spec/ghost"}
    )
    assert resp.status_code == 200
    assert "no knowledge document" in resp.text
    assert "knowledge/spec/ghost" in resp.text

    blank = client.get("/knowledge/graph/inspect")
    assert blank.status_code == 200
    assert "no knowledge document" in blank.text


def test_knowledge_graph_inspect_wiring_aborts_superseded_requests():
    """knowledge-graph.js inspect wiring (source contract, no JS
    runtime): a new inspect fetch aborts its in-flight predecessor
    (htmx:beforeSend carries the live xhr), and the failure wiring covers
    response/send errors only — htmx never fires htmx:timeout without a
    configured timeout, so that listener is dead code."""
    import cairn.dashboard
    from pathlib import Path

    src = (
        Path(cairn.dashboard.__file__).resolve().parent
        / "static"
        / "knowledge-graph.js"
    ).read_text(encoding="utf-8")

    assert re.search(r"addEventListener\(\s*[\"']htmx:beforeSend[\"']", src), (
        "no htmx:beforeSend listener: a superseded inspect fetch is never aborted"
    )
    assert re.search(r"htmx:beforeSend[\s\S]{0,600}?\.abort\(\)", src), (
        "the beforeSend listener never aborts the in-flight inspect xhr"
    )
    assert "htmx:timeout" not in src, (
        "dead htmx:timeout listener (htmx fires it only with a configured timeout)"
    )
    # The genuine failure paths keep their failure note.
    assert "htmx:responseError" in src
    assert "htmx:sendError" in src
