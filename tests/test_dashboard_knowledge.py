"""Knowledge catalog view (M3): /knowledge + the doc detail seam.

Covers the validation-contract behaviors VAL-KNOW-001 (sidebar entry
navigates to /knowledge), VAL-KNOW-002 (every doc listed once with family
+ status), VAL-KNOW-004 (rows resolve to the correct detail pages),
VAL-KNOW-005 (explicit empty state on a doc-less workspace) and
VAL-KNOW-019 (palette offers the knowledge views), plus the htmx filter
seam (the fragment half of VAL-KNOW-003) and the shell/palette composition.

The server half only: the interactive browser halves — a keystroke's
fragment in the network log, sidebar clicks — are browser checks.
"""
from __future__ import annotations

import json
import re

import pytest

HX = {"HX-Request": "true"}


# ---------------------------------------------------------------------------
# Seeding: docs through the store API + the derived index, hermetically
# ---------------------------------------------------------------------------


def _seed_docs(kdir):
    """Four docs through add_document + one status flip, and the derived
    index rebuilt over them.

    - ``rule`` (business-rule, tag deploys): overlaps nothing — the
      zero-relationship row.
    - ``spec`` (spec, tag storage): derived-tag neighbor of both ADRs.
    - ``old`` / ``new`` (decision, tag storage): a supersedes pair
      (extracted edge both ways via the index) sharing the spec's tag.
    """
    from cairn.knowledge.store import add_document, update_status
    from cairn.okf.bundle import OKFBundle

    bundle = OKFBundle(str(kdir))
    old = add_document(
        bundle, "Old decision", "Pick Postgres for storage.", "decision",
        tags=["storage"],
    )
    new = add_document(
        bundle, "New decision", "Move to SQLite instead.", "decision",
        tags=["storage"],
        relationships=[
            {"concept_id": old, "relation": "supersedes", "kind": "extracted"}
        ],
    )
    spec = add_document(
        bundle, "Storage spec",
        "Rows live in `demo_pkg/store.py`.\n\n## Details\n\nOne table per fact.",
        "spec", tags=["storage"],
    )
    rule = add_document(
        bundle, "Deploy gate", "Ship only on green builds.", "business-rule",
        tags=["deploys"],
    )
    update_status(bundle, old, "superseded")
    return {
        "old": old,
        "new": new,
        "spec": spec,
        "rule": rule,
    }


def _seeded_client(tmp_path):
    """A client over a schema-complete DB whose knowledge dir carries the
    four seeded docs and a rebuilt relationship index."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app
    from cairn.knowledge.index import rebuild_knowledge_index
    from cairn.graph.schema import get_db
    from cairn.okf.bundle import OKFBundle
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    kdir = tmp_path / "knowledge"
    ids = _seed_docs(kdir)
    conn = get_db(db)
    try:
        rebuild_knowledge_index(conn, OKFBundle(str(kdir)))
        conn.commit()
    finally:
        conn.close()
    return (
        TestClient(create_app(db_path=db, knowledge_dir=str(kdir))),
        ids,
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


def _row_hrefs(html: str) -> list:
    """The /knowledge/{family}/{slug} hrefs a catalog page carries."""
    return re.findall(r'href="(/knowledge/[^"?]+)"', html)


def _doc_path(doc_id: str) -> str:
    """The detail URL for a bare concept id: the id minus its
    ``knowledge/`` namespace prefix (the route path supplies it)."""
    prefix = "knowledge/"
    tail = doc_id[len(prefix):] if doc_id.startswith(prefix) else doc_id
    return f"/knowledge/{tail}"


# ---------------------------------------------------------------------------
# Shell: sidebar entry + palette views (one composition, two consumers)
# ---------------------------------------------------------------------------


def test_shell_nav_knowledge_entry_and_palette_views():
    """The Knowledge section leads with the catalog entry, and the
    palette carries both knowledge destinations — the graph view is
    palette-only (one sidebar entry per surface family)."""
    from cairn.dashboard.shell import NAV_LABELS, shell_context

    assert "knowledge" in NAV_LABELS
    ctx = shell_context([], "", "/knowledge")
    knowledge_section = next(
        s for s in ctx["nav"]["sections"] if s["label"] == "Knowledge"
    )
    first = knowledge_section["items"][0]
    assert first["id"] == "knowledge"
    assert first["label"] == "Knowledge"
    assert first["href"] == "/knowledge"
    assert first["active"] is True

    by_label = {v["label"]: v["href"] for v in ctx["palette"]["views"]}
    assert by_label["Knowledge"] == "/knowledge"
    assert by_label["Knowledge Graph"] == "/knowledge/graph"


def test_shell_context_href_composition_covers_knowledge_views():
    """The palette's knowledge hrefs ride the selected store exactly like
    the nav views'."""
    from cairn.dashboard.shell import shell_context

    ctx = shell_context([], "aaaaaaaaaaaaaaaa", "/")
    by_label = {v["label"]: v["href"] for v in ctx["palette"]["views"]}
    assert by_label["Knowledge"] == "/knowledge?store=aaaaaaaaaaaaaaaa"
    assert (
        by_label["Knowledge Graph"]
        == "/knowledge/graph?store=aaaaaaaaaaaaaaaa"
    )


