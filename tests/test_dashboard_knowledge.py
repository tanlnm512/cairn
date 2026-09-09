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
    """The /knowledge/{family}/{slug} hrefs a catalog page carries. The
    header's relationship-graph link (/knowledge/graph) is navigation,
    not a row — excluded here."""
    return [
        href
        for href in re.findall(r'href="(/knowledge/[^"?]+)"', html)
        if href != "/knowledge/graph"
    ]


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


def test_knowledge_catalog_out_of_vocab_filters_fall_back(tmp_path):
    """A ?family=/&status= value the selects can't offer falls back to no
    filter — silent, like the tasks/memory/wiki filters: every doc
    renders and both selects read 'all'. In-vocab values keep narrowing
    (the control half)."""
    client, _ = _seeded_client(tmp_path)

    bogus_family = client.get("/knowledge", params={"family": "bogus"})
    assert bogus_family.status_code == 200
    for title in ("Old decision", "Storage spec", "Deploy gate"):
        assert title in bogus_family.text
    assert '<select name="family">' in bogus_family.text
    assert '<option value="all" selected>' in bogus_family.text
    assert '<option value="bogus"' not in bogus_family.text

    bogus_status = client.get("/knowledge", params={"status": "bogus"})
    assert bogus_status.status_code == 200
    assert "Old decision" in bogus_status.text
    assert "Storage spec" in bogus_status.text
    assert '<option value="all" selected>' in bogus_status.text

    both = client.get(
        "/knowledge", params={"family": "bogus", "status": "nope"}
    )
    assert both.status_code == 200
    assert "Storage spec" in both.text
    assert "Deploy gate" in both.text

    # Control: in-vocab values still narrow — the fallback never weakens
    # a real filter.
    filtered = client.get("/knowledge", params={"family": "decision"})
    assert "Old decision" in filtered.text
    assert "Storage spec" not in filtered.text
    assert '<option value="decision" selected>' in filtered.text


def test_knowledge_catalog_custom_family_stays_filterable(tmp_path):
    """A corpus doc whose family sits outside the classifier's vocabulary
    stays reachable from the family filter: the option is offered and
    selecting it narrows to that doc (the vocabulary is the classifier's
    families plus what the corpus contains)."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app
    from cairn.knowledge.index import rebuild_knowledge_index
    from cairn.knowledge.store import add_document
    from cairn.graph.schema import get_db
    from cairn.okf.bundle import OKFBundle
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    kdir = tmp_path / "knowledge"
    _seed_docs(kdir)
    runbook = add_document(
        OKFBundle(str(kdir)), "Key rotation runbook", "Rotate the keys.",
        "runbook", tags=["ops"],
    )
    conn = get_db(db)
    try:
        rebuild_knowledge_index(conn, OKFBundle(str(kdir)))
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(db_path=db, knowledge_dir=str(kdir)))

    page = client.get("/knowledge", params={"family": "runbook"})
    assert page.status_code == 200
    assert "Key rotation runbook" in page.text
    assert "Storage spec" not in page.text
    assert '<option value="runbook" selected>' in page.text
    # The row still links to the detail page under the custom namespace.
    assert f'href="/knowledge/runbook/{runbook.rsplit("/", 1)[1]}"' in page.text


def test_knowledge_status_badges_style_every_status():
    """Every doc status a knowledge row can render (DOC_STATUSES) has a
    badge color rule in app.css — no status falls through to the
    unstyled base badge."""
    import cairn.dashboard
    from cairn.knowledge.store import DOC_STATUSES
    from pathlib import Path

    css = (
        Path(cairn.dashboard.__file__).resolve().parent / "static" / "app.css"
    ).read_text(encoding="utf-8")
    for status in DOC_STATUSES:
        assert f".badge-{status}" in css, status


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
    # The h2 must come from the RENDERED BODY, not the panel headings below it.
    body_region = resp.text.split('<div class="wiki-body">', 1)[1].split(
        '<section class="doc-subpanel"', 1
    )[0]
    assert "<h2" in body_region  # "## Details" rendered as a heading
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


# ---------------------------------------------------------------------------
# /knowledge/{family}/{slug} detail: relationship panels
# (VAL-KNOW-006..012 server halves; the browser halves are agent-browser
# checks against the fixture dashboard)
# ---------------------------------------------------------------------------


def _seed_detail_docs(kdir):
    """The detail-page corpus: a 3-ADR supersede chain, an inferred-link
    pair (declared, so no derived edge competes), a doc with verified +
    unverified code refs, and an isolated zero-relationship doc.

    Shared tags inside the chain give the ADRs derived relates-to edges
    on top of the declared supersedes edges, mirroring the fixture; the
    lone doc's tag overlaps nothing.
    """
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
    ref_doc = add_document(
        bundle, "Storage spec",
        "Rows live in `demo_pkg/store.py` keyed by `Store.total_revenue`.",
        "spec", tags=["storage"],
        resource="demo/docs/spec-storage.md",
        verified_refs=[
            {"ref": "demo_pkg/store.py", "kind": "file", "verified": True},
            {"ref": "Store.total_revenue", "kind": "symbol", "verified": True},
            {"ref": "demo_pkg/missing.py", "kind": "file", "verified": False},
        ],
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
        "ref_doc": ref_doc,
        "inferred_source": inferred_source,
        "inferred_target": inferred_target,
        "lone": lone,
    }


def _detail_client(tmp_path):
    """A client over the detail corpus with its derived index rebuilt.

    Returns ``(client, ids, db_path)`` so tests can read the index the
    page is supposed to agree with."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app
    from cairn.knowledge.index import rebuild_knowledge_index
    from cairn.graph.schema import get_db
    from cairn.okf.bundle import OKFBundle
    from tests.test_dashboard_app import _graph_db_file

    db = _graph_db_file(tmp_path, seed=False)
    kdir = tmp_path / "knowledge"
    ids = _seed_detail_docs(kdir)
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


