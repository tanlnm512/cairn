"""Tests for memory validity intervals: capture stamping + projection mirror.

Extensions carry the validity interval (source of truth); the
``memory_validity`` SQL table is the derived indexed projection. Capture
stamps ``valid_from`` = creation time; re-stores keep it and follow the
concept_id rename.
"""
from __future__ import annotations

import sqlite3

import pytest

from cairn.graph.schema import _apply_schema
from cairn.memory.promotion import capture_memory, search_memory
from cairn.memory.store import create_memory, get_memory, store_memory, write_validity
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


def _validity_row(conn, concept_id):
    return conn.execute(
        "SELECT valid_from, valid_until, successor_symbol "
        "FROM memory_validity WHERE concept_id = ?",
        (concept_id,),
    ).fetchone()


class TestCaptureStamping:
    def test_capture_stamps_extensions_and_projection_row(self, db, bundle):
        result = capture_memory(
            db, bundle, "decision", "ApiFactory retry policy", "Use exponential backoff"
        )
        concept = result["concept"]
        cid = result["path"]
        # valid_from = creation time (the concept timestamp); open interval,
        # so no null-bound keys are carried in extensions.
        assert concept.extensions["valid_from"] == concept.timestamp
        assert concept.extensions.get("valid_until") is None
        assert concept.extensions.get("successor_symbol") is None
        row = _validity_row(db, cid)
        assert row is not None
        assert row["valid_from"] == concept.timestamp
        assert row["valid_until"] is None
        assert row["successor_symbol"] is None

    def test_captured_file_carries_validity_extensions(self, db, bundle):
        result = capture_memory(
            db, bundle, "decision", "Cairn doctor gate", "Run before shipping"
        )
        on_disk = get_memory(bundle, result["path"])
        assert on_disk.extensions.get("valid_from") == result["concept"].timestamp
        assert on_disk.extensions.get("valid_until") is None


