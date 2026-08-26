"""Approved-run execution: write path, approval gate, verify, idempotency."""
from __future__ import annotations

import pytest

from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.executor import execute_manifest
from cairn.knowledge.store import list_documents
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store

ADR = (
    "---\ntitle: Use events\nstatus: accepted\n---\n"
    "# ADR: Use events\n\nPublish structured events.\n"
)
RUNBOOK = "---\ntitle: Setup runbook\nstatus: accepted\n---\nInstall it.\n"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAIRN_WORKSPACE", raising=False)
    monkeypatch.delenv("CAIRN_STORE_KEY", raising=False)
    # Hermeticity: resolve_store() reads CAIRN_DB/CAIRN_KNOWLEDGE at call
    # time, so pointing both at tmp_path keeps every store write out of the
    # developer's real ~/.cairn. The module-level CAIRN_HOME is patched too:
    # StorePaths.ensure() would otherwise mkdir an (empty) per-workspace dir
    # under the real ~/.cairn even with the two path overrides in place.
    home = tmp_path / "cairn-home"
    monkeypatch.setenv("CAIRN_HOME", str(home))
    monkeypatch.setenv("CAIRN_DB", str(home / ".kg"))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(home / ".knowledge"))

    import cairn.paths as paths_mod

    monkeypatch.setattr(paths_mod, "CAIRN_HOME", home)
    monkeypatch.setattr(paths_mod, "REGISTRY_FILE", home / "workspaces.json")
    yield tmp_path


@pytest.fixture
def staged(workspace):
    docs = workspace / "docs"
    docs.mkdir()
    (docs / "adr.md").write_text(ADR, encoding="utf-8")
    (docs / "runbook.md").write_text(RUNBOOK, encoding="utf-8")
    (docs / "draft.md").write_text("---\nstatus: draft\n---\nWIP.", encoding="utf-8")
    return run_ingest(
        files=[], dirs=[docs], outbox=workspace / "outbox"
    )


def _bundle() -> OKFBundle:
    return OKFBundle(str(resolve_store().knowledge))


def _conn():
    from cairn.cli.main import get_db
    from cairn.paths import resolve_store

    resolve_store().ensure()
    return get_db()


def test_execute_writes_every_accepted_row(staged):
    conn = _conn()
    try:
        report = execute_manifest(staged, conn)
    finally:
        conn.close()

    assert report["accepted"] == 2
    assert len(report["written"]) == 2
    docs = list_documents(_bundle())
    titles = {d.title for d in docs}
    assert any("Use events" in t for t in titles)
    assert any("Setup runbook" in t for t in titles)
    assert all(d.extensions.get("doc_source") == "imported" for d in docs)


def test_skipped_rows_are_never_written(staged):
    conn = _conn()
    try:
        execute_manifest(staged, conn)
    finally:
        conn.close()

    docs = list_documents(_bundle())
    assert not any("WIP" in d.body for d in docs)


def test_second_identical_run_is_count_stable(staged):
    conn = _conn()
    try:
        first = execute_manifest(staged, conn)
        second = execute_manifest(staged, conn)
    finally:
        conn.close()

    assert len(first["written"]) == len(second["written"]) == 2
    assert set(first["written"]) == set(second["written"])
    assert len(list_documents(_bundle())) == 2


def test_description_lands_not_the_title(staged):
    conn = _conn()
    try:
        execute_manifest(staged, conn)
    finally:
        conn.close()

    for doc in list_documents(_bundle()):
        if "Use events" in doc.title:
            assert doc.description != doc.title
            assert doc.description


def test_cli_without_ingest_leaves_store_untouched(staged, workspace):
    from click.testing import CliRunner
    from cairn.cli.knowledge import knowledge

    result = CliRunner().invoke(
        knowledge,
        ["ingest", "--dir", str(workspace / "docs"),
         "--outbox", str(workspace / "outbox2")],
    )
    assert result.exit_code == 0, result.output
    store = resolve_store().knowledge
    assert not store.exists() or not any(store.rglob("*.md"))


