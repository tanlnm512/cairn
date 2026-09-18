"""Tests for memory store fixes (H4, H5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cairn.graph.schema import get_db
from cairn.memory.store import (
    check_overlap,
    create_memory,
    delete_memory,
    get_memory,
    share_memory,
    store_memory,
    validate_agent_id,
)
from cairn.okf.bundle import OKFBundle


def test_validate_agent_id_accepts_conforming_ids():
    """Non-empty ids of at most 64 chars from [A-Za-z0-9._-] pass through."""
    assert validate_agent_id("alpha-1") == "alpha-1"
    assert validate_agent_id("codex.claw_01") == "codex.claw_01"
    assert validate_agent_id("a" * 64) == "a" * 64


def test_validate_agent_id_rejects_empty_and_oversized():
    """Empty, None, and >64-char ids raise ValueError."""
    for bad in ("", None):
        with pytest.raises(ValueError):
            validate_agent_id(bad)
    for bad in ("a" * 65, "a" * 300):
        with pytest.raises(ValueError):
            validate_agent_id(bad)


def test_validate_agent_id_rejects_out_of_charset():
    """Characters outside [A-Za-z0-9._-] raise ValueError."""
    for bad in ('../evil" --x', "a/b", "a b", "a\n", "agent#1", "café"):
        with pytest.raises(ValueError):
            validate_agent_id(bad)


def test_share_memory_inserts_share_rows(fresh_db):
    """share_memory records one (agent_id, symbol, memory_id, 'share', ts) row per symbol."""
    inserted = share_memory(
        "agentA", ["auth", "parser"], "memory/tribal/m-abc123", conn=fresh_db
    )
    assert inserted == 2
    rows = fresh_db.execute(
        "SELECT agent_id, symbol, memory_id, kind, ts FROM agent_symbols ORDER BY symbol"
    ).fetchall()
    assert [(r["agent_id"], r["symbol"], r["memory_id"], r["kind"]) for r in rows] == [
        ("agentA", "auth", "memory/tribal/m-abc123", "share"),
        ("agentA", "parser", "memory/tribal/m-abc123", "share"),
    ]
    assert all(r["ts"] for r in rows)


def test_share_memory_is_idempotent(fresh_db):
    """Re-sharing an already-recorded (agent_id, symbol, memory_id) inserts no duplicate rows."""
    first = share_memory(
        "agentA", ["auth", "hot"], "memory/tribal/m-abc123", conn=fresh_db
    )
    again = share_memory(
        "agentA", ["auth", "hot"], "memory/tribal/m-abc123", conn=fresh_db
    )
    assert first == 2
    assert again == 0
    count = fresh_db.execute("SELECT COUNT(*) FROM agent_symbols").fetchone()[0]
    assert count == 2


def test_share_memory_does_not_commit(fresh_db):
    """The caller owns the transaction boundary: an uncommitted share rolls back."""
    share_memory("agentA", ["auth"], "memory/tribal/m-abc123", conn=fresh_db)
    fresh_db.rollback()
    count = fresh_db.execute("SELECT COUNT(*) FROM agent_symbols").fetchone()[0]
    assert count == 0


def test_share_memory_validates_agent_and_symbols(fresh_db):
    """Invalid agent ids and empty/non-string symbols raise ValueError before any insert."""
    for bad_agent in ("", None, "../evil", "a" * 65):
        with pytest.raises(ValueError):
            share_memory(bad_agent, ["auth"], conn=fresh_db)
    for bad_symbols in (["auth", ""], [None], ["auth", 7]):
        with pytest.raises(ValueError):
            share_memory("agentA", bad_symbols, conn=fresh_db)
    count = fresh_db.execute("SELECT COUNT(*) FROM agent_symbols").fetchone()[0]
    assert count == 0


def test_check_overlap_returns_other_agents_rows(fresh_db):
    """check_overlap returns (agent_id, symbol, kind, ts) rows for other agents
    on the queried symbols only; non-overlapping symbols are not reported."""
    share_memory("agentA", ["auth", "session"], "memory/tribal/m-abc123", conn=fresh_db)
    rows = check_overlap("agentB", ["auth", "users"], conn=fresh_db)
    assert [(r[0], r[1], r[2]) for r in rows] == [("agentA", "auth", "share")]
    assert all(r[3] for r in rows)


def test_check_overlap_excludes_own_activity(fresh_db):
    """An agent's own rows are never overlap."""
    share_memory("agentB", ["auth"], "memory/tribal/m-def456", conn=fresh_db)
    assert check_overlap("agentB", ["auth"], conn=fresh_db) == []