# ---------------------------------------------------------------------------
# Data layer: list_knowledge_docs over the bundle + relationship index
# ---------------------------------------------------------------------------


def test_list_knowledge_docs_lists_each_doc_once(tmp_path):
    """Every stored doc appears exactly once with family, status, tags,
    and the bare id the detail href is composed from."""
    from cairn.dashboard.data import list_knowledge_docs
    from cairn.graph.schema import get_db
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    kdir = tmp_path / "knowledge"
    ids = _seed_docs(kdir)

    conn = get_db(db)
    try:
        result = list_knowledge_docs(conn, str(kdir))
    finally:
        conn.close()

    listed = [d["id"] for d in result["docs"]]
    assert sorted(listed) == sorted(ids.values())
    assert len(listed) == len(set(listed))  # exactly once each
    by_id = {d["id"]: d for d in result["docs"]}
    assert by_id[ids["old"]]["family"] == "decision"
    assert by_id[ids["old"]]["status"] == "superseded"
    assert by_id[ids["new"]]["status"] == "active"
    assert by_id[ids["spec"]]["family"] == "spec"
    assert by_id[ids["rule"]]["family"] == "business-rule"
    assert by_id[ids["rule"]]["slug"] == "deploy-gate"
    assert by_id[ids["rule"]]["tags"] == ["deploys"]
    # Family + status totals count the corpus before filtering.
    assert result["families"] == {
        "business-rule": 1,
        "decision": 2,
        "spec": 1,
    }
    assert result["statuses"] == {"active": 3, "superseded": 1}
    assert result["total"] == 4
    # Rows sort by family, then title.
    keys = [(d["family"], d["title"]) for d in result["docs"]]
    assert keys == sorted(keys)


def test_list_knowledge_docs_counts_index_neighbors(tmp_path):
    """The links column reads the derived index: distinct related docs
    from knowledge_edges, both directions, supersede pair and derived
    tag overlap alike; an isolated doc counts zero."""
    from cairn.dashboard.data import list_knowledge_docs
    from cairn.knowledge.index import rebuild_knowledge_index
    from cairn.graph.schema import get_db
    from cairn.okf.bundle import OKFBundle
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    kdir = tmp_path / "knowledge"
    ids = _seed_docs(kdir)
    conn = get_db(db)
    try:
        rebuild_knowledge_index(conn, OKFBundle(str(kdir)))
        conn.commit()
        result = list_knowledge_docs(conn, str(kdir))
    finally:
        conn.close()

    by_id = {d["id"]: d for d in result["docs"]}
    # old↔new supersedes (both directions) + old/new↔spec derived overlap.
    assert by_id[ids["old"]]["links"] == 2
    assert by_id[ids["new"]]["links"] == 2
    assert by_id[ids["spec"]]["links"] == 2
    assert by_id[ids["rule"]]["links"] == 0  # overlaps nothing


def test_list_knowledge_docs_filters(tmp_path):
    """family/status narrow to the vocabulary value; tag keeps docs
    carrying it; totals stay pre-filter so the filters render stable
    counts."""
    from cairn.dashboard.data import list_knowledge_docs
    from cairn.graph.schema import get_db
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    kdir = tmp_path / "knowledge"
    ids = _seed_docs(kdir)
    conn = get_db(db)
    try:
        decisions = list_knowledge_docs(
            conn, str(kdir), family="decision"
        )
        superseded = list_knowledge_docs(conn, str(kdir), status="superseded")
        deploy_docs = list_knowledge_docs(conn, str(kdir), tag="deploys")
        no_match = list_knowledge_docs(
            conn, str(kdir), family="decision", status="superseded", tag="none"
        )
    finally:
        conn.close()

    assert [d["id"] for d in decisions["docs"]] == sorted(
        [ids["old"], ids["new"]]
    )
    assert [d["id"] for d in superseded["docs"]] == [ids["old"]]
    assert [d["id"] for d in deploy_docs["docs"]] == [ids["rule"]]
    assert no_match["docs"] == []
    # Totals ride every call, filtered or not.
    assert decisions["total"] == 4


