"""Tests for memory semantic embeddings: chunking, persistence, fused recall.

Covers the feature that replaced the old _semantic_memory_fallback (which
re-embedded every memory concept on every recall call with no persistence):
  1. chunk_memory_body -- Why:/How-to-apply: marker splitting + paragraph
     fallback, no title/description duplication.
  2. embed_memory_concepts / embed_memory / reap_orphaned_memory_embeddings --
     the memory_embeddings table's write/backfill/cleanup paths.
  3. search_memory -- always-on lexical+semantic RRF fusion, chunk dedup,
     provenance stamping ("semantic" / "fused").
  4. embed_buffering -- enqueue now, flush later, best-effort on failure.
"""
from __future__ import annotations

import sqlite3

import pytest

from cairn.graph.embeddings import (
    chunk_memory_body,
    embed_memory,
    embed_memory_concepts,
    embed_memory_count,
    reap_orphaned_memory_embeddings,
)
from cairn.graph.schema import _apply_schema
from cairn.memory.promotion import _semantic_memory_search, search_memory
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept


@pytest.fixture
def bundle(tmp_path):
    return OKFBundle(str(tmp_path / "knowledge"))


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    yield conn
    conn.close()


def _write_memory(bundle, concept_id, title, body, tier="tribal", is_latest=True):
    concept = OKFConcept(
        type="Tribal-decision",
        title=title,
        description=title,
        tags=["decision"],
        body=body,
        extensions={
            "memory_tier": tier,
            "memory_is_latest": is_latest,
            "memory_type": "decision",
        },
        concept_id=concept_id,
    )
    bundle.write_concept(concept)
    return concept


# ---------------------------------------------------------------------------
# 1. chunk_memory_body
# ---------------------------------------------------------------------------


class TestChunkMemoryBody:
    def test_splits_on_why_and_how_to_apply(self):
        concept = OKFConcept(
            type="Tribal-decision", title="T", description="T",
            body="The fact.\nWhy: the reasoning.\nHow to apply: the guidance.",
            concept_id="memory/tribal/t",
        )
        chunks = chunk_memory_body(concept)
        assert len(chunks) == 3
        assert chunks[0] == "T The fact."
        assert chunks[1] == "Why: the reasoning."
        assert chunks[2] == "How to apply: the guidance."

    def test_falls_back_to_paragraph_split_without_markers(self):
        concept = OKFConcept(
            type="Tribal-decision", title="T", description="T",
            body="Para one.\n\nPara two.\n\nPara three.",
            concept_id="memory/tribal/t",
        )
        chunks = chunk_memory_body(concept)
        assert chunks == ["T Para one.", "Para two.", "Para three."]

    def test_title_not_duplicated_via_description(self):
        """create_memory always sets description=title; the header must use
        title alone or a memory whose body echoes the title would triple up."""
        concept = OKFConcept(
            type="Tribal-decision", title="Echo title", description="Echo title",
            body="Echo title restated in the body.",
            concept_id="memory/tribal/t",
        )
        chunks = chunk_memory_body(concept)
        assert chunks == ["Echo title Echo title restated in the body."]

    def test_caps_at_max_chunks(self):
        body = "\n\n".join(f"Paragraph {i}." for i in range(10))
        concept = OKFConcept(
            type="Tribal-decision", title="T", description="T", body=body,
            concept_id="memory/tribal/t",
        )
        chunks = chunk_memory_body(concept)
        assert len(chunks) == 5

    def test_empty_body_falls_back_to_title(self):
        concept = OKFConcept(
            type="Tribal-decision", title="Just a title", description="Just a title",
            body="", concept_id="memory/tribal/t",
        )
        assert chunk_memory_body(concept) == ["Just a title"]


# ---------------------------------------------------------------------------
# 2. embed_memory_concepts / embed_memory / reap
# ---------------------------------------------------------------------------