def _panel_tuples(html: str) -> set:
    """The (neighbor bare id, relation, kind) tuples the relationship
    panel renders — same shape `related_docs` (and the CLI) reports."""
    shown = set()
    for relation, block in re.findall(
        r'<div class="rel-group" data-relation="([^"]+)">(.*?)</div>',
        html,
        re.S,
    ):
        for href, kind in re.findall(
            r'href="(/knowledge/[^"?]+)">.*?'
            r'<span class="badge badge-kind-([^"]+)">',
            block,
            re.S,
        ):
            shown.add(("knowledge/" + href[len("/knowledge/"):], relation, kind))
    return shown


def test_knowledge_detail_chain_ordered_directional_all_members(tmp_path):
    """VAL-KNOW-008: the chain widget renders the full supersede chain as
    an ordered oldest->newest list with visible direction — all 3 ADRs in
    causal order with a labeled, arrowed hop between neighbors — from
    EITHER endpoint, and marks the page's own doc as current."""
    client, ids, _ = _detail_client(tmp_path)

    for entry in ("adr1", "adr3"):
        resp = client.get(_doc_path(ids[entry]))
        assert resp.status_code == 200, entry
        assert "Supersede chain" in resp.text, entry
        chain = re.search(
            r'<ol class="supersede-chain">(.*?)</ol>', resp.text, re.S
        )
        assert chain, entry
        block = chain.group(1)
        p1, p2, p3 = (
            block.find(_doc_path(ids["adr1"])),
            block.find(_doc_path(ids["adr2"])),
            block.find(_doc_path(ids["adr3"])),
        )
        assert 0 <= p1 < p2 < p3, entry  # oldest -> newest, every member
        assert block.count("→") == 2, entry  # one arrowed hop per pair
        assert block.count("superseded-by") == 2, entry  # labeled direction

    # The viewed doc is the chain's marked-current member.
    own = client.get(_doc_path(ids["adr2"]))
    current = re.search(
        r'<li class="chain-member chain-current">\s*'
        r'<a href="([^"]+)"', own.text
    )
    assert current and current.group(1) == _doc_path(ids["adr2"])


def test_knowledge_detail_chain_and_panel_traverse_by_links(tmp_path):
    """VAL-KNOW-012 (server half): the chain and relationship links are
    real detail hrefs — clicking through them walks 0001 -> 0002 -> 0003
    landing on each doc's own page."""
    client, ids, _ = _detail_client(tmp_path)
    page = client.get(_doc_path(ids["adr1"]))
    assert "<h1>ADR 0001: Postgres</h1>" in page.text
    assert f'href="{_doc_path(ids["adr2"])}"' in page.text

    page = client.get(_doc_path(ids["adr2"]))
    assert "<h1>ADR 0002: SQLite</h1>" in page.text
    assert f'href="{_doc_path(ids["adr3"])}"' in page.text

    page = client.get(_doc_path(ids["adr3"]))
    assert "<h1>ADR 0003: DuckDB</h1>" in page.text


def test_knowledge_detail_panel_groups_relations_with_kinds(tmp_path):
    """VAL-KNOW-007: the relationship panel groups rows under their
    relation and labels each with its kind — the declared extracted
    supersedes edge, the derived tag-overlap edges, and the inferred
    edge all show their correct kind."""
    client, ids, _ = _detail_client(tmp_path)

    resp = client.get(_doc_path(ids["adr2"]))
    assert resp.status_code == 200
    assert 'data-relation="supersedes"' in resp.text
    assert 'data-relation="relates-to"' in resp.text
    assert "badge-kind-extracted" in resp.text
    assert "badge-kind-derived" in resp.text
    assert f'href="{_doc_path(ids["adr1"])}"' in resp.text

    inferred = client.get(_doc_path(ids["inferred_source"]))
    assert "badge-kind-inferred" in inferred.text
    assert f'href="{_doc_path(ids["inferred_target"])}"' in inferred.text


