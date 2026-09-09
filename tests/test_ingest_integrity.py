"""Ingest integrity: the count leg on incremental runs, durable inferred
links, bare-pointer resolution, and machine-readable ``knowledge list``.

Pinned behaviors:

1. The post-ingest count verify leg compares the whole store against the
   full expected population (``pre_existing + accepted - overwritten``),
   so an incremental ``--ingest`` into a non-empty store verifies and
   exits 0 exactly like a first-into-empty run; under-writes still fail.
2. Re-ingesting a document preserves its promoted ``kind: inferred``
   ``relates_to`` entries (critic-approved doc-link results), so the
   frontmatter record and the rebuilt ``knowledge_edges`` index keep
   approved links across re-scans.
3. A bare repo-relative ``relates_to`` pointer resolves through its
   resource-prefixed form (fed docs promote ``workspace/<relpath>``
   resources) and indexes as a declared edge; genuinely unresolvable
   pointers still warn.
4. ``cairn knowledge list --json`` emits one machine-readable row per
   document, keyed by bare bundle-relative concept_id.
"""
from __future__ import annotations

import json

import pytest

from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.executor import execute_manifest
from cairn.knowledge.store import list_documents
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store

pytestmark = pytest.mark.usefixtures("hash_backend")

DOC_A = "---\ntitle: Alpha doc\nstatus: accepted\n---\nAlpha body.\n"
DOC_B = "---\ntitle: Beta doc\nstatus: accepted\n---\nBeta body.\n"
DOC_C = "---\ntitle: Gamma doc\nstatus: accepted\n---\nGamma body.\n"

FED_TARGET = "---\ntitle: Fed target\nstatus: accepted\n---\nTarget body.\n"
FED_LINKER_BARE = (
    "---\ntitle: Fed linker\nstatus: accepted\n"
    "relates_to: [target.md]\n---\nNames a sibling by bare path.\n"
)
FED_LINKER_PROMOTED = (
    "---\ntitle: Repo linker\nstatus: accepted\n"
    "relates_to: [docs/adr.md]\n---\nNames a scanned doc by bare path.\n"
)
FED_LINKER_GHOST = (
    "---\ntitle: Ghost linker\nstatus: accepted\n"
    "relates_to: [nowhere.md]\n---\nNames nothing.\n"
)
SCAN_TARGET = "---\ntitle: Scanned ADR\nstatus: accepted\n---\nScanned body.\n"


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


def _write_docs(directory, files):
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


def _stage(workspace, files, subdir="docs", outbox="outbox", repos=()):
    """Stage one ingest run over fed files (and/or repo scans)."""
    docs = _write_docs(workspace / subdir, files) if files else None
    return run_ingest(
        files=[],
        dirs=[docs] if docs else [],
        outbox=workspace / outbox,
        repos=list(repos),
    )


def _execute(manifest):
    conn = _conn()
    try:
        return execute_manifest(manifest, conn)
    finally:
        conn.close()


def _edges(kind, relation):
    conn = _conn()
    try:
        return conn.execute(
            "SELECT doc_id, related_id, relation, kind FROM knowledge_edges "
            "WHERE kind = ? AND relation = ?",
            (kind, relation),
        ).fetchall()
    finally:
        conn.close()


def _cli(*args):
    from click.testing import CliRunner
    from cairn.cli.knowledge import knowledge

    return CliRunner().invoke(knowledge, list(args))


# --- 1. the count verify leg on incremental ingests -------------------------