def test_cli_with_ingest_writes_and_reports(staged, workspace):
    from click.testing import CliRunner
    from cairn.cli.knowledge import knowledge

    result = CliRunner().invoke(
        knowledge,
        ["ingest", "--dir", str(workspace / "docs"),
         "--outbox", str(workspace / "outbox3"), "--ingest"],
    )
    assert result.exit_code == 0, result.output
    assert "Wrote 2 document(s) to the store" in result.output
    assert len(list_documents(_bundle())) == 2


class TestVerifyStep:
    def test_report_carries_verify_fields(self, staged):
        conn = _conn()
        try:
            report = execute_manifest(staged, conn)
        finally:
            conn.close()

        assert report["store_count"] == report["expected_count"] == 2
        # TC-024: the count leg requires EQUALITY with the accepted count.
        assert report["count_ok"] is True
        assert report["smoke_search_hit"] is True
        # TC-024 third leg: the cairn-validate conformance check, in-process.
        assert report["validate_ok"] is True
        assert report["validate_errors"] == 0
        assert report["validate_message"] == ""

    def test_verify_manifest_alone_reads_current_store(self, staged):
        from cairn.knowledge.ingest.executor import verify_manifest

        result = verify_manifest(staged, _conn())
        assert result["expected_count"] == 2
        assert result["store_count"] == 0
        assert result["count_ok"] is False
        assert result["smoke_search_hit"] is False
        # _conn() ensured the bundle dir: it exists but is empty, and an
        # empty bundle is conformant (0 conformance errors).
        assert result["validate_ok"] is True
        assert result["validate_errors"] == 0
        assert set(result) == {
            "store_count", "expected_count", "count_ok", "smoke_search_hit",
            "validate_ok", "validate_errors", "validate_message",
        }

    def test_verify_before_any_write_fails_validate_leg(self, workspace):
        from cairn.knowledge.ingest.executor import verify_manifest

        result = verify_manifest({"rows": [], "counts": {}}, conn=None)
        assert result["store_count"] == 0
        assert result["count_ok"] is False
        assert result["smoke_search_hit"] is False
        # Absent bundle: check_bundle reports the missing root.
        assert result["validate_ok"] is False
        assert result["validate_errors"] == 1
        assert result["validate_message"]

    def test_count_overage_fails_not_at_least(self, staged):
        # TC-024/US5-AC1: strict equality. A store holding MORE documents
        # than the manifest accepted must fail the count leg; a `>=`
        # comparison would mask it as ok.
        from cairn.knowledge.ingest.executor import verify_manifest
        from cairn.knowledge.store import add_document

        conn = _conn()
        try:
            bundle = _bundle()
            for i in range(3):
                add_document(bundle, title=f"Pre-existing {i}", body="Older.",
                             doc_type="decision")
            result = verify_manifest(staged, conn)
        finally:
            conn.close()

        assert result["store_count"] == 3
        assert result["expected_count"] == 2
        assert result["count_ok"] is False

    def test_validate_leg_degrades_not_crashes(self, staged, monkeypatch):
        # A raising conformance checker must surface as validate_ok=False
        # with the message, never crash the run.
        import cairn.okf.conformance as conformance_mod
        from cairn.knowledge.ingest.executor import verify_manifest

        def boom(_root):
            raise RuntimeError("checker exploded")

        monkeypatch.setattr(conformance_mod, "check_bundle", boom)
        conn = _conn()
        try:
            result = verify_manifest(staged, conn)
        finally:
            conn.close()
        assert result["validate_ok"] is False
        assert result["validate_errors"] is None
        assert "checker exploded" in result["validate_message"]


def test_embed_runs_when_backend_available(staged, workspace):
    from cairn.graph import embeddings as emb

    if not emb.embeddings_available():
        pytest.skip("semantic backend not installed")

    conn = _conn()
    try:
        report = execute_manifest(staged, conn)
    finally:
        conn.close()
    assert report["embedded"] is not None