def test_list_knowledge_docs_empty_and_unindexed_store(tmp_path):
    """A doc-less workspace lists nothing, and a store predating the
    relationship index (no knowledge_edges table) lists rows with zero
    links — the catalog renders, never fails, on an old DB."""
    from cairn.dashboard.data import list_knowledge_docs
    from cairn.graph.schema import get_db
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    conn = get_db(db)
    try:
        conn.execute("DROP TABLE knowledge_edges")
        conn.commit()
        empty = list_knowledge_docs(conn, str(tmp_path / "knowledge"))
        _seed_docs(tmp_path / "k2")
        no_index = list_knowledge_docs(conn, str(tmp_path / "k2"))
    finally:
        conn.close()

    assert empty["docs"] == []
    assert empty["total"] == 0
    assert [d["links"] for d in no_index["docs"]] == [0, 0, 0, 0]


# ---------------------------------------------------------------------------
# /knowledge catalog route
# ---------------------------------------------------------------------------


def test_knowledge_catalog_lists_every_doc_with_family_and_status(tmp_path):
    """VAL-KNOW-002: the page lists every seeded doc exactly once, each
    row showing its family and status, linked to its detail page."""
    client, ids = _seeded_client(tmp_path)
    resp = client.get("/knowledge")
    assert resp.status_code == 200

    for title in (
        "Old decision",
        "New decision",
        "Storage spec",
        "Deploy gate",
    ):
        assert resp.text.count(title) == 1, title
    for family in ("decision", "spec", "business-rule"):
        assert f"badge badge-{family}" in resp.text
    assert "badge badge-superseded" in resp.text
    assert "badge badge-active" in resp.text
    # Each row links to the doc's detail page by bare id.
    for doc_id in ids.values():
        assert f'href="{_doc_path(doc_id)}"' in resp.text


def test_knowledge_catalog_filter_fragment_is_region_only(tmp_path):
    """VAL-KNOW-003 (server half): an HX-Request with ?family= renders
    the results region alone — no shell, no filter form — filtered to the
    family; the full page still renders the shell."""
    client, _ = _seeded_client(tmp_path)

    fragment = client.get("/knowledge", params={"family": "decision"}, headers=HX)
    assert fragment.status_code == 200
    assert "<html" not in fragment.text
    assert "<aside" not in fragment.text
    assert "filter-bar" not in fragment.text
    assert fragment.text.count('id="knowledge-results"') == 1
    assert "Old decision" in fragment.text
    assert "Storage spec" not in fragment.text
    assert "Deploy gate" not in fragment.text

    page = client.get("/knowledge", params={"family": "decision"})
    assert page.status_code == 200
    assert "<aside" in page.text
    assert 'id="knowledge-results"' in page.text
    assert "Old decision" in page.text
    assert "Storage spec" not in page.text


def test_knowledge_catalog_status_and_tag_filters_narrow(tmp_path):
    """The status select and the tag field read the same params the data
    function filters on, on both the page and the fragment branch."""
    client, ids = _seeded_client(tmp_path)

    page = client.get("/knowledge", params={"status": "superseded"})
    assert "Old decision" in page.text
    assert "New decision" not in page.text
    assert '<select name="status">' in page.text
    assert '<option value="superseded" selected>' in page.text

    fragment = client.get("/knowledge", params={"tag": "deploys"}, headers=HX)
    assert "Deploy gate" in fragment.text
    assert "Storage spec" not in fragment.text


def test_knowledge_catalog_empty_state(tmp_path):
    """VAL-KNOW-005: a doc-less workspace renders an explicit empty state
    naming the ingest command — not a blank page or a bare table shell."""
    client = _empty_client(tmp_path)
    resp = client.get("/knowledge")
    assert resp.status_code == 200
    assert 'class="empty-state"' in resp.text
    assert "No knowledge documents" in resp.text
    assert "cairn knowledge ingest" in resp.text
    assert "<table" not in resp.text


