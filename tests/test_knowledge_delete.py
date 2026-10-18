"""Knowledge document deletion: embedding-row cleanup and unlink ordering."""
from __future__ import annotations

import sqlite3

import pytest

from cairn.graph.schema import _apply_schema
from cairn.knowledge.store import add_document, delete_document
from cairn.okf.bundle import OKFBundle


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _apply_schema(c)
    yield c
    c.close()


def _seed(bundle: OKFBundle, conn) -> str:
    """One doc plus an embedding row keyed by its bundle-relative id."""
    cid = add_document(bundle, "Refund policy", "Refunds within 30 days.", "spec")
    conn.execute(
        "INSERT INTO knowledge_embeddings (doc_id, chunk_index, model, dim, vec, chunk) "
        "VALUES (?, 0, 'test-model', 2, ?, 'refund policy')",
        (cid, b"vec"),
    )
    conn.commit()
    return cid


def _embedding_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM knowledge_embeddings").fetchone()[0]


def test_delete_cleans_embeddings_with_relative_root(tmp_path, conn, monkeypatch):
    """A relative bundle root must still target the bundle-relative row id:
    the parsed concept carries an absolute path, and deleting against that
    shape silently no-ops."""
    monkeypatch.chdir(tmp_path)
    bundle = OKFBundle("knowledge")
    cid = _seed(bundle, conn)
    assert delete_document(bundle, cid, conn=conn) is True
    assert _embedding_count(conn) == 0
    assert not (bundle.root / f"{cid}.md").exists()


def test_delete_cleans_embeddings_with_symlinked_root(tmp_path, conn):
    """A symlinked bundle root must resolve to the same relative row id."""
    real = tmp_path / "real-knowledge"
    bundle = OKFBundle(str(real))
    cid = _seed(bundle, conn)
    link = tmp_path / "link-knowledge"
    link.symlink_to(real)
    assert delete_document(OKFBundle(str(link)), cid, conn=conn) is True
    assert _embedding_count(conn) == 0


def test_deferred_unlink_keeps_file_until_rows_committed(tmp_path, conn):
    """unlink=False stages only the embedding DELETE; the file survives
    until the caller commits and removes it, so a failed commit can never
    strand embedding rows behind a deleted document."""
    bundle = OKFBundle(str(tmp_path / "knowledge"))
    cid = _seed(bundle, conn)
    assert delete_document(bundle, cid, conn=conn, unlink=False) is True
    assert (bundle.root / f"{cid}.md").exists(), "file must survive pre-commit"
    conn.commit()
    assert delete_document(bundle, cid) is True
    assert not (bundle.root / f"{cid}.md").exists()
    assert _embedding_count(conn) == 0


def test_add_document_takes_the_bundle_advisory_lock(tmp_path, monkeypatch):
    """add_document's exists-check + write is a read-modify-write mutation, so
    it must run under the same advisory lock update_status/delete_document take."""
    from cairn.okf import bundle as bundle_mod

    entered = []
    original = bundle_mod.OKFBundle.lock

    def spy(self, timeout=5.0):
        entered.append(self.root)
        return original(self, timeout=timeout)

    monkeypatch.setattr(bundle_mod.OKFBundle, "lock", spy)
    bundle = OKFBundle(str(tmp_path / "knowledge"))
    add_document(bundle, "Refund policy", "Refunds within 30 days.", "spec")
    assert entered, "add_document must hold the bundle advisory lock"
    assert (bundle.root / "knowledge" / "spec" / "refund-policy.md").exists()