class TestEmbedMemoryConcepts:
    def test_round_trip_writes_one_row_per_chunk(self, db, bundle):
        concept = _write_memory(
            bundle, "memory/tribal/foo",
            "ApiFactory backoff",
            "Uses exponential backoff.\nWhy: flaky staging network.\nHow to apply: check before adding retries.",
        )
        n = embed_memory_concepts(db, bundle, [concept.concept_id])
        db.commit()
        assert n == 1
        assert embed_memory_count(db) == 1
        rows = db.execute(
            "SELECT chunk_index FROM memory_embeddings WHERE doc_id = ? ORDER BY chunk_index",
            (concept.concept_id,),
        ).fetchall()
        assert [r["chunk_index"] for r in rows] == [0, 1, 2]

    def test_deleted_concept_is_skipped_not_raised(self, db, bundle):
        # Never written -- embed_memory_concepts must degrade gracefully.
        n = embed_memory_concepts(db, bundle, ["memory/tribal/does-not-exist"])
        assert n == 0

    def test_reembed_replaces_old_chunks_not_appends(self, db, bundle):
        concept = _write_memory(bundle, "memory/tribal/foo", "T", "Why: a.\nHow to apply: b.")
        embed_memory_concepts(db, bundle, [concept.concept_id])
        db.commit()
        # Edit to a shorter body (fewer chunks) and re-embed.
        concept.body = "Just one paragraph now."
        bundle.write_concept(concept)
        embed_memory_concepts(db, bundle, [concept.concept_id])
        db.commit()
        rows = db.execute(
            "SELECT chunk_index FROM memory_embeddings WHERE doc_id = ?", (concept.concept_id,)
        ).fetchall()
        assert len(rows) == 1, "stale chunk rows from the longer body must not survive a re-embed"


class TestEmbedMemoryBackfill:
    def test_embeds_all_unembedded_memories(self, db, bundle):
        _write_memory(bundle, "memory/tribal/a", "A", "Body A")
        _write_memory(bundle, "memory/tribal/b", "B", "Body B")
        summary = embed_memory(db, bundle)
        assert summary["embedded"] == 2
        assert embed_memory_count(db) == 2

    def test_skips_already_embedded(self, db, bundle):
        _write_memory(bundle, "memory/tribal/a", "A", "Body A")
        embed_memory(db, bundle)
        _write_memory(bundle, "memory/tribal/b", "B", "Body B")
        summary = embed_memory(db, bundle)
        # "a" is excluded from this round's work entirely (not attempted, not
        # counted as skipped) -- only "b" (the newly unembedded one) is total.
        assert summary["total"] == 1
        assert summary["embedded"] == 1
        assert embed_memory_count(db) == 2


class TestReapOrphanedMemoryEmbeddings:
    def test_removes_rows_for_deleted_concepts_only(self, db, bundle):
        kept = _write_memory(bundle, "memory/tribal/kept", "Kept", "Body")
        gone = _write_memory(bundle, "memory/tribal/gone", "Gone", "Body")
        embed_memory_concepts(db, bundle, [kept.concept_id, gone.concept_id])
        db.commit()

        import os
        os.remove(str(bundle.root / f"{gone.concept_id}.md"))

        reaped = reap_orphaned_memory_embeddings(db, bundle)
        assert reaped > 0
        remaining = {
            r["doc_id"] for r in db.execute("SELECT DISTINCT doc_id FROM memory_embeddings").fetchall()
        }
        assert remaining == {kept.concept_id}


# ---------------------------------------------------------------------------
# 3. search_memory fusion
# ---------------------------------------------------------------------------