def test_knowledge_detail_panel_tuples_match_related_docs(tmp_path):
    """VAL-KNOW-021 (server half): the panel's (neighbor, relation, kind)
    tuples equal the set `related_docs` returns — the UI never shows an
    edge the CLI wouldn't, and never drops one it would."""
    from cairn.graph.schema import get_db
    from cairn.knowledge.index import related_docs
    from cairn.okf.bundle import OKFBundle

    client, ids, db = _detail_client(tmp_path)
    conn = get_db(db)
    try:
        expected = {
            (n["doc_id"], n["relation"], n["kind"])
            for n in related_docs(conn, OKFBundle(str(tmp_path / "knowledge")), ids["adr2"])
        }
    finally:
        conn.close()

    resp = client.get(_doc_path(ids["adr2"]))
    assert _panel_tuples(resp.text) == expected


def test_knowledge_detail_no_relationships_empty_state(tmp_path):
    """VAL-KNOW-011: a doc with zero stored relationships still renders
    the relationship panel with an explicit empty state — not a missing
    section, and no chain widget for a chain of one."""
    client, ids, _ = _detail_client(tmp_path)
    resp = client.get(_doc_path(ids["lone"]))
    assert resp.status_code == 200
    assert "Relationships" in resp.text
    assert 'class="empty-state"' in resp.text
    assert "No relationships" in resp.text
    assert "Supersede chain" not in resp.text
    assert 'class="rel-group"' not in resp.text


def test_knowledge_detail_refs_show_resolution_status(tmp_path):
    """VAL-KNOW-009: linked code refs render with their kind and a
    per-ref resolution status; verified refs read verified (symbols deep
    linking into the graph), unverified ones read unresolved."""
    client, ids, _ = _detail_client(tmp_path)
    resp = client.get(_doc_path(ids["ref_doc"]))
    assert resp.status_code == 200
    assert "Linked code" in resp.text
    assert "<code>demo_pkg/store.py</code>" in resp.text
    assert "<code>Store.total_revenue</code>" in resp.text
    assert "<code>demo_pkg/missing.py</code>" in resp.text
    assert ">verified</span>" in resp.text
    assert ">unresolved</span>" in resp.text
    assert ">file</span>" in resp.text
    assert ">symbol</span>" in resp.text
    assert 'href="/graph?scope=symbol&amp;focus=Store.total_revenue"' in resp.text


def test_knowledge_detail_provenance_matches_staged_manifest(tmp_path, monkeypatch):
    """VAL-KNOW-010: the provenance section shows the source repo + path
    and a reference to the doc's row in the staged ingest manifest —
    values matching that manifest — degrading to the concept's recorded
    resource (and an explicit no-staged-manifest note) when the outbox
    is gone, and to an explicit no-ingest note for store-added docs."""
    import json

    client, ids, _ = _detail_client(tmp_path)
    outbox = tmp_path / "ws" / ".cairn" / "ingest-outbox"
    outbox.mkdir(parents=True)
    manifest = {
        "version": 1,
        "workspace": str(outbox),
        "rows": [
            {
                "concept_id": ids["adr2"],
                "repo": "demo",
                "source_path": "demo/docs/adr-0002-sqlite.md",
                "staged_path": ids["adr2"] + ".md",
                "origin": "demo",
            }
        ],
    }
    (outbox / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("CAIRN_WORKSPACE", str(tmp_path / "ws"))

    resp = client.get(_doc_path(ids["adr2"]))
    assert resp.status_code == 200
    assert "Ingest provenance" in resp.text
    prov = re.search(
        r'Ingest provenance(.*?)</section>', resp.text, re.S
    ).group(1)
    assert "<dd>demo</dd>" in prov  # source repo from the manifest row
    assert "<code>demo/docs/adr-0002-sqlite.md</code>" in prov
    assert ".cairn/ingest-outbox/manifest.json" in prov  # the manifest ref
    assert ids["adr2"] in prov  # the row the doc staged under

    # Outbox gone: the recorded resource carries the provenance, the
    # manifest reference says so explicitly.
    monkeypatch.setenv("CAIRN_WORKSPACE", str(tmp_path / "no-such-ws"))
    resp = client.get(_doc_path(ids["ref_doc"]))
    assert "<code>demo/docs/spec-storage.md</code>" in resp.text
    assert "no staged manifest" in resp.text

    # A store-added doc states it has no ingest lineage at all.
    resp = client.get(_doc_path(ids["lone"]))
    assert "added directly to the store" in resp.text


def test_knowledge_detail_panels_degrade_on_preindex_store(tmp_path):
    """A store predating the relationship index (edges + refs tables
    dropped) renders the detail page with empty panels — never a 500."""
    from cairn.graph.schema import get_db

    client, ids, db = _detail_client(tmp_path)
    conn = get_db(db)
    try:
        conn.execute("DROP TABLE knowledge_edges")
        conn.execute("DROP TABLE knowledge_doc_refs")
        conn.commit()
    finally:
        conn.close()

    resp = client.get(_doc_path(ids["adr2"]))
    assert resp.status_code == 200
    assert "<h1>ADR 0002: SQLite</h1>" in resp.text
    assert 'class="empty-state"' in resp.text
    assert "No linked code references" in resp.text
