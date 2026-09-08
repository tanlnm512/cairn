"""M1 backwards-compatibility guards + dangling-pointer visibility.

Covers the validation-contract behaviors: VAL-INGEST-017 (a relationship-
free doc ingests exactly as before -- same core keys and knowledge
extensions, embedding and search unchanged) and VAL-INGEST-018 (the ingest
flag set and the 22-tool MCP boot verification are unchanged), plus the
dangling-pointer warnings: a declared ``relates_to`` pointer matching
neither a concept id nor a resource path is surfaced in ingest dry-run and
``cairn knowledge rebuild`` output -- naming the doc and the pointer --
instead of silently staying frontmatter-only.
"""
from __future__ import annotations

import json

import pytest

from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.executor import execute_manifest
from cairn.knowledge.index import rebuild_knowledge_index
from cairn.knowledge.search import search_knowledge
from cairn.knowledge.store import add_document, list_documents
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store

PLAIN_DOC = (
    "---\n"
    "title: Plain doc\n"
    "status: accepted\n"
    "tags: [ops]\n"
    "description: No relationships declared.\n"
    "---\n"
    "Ordinary operational notes.\n"
)

GHOST_LINK_DOC = (
    "---\n"
    "title: Ghost link\n"
    "status: accepted\n"
    "relates_to:\n"
    "  - concept_id: knowledge/spec/ghost\n"
    "    relation: relates-to\n"
    "    kind: extracted\n"
    "---\n"
    "Declares a link to nothing.\n"
)

RUN_PATH_LINK_DOC = (
    "---\n"
    "title: Run path link\n"
    "status: accepted\n"
    "relates_to: [workspace/target.md]\n"
    "---\n"
    "Names a sibling by source path.\n"
)

TARGET_DOC = (
    "---\n"
    "title: Run target\n"
    "status: accepted\n"
    "---\n"
    "Linked by a sibling.\n"
)

PROMOTED_PATH_LINK_DOC = (
    "---\n"
    "title: Promoted path link\n"
    "status: accepted\n"
    "relates_to: [docs/adr-0001-postgres.md]\n"
    "---\n"
    "Names a promoted doc by source path.\n"
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Hermetic store: every write lands under tmp_path (see
    test_ingest_execute.py for the rationale behind each env patch)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAIRN_WORKSPACE", raising=False)
    monkeypatch.delenv("CAIRN_STORE_KEY", raising=False)
    home = tmp_path / "cairn-home"
    monkeypatch.setenv("CAIRN_HOME", str(home))
    monkeypatch.setenv("CAIRN_DB", str(home / ".kg"))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(home / ".knowledge"))

    import cairn.paths as paths_mod

    monkeypatch.setattr(paths_mod, "CAIRN_HOME", home)
    monkeypatch.setattr(paths_mod, "REGISTRY_FILE", home / "workspaces.json")
    yield tmp_path


def _bundle() -> OKFBundle:
    return OKFBundle(str(resolve_store().knowledge))


def _conn():
    from cairn.cli.main import get_db

    resolve_store().ensure()
    return get_db()


def _rebuild():
    """One rebuild on a fresh connection (committed); returns the report."""
    conn = _conn()
    try:
        report = rebuild_knowledge_index(conn, _bundle())
        conn.commit()
        return report
    finally:
        conn.close()


def _cli(workspace, *args):
    from click.testing import CliRunner
    from cairn.cli.knowledge import knowledge

    return CliRunner().invoke(knowledge, list(args))


def _write_doc(workspace, name, text, subdir="docs"):
    docs = workspace / subdir
    docs.mkdir(exist_ok=True)
    (docs / name).write_text(text, encoding="utf-8")
    return docs


def _ingest(workspace, text, name="plain.md"):
    """Stage + execute one ingest run; returns (manifest, report)."""
    docs = _write_doc(workspace, name, text)
    manifest = run_ingest(files=[], dirs=[docs], outbox=workspace / "outbox")
    conn = _conn()
    try:
        return manifest, execute_manifest(manifest, conn)
    finally:
        conn.close()


def _edges(conn):
    return sorted(
        (r[0], r[1], r[2], r[3])
        for r in conn.execute(
            "SELECT doc_id, related_id, relation, kind FROM knowledge_edges"
        ).fetchall()
    )


# --- VAL-INGEST-017: plain-doc ingest is indistinguishable from before ---


