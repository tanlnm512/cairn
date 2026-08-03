"""Regression tests for doc_status lifecycle enforcement (2026-07-24).

Before this fix, update_status() accepted any string with no validation,
and no search path filtered on doc_status at all -- an "archived" document
surfaced in knowledge_search results exactly like an active one. See
[[project-cairn-internals]].
"""
from __future__ import annotations

import sqlite3
import tempfile

import pytest

from cairn.graph.schema import _apply_schema
from cairn.knowledge.store import DOC_STATUSES, add_document, get_document, update_status
from cairn.knowledge.search import search_knowledge
from cairn.okf.bundle import OKFBundle


@pytest.fixture
def bundle():
    with tempfile.TemporaryDirectory() as tmp:
        yield OKFBundle(tmp)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _apply_schema(c)
    yield c
    c.close()


class TestUpdateStatusValidation:
    def test_unknown_status_value_rejected(self, bundle):
        cid = add_document(bundle, "Refund Policy", "body", "business-rule")
        assert update_status(bundle, cid, "definitely-not-a-real-status") is False
        # Status must be unchanged.
        assert get_document(bundle, cid).extensions["doc_status"] == "active"

    def test_forward_transitions_succeed(self, bundle):
        cid = add_document(bundle, "Refund Policy", "body", "business-rule")
        assert update_status(bundle, cid, "superseded") is True
        assert get_document(bundle, cid).extensions["doc_status"] == "superseded"
        assert update_status(bundle, cid, "archived") is True
        assert get_document(bundle, cid).extensions["doc_status"] == "archived"

    def test_active_to_archived_directly_allowed(self, bundle):
        cid = add_document(bundle, "Refund Policy", "body", "business-rule")
        assert update_status(bundle, cid, "archived") is True

    def test_backward_transition_rejected(self, bundle):
        cid = add_document(bundle, "Refund Policy", "body", "business-rule")
        update_status(bundle, cid, "archived")
        assert update_status(bundle, cid, "active") is False
        assert get_document(bundle, cid).extensions["doc_status"] == "archived"

    def test_same_status_is_a_noop_success(self, bundle):
        cid = add_document(bundle, "Refund Policy", "body", "business-rule")
        assert update_status(bundle, cid, "active") is True

    def test_missing_doc_returns_false(self, bundle):
        assert update_status(bundle, "knowledge/business-rule/nope", "archived") is False

    def test_doc_statuses_ordering(self):
        assert DOC_STATUSES == ("active", "superseded", "archived")


class TestSearchKnowledgeArchivedFiltering:
    def test_archived_doc_excluded_by_default(self, bundle, conn):
        cid = add_document(
            bundle, "Refund Policy For Late Orders", "Refunds for late deliveries.",
            "business-rule",
        )
        update_status(bundle, cid, "archived")

        results = search_knowledge(conn, bundle, "refund policy late orders")
        assert all(r["concept_id"] != cid for r in results)

    def test_archived_doc_included_when_requested(self, bundle, conn):
        cid = add_document(
            bundle, "Refund Policy For Late Orders", "Refunds for late deliveries.",
            "business-rule",
        )
        update_status(bundle, cid, "archived")

        results = search_knowledge(
            conn, bundle, "refund policy late orders", include_archived=True
        )
        assert any(r["concept_id"] == cid for r in results)

    def test_active_doc_still_returned_normally(self, bundle, conn):
        cid = add_document(
            bundle, "Refund Policy For Late Orders", "Refunds for late deliveries.",
            "business-rule",
        )
        results = search_knowledge(conn, bundle, "refund policy late orders")
        assert any(r["concept_id"] == cid for r in results)

    def test_superseded_doc_still_returned_by_default(self, bundle, conn):
        """Only 'archived' is excluded by default -- 'superseded' stays visible."""
        cid = add_document(
            bundle, "Refund Policy For Late Orders", "Refunds for late deliveries.",
            "business-rule",
        )
        update_status(bundle, cid, "superseded")
        results = search_knowledge(conn, bundle, "refund policy late orders")
        assert any(r["concept_id"] == cid for r in results)

    def test_single_token_substring_path_also_filters_archived(self, bundle, conn):
        """Single-token queries take the _lexical_search_substring path --
        must be filtered too, not just the multi-token path."""
        cid = add_document(bundle, "Refund", "Refund details.", "business-rule")
        update_status(bundle, cid, "archived")

        results = search_knowledge(conn, bundle, "refund")
        assert all(r["concept_id"] != cid for r in results)