def test_check_overlap_counts_intent_rows(fresh_db):
    """Other agents' kind='intent' rows overlap, not only shares."""
    share_memory("agentA", ["auth"], "memory/tribal/m-abc123", conn=fresh_db)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh_db.execute(
        "INSERT INTO agent_symbols (agent_id, symbol, memory_id, kind, ts)"
        " VALUES ('agentC', 'parser', NULL, 'intent', ?)",
        (ts,),
    )
    rows = check_overlap("agentB", ["auth", "parser"], conn=fresh_db)
    assert sorted((r[0], r[1], r[2]) for r in rows) == [
        ("agentA", "auth", "share"),
        ("agentC", "parser", "intent"),
    ]


def test_check_overlap_respects_recency_window(fresh_db):
    """Rows older than the recency window are excluded; rows within it are kept."""
    share_memory("agentA", ["fresh", "stale"], "memory/tribal/m-abc123", conn=fresh_db)
    stale_ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    fresh_db.execute("UPDATE agent_symbols SET ts = ? WHERE symbol = 'stale'", (stale_ts,))
    rows = check_overlap("agentB", ["fresh", "stale"], conn=fresh_db, recency_days=7)
    assert [r[1] for r in rows] == ["fresh"]


def test_check_overlap_empty_symbols_returns_no_rows(fresh_db):
    """An empty symbol set intersects nothing."""
    share_memory("agentA", ["auth"], "memory/tribal/m-abc123", conn=fresh_db)
    assert check_overlap("agentB", [], conn=fresh_db) == []


def test_check_overlap_validates_agent_and_symbols(fresh_db):
    """Invalid agent ids and empty/non-string symbols raise ValueError."""
    for bad_agent in ("", None, "../evil", "a" * 65):
        with pytest.raises(ValueError):
            check_overlap(bad_agent, ["auth"], conn=fresh_db)
    for bad_symbols in (["auth", ""], [None], ["auth", 7]):
        with pytest.raises(ValueError):
            check_overlap("agentB", bad_symbols, conn=fresh_db)


def test_check_overlap_writes_nothing(fresh_db):
    """check_overlap is a pure read: the agent_symbols row set is unchanged."""
    share_memory("agentA", ["auth"], "memory/tribal/m-abc123", conn=fresh_db)
    check_overlap("agentB", ["auth"], conn=fresh_db)
    count = fresh_db.execute("SELECT COUNT(*) FROM agent_symbols").fetchone()[0]
    assert count == 1


def test_check_overlap_runs_on_read_only_conn(tmp_path):
    """The overlap read works through a mode=ro connection."""
    db_path = str(tmp_path / "ro.db")
    writable = get_db(db_path)
    try:
        share_memory("agentA", ["auth"], "memory/tribal/m-abc123", conn=writable)
        writable.commit()
    finally:
        writable.close()
    ro = get_db(db_path, read_only=True)
    try:
        rows = check_overlap("agentB", ["auth"], conn=ro)
    finally:
        ro.close()
    assert [(r[0], r[1], r[2]) for r in rows] == [("agentA", "auth", "share")]


