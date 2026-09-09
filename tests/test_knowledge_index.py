"""Derived knowledge index: knowledge_edges + knowledge_doc_refs (D1).

Covers the validation-contract behaviors: VAL-INGEST-003 (ingested links
queryable as knowledge_edges rows with correct kind), VAL-INGEST-008
(tag/module overlap materialized as kind=derived edges) and VAL-INGEST-009
(running the rebuild twice leaves table contents identical), plus the
normalized-id helper the rebuild and later CLI/dashboard consumers share.
"""
from __future__ import annotations

import pytest

from cairn.knowledge.index import rebuild_knowledge_index
from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.executor import execute_manifest
from cairn.knowledge.store import add_document
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store

LINKED_DOC = (
    "---\n"
    "title: Linked doc\n"
    "status: accepted\n"
    "tags: [linking]\n"
    "relates_to:\n"
    "  - concept_id: {target}\n"
    "    relation: relates-to\n"
    "    kind: extracted\n"
    "---\n"
    "Declares a link.\n"
)

TAGGED_DOC = (
    "---\n"
    "title: {title}\n"
    "status: accepted\n"
    "tags: [ops]\n"
    "---\n"
    "{body}\n"
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


def _rebuild(conn=None):
    """One rebuild on a fresh connection (committed); returns the report."""
    close = conn is None
    conn = conn or _conn()
    try:
        report = rebuild_knowledge_index(conn, _bundle())
        conn.commit()
        return report
    finally:
        if close:
            conn.close()


def _edges(conn):
    """Sorted (doc_id, related_id, relation, kind) tuples."""
    return sorted(
        (r[0], r[1], r[2], r[3])
        for r in conn.execute(
            "SELECT doc_id, related_id, relation, kind FROM knowledge_edges"
        ).fetchall()
    )


def _edge_rows(conn):
    """Sorted full knowledge_edges rows, created_at included."""
    return sorted(
        (r[0], r[1], r[2], r[3], r[4], r[5])
        for r in conn.execute(
            "SELECT doc_id, related_id, relation, kind, provenance, created_at "
            "FROM knowledge_edges"
        ).fetchall()
    )


def _dump(conn):
    """Full contents of both index tables, deterministically ordered."""
    edges = [tuple(r) for r in conn.execute(
        "SELECT doc_id, related_id, relation, kind, provenance, created_at "
        "FROM knowledge_edges ORDER BY doc_id, related_id, relation, kind"
    ).fetchall()]
    refs = [tuple(r) for r in conn.execute(
        "SELECT doc_id, ref, ref_kind, verified FROM knowledge_doc_refs "
        "ORDER BY doc_id, ref, ref_kind"
    ).fetchall()]
    return edges, refs


def _ingest_dir(workspace, docs_dir, outbox="outbox"):
    """Stage + execute one ingest run; returns (manifest, report)."""
    manifest = run_ingest(
        files=[], dirs=[docs_dir], outbox=workspace / outbox
    )
    conn = _conn()
    try:
        report = execute_manifest(manifest, conn)
    finally:
        conn.close()
    return manifest, report


def _add_doc(bundle, title, *, tags=None, modules=None, relationships=None):
    return add_document(
        bundle,
        title=title,
        body=f"Body of {title}.",
        doc_type="spec",
        tags=tags or [],
        affects_modules=modules or [],
        relationships=relationships,
    )


# --- VAL-INGEST-003: ingested links queryable as knowledge_edges rows ---


class TestExtractedEdges:
    def test_declared_link_becomes_exactly_one_edge_row(self, workspace):
        bundle = _bundle()
        cid_b = _add_doc(bundle, "Target doc")
        cid_a = _add_doc(
            bundle,
            "Source doc",
            relationships=[
                {"concept_id": cid_b, "relation": "relates-to", "kind": "extracted"}
            ],
        )
        report = _rebuild()
        conn = _conn()
        try:
            assert _edges(conn) == [(cid_a, cid_b, "relates-to", "extracted")]
        finally:
            conn.close()
        assert report["edges"] == 1

    def test_ingest_end_to_end_indexes_declared_link(self, workspace):
        docs_b = workspace / "docs-b"
        docs_b.mkdir()
        (docs_b / "target.md").write_text(
            TAGGED_DOC.format(title="Ingest target", body="Target body."),
            encoding="utf-8",
        )
        manifest_b, _ = _ingest_dir(workspace, docs_b, outbox="outbox-b")
        cid_b = manifest_b["rows"][0]["concept_id"]

        docs_a = workspace / "docs-a"
        docs_a.mkdir()
        (docs_a / "source.md").write_text(
            LINKED_DOC.format(target=cid_b), encoding="utf-8"
        )
        manifest_a, report = _ingest_dir(workspace, docs_a, outbox="outbox-a")
        cid_a = manifest_a["rows"][0]["concept_id"]

        assert report["index_edges"] == 1
        conn = _conn()
        try:
            rows = [r for r in _edges(conn) if r[0] == cid_a]
            assert rows == [(cid_a, cid_b, "relates-to", "extracted")]
        finally:
            conn.close()

    def test_reingest_does_not_duplicate_rows(self, workspace):
        bundle = _bundle()
        cid_b = _add_doc(bundle, "Target doc")
        _add_doc(
            bundle,
            "Source doc",
            relationships=[
                {"concept_id": cid_b, "relation": "relates-to", "kind": "extracted"}
            ],
        )
        _rebuild()
        _rebuild()
        _rebuild()
        conn = _conn()
        try:
            assert len(_edges(conn)) == 1
        finally:
            conn.close()

    def test_dangling_pointer_stays_out_of_the_index(self, workspace):
        bundle = _bundle()
        cid_a = _add_doc(
            bundle,
            "Dangling source",
            relationships=[
                {
                    "concept_id": "knowledge/spec/ghost",
                    "relation": "relates-to",
                    "kind": "extracted",
                }
            ],
        )
        _rebuild()
        conn = _conn()
        try:
            assert _edges(conn) == []
        finally:
            conn.close()
        # The durable record keeps the declared pointer.
        doc = bundle.read_concept(cid_a)
        assert doc.extensions["relates_to"][0]["concept_id"] == "knowledge/spec/ghost"


# --- VAL-INGEST-008: tag/module overlap materialized as derived edges ---


class TestDerivedEdges:
    def test_tag_overlap_yields_derived_edges_both_directions(self, workspace):
        bundle = _bundle()
        cid_a = _add_doc(bundle, "Ops A", tags=["ops"], modules=["src/a"])
        cid_b = _add_doc(bundle, "Ops B", tags=["ops"], modules=["src/b"])
        _rebuild()
        conn = _conn()
        try:
            assert set(_edges(conn)) == {
                (cid_a, cid_b, "relates-to", "derived"),
                (cid_b, cid_a, "relates-to", "derived"),
            }
        finally:
            conn.close()

    def test_tag_overlap_provenance(self, workspace):
        bundle = _bundle()
        _add_doc(bundle, "Ops A", tags=["ops"], modules=["src/a"])
        _add_doc(bundle, "Ops B", tags=["ops"], modules=["src/b"])
        _rebuild()
        conn = _conn()
        try:
            provenance = {r[0] for r in conn.execute(
                "SELECT provenance FROM knowledge_edges"
            ).fetchall()}
        finally:
            conn.close()
        assert provenance == {"tag-overlap"}

    def test_module_overlap_provenance(self, workspace):
        bundle = _bundle()
        _add_doc(bundle, "Mod A", tags=["one"], modules=["src/shared"])
        _add_doc(bundle, "Mod B", tags=["two"], modules=["src/shared"])
        _rebuild()
        conn = _conn()
        try:
            provenance = {r[0] for r in conn.execute(
                "SELECT provenance FROM knowledge_edges"
            ).fetchall()}
        finally:
            conn.close()
        assert provenance == {"module-overlap"}

    def test_tag_and_module_overlap_combined_provenance(self, workspace):
        bundle = _bundle()
        _add_doc(bundle, "Both A", tags=["ops"], modules=["src/shared"])
        _add_doc(bundle, "Both B", tags=["ops"], modules=["src/shared"])
        _rebuild()
        conn = _conn()
        try:
            provenance = {r[0] for r in conn.execute(
                "SELECT provenance FROM knowledge_edges"
            ).fetchall()}
        finally:
            conn.close()
        assert provenance == {"tag+module-overlap"}

    def test_no_overlap_yields_no_derived_edges(self, workspace):
        bundle = _bundle()
        _add_doc(bundle, "Solo A", tags=["one"], modules=["src/a"])
        _add_doc(bundle, "Solo B", tags=["two"], modules=["src/b"])
        report = _rebuild()
        conn = _conn()
        try:
            assert _edges(conn) == []
        finally:
            conn.close()
        assert report["derived"] == 0

    def test_declared_pair_gets_no_derived_duplicate(self, workspace):
        bundle = _bundle()
        cid_b = _add_doc(bundle, "Declared B", tags=["ops"], modules=["src/b"])
        cid_a = _add_doc(
            bundle,
            "Declared A",
            tags=["ops"],
            modules=["src/a"],
            relationships=[
                {"concept_id": cid_b, "relation": "relates-to", "kind": "extracted"}
            ],
        )
        _rebuild()
        conn = _conn()
        try:
            assert set(_edges(conn)) == {(cid_a, cid_b, "relates-to", "extracted")}
        finally:
            conn.close()

    def test_ingest_overlap_docs_produce_derived_edges(self, workspace):
        docs = workspace / "tagged"
        (docs / "one").mkdir(parents=True)
        (docs / "two").mkdir(parents=True)
        (docs / "one" / "first.md").write_text(
            TAGGED_DOC.format(title="Tagged one", body="First."), encoding="utf-8"
        )
        (docs / "two" / "second.md").write_text(
            TAGGED_DOC.format(title="Tagged two", body="Second."), encoding="utf-8"
        )
        manifest, report = _ingest_dir(workspace, docs)
        cid_a = manifest["rows"][0]["concept_id"]
        cid_b = manifest["rows"][1]["concept_id"]
        assert report["index_edges"] == 2
        conn = _conn()
        try:
            assert set(_edges(conn)) == {
                (cid_a, cid_b, "relates-to", "derived"),
                (cid_b, cid_a, "relates-to", "derived"),
            }
        finally:
            conn.close()


# --- VAL-INGEST-009: rebuild is idempotent ---


class TestRebuildIdempotency:
    def _mixed_corpus(self, bundle):
        cid_b = _add_doc(bundle, "Link target", tags=["link"], modules=["src/t"])
        cid_a = _add_doc(
            bundle,
            "Link source",
            tags=["solo"],
            modules=["src/s"],
            relationships=[
                {"concept_id": cid_b, "relation": "relates-to", "kind": "extracted"}
            ],
        )
        _add_doc(bundle, "Overlap A", tags=["ops"], modules=["src/x"])
        _add_doc(bundle, "Overlap B", tags=["ops"], modules=["src/y"])
        return cid_a, cid_b

    def test_rebuild_twice_identical_table_contents(self, workspace):
        self._mixed_corpus(_bundle())
        _rebuild()
        conn = _conn()
        try:
            first = _dump(conn)
        finally:
            conn.close()
        _rebuild()
        conn = _conn()
        try:
            second = _dump(conn)
        finally:
            conn.close()
        assert first == second
        # created_at stamps survive: churn would show up in the dump above,
        # but pin the count explicitly (edges = 1 extracted + 2 derived).
        assert len(first[0]) == 3

    def test_rebuild_preserves_created_at_for_unchanged_edges(self, workspace):
        bundle = _bundle()
        cid_b = _add_doc(bundle, "Target", tags=["ops"])
        cid_a = _add_doc(bundle, "Source", tags=["ops"])
        _rebuild()
        conn = _conn()
        try:
            stamps_before = {
                (r[0], r[1]): r[2]
                for r in conn.execute(
                    "SELECT doc_id, related_id, created_at FROM knowledge_edges"
                ).fetchall()
            }
        finally:
            conn.close()
        _rebuild()
        conn = _conn()
        try:
            stamps_after = {
                (r[0], r[1]): r[2]
                for r in conn.execute(
                    "SELECT doc_id, related_id, created_at FROM knowledge_edges"
                ).fetchall()
            }
        finally:
            conn.close()
        assert stamps_before == stamps_after
        assert set(stamps_before) == {(cid_a, cid_b), (cid_b, cid_a)}

    def test_rebuild_drops_edges_of_deleted_docs(self, workspace):
        bundle = _bundle()
        cid_b = _add_doc(bundle, "Doomed target", tags=["ops"])
        cid_a = _add_doc(
            bundle,
            "Survivor",
            tags=["ops"],
            relationships=[
                {"concept_id": cid_b, "relation": "relates-to", "kind": "extracted"}
            ],
        )
        _rebuild()
        from pathlib import Path

        (Path(bundle.root) / f"{cid_b}.md").unlink()
        _rebuild()
        conn = _conn()
        try:
            assert (cid_a, cid_b, "relates-to", "extracted") not in _edges(conn)
            assert _edges(conn) == []
        finally:
            conn.close()

    def test_cli_rebuild_verb_is_idempotent(self, workspace):
        self._mixed_corpus(_bundle())
        _rebuild()
        conn = _conn()
        try:
            before = _dump(conn)
        finally:
            conn.close()

        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        db = str(workspace / "cairn-home" / ".kg")
        for _ in range(2):
            result = CliRunner().invoke(knowledge, ["rebuild", "--db", db])
            assert result.exit_code == 0, result.output

        conn = _conn()
        try:
            after = _dump(conn)
        finally:
            conn.close()
        assert before == after
        assert "edge(s)" in CliRunner().invoke(
            knowledge, ["rebuild", "--db", db]
        ).output


# --- knowledge_doc_refs: the sources/verified family materialized ---


class TestDocRefs:
    def _doc_with_refs(self, bundle, verified=None, sources=None):
        cid = _add_doc(bundle, "Referencing doc")
        concept = bundle.read_concept(cid)
        if verified is not None:
            concept.verified = verified
        if sources is not None:
            concept.sources = sources
        bundle.write_concept(concept)
        return cid

    def test_verified_entries_become_rows(self, workspace):
        bundle = _bundle()
        cid = self._doc_with_refs(
            bundle,
            verified=[
                {"ref": "src/cairn/refs.py", "kind": "file", "verified": True},
                {"ref": "symbol_exists", "kind": "symbol", "verified": True},
            ],
        )
        _rebuild()
        conn = _conn()
        try:
            rows = sorted(
                (r[0], r[1], r[2], r[3])
                for r in conn.execute(
                    "SELECT doc_id, ref, ref_kind, verified FROM knowledge_doc_refs"
                ).fetchall()
            )
        finally:
            conn.close()
        assert rows == [
            (cid, "src/cairn/refs.py", "file", 1),
            (cid, "symbol_exists", "symbol", 1),
        ]

    def test_missing_kind_defaults_to_file_and_flag_is_mirrored(self, workspace):
        bundle = _bundle()
        cid = self._doc_with_refs(
            bundle,
            verified=[{"ref": "src/plain/path.py"}],
        )
        _rebuild()
        conn = _conn()
        try:
            rows = [tuple(r) for r in conn.execute(
                "SELECT ref_kind, verified FROM knowledge_doc_refs WHERE doc_id = ?",
                (cid,),
            ).fetchall()]
        finally:
            conn.close()
        assert rows == [("file", 0)]

    def test_sources_family_is_read_too(self, workspace):
        bundle = _bundle()
        cid = self._doc_with_refs(
            bundle,
            sources=[{"ref": "README.md", "kind": "file", "verified": True}],
        )
        _rebuild()
        conn = _conn()
        try:
            rows = [tuple(r) for r in conn.execute(
                "SELECT ref, ref_kind, verified FROM knowledge_doc_refs WHERE doc_id = ?",
                (cid,),
            ).fetchall()]
        finally:
            conn.close()
        assert rows == [("README.md", "file", 1)]

    def test_same_ref_across_families_indexes_once_with_verified_max(
        self, workspace
    ):
        """knowledge_doc_refs' primary key is (doc_id, ref, ref_kind): a
        ref carried by BOTH the sources and verified families with
        differing verified flags indexes as one row, verified=max (a ref
        either family verifies is verified). Two rows would crash the
        rebuild on the PK -- and the rebuild runs after every ingest."""
        bundle = _bundle()
        cid = self._doc_with_refs(
            bundle,
            verified=[{"ref": "src/shared.py", "kind": "file", "verified": True}],
            sources=[{"ref": "src/shared.py", "kind": "file"}],
        )
        report = _rebuild()
        conn = _conn()
        try:
            rows = [tuple(r) for r in conn.execute(
                "SELECT ref, ref_kind, verified FROM knowledge_doc_refs "
                "WHERE doc_id = ?",
                (cid,),
            ).fetchall()]
        finally:
            conn.close()
        assert rows == [("src/shared.py", "file", 1)]
        assert report["doc_refs"] == 1

    def test_entries_without_a_ref_are_skipped(self, workspace):
        bundle = _bundle()
        self._doc_with_refs(
            bundle,
            verified=[{"kind": "file"}, "a-bare-string", {"ref": "  "}],
        )
        report = _rebuild()
        conn = _conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM knowledge_doc_refs"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 0
        assert report["doc_refs"] == 0


# --- id normalization: one helper for rebuild + later consumers ---


class TestNormalizeDocId:
    def test_bare_id_passes_through(self, workspace):
        from cairn.knowledge.store import normalize_doc_id

        bundle = _bundle()
        assert (
            normalize_doc_id(bundle, "knowledge/spec/refund-policy")
            == "knowledge/spec/refund-policy"
        )

    def test_md_suffix_stripped(self, workspace):
        from cairn.knowledge.store import normalize_doc_id

        assert (
            normalize_doc_id(_bundle(), "knowledge/spec/refund-policy.md")
            == "knowledge/spec/refund-policy"
        )

    def test_absolute_path_inside_root_normalized(self, workspace):
        from cairn.knowledge.store import normalize_doc_id

        bundle = _bundle()
        shaped = str(bundle.root / "knowledge/spec/refund-policy")
        assert normalize_doc_id(bundle, shaped) == "knowledge/spec/refund-policy"

    def test_path_outside_root_best_effort(self, workspace):
        from cairn.knowledge.store import normalize_doc_id

        bundle = _bundle()
        assert (
            normalize_doc_id(bundle, "/somewhere/else/knowledge/spec/x.md")
            == "/somewhere/else/knowledge/spec/x"
        )

    def test_path_shaped_frontmatter_pointer_normalized_in_index(self, workspace):
        bundle = _bundle()
        cid_b = _add_doc(bundle, "Path target")
        cid_a = _add_doc(bundle, "Path source")
        concept = bundle.read_concept(cid_a)
        concept.extensions["relates_to"] = [
            {
                "concept_id": str(bundle.root / f"{cid_b}.md"),
                "relation": "relates-to",
                "kind": "extracted",
            }
        ]
        bundle.write_concept(concept)
        _rebuild()
        conn = _conn()
        try:
            assert _edges(conn) == [(cid_a, cid_b, "relates-to", "extracted")]
        finally:
            conn.close()

    def test_source_path_pointer_resolves_via_resource(self, workspace):
        """Authors may declare a link by source path (pre-concept-id
        frontmatter); ingest stores that path as the target's resource."""
        bundle = _bundle()
        cid_b = add_document(
            bundle,
            title="Resourced target",
            body="Target body.",
            doc_type="spec",
            resource="/docs/adr-0001-postgres.md",
        )
        cid_a = _add_doc(
            bundle,
            "Resourced source",
            relationships=[
                {
                    "concept_id": "docs/adr-0001-postgres.md",
                    "relation": "relates-to",
                    "kind": "extracted",
                }
            ],
        )
        _rebuild()
        conn = _conn()
        try:
            assert _edges(conn) == [(cid_a, cid_b, "relates-to", "extracted")]
        finally:
            conn.close()

    def test_resource_pointer_to_self_is_skipped(self, workspace):
        bundle = _bundle()
        add_document(
            bundle,
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
        _rebuild()
        conn = _conn()
        try:
            assert _edges(conn) == []
        finally:
            conn.close()


# --- executor integration: --ingest rebuilds the index automatically ---


class TestExecuteManifestIntegration:
    def test_report_carries_index_counts(self, workspace):
        docs = workspace / "report-docs"
        (docs / "a").mkdir(parents=True)
        (docs / "b").mkdir(parents=True)
        (docs / "a" / "one.md").write_text(
            TAGGED_DOC.format(title="Report one", body="One."), encoding="utf-8"
        )
        (docs / "b" / "two.md").write_text(
            TAGGED_DOC.format(title="Report two", body="Two."), encoding="utf-8"
        )
        _, report = _ingest_dir(workspace, docs)
        assert report["index_edges"] == 2
        assert report["index_doc_refs"] == 0