class TestWriteValidity:
    def test_full_interval_roundtrip_and_upsert(self, db, bundle):
        result = capture_memory(db, bundle, "pattern", "Backoff helper", "body")
        concept = result["concept"]
        cid = result["path"]

        write_validity(
            concept,
            valid_from="2026-01-01T00:00:00Z",
            valid_until="2026-06-01T00:00:00Z",
            successor_symbol="new_symbol",
            bundle=bundle,
            conn=db,
        )
        on_disk = get_memory(bundle, cid)
        assert on_disk.extensions.get("valid_from") == "2026-01-01T00:00:00Z"
        assert on_disk.extensions.get("valid_until") == "2026-06-01T00:00:00Z"
        assert on_disk.extensions.get("successor_symbol") == "new_symbol"
        row = _validity_row(db, cid)
        assert row["valid_from"] == "2026-01-01T00:00:00Z"
        assert row["valid_until"] == "2026-06-01T00:00:00Z"
        assert row["successor_symbol"] == "new_symbol"

        # Upsert: a second write updates the single projection row.
        write_validity(
            concept,
            valid_from="2026-01-01T00:00:00Z",
            valid_until="2026-07-01T00:00:00Z",
            conn=db,
        )
        rows = db.execute(
            "SELECT valid_until FROM memory_validity WHERE concept_id = ?", (cid,)
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["valid_until"] == "2026-07-01T00:00:00Z"

    def test_open_interval_omits_null_bounds_from_frontmatter(self, db, bundle):
        result = capture_memory(db, bundle, "pattern", "Null bound omission", "body")
        concept = result["concept"]
        cid = result["path"]
        # A null bound is an open interval: absent from extensions, no
        # frontmatter line; the SQL row still carries NULLs.
        assert "valid_until" not in concept.extensions
        assert "successor_symbol" not in concept.extensions
        on_disk = get_memory(bundle, cid)
        assert "valid_until" not in on_disk.extensions
        assert "successor_symbol" not in on_disk.extensions
        assert "valid_until" not in (bundle.root / f"{cid}.md").read_text()
        row = _validity_row(db, cid)
        assert row["valid_until"] is None
        assert row["successor_symbol"] is None

        # Closing then clearing the interval removes the bound again.
        valid_from = concept.extensions["valid_from"]
        write_validity(
            concept, valid_from=valid_from,
            valid_until="2026-06-01T00:00:00Z", bundle=bundle, conn=db,
        )
        closed = get_memory(bundle, cid)
        assert closed.extensions.get("valid_until") == "2026-06-01T00:00:00Z"
        write_validity(concept, valid_from=valid_from, bundle=bundle, conn=db)
        cleared = get_memory(bundle, cid)
        assert "valid_until" not in cleared.extensions
        assert _validity_row(db, cid)["valid_until"] is None

    def test_write_validity_requires_valid_from(self, db):
        concept = create_memory("decision", "t", "b")
        with pytest.raises(ValueError):
            write_validity(concept, valid_from="")


class TestReStore:
    def test_retable_keeps_valid_from_and_follows_rename(self, db, bundle):
        result = capture_memory(
            db, bundle, "decision", "Session notes policy", "Archive after decay window"
        )
        cid = result["path"]
        original_from = result["concept"].extensions["valid_from"]

        concept = get_memory(bundle, cid)
        new_id = store_memory(concept, bundle, tier="archived", old_id=cid, conn=db)
        # The rename moves the projection row; the interval is unchanged.
        assert _validity_row(db, cid) is None
        row = _validity_row(db, new_id)
        assert row["valid_from"] == original_from
        assert row["valid_until"] is None

    def test_existing_valid_until_survives_retable(self, db, bundle):
        result = capture_memory(
            db, bundle, "decision", "Legacy parser note", "Refers to removed symbol"
        )
        cid = result["path"]
        original_from = result["concept"].extensions["valid_from"]

        concept = get_memory(bundle, cid)
        concept.extensions["valid_until"] = "2026-09-01T00:00:00Z"
        new_id = store_memory(concept, bundle, tier="archived", old_id=cid, conn=db)
        on_disk = get_memory(bundle, new_id)
        assert on_disk.extensions.get("valid_from") == original_from
        assert on_disk.extensions.get("valid_until") == "2026-09-01T00:00:00Z"
        row = _validity_row(db, new_id)
        assert row["valid_from"] == original_from
        assert row["valid_until"] == "2026-09-01T00:00:00Z"


class TestAsOfSearch:
    """search_memory's ``as_of`` predicate filters by the validity interval."""

    def _captured(self, db, bundle, title):
        result = capture_memory(db, bundle, "decision", title, "body")
        return result, get_memory(bundle, result["path"])

    def test_as_of_after_creation_shows_memory(self, db, bundle):
        self._captured(db, bundle, "AsOfFuture probe")
        results = search_memory(db, bundle, "AsOfFuture probe", as_of="2099-12-31")
        assert any(r.title == "AsOfFuture probe" for r in results)

    def test_as_of_before_creation_hides_memory(self, db, bundle):
        self._captured(db, bundle, "AsOfPast probe")
        results = search_memory(db, bundle, "AsOfPast probe", as_of="2000-01-01")
        assert not any(r.title == "AsOfPast probe" for r in results)

    def test_default_recall_hides_expired_memory(self, db, bundle):
        _, concept = self._captured(db, bundle, "AsOfExpired probe")
        write_validity(
            concept,
            valid_from="2019-01-01T00:00:00Z",
            valid_until="2020-01-01T00:00:00Z",
            bundle=bundle,
            conn=db,
        )
        results = search_memory(db, bundle, "AsOfExpired probe")
        assert not any(r.title == "AsOfExpired probe" for r in results)
        inside = search_memory(db, bundle, "AsOfExpired probe", as_of="2019-06-01")
        assert any(r.title == "AsOfExpired probe" for r in inside)

    def test_include_superseded_does_not_bypass_validity(self, db, bundle):
        _, concept = self._captured(db, bundle, "AsOfOrtho probe")
        write_validity(
            concept,
            valid_from="2019-01-01T00:00:00Z",
            valid_until="2020-01-01T00:00:00Z",
            bundle=bundle,
            conn=db,
        )
        results = search_memory(
            db, bundle, "AsOfOrtho probe", include_superseded=True
        )
        assert not any(r.title == "AsOfOrtho probe" for r in results)

    def test_valid_until_boundary_is_exclusive(self, db, bundle):
        _, concept = self._captured(db, bundle, "AsOfBoundary probe")
        write_validity(
            concept,
            valid_from="2026-01-01T00:00:00Z",
            valid_until="2026-06-01T00:00:00Z",
            bundle=bundle,
            conn=db,
        )
        before = search_memory(db, bundle, "AsOfBoundary probe", as_of="2026-05-31")
        assert any(r.title == "AsOfBoundary probe" for r in before)
        at = search_memory(db, bundle, "AsOfBoundary probe", as_of="2026-06-01")
        assert not any(r.title == "AsOfBoundary probe" for r in at)

    def test_pre_feature_concept_without_validity_extensions_visible(
        self, db, bundle
    ):
        """Missing validity extensions default to visible, like memory_is_latest."""
        concept = OKFConcept(
            type="Tribal-decision", title="AsOfLegacy probe", body="legacy memory",
            concept_id="memory/tribal/asof-legacy-abc123",
            extensions={"memory_tier": "tribal", "memory_type": "decision"},
        )
        bundle.write_concept(concept)
        results = search_memory(db, bundle, "AsOfLegacy probe", as_of="2000-01-01")
        assert any(r.title == "AsOfLegacy probe" for r in results)

    def test_as_of_invalid_date_raises(self, db, bundle):
        self._captured(db, bundle, "AsOfInvalid probe")
        with pytest.raises(ValueError):
            search_memory(db, bundle, "AsOfInvalid probe", as_of="not-a-date")