class TestIncrementalCountLeg:
    def test_second_batch_into_nonempty_store_counts_ok(self, workspace):
        first = _stage(workspace, {"a.md": DOC_A, "b.md": DOC_B}, subdir="d1",
                       outbox="ob-1")
        r1 = _execute(first)
        second = _stage(workspace, {"c.md": DOC_C}, subdir="d2", outbox="ob-2")
        r2 = _execute(second)
        assert r1["count_ok"] is True
        assert r2["count_ok"] is True
        assert r2["store_count"] == r2["expected_count"] == 3

    def test_reingesting_the_same_docs_counts_ok(self, workspace):
        manifest = _stage(workspace, {"a.md": DOC_A, "b.md": DOC_B})
        first = _execute(manifest)
        second = _execute(manifest)
        assert first["count_ok"] is True
        assert second["count_ok"] is True
        assert second["store_count"] == second["expected_count"] == 2

    def test_mixed_batch_of_new_and_existing_docs_counts_ok(self, workspace):
        _execute(_stage(workspace, {"a.md": DOC_A}, subdir="d1", outbox="ob-1"))
        # a later batch re-feeds the existing doc alongside a new one
        mixed = _stage(workspace, {"a.md": DOC_A, "c.md": DOC_C},
                       subdir="d2", outbox="ob-2")
        report = _execute(mixed)
        assert report["count_ok"] is True
        assert report["store_count"] == report["expected_count"] == 2
        assert len(list_documents(_bundle())) == 2

    def test_incremental_cli_ingest_exits_zero(self, workspace):
        docs = _write_docs(workspace / "docs", {"a.md": DOC_A})
        result = _cli("ingest", "--dir", str(docs),
                      "--outbox", str(workspace / "ob-1"), "--ingest")
        assert result.exit_code == 0, result.output
        (docs / "c.md").write_text(DOC_C, encoding="utf-8")
        result = _cli("ingest", "--dir", str(docs),
                      "--outbox", str(workspace / "ob-2"), "--ingest")
        assert result.exit_code == 0, result.output
        assert "count_ok: True" in result.output
        assert len(list_documents(_bundle())) == 2

    def test_underwrite_still_fails_the_leg(self, workspace, monkeypatch):
        # The whole-store expectation still detects a dropped write: with
        # one row of the second batch silently skipped, the counts no
        # longer add up.
        import cairn.knowledge.ingest.executor as executor_mod

        _execute(_stage(workspace, {"a.md": DOC_A}, subdir="d1", outbox="ob-1"))
        second = _stage(workspace, {"c.md": DOC_C}, subdir="d2", outbox="ob-2")
        real = executor_mod.add_document
        calls = {"n": 0}

        def drop_first(*args, **kwargs):
            if calls["n"] == 0:
                calls["n"] += 1
                return "knowledge/spec/skipped"
            return real(*args, **kwargs)

        monkeypatch.setattr(executor_mod, "add_document", drop_first)
        report = _execute(second)
        assert report["count_ok"] is False

    def test_verify_standalone_keeps_batch_equality(self, workspace):
        # Pre-write use (no executor pre-state) keeps comparing the batch
        # alone against the store as it stands: a store not holding exactly
        # the batch is a mismatch (TC-024) -- the executor's pre-state is
        # what lets incremental runs verify.
        from cairn.knowledge.ingest.executor import verify_manifest
        from cairn.knowledge.store import add_document

        add_document(_bundle(), title="Pre-existing", body="Older.",
                     doc_type="decision")
        manifest = _stage(workspace, {"a.md": DOC_A, "b.md": DOC_B})
        conn = _conn()
        try:
            result = verify_manifest(manifest, conn)
        finally:
            conn.close()
        assert result["expected_count"] == 2
        assert result["store_count"] == 1
        assert result["count_ok"] is False


# --- 2. inferred relationships survive re-ingest ----------------------------


class TestInferredSurvivesReingest:
    def test_reingest_preserves_inferred_frontmatter_and_edges(self, workspace):
        from cairn.knowledge.doc_link import apply_doc_link_edges

        manifest = _stage(workspace, {"a.md": DOC_A, "b.md": DOC_B})
        ids = sorted(row["concept_id"] for row in manifest["rows"])
        conn = _conn()
        try:
            execute_manifest(manifest, conn)
            # A critic-approved doc-link completion (the promotion path)
            apply_doc_link_edges(_bundle(), conn, [(ids[0], "relates-to", ids[1])])
            report = execute_manifest(manifest, conn)
        finally:
            conn.close()
        assert report["count_ok"] is True
        assert report["dangling_pointers"] == []
        bundle = _bundle()
        for cid in ids:
            entries = bundle.read_concept(cid).extensions["relates_to"]
            inferred = [e for e in entries if e["kind"] == "inferred"]
            assert len(inferred) == 1
            assert inferred[0]["relation"] == "relates-to"
        assert len(_edges("inferred", "relates-to")) == 2

    def test_reingest_is_idempotent_on_the_merged_frontmatter(self, workspace):
        from cairn.knowledge.doc_link import apply_doc_link_edges

        manifest = _stage(workspace, {"a.md": DOC_A, "b.md": DOC_B})
        ids = sorted(row["concept_id"] for row in manifest["rows"])
        conn = _conn()
        try:
            execute_manifest(manifest, conn)
            apply_doc_link_edges(_bundle(), conn, [(ids[0], "relates-to", ids[1])])
            execute_manifest(manifest, conn)
            execute_manifest(manifest, conn)
        finally:
            conn.close()
        bundle = _bundle()
        for cid in ids:
            entries = bundle.read_concept(cid).extensions["relates_to"]
            assert len(entries) == 1
            assert entries[0]["kind"] == "inferred"

    def test_first_ingest_without_links_gains_no_merge(self, workspace):
        # A fresh doc's promoted frontmatter carries exactly the declared
        # relationships -- nothing is merged from an empty store.
        manifest = _stage(workspace, {"a.md": DOC_A})
        _execute(manifest)
        (doc,) = list_documents(_bundle())
        assert "relates_to" not in doc.extensions


