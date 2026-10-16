"""Tests for memory store fixes (H4, H5)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from cairn.graph.schema import get_db
from cairn.memory.promotion import promote_memory
from cairn.memory.store import (
    check_overlap,
    consolidate_memories,
    create_memory,
    delete_memory,
    demote_memory,
    get_memory,
    list_memories,
    share_memory,
    store_memory,
    validate_agent_id,
)
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept


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




def _archived_memory(bundle, title, *, timestamp=None):
    """Store one archived-tier memory, optionally with a hand-set timestamp."""
    mem = create_memory(type_="pattern", title=title, body="content", confidence=0.7)
    if timestamp is not None:
        mem.timestamp = timestamp
    return store_memory(mem, bundle, tier="archived")


def test_purge_archived_deletes_old_z_timestamped_and_keeps_recent(tmp_path):
    """purge_archived ages Z-suffixed timestamps (the store's own format) and
    skips malformed/naive ones instead of degrading them to age 0."""
    from cairn.memory.store import purge_archived

    bundle = OKFBundle(str(tmp_path / "knowledge"))
    old = _archived_memory(
        bundle, "old memory", timestamp="2024-01-01T00:00:00Z"
    )
    recent = _archived_memory(
        bundle, "recent memory",
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    malformed = _archived_memory(bundle, "malformed memory", timestamp="not-a-date")

    purged = purge_archived(bundle, max_days=90)

    assert purged == 1
    assert not (bundle.root / f"{old}.md").exists()
    assert (bundle.root / f"{recent}.md").exists()
    assert (bundle.root / f"{malformed}.md").exists()


def test_delete_memory_removes_refs_written_with_absolute_id(tmp_path, fresh_db):
    """memory_refs rows persisted under the absolute concept_id form (the form
    the search-time writers record) are deleted with their memory."""
    bundle = OKFBundle(str(tmp_path / "knowledge"))
    mem = create_memory(type_="pattern", title="abs ref target", body="content")
    mem_id = store_memory(mem, bundle)
    abs_id = str(bundle.root / mem_id)

    cursor = fresh_db.cursor()
    ts = datetime.now(timezone.utc).isoformat()
    cursor.execute(
        "INSERT INTO memory_refs (memory_path, session_id, referenced_at)"
        " VALUES (?, ?, ?)",
        (abs_id, "session1", ts),
    )
    fresh_db.commit()

    result = delete_memory(bundle, mem_id, conn=fresh_db)
    assert result is True

    remaining = cursor.execute("SELECT memory_path FROM memory_refs").fetchall()
    assert abs_id not in [r[0] for r in remaining]


def _write_compass_concept(bundle: OKFBundle) -> None:
    """One compass-namespaced concept reachable through get_memory's fallback."""
    bundle.write_concept(OKFConcept(
        type="Compass", title="guide", body="navigation",
        concept_id="compass/guide", tags=["guide"], extensions={},
    ))


def test_get_demote_promote_refuse_non_memory_namespace(tmp_path, fresh_db):
    """Concepts resolved outside memory/ are refused, like delete_memory does."""
    bundle = OKFBundle(str(tmp_path / "knowledge"))
    _write_compass_concept(bundle)

    assert get_memory(bundle, "compass/guide") is None
    assert demote_memory(bundle, "compass/guide", target_tier="raw", conn=fresh_db) is None
    assert promote_memory(bundle, "compass/guide", conn=fresh_db) is None

    assert (bundle.root / "compass" / "guide.md").exists()
    assert bundle.list_concepts(prefix="memory/") == []


def test_create_memory_clamps_confidence_to_unit_range():
    """Percent-scale or negative confidence clamps into [0, 1] before tiering."""
    hot = create_memory(type_="decision", title="hot", body="b", confidence=95)
    assert hot.extensions["memory_score"] == 1.0
    assert hot.extensions["memory_tier"] == "tribal"
    cold = create_memory(type_="decision", title="cold", body="b", confidence=-3)
    assert cold.extensions["memory_score"] == 0.0
    assert cold.extensions["memory_tier"] == "raw"


def test_purge_archived_counts_only_removed_files(tmp_path):
    """An aged candidate whose file is already absent is not counted as purged."""
    from cairn.memory.store import purge_archived

    bundle = OKFBundle(str(tmp_path / "knowledge"))
    old = _archived_memory(bundle, "vanished memory", timestamp="2024-01-01T00:00:00Z")
    (bundle.root / f"{old}.md").unlink()

    assert purge_archived(bundle, max_days=90) == 0


def test_list_memories_warns_on_unreadable_concept(tmp_path, caplog):
    """A corrupt memory file still drops from the listing, but logs which id."""
    bundle = OKFBundle(str(tmp_path / "knowledge"))
    mem = create_memory(type_="pattern", title="corrupt target", body="b", confidence=0.7)
    cid = store_memory(mem, bundle)
    (bundle.root / f"{cid}.md").write_bytes(b"\xff\xfe not utf-8 \x80")

    with caplog.at_level(logging.WARNING, logger="cairn.memory.store"):
        mems = list_memories(bundle)

    assert mems == []
    assert any(cid in r.getMessage() for r in caplog.records)


def test_consolidate_records_relative_source_ids(tmp_path):
    """consolidated_from stores bundle-relative ids, not absolute machine paths."""
    bundle = OKFBundle(str(tmp_path / "knowledge"))
    for body in ("v1", "v2"):
        store_memory(
            create_memory(type_="pattern", title="dup note", body=body, confidence=0.1),
            bundle, tier="raw",
        )

    count = consolidate_memories(bundle)

    assert count == 2
    tribal = bundle.list_concepts(prefix="memory/tribal")
    assert len(tribal) == 1
    unified = bundle.read_concept(tribal[0])
    sources = unified.extensions["consolidated_from"]
    assert len(sources) == 2
    assert all(not cid.startswith("/") for cid in sources)
    assert all(cid.startswith("memory/raw/") for cid in sources)


def test_consolidate_counts_only_successful_archives(tmp_path, caplog, monkeypatch):
    """A failed per-source archive is warned about and excluded from the count."""
    bundle = OKFBundle(str(tmp_path / "knowledge"))
    for body in ("v1", "v2"):
        store_memory(
            create_memory(type_="pattern", title="dup note", body=body, confidence=0.1),
            bundle, tier="raw",
        )

    original = bundle.write_concept

    def flaky(concept):
        if concept.extensions.get("memory_tier") == "archived":
            raise OSError("disk full")
        return original(concept)

    monkeypatch.setattr(bundle, "write_concept", flaky)

    with caplog.at_level(logging.WARNING, logger="cairn.memory.store"):
        count = consolidate_memories(bundle)

    assert count == 0
    assert any(
        "failed to archive consolidated source" in r.getMessage()
        for r in caplog.records
    )
    assert len(bundle.list_concepts(prefix="memory/tribal")) == 1
    assert len(bundle.list_concepts(prefix="memory/raw")) == 2


def _seed_embedding_row(conn, doc_id: str) -> None:
    """One minimal memory_embeddings row keyed to doc_id."""
    conn.execute(
        "INSERT INTO memory_embeddings"
        " (doc_id, chunk_index, model, dim, vec, chunk, embedded_at)"
        " VALUES (?, 0, 'm', 1, x'00', 'c', '2026-01-01T00:00:00')",
        (doc_id,),
    )


def test_delete_memory_removes_validity_and_embedding_rows(tmp_path, fresh_db):
    """Deleting a memory drops its validity and embedding rows, not just refs."""
    bundle = OKFBundle(str(tmp_path / "knowledge"))
    mem_id = store_memory(
        create_memory(type_="pattern", title="doomed note", body="content", confidence=0.7),
        bundle, conn=fresh_db,
    )
    _seed_embedding_row(fresh_db, mem_id)
    assert fresh_db.execute("SELECT COUNT(*) FROM memory_validity").fetchone()[0] == 1

    assert delete_memory(bundle, mem_id, conn=fresh_db) is True

    assert fresh_db.execute("SELECT COUNT(*) FROM memory_validity").fetchone()[0] == 0
    assert fresh_db.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0
    assert fresh_db.execute("SELECT COUNT(*) FROM memory_refs").fetchone()[0] == 0


def test_demote_renames_embedding_through_symlinked_root(tmp_path, fresh_db):
    """A symlinked bundle root still resolves absolute concept ids for the rename."""
    real = tmp_path / "real"
    link = tmp_path / "link"
    real.mkdir()
    link.symlink_to(real)
    bundle = OKFBundle(str(link / "k"))
    mem_id = store_memory(
        create_memory(type_="pattern", title="symlink demote", body="content", confidence=0.7),
        bundle,
    )
    _seed_embedding_row(fresh_db, mem_id)

    new_id = demote_memory(bundle, mem_id, target_tier="raw", conn=fresh_db)

    assert new_id is not None
    docs = [r["doc_id"] for r in
            fresh_db.execute("SELECT doc_id FROM memory_embeddings").fetchall()]
    assert docs == [new_id]


def test_purge_archived_deletes_db_rows(tmp_path, fresh_db):
    """purge_archived drops the validity, ref, and embedding rows of purged files."""
    from cairn.memory.store import purge_archived

    bundle = OKFBundle(str(tmp_path / "knowledge"))
    old = store_memory(
        create_memory(type_="pattern", title="aged row carrier", body="content", confidence=0.7),
        bundle, tier="archived", conn=fresh_db,
    )
    stored = bundle.read_concept(old)
    stored.timestamp = "2024-01-01T00:00:00Z"
    bundle.write_concept(stored)
    _seed_embedding_row(fresh_db, old)
    fresh_db.execute(
        "INSERT INTO memory_refs (id, memory_path, session_id, referenced_at, context)"
        " VALUES ('r1', ?, 's1', '2026-01-01T00:00:00', '')",
        (old,),
    )

    purged = purge_archived(bundle, max_days=90, conn=fresh_db)

    assert purged == 1
    assert fresh_db.execute("SELECT COUNT(*) FROM memory_validity").fetchone()[0] == 0
    assert fresh_db.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0
    assert fresh_db.execute("SELECT COUNT(*) FROM memory_refs").fetchone()[0] == 0


def test_consolidate_carries_rows_to_new_ids(tmp_path, fresh_db):
    """Consolidation renames source rows to the archived ids and rows the unified concept."""
    bundle = OKFBundle(str(tmp_path / "knowledge"))
    ids = []
    for body in ("v1", "v2"):
        ids.append(store_memory(
            create_memory(type_="pattern", title="dup rows", body=body, confidence=0.1),
            bundle, tier="raw", conn=fresh_db,
        ))
    _seed_embedding_row(fresh_db, ids[0])

    count = consolidate_memories(bundle, conn=fresh_db)

    assert count == 2
    validity_ids = {r["concept_id"] for r in
                    fresh_db.execute("SELECT concept_id FROM memory_validity").fetchall()}
    embed_ids = {r["doc_id"] for r in
                 fresh_db.execute("SELECT doc_id FROM memory_embeddings").fetchall()}
    assert not any(i in validity_ids for i in ids)
    assert len(validity_ids) == 3
    assert embed_ids and all(i.startswith("memory/archived/") for i in embed_ids)
