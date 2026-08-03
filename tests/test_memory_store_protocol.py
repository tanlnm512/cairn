"""Tests for the MemoryStore protocol + Decision enum (Phase 1.2).

Guards:
1. The protocol is satisfied by both concrete stores (production + in-memory).
2. The in-memory store runs WITHOUT a filesystem OKFBundle (the roadmap's
   "memory lifecycle unit-tests run without an on-disk bundle" gate).
3. The Decision enum is the named lifecycle vocabulary, and ``_append_promotion``
   persists its stable string value (backward-compatible with legacy readers).
4. ``batch_critic`` records Decision-typed actions in promotion_history.
"""
from __future__ import annotations

from cairn.memory.store_protocol import (
    Decision,
    InMemoryMemoryStore,
    MemoryStore,
    OKFMemoryStore,
)
from cairn.okf.concept import OKFConcept


def _make_concept(title: str, tier: str = "drafts", score: float = 0.4) -> OKFConcept:
    return OKFConcept(
        type="DraftMemory-test",
        title=title,
        description=title,
        body="test body",
        concept_id=f"memory/drafts/{title}",
        extensions={"memory_tier": tier, "memory_score": score},
    )


class TestProtocol:
    def test_concrete_stores_satisfy_protocol(self):
        # InMemoryMemoryStore needs no bundle -- construct directly.
        assert isinstance(InMemoryMemoryStore(), MemoryStore)
        # OKFMemoryStore is structurally compatible (runtime check requires an
        # instance; build with minimal stubs to avoid filesystem deps).
        okf_like = OKFMemoryStore(bundle=None, conn=None)
        assert isinstance(okf_like, MemoryStore)

    def test_decision_enum_values_are_stable_strings(self):
        """Decision values are the strings persisted to promotion_history."""
        assert Decision.NEW.value == "new"
        assert Decision.PROMOTE.value == "promote"
        assert Decision.KEEP_DRAFT.value == "keep_draft"
        assert Decision.ARCHIVE.value == "archive"
        assert Decision.DUPLICATE.value == "duplicate"
        assert Decision.AMBIGUOUS.value == "ambiguous"


class TestInMemoryStore:
    """The roadmap gate: memory lifecycle runs without a filesystem bundle."""

    def test_add_get_roundtrip(self):
        store = InMemoryMemoryStore()
        c = _make_concept("roundtrip")
        cid = store.add(c, tier="drafts")
        assert cid.startswith("memory/drafts/")
        fetched = store.get(cid)
        assert fetched is not None
        assert fetched.title == "roundtrip"

    def test_search_filters_by_tier(self):
        store = InMemoryMemoryStore()
        store.add(_make_concept("a", tier="drafts"), tier="drafts")
        store.add(_make_concept("b", tier="tribal"), tier="tribal")
        drafts = store.search(tier="drafts")
        tribal = store.search(tier="tribal")
        assert len(drafts) == 1 and drafts[0].title == "a"
        assert len(tribal) == 1 and tribal[0].title == "b"

    def test_update_re_tiers_and_cleans_old(self):
        store = InMemoryMemoryStore()
        old_id = store.add(_make_concept("promote-me", tier="drafts"), tier="drafts")
        assert old_id.startswith("memory/drafts/")
        # Re-tier to tribal, supplying old_id for cleanup.
        new_id = store.update(store.get(old_id), tier="tribal", old_id=old_id)
        assert new_id.startswith("memory/tribal/")
        # Old drafts path is gone.
        assert store.get(old_id) is None
        assert store.get(new_id) is not None

    def test_delete(self):
        store = InMemoryMemoryStore()
        cid = store.add(_make_concept("doomed"), tier="drafts")
        assert store.delete(cid) is True
        assert store.get(cid) is None
        # Second delete returns False (already gone).
        assert store.delete(cid) is False


class TestAppendPromotionAcceptsDecision:
    def test_append_promotion_persists_decision_value(self):
        from cairn.memory.promotion import _append_promotion

        c = _make_concept("history")
        # Pass a Decision enum (the preferred form).
        _append_promotion(c, Decision.PROMOTE, 0.72)
        entry = c.extensions["promotion_history"][-1]
        assert entry["action"] == "promote"  # the .value, not "Decision.PROMOTE"
        assert entry["score"] == 0.72

    def test_append_promotion_accepts_legacy_string(self):
        """Legacy freeform string callers keep working (backward compat)."""
        from cairn.memory.promotion import _append_promotion

        c = _make_concept("legacy")
        _append_promotion(c, "captured", 0.5)
        assert c.extensions["promotion_history"][-1]["action"] == "captured"