def test_store_twice_same_title_distinct_ids_h4(tmp_path, fresh_db):
    """H4: Storing two same-title memories into non-raw tiers produces distinct concept_ids.

    Raw tier keeps date-prefixed IDs, non-raw tiers get UUID suffixes.
    """
    import re
    bundle = OKFBundle(str(tmp_path / "knowledge"))

    # Test in drafts tier (non-raw)
    mem1_drafts = create_memory(
        type_="pattern",
        title="backoff retry policy",
        body="First memory about backoff",
        confidence=0.4,  # This score maps to drafts tier
    )
    id1_drafts = store_memory(mem1_drafts, bundle)

    mem2_drafts = create_memory(
        type_="pattern",
        title="backoff retry policy",
        body="Second memory about backoff",
        confidence=0.4,
    )
    id2_drafts = store_memory(mem2_drafts, bundle)

    # Assert distinct IDs
    assert id1_drafts != id2_drafts, "Same-title non-raw memories must have distinct IDs"

    # Assert both memories exist and can be retrieved
    retrieved1 = get_memory(bundle, id1_drafts)
    retrieved2 = get_memory(bundle, id2_drafts)
    assert retrieved1 is not None, "First memory should exist"
    assert retrieved2 is not None, "Second memory should exist"
    assert retrieved1.body == "First memory about backoff"
    assert retrieved2.body == "Second memory about backoff"

    # Test in tribal tier (non-raw)
    mem1_tribal = create_memory(
        type_="pattern",
        title="api design pattern",
        body="First tribal memory",
        confidence=0.7,  # This score maps to tribal tier
    )
    id1_tribal = store_memory(mem1_tribal, bundle)

    mem2_tribal = create_memory(
        type_="pattern",
        title="api design pattern",
        body="Second tribal memory",
        confidence=0.7,
    )
    id2_tribal = store_memory(mem2_tribal, bundle)

    assert id1_tribal != id2_tribal, "Same-title tribal memories must have distinct IDs"

    # Test in archived tier (non-raw) - directly set tier
    mem1_archived = create_memory(
        type_="pattern",
        title="archived pattern",
        body="First archived memory",
        confidence=0.7,
    )
    mem1_archived.extensions["memory_tier"] = "archived"
    id1_archived = store_memory(mem1_archived, bundle)

    mem2_archived = create_memory(
        type_="pattern",
        title="archived pattern",
        body="Second archived memory",
        confidence=0.7,
    )
    mem2_archived.extensions["memory_tier"] = "archived"
    id2_archived = store_memory(mem2_archived, bundle)

    assert id1_archived != id2_archived, "Same-title archived memories must have distinct IDs"

    # Raw tier is collision-safe with the same uuid suffix scheme as
    # every other tier. It
    # keeps the date prefix (so decay can purge by age) AND appends a uuid.
    mem1_raw = create_memory(
        type_="pattern",
        title="raw capture",
        body="First raw memory",
        confidence=0.1,  # This score maps to raw tier
    )
    id1_raw = store_memory(mem1_raw, bundle)

    # Verify non-raw IDs have UUID suffix format (slug-XXXXXX where X is hex)
    for non_raw_id in [id1_drafts, id2_drafts, id1_tribal, id2_tribal, id1_archived, id2_archived]:
        filename = non_raw_id.split("/")[-1]  # Get last part: "slug-XXXXXX"
        parts = filename.split("-")
        # Last part should be 6 hex chars (UUID suffix)
        assert len(parts) >= 2, f"Non-raw ID should have slug-uuid format, got: {filename}"
        uuid_part = parts[-1]
        assert len(uuid_part) == 6, f"UUID suffix should be 6 chars, got: {uuid_part}"
        assert re.match(r"^[0-9a-f]{6}$", uuid_part), f"UUID suffix should be hex, got: {uuid_part}"

    # Raw tier now also carries the uuid suffix (P7 fix). Format:
    # memory/raw/YYYY-MM-DD-slug-XXXXXX — date prefix kept for decay, uuid
    # added for collision safety.
    raw_filename = id1_raw.split("/")[-1]
    raw_parts = raw_filename.split("-")
    assert len(raw_parts) >= 3, f"Raw ID should have date prefix, got: {raw_filename}"
    assert re.match(r"^\d{4}$", raw_parts[0]), f"Raw ID first part should be 4-digit year, got: {raw_parts[0]}"
    # The last part should now BE a 6-char hex uuid (the collision-safety suffix).
    assert re.match(r"^[0-9a-f]{6}$", raw_parts[-1]), (
        f"Raw tier should have uuid suffix for collision safety, got: {raw_parts[-1]}"
    )