def test_knowledge_catalog_filtered_to_nothing_keeps_explanation(tmp_path):
    """A filter matching zero docs explains itself too — the empty state
    names the filters, not the (wrong) ingest hint."""
    client, _ = _seeded_client(tmp_path)
    resp = client.get("/knowledge", params={"family": "spec", "status": "archived"})
    assert resp.status_code == 200
    assert 'class="empty-state"' in resp.text
    assert "No documents match the current filters" in resp.text
    assert "No knowledge documents in this workspace" not in resp.text


def test_knowledge_page_filter_form_swaps_results_region(tmp_path):
    """The filter contract lives in the DOM: the form hx-gets the route
    (input/change, delay:300ms) and morphs #knowledge-results; the form
    stays OUTSIDE the region so a swap can never touch a field."""
    client, _ = _seeded_client(tmp_path)
    resp = client.get("/knowledge")
    assert resp.status_code == 200

    form_tag = re.search(r'<form class="filter-bar"[^>]*>', resp.text)
    assert form_tag, "the knowledge filter form is missing"
    form = form_tag.group(0)
    assert 'hx-get="/knowledge"' in form
    assert 'hx-target="#knowledge-results"' in form
    assert 'hx-swap="morph"' in form
    assert "delay:300ms" in form

    region_tag = re.search(r'<div id="knowledge-results"[^>]*>', resp.text)
    assert region_tag, "the knowledge results region is missing"
    assert "aria-live" in region_tag.group(0)
    assert "<form" not in region_tag.group(0)
    assert 'name="family"' not in region_tag.group(0)


def test_knowledge_catalog_in_palette_seed(tmp_path):
    """VAL-KNOW-019 (server half): the palette seed JSON riding every
    page offers both knowledge views."""
    client, _ = _seeded_client(tmp_path)
    resp = client.get("/knowledge")
    seed = json.loads(
        re.search(
            r'<script id="palette-data" type="application/json">(.*?)</script>',
            resp.text,
            re.S,
        ).group(1)
    )
    by_label = {v["label"]: v["href"] for v in seed["views"]}
    assert by_label["Knowledge"] == "/knowledge"
    assert by_label["Knowledge Graph"] == "/knowledge/graph"


# ---------------------------------------------------------------------------
# /knowledge/{family}/{slug} detail seam (rows must resolve)
# ---------------------------------------------------------------------------


def test_knowledge_rows_resolve_to_matching_detail_pages(tmp_path):
    """VAL-KNOW-004: every catalog row link resolves to a detail page
    whose title is the row's doc."""
    client, ids = _seeded_client(tmp_path)
    hrefs = set(_row_hrefs(client.get("/knowledge").text))
    assert hrefs == {_doc_path(doc_id) for doc_id in ids.values()}
    row_titles = {
        _doc_path(doc_id): title
        for doc_id, title in (
            (ids["old"], "Old decision"),
            (ids["new"], "New decision"),
            (ids["spec"], "Storage spec"),
            (ids["rule"], "Deploy gate"),
        )
    }
    for href in sorted(hrefs):
        detail = client.get(href)
        assert detail.status_code == 200, href
        assert "<h1>" in detail.text, href
        assert f"<h1>{row_titles[href]}</h1>" in detail.text, href


def test_knowledge_detail_renders_title_and_rendered_body(tmp_path):
    """The detail page renders the doc title and its body through the
    markdown renderer — headings and code spans become elements, the
    frontmatter is never dumped."""
    client, ids = _seeded_client(tmp_path)
    resp = client.get(_doc_path(ids["spec"]))
    assert resp.status_code == 200
    assert "<h1>Storage spec</h1>" in resp.text
    assert "<h2" in resp.text  # "## Details" rendered as a heading
    assert "## Details" not in resp.text
    assert "<code>demo_pkg/store.py</code>" in resp.text  # rendered span
    assert "type: Knowledge-spec" not in resp.text  # frontmatter not dumped
    assert "badge badge-spec" in resp.text


def test_knowledge_detail_unknown_and_out_of_namespace_404(tmp_path):
    """An unknown slug, a family the doc id never had, and an
    out-of-namespace resolution (the knowledge prefix of a wiki-shaped
    path) all render the plain not-found page — never a traceback."""
    client, ids = _seeded_client(tmp_path)
    for path in (
        "/knowledge/decision/no-such-doc",
        f"/knowledge/spec/{ids['old'].split('/', 2)[2]}",  # real slug, wrong family
    ):
        resp = client.get(path)
        assert resp.status_code == 404, path
        assert "Traceback" not in resp.text
        assert "Knowledge document not found" in resp.text