# --- 3. bare repo-relative pointers resolve through their resources ---------


class TestBarePointerResolution:
    def test_bare_pointer_between_fed_docs_indexes(self, workspace):
        docs = _write_docs(workspace / "docs",
                           {"target.md": FED_TARGET, "linker.md": FED_LINKER_BARE})
        result = _cli("ingest", "--dir", str(docs),
                      "--outbox", str(workspace / "ob"), "--ingest")
        assert result.exit_code == 0, result.output
        assert "warning" not in result.output.lower()
        rows = _edges("extracted", "relates-to")
        assert len(rows) == 1

    def test_dry_run_bare_pointer_between_fed_docs_warns_nothing(self, workspace):
        docs = _write_docs(workspace / "docs",
                           {"target.md": FED_TARGET, "linker.md": FED_LINKER_BARE})
        result = _cli("ingest", "--dir", str(docs),
                      "--outbox", str(workspace / "ob"))
        assert result.exit_code == 0, result.output
        assert "warning" not in result.output.lower()

    def test_bare_pointer_to_repo_scanned_doc_resolves(self, workspace):
        repo = workspace / "repo-7"
        _write_docs(repo / "docs", {"adr.md": SCAN_TARGET})
        scan = run_ingest(files=[], dirs=[], outbox=workspace / "ob-scan",
                          repos=[repo])
        _execute(scan)
        docs = _write_docs(workspace / "fed", {"linker.md": FED_LINKER_PROMOTED})
        result = _cli("ingest", "--dir", str(docs),
                      "--outbox", str(workspace / "ob-fed"), "--ingest")
        assert result.exit_code == 0, result.output
        assert "warning" not in result.output.lower()
        rows = _edges("extracted", "relates-to")
        assert len(rows) == 1

    def test_unknown_pointer_still_warns_and_never_indexes(self, workspace):
        docs = _write_docs(workspace / "docs", {"ghost.md": FED_LINKER_GHOST})
        result = _cli("ingest", "--dir", str(docs),
                      "--outbox", str(workspace / "ob"), "--ingest")
        assert result.exit_code == 0, result.output
        assert "nowhere.md" in result.output
        assert _edges("extracted", "relates-to") == []

    def test_rebuild_resolves_the_same_bare_pointer(self, workspace):
        # The rebuild's resolution and the dry-run's agree: a pointer the
        # dry run resolves is not reported dangling by the rebuild either.
        from cairn.knowledge.index import rebuild_knowledge_index

        docs = _write_docs(workspace / "docs",
                           {"target.md": FED_TARGET, "linker.md": FED_LINKER_BARE})
        result = _cli("ingest", "--dir", str(docs),
                      "--outbox", str(workspace / "ob"), "--ingest")
        assert result.exit_code == 0, result.output
        conn = _conn()
        try:
            report = rebuild_knowledge_index(conn, _bundle())
            conn.commit()
        finally:
            conn.close()
        assert report["dangling"] == []


# --- 4. cairn knowledge list --json -----------------------------------------


class TestListJson:
    def test_list_json_emits_bare_concept_ids(self, workspace):
        manifest = _stage(workspace, {"a.md": DOC_A, "b.md": DOC_B})
        _execute(manifest)
        result = _cli("list", "--json")
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)
        assert {row["concept_id"] for row in rows} == {
            row["concept_id"] for row in manifest["rows"]
        }
        by_id = {row["concept_id"]: row for row in rows}
        for staged in manifest["rows"]:
            emitted = by_id[staged["concept_id"]]
            assert emitted["title"] == staged["title"]
            assert emitted["doc_type"] == staged["doc_type"]
            assert emitted["doc_status"] == "active"

    def test_list_json_type_filter(self, workspace):
        manifest = _stage(workspace, {"a.md": DOC_A})
        _execute(manifest)
        (spec_id,) = {row["concept_id"] for row in manifest["rows"]}
        result = _cli("add", "--title", "Choice record", "--body", "Decided.",
                      "--type", "decision")
        assert result.exit_code == 0, result.output
        result = _cli("list", "--json", "--type", "decision")
        rows = json.loads(result.stdout)
        assert [r["concept_id"] for r in rows] == ["knowledge/decision/choice-record"]
        result = _cli("list", "--json", "--type", "spec")
        rows = json.loads(result.stdout)
        assert [r["concept_id"] for r in rows] == [spec_id]

    def test_list_json_empty_store_emits_empty_array(self, workspace):
        result = _cli("list", "--json")
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == []

    def test_list_human_output_unchanged(self, workspace):
        manifest = _stage(workspace, {"a.md": DOC_A})
        _execute(manifest)
        (row,) = manifest["rows"]
        result = _cli("list")
        assert result.exit_code == 0, result.output
        assert f"[active] {row['title']}" in result.output