class TestPlainDocBackwardsCompat:
    def test_promoted_frontmatter_keeps_pre_mission_shape(self, workspace):
        manifest, _ = _ingest(workspace, PLAIN_DOC)
        row = manifest["rows"][0]
        (doc,) = list_documents(_bundle())
        # Core keys exactly as v0.18.0 composed them (identity pipeline
        # included): the display title, tags, resource, and body survive.
        assert doc.title == row["title"]
        assert doc.description == "No relationships declared."
        assert doc.tags == ["ops", "workspace-plain-md", "workspace"]
        assert doc.type == "Knowledge-spec"
        # Bundle reads carry path-shaped ids; the stored id is the bare one.
        from cairn.knowledge.store import normalize_doc_id

        assert normalize_doc_id(_bundle(), doc.concept_id) == row["concept_id"]
        assert doc.resource == "workspace/plain.md"
        assert "Ordinary operational notes." in doc.body
        # Exactly the v0.18.0 knowledge extensions -- no relationship or
        # verified-family keys appear for a relationship-free doc.
        assert doc.extensions == {
            "tier": "asserted",
            "doc_status": "active",
            "doc_owner": "",
            "doc_source": "imported",
            "epic_link": "",
            "affects_modules": [],
            "affects_repos": ["workspace"],
        }
        assert not doc.verified
        assert not doc.sources

    def test_plain_doc_is_searchable_and_embedding_unchanged(self, workspace):
        manifest, report = _ingest(workspace, PLAIN_DOC)
        cid = manifest["rows"][0]["concept_id"]
        from cairn.graph import embeddings as emb

        if emb.embeddings_available():
            assert report["embedded"] == 1
        else:
            assert report["embedded"] is None
        conn = _conn()
        try:
            hits = search_knowledge(conn, _bundle(), "ordinary operational", limit=5)
        finally:
            conn.close()
        assert [h["concept_id"] for h in hits] == [cid]

    def test_plain_doc_dry_run_output_unchanged(self, workspace):
        docs = _write_doc(workspace, "plain.md", PLAIN_DOC)
        result = _cli(
            workspace, "ingest", "--dir", str(docs),
            "--outbox", str(workspace / "outbox"),
        )
        assert result.exit_code == 0, result.output
        assert "staged: workspace/plain.md -> knowledge/" in result.output
        assert "warning" not in result.output.lower()
        # Dry run: still no store write.
        assert not resolve_store().knowledge.exists()

    def test_plain_doc_ingest_output_has_no_warnings(self, workspace):
        docs = _write_doc(workspace, "plain.md", PLAIN_DOC)
        result = _cli(
            workspace, "ingest", "--dir", str(docs),
            "--outbox", str(workspace / "outbox"), "--ingest",
        )
        assert result.exit_code == 0, result.output
        assert "warning" not in result.output.lower()


# --- VAL-INGEST-018: flag set + 22-tool MCP surface unchanged ---


class TestSurfacesUnchanged:
    def test_ingest_flag_set_unchanged(self):
        result = _cli(None, "ingest", "--help")
        assert result.exit_code == 0
        for flag in (
            "--file",
            "--dir",
            "--repo",
            "--ingest",
            "--include-drafts",
            "--outbox",
        ):
            assert flag in result.output

    def test_mcp_boot_verifies_exactly_22_tools(self):
        from cairn.mcp_server.server import _EXPECTED_TOOL_COUNT, verify_tool_count

        verify_tool_count()  # raises AssertionError on drift
        assert _EXPECTED_TOOL_COUNT == 22


# --- dangling relates_to pointers are surfaced, never silent ---


class TestRebuildDanglingWarnings:
    def test_rebuild_report_names_doc_and_pointer(self, workspace):
        bundle = _bundle()
        cid = add_document(
            bundle,
            title="Ghost link",
            body="Declares a link to nothing.",
            doc_type="spec",
            relationships=[
                {
                    "concept_id": "knowledge/spec/ghost",
                    "relation": "relates-to",
                    "kind": "extracted",
                }
            ],
        )
        report = _rebuild()
        assert report["dangling"] == [
            {"doc_id": cid, "concept_id": "knowledge/spec/ghost"}
        ]
        conn = _conn()
        try:
            assert _edges(conn) == []
        finally:
            conn.close()

    def test_rebuild_report_clear_when_everything_resolves(self, workspace):
        bundle = _bundle()
        cid_b = add_document(bundle, title="Target", body=".", doc_type="spec")
        add_document(
            bundle,
            title="Source",
            body=".",
            doc_type="spec",
            relationships=[
                {"concept_id": cid_b, "relation": "relates-to", "kind": "extracted"}
            ],
        )
        report = _rebuild()
        assert report["dangling"] == []

    def test_self_pointer_resolves_and_is_not_flagged(self, workspace):
        add_document(
            bundle=_bundle(),
            title="Self path doc",
            body="Body.",
            doc_type="spec",
            resource="/docs/self.md",
            relationships=[
                {
                    "concept_id": "docs/self.md",
                    "relation": "relates-to",
                    "kind": "extracted",
                }
            ],
        )
        report = _rebuild()
        assert report["dangling"] == []
        conn = _conn()
        try:
            assert _edges(conn) == []
        finally:
            conn.close()

    def test_rebuild_cli_output_warns_naming_doc_and_pointer(self, workspace):
        bundle = _bundle()
        cid = add_document(
            bundle,
            title="Ghost link",
            body="Declares a link to nothing.",
            doc_type="spec",
            relationships=[
                {
                    "concept_id": "knowledge/spec/ghost",
                    "relation": "relates-to",
                    "kind": "extracted",
                }
            ],
        )
        db = str(workspace / "cairn-home" / ".kg")
        for _ in range(2):  # visibility is stable across idempotent rebuilds
            result = _cli(workspace, "rebuild", "--db", db)
            assert result.exit_code == 0, result.output
            assert "warning" in result.output
            assert cid in result.output
            assert "knowledge/spec/ghost" in result.output

    def test_rebuild_cli_quiet_without_dangling_pointers(self, workspace):
        bundle = _bundle()
        cid_b = add_document(bundle, title="Target", body=".", doc_type="spec")
        add_document(
            bundle,
            title="Source",
            body=".",
            doc_type="spec",
            relationships=[
                {"concept_id": cid_b, "relation": "relates-to", "kind": "extracted"}
            ],
        )
        db = str(workspace / "cairn-home" / ".kg")
        result = _cli(workspace, "rebuild", "--db", db)
        assert result.exit_code == 0, result.output
        assert "warning" not in result.output.lower()