def test_delete_exact_no_sibling_clobber_h5(tmp_path, fresh_db):
    """H5: delete_memory deletes refs only for exact memory_path, not siblings."""
    bundle = OKFBundle(str(tmp_path / "knowledge"))

    # Create three memories with overlapping names
    mem1 = create_memory(
        type_="pattern",
        title="api client",
        body="API client pattern",
        confidence=0.7,
    )
    id1 = store_memory(mem1, bundle)

    mem2 = create_memory(
        type_="pattern",
        title="api",
        body="General API pattern",
        confidence=0.7,
    )
    id2 = store_memory(mem2, bundle)

    mem3 = create_memory(
        type_="pattern",
        title="api v2",
        body="API v2 pattern",
        confidence=0.7,
    )
    id3 = store_memory(mem3, bundle)

    # Manually add some memory_refs to simulate cross-session references
    # We'll create refs that include overlapping substrings
    cursor = fresh_db.cursor()
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    cursor.execute("INSERT INTO memory_refs (memory_path, session_id, referenced_at) VALUES (?, ?, ?)", (id1, "session1", ts))
    cursor.execute("INSERT INTO memory_refs (memory_path, session_id, referenced_at) VALUES (?, ?, ?)", (id2, "session1", ts))
    cursor.execute("INSERT INTO memory_refs (memory_path, session_id, referenced_at) VALUES (?, ?, ?)", (id3, "session1", ts))
    fresh_db.commit()

    # Verify all three refs exist
    refs_before = cursor.execute("SELECT memory_path FROM memory_refs").fetchall()
    assert len(refs_before) == 3, f"Should have 3 refs before delete, got {len(refs_before)}"

    # Delete the exact memory "api" (id2)
    # Note: delete_memory expects relative path without .md
    # Normalize id2 to relative path if needed
    import pathlib
    rel_path = str(pathlib.Path(id2).relative_to(bundle.root)) if id2.startswith(str(bundle.root)) else id2

    result = delete_memory(bundle, rel_path, conn=fresh_db)
    assert result is True, "delete_memory should succeed"

    # Verify only "api" ref was deleted, not "api client" or "api v2"
    refs_after = cursor.execute("SELECT memory_path FROM memory_refs").fetchall()
    assert len(refs_after) == 2, f"Should have 2 refs after delete, got {len(refs_after)}"

    remaining_paths = [r[0] for r in refs_after]
    assert id1 in remaining_paths, f"api client (id1={id1}) should still exist"
    assert id3 in remaining_paths, f"api v2 (id3={id3}) should still exist"
    assert id2 not in remaining_paths, f"api (id2={id2}) should be deleted"

    # Verify the actual memory files: only id2 file should be deleted
    assert (bundle.root / f"{id1}.md").exists(), "api client file should still exist"
    assert not (bundle.root / f"{id2}.md").exists(), "api file should be deleted"
    assert (bundle.root / f"{id3}.md").exists(), "api v2 file should still exist"


def test_delete_memory_exact_match_not_like(tmp_path, fresh_db):
    """H5: Verify delete_memory uses exact match (WHERE memory_path = ?), not LIKE."""
    bundle = OKFBundle(str(tmp_path / "knowledge"))

    # Create memories with IDs that are substrings of each other
    mem1 = create_memory(
        type_="pattern",
        title="test",
        body="Test memory",
        confidence=0.7,
    )
    id1 = store_memory(mem1, bundle)  # Will be something like memory/tribal/test-<uuid>

    mem2 = create_memory(
        type_="pattern",
        title="test extended",
        body="Test extended memory",
        confidence=0.7,
    )
    id2 = store_memory(mem2, bundle)  # Will be something like memory/tribal/test-extended-<uuid>

    # Add memory_refs
    cursor = fresh_db.cursor()
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    cursor.execute("INSERT INTO memory_refs (memory_path, session_id, referenced_at) VALUES (?, ?, ?)", (id1, "session1", ts))
    cursor.execute("INSERT INTO memory_refs (memory_path, session_id, referenced_at) VALUES (?, ?, ?)", (id2, "session1", ts))
    fresh_db.commit()

    # Get the relative path for id1
    import pathlib
    rel_path1 = str(pathlib.Path(id1).relative_to(bundle.root)) if id1.startswith(str(bundle.root)) else id1

    # Delete id1
    delete_memory(bundle, rel_path1, conn=fresh_db)

    # Verify only id1 ref is gone
    refs_after = cursor.execute("SELECT memory_path FROM memory_refs").fetchall()
    assert len(refs_after) == 1, f"Should have 1 ref after delete, got {len(refs_after)}"
    remaining_path = refs_after[0][0]
    assert remaining_path == id2, f"Only id2 should remain, got {remaining_path}"

    # Verify files
    assert not (bundle.root / f"{id1}.md").exists(), "id1 file should be deleted"
    assert (bundle.root / f"{id2}.md").exists(), "id2 file should still exist"
