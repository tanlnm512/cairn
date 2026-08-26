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
        assert report["count_ok"] is True
        assert report["smoke_search_hit"] is True

    def test_verify_manifest_alone_reads_current_store(self, staged):
        from cairn.knowledge.ingest.executor import verify_manifest

        result = verify_manifest(staged, _conn())
        assert result["expected_count"] == 2
        assert result["store_count"] == 0
        assert result["count_ok"] is False
        assert result["smoke_search_hit"] is False
        assert set(result) == {
            "store_count", "expected_count", "count_ok", "smoke_search_hit"
        }


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