class TestDryRunDanglingWarnings:
    def test_dry_run_warns_naming_doc_and_pointer(self, workspace):
        docs = _write_doc(workspace, "ghost-link.md", GHOST_LINK_DOC)
        outbox = workspace / "outbox"
        result = _cli(workspace, "ingest", "--dir", str(docs), "--outbox", str(outbox))
        assert result.exit_code == 0, result.output
        assert "warning" in result.output
        assert "knowledge/spec/ghost" in result.output
        assert "workspace/ghost-link.md" in result.output
        # The declared pointer still stages verbatim: the warning is
        # advisory, frontmatter stays the durable record.
        manifest = json.loads((outbox / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["rows"][0]["relationships"] == [
            {
                "concept_id": "knowledge/spec/ghost",
                "relation": "relates-to",
                "kind": "extracted",
            }
        ]
        assert not resolve_store().knowledge.exists()

    def test_dry_run_quiet_when_pointer_names_same_run_doc(self, workspace):
        _write_doc(workspace, "target.md", TARGET_DOC)
        _write_doc(workspace, "run-link.md", RUN_PATH_LINK_DOC)
        docs = workspace / "docs"
        result = _cli(
            workspace, "ingest", "--dir", str(docs),
            "--outbox", str(workspace / "outbox"),
        )
        assert result.exit_code == 0, result.output
        assert "warning" not in result.output.lower()

    def test_dry_run_quiet_when_pointer_names_promoted_doc(self, workspace):
        bundle = _bundle()
        add_document(
            bundle,
            title="Postgres decision",
            body="Target body.",
            doc_type="decision",
            resource="/docs/adr-0001-postgres.md",
        )
        docs = _write_doc(workspace, "promoted-link.md", PROMOTED_PATH_LINK_DOC)
        result = _cli(
            workspace, "ingest", "--dir", str(docs),
            "--outbox", str(workspace / "outbox"),
        )
        assert result.exit_code == 0, result.output
        assert "warning" not in result.output.lower()


class TestIngestDanglingWarnings:
    def test_ingest_report_and_output_surface_dangling_pointers(self, workspace):
        docs = _write_doc(workspace, "ghost-link.md", GHOST_LINK_DOC)
        result = _cli(
            workspace, "ingest", "--dir", str(docs),
            "--outbox", str(workspace / "outbox"), "--ingest",
        )
        assert result.exit_code == 0, result.output
        assert "warning" in result.output
        assert "knowledge/spec/ghost" in result.output
        # The promoted frontmatter keeps the pointer verbatim.
        (doc,) = list_documents(_bundle())
        assert doc.extensions["relates_to"][0]["concept_id"] == "knowledge/spec/ghost"

    def test_execute_report_carries_dangling_pointers(self, workspace):
        docs = _write_doc(workspace, "ghost-link.md", GHOST_LINK_DOC)
        manifest = run_ingest(files=[], dirs=[docs], outbox=workspace / "outbox")
        cid = manifest["rows"][0]["concept_id"]
        conn = _conn()
        try:
            report = execute_manifest(manifest, conn)
        finally:
            conn.close()
        assert report["dangling_pointers"] == [
            {"doc_id": cid, "concept_id": "knowledge/spec/ghost"}
        ]

    def test_ingest_run_resolving_pointers_warns_nothing(self, workspace):
        _write_doc(workspace, "target.md", TARGET_DOC)
        _write_doc(workspace, "run-link.md", RUN_PATH_LINK_DOC)
        docs = workspace / "docs"
        result = _cli(
            workspace, "ingest", "--dir", str(docs),
            "--outbox", str(workspace / "outbox"), "--ingest",
        )
        assert result.exit_code == 0, result.output
        assert "warning" not in result.output.lower()
        conn = _conn()
        try:
            edges = _edges(conn)
        finally:
            conn.close()
        # The declared pointer indexed as one directional extracted edge.
        assert len(edges) == 1
        assert edges[0][2] == "relates-to"
        assert edges[0][3] == "extracted"