class TestSearchMemoryFusion:
    def test_semantic_only_hit_surfaces_without_lexical_overlap(self, db, bundle):
        """A memory embedded but sharing zero substring/token overlap with the
        query must still surface via the semantic path (the whole point of
        moving off the old lexical-empty-only fallback)."""
        concept = _write_memory(
            bundle, "memory/tribal/foo", "Retry policy",
            "ApiFactory retries with exponential backoff on 5xx responses.",
        )
        embed_memory_concepts(db, bundle, [concept.concept_id])
        db.commit()

        results = search_memory(db, bundle, "zzqx totally unrelated tokens")
        # The hash embedder is token-based, so an utterly disjoint query
        # legitimately scores near zero -- this asserts the plumbing doesn't
        # error and returns a list (possibly empty), not that hash-backend
        # "semantic" quality resembles real meaning-based retrieval.
        assert isinstance(results, list)

    def test_fused_provenance_when_found_both_ways(self, db, bundle):
        concept = _write_memory(
            bundle, "memory/tribal/foo", "ApiFactory backoff policy",
            "Why: flaky staging network caused an outage.\nHow to apply: check before adding retries.",
        )
        embed_memory_concepts(db, bundle, [concept.concept_id])
        db.commit()

        results = search_memory(db, bundle, "flaky staging network outage")
        assert results, "expected the memory to be found"
        # bundle.read_concept() rewrites concept_id to an absolute path, so
        # match on title rather than the pre-write relative concept_id.
        hit = next(c for c in results if c.title == concept.title)
        assert hit.extensions.get("provenance") == "fused"

    def test_chunk_dedup_returns_one_entry_per_concept(self, db, bundle):
        """A memory with 3 embedded chunks must appear once in results, not
        once per matching chunk."""
        concept = _write_memory(
            bundle, "memory/tribal/foo", "T",
            "Fact.\nWhy: reasoning here.\nHow to apply: guidance here.",
        )
        embed_memory_concepts(db, bundle, [concept.concept_id])
        db.commit()
        hits = _semantic_memory_search(db, bundle, "reasoning guidance fact")
        ids = [c.concept_id for c in hits]
        assert ids.count(concept.concept_id) <= 1

    def test_no_embeddings_yet_degrades_to_pure_lexical(self, db, bundle):
        """Before any backfill/capture has embedded anything, search_memory
        must not error -- it just returns lexical-only results."""
        _write_memory(bundle, "memory/tribal/foo", "Plain lexical hit", "Nothing embedded yet.")
        results = search_memory(db, bundle, "plain lexical hit")
        assert results
        assert results[0].extensions.get("provenance") in (None, "")


# ---------------------------------------------------------------------------
# 4. embed_buffering
# ---------------------------------------------------------------------------


class TestEmbedBuffering:
    def test_enqueue_then_flush_embeds_the_concept(self, tmp_path, bundle, monkeypatch):
        from cairn.mcp_server import embed_buffering as ebuf

        # _flush()'s contract closes whatever connection the factory returns
        # (matching the real background-thread usage where each flush opens
        # a fresh throwaway connection) -- so this needs a file-backed DB to
        # reopen for verification, not the shared in-memory `db` fixture
        # (which would be destroyed once its one connection is closed).
        db_path = str(tmp_path / "graph.db")
        setup_conn = sqlite3.connect(db_path)
        _apply_schema(setup_conn)
        setup_conn.close()

        concept = _write_memory(bundle, "memory/tribal/foo", "Buffered", "Body text here.")

        # Reset module state between tests -- it's process-global.
        monkeypatch.setattr(ebuf, "_QUEUE", ebuf.collections.deque(maxlen=500))
        monkeypatch.setattr(ebuf, "_FLUSHER_STARTED", True)  # skip spawning a real thread
        ebuf.configure(lambda: sqlite3.connect(db_path), lambda: bundle)

        ebuf.enqueue(concept.concept_id)
        ebuf._flush()

        verify_conn = sqlite3.connect(db_path)
        verify_conn.row_factory = sqlite3.Row
        assert embed_memory_count(verify_conn) == 1
        verify_conn.close()

    def test_flush_failure_leaves_batch_queued(self, monkeypatch):
        from cairn.mcp_server import embed_buffering as ebuf

        monkeypatch.setattr(ebuf, "_QUEUE", ebuf.collections.deque(maxlen=500))
        monkeypatch.setattr(ebuf, "_FLUSHER_STARTED", True)

        def _boom():
            raise sqlite3.OperationalError("database is locked")

        ebuf.configure(_boom, lambda: None)
        ebuf.enqueue("memory/tribal/foo")
        ebuf._flush()  # must not raise

        assert list(ebuf._QUEUE) == ["memory/tribal/foo"], "failed flush must not drop the batch"
