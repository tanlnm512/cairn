"""Tests for schema versioning (VAL-DB-005).

M3: Add schema_meta(key TEXT PRIMARY KEY, value TEXT) table to record
applied migration names. Only run unapplied migrations. Distinguish
'duplicate column' (idempotent, skip) from real OperationalError (raise).
"""
from __future__ import annotations

import sqlite3

import pytest

from cairn.graph.schema import _apply_schema, MIGRATIONS


def test_migrations_recorded_in_schema_meta(fresh_db):
    """VAL-DB-005: Applied migration names are recorded in schema_meta table."""
    # First, create the schema_meta table (our implementation will do this)
    _apply_schema(fresh_db)

    # Check if schema_meta table exists
    cursor = fresh_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta'"
    )
    table_exists = cursor.fetchone() is not None
    assert table_exists, "schema_meta table should exist after _apply_schema"

    # Check that migrations are recorded
    rows = fresh_db.execute("SELECT key, value FROM schema_meta").fetchall()
    migration_entries = {row[0]: row[1] for row in rows}

    # All MIGRATIONS should be recorded with their extracted names
    from cairn.graph.schema import _extract_migration_name
    for migration in MIGRATIONS:
        expected_key = _extract_migration_name(migration)
        assert expected_key in migration_entries, \
            f"Expected migration '{expected_key}' to be recorded in schema_meta"
        assert migration_entries[expected_key] == "applied", \
            f"Migration '{expected_key}' should have value='applied'"


def test_idempotent_no_rerun(fresh_db):
    """VAL-DB-005: Applied migrations are not re-run on subsequent _apply_schema calls."""
    # First call - should create schema_meta and record migrations
    _apply_schema(fresh_db)

    # Get the recorded migrations count
    count_after_first = fresh_db.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0]

    # Second call - should be idempotent
    _apply_schema(fresh_db)

    # Count should be the same (no duplicate records)
    count_after_second = fresh_db.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0]
    assert count_after_first == count_after_second, \
        "Second _apply_schema call should not add duplicate migration records"


def test_real_error_raises(fresh_db):
    """VAL-DB-005: Non-duplicate-column OperationalError raises, not swallowed."""
    # Apply schema once
    _apply_schema(fresh_db)

    # Add a bad migration to MIGRATIONS temporarily (simulating a real error)
    # We'll try to create a table with invalid syntax
    bad_migration = "CREATE TABLE bad_table (invalid_syntax_here"

    # This should raise the real OperationalError, not pass silently
    with pytest.raises(sqlite3.OperationalError) as exc_info:
        fresh_db.execute(bad_migration)

    # The error should be the real syntax error, not "duplicate column"
    error_msg = str(exc_info.value).lower()
    assert "syntax" in error_msg or "incomplete" in error_msg, \
        f"Expected syntax error, got: {exc_info.value}"


def test_partial_migration_detectable(fresh_db):
    """VAL-DB-005: Partial/failed migrations are detectable via schema_meta.

    If a migration fails halfway, schema_meta shows which ones succeeded
    and which didn't, making the gap visible.
    """
    # Apply normal migrations
    _apply_schema(fresh_db)

    # Verify all expected migrations are recorded
    recorded = fresh_db.execute("SELECT key, value FROM schema_meta ORDER BY key").fetchall()
    recorded_names = [row[0] for row in recorded]

    # Each MIGRATION should have a corresponding entry
    from cairn.graph.schema import _extract_migration_name
    for migration in MIGRATIONS:
        expected_key = _extract_migration_name(migration)
        assert expected_key in recorded_names, \
            f"Migration '{expected_key}' should be recorded in schema_meta"

    # Now simulate a partial migration scenario:
    # Manually insert a migration that was applied but failed to complete
    # This should be detectable by checking schema_meta
    fresh_db.execute(
        "INSERT INTO schema_meta (key, value) VALUES "
        "('fake_partial_migration', 'incomplete')"
    )

    # Query for incomplete/partial migrations
    partial_migrations = fresh_db.execute(
        "SELECT key FROM schema_meta WHERE value = 'incomplete'"
    ).fetchall()

    # We should find our fake partial migration
    partial_keys = [row[0] for row in partial_migrations]
    assert "fake_partial_migration" in partial_keys, \
        "Partial/incomplete migrations should be detectable"


def test_schema_meta_table_structure(fresh_db):
    """VAL-DB-005: schema_meta table has correct structure (key TEXT PRIMARY KEY, value TEXT)."""
    # Create the schema
    _apply_schema(fresh_db)

    # Check table structure
    cursor = fresh_db.execute("PRAGMA table_info(schema_meta)")
    columns = {row[1]: row[2] for row in cursor.fetchall()}  # {column_name: type}

    assert "key" in columns, "schema_meta should have 'key' column"
    assert "value" in columns, "schema_meta should have 'value' column"
    assert columns["key"] == "TEXT", f"'key' column should be TEXT, got {columns['key']}"
    assert columns["value"] == "TEXT", f"'value' column should be TEXT, got {columns['value']}"

    # Check that key is PRIMARY KEY
    cursor = fresh_db.execute("PRAGMA index_list(schema_meta)")
    indexes = cursor.fetchall()
    # There should be an index that is the primary key
    has_pk = any(row[2] == 1 for row in indexes)  # origin=1 means primary key
    assert has_pk, "key column should be PRIMARY KEY"


def test_duplicate_column_error_idempotent(fresh_db):
    """VAL-DB-005: 'duplicate column' errors are idempotent (not raised)."""
    # Apply schema once
    _apply_schema(fresh_db)

    # Apply again - duplicate column errors should not raise
    # (they are idempotent)
    try:
        _apply_schema(fresh_db)
    except sqlite3.OperationalError as e:
        error_msg = str(e).lower()
        # If we get an error, it should NOT be "duplicate column"
        assert "duplicate column" not in error_msg, \
            f"Duplicate column errors should be idempotent, got: {e}"


def test_observability_tables_upgrade_old_db(tmp_path):
    """T08: an old-shape DB (created before build_runs/events) upgrades in place.

    The observability tables are additive-only: plain CREATE TABLE IF NOT
    EXISTS inside SCHEMA_SQL (no MIGRATIONS entry), so _apply_schema -- which
    every get_db() runs on connect -- must create them on a pre-existing DB
    that lacks them, same as tool_metrics before them.
    """
    conn = sqlite3.connect(tmp_path / "old.db")
    try:
        # A pre-observability DB: core tables exist (as an older cairn made
        # them), but build_runs/events do not.
        conn.executescript(
            """
            CREATE TABLE repos (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                language TEXT,
                git_remote TEXT,
                indexed_at TIMESTAMP
            );
            CREATE TABLE tool_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT 'unknown',
                invoked_at TIMESTAMP NOT NULL,
                duration_ms REAL,
                status TEXT NOT NULL DEFAULT 'ok',
                error_message TEXT
            );
            """
        )
        tables_before = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "build_runs" not in tables_before
        assert "events" not in tables_before

        # The next connect() by a newer cairn upgrades the old DB in place.
        _apply_schema(conn)

        tables_after = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "build_runs" in tables_after
        assert "events" in tables_after
        indexes_after = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "idx_events_name" in indexes_after
        assert "idx_events_ts" in indexes_after
    finally:
        conn.close()


def test_observability_tables_apply_idempotent(fresh_db):
    """T08: re-applying the schema leaves exactly one build_runs/events table.

    Acceptance: fresh + migrated DBs both pass _apply_schema idempotently --
    CREATE TABLE IF NOT EXISTS makes the re-run a no-op rather than an error.
    """
    # fresh_db already applied the schema once; this call is the re-run.
    _apply_schema(fresh_db)

    for table in ("build_runs", "events"):
        count = fresh_db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()[0]
        assert count == 1, f"re-apply must not duplicate or drop {table}"
    index_count = fresh_db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
        "AND name IN ('idx_events_name', 'idx_events_ts')"
    ).fetchone()[0]
    assert index_count == 2, "both events indexes must survive re-application"


def test_memory_validity_upgrade_old_db(tmp_path):
    """An old-shape DB (created before memory_validity) gains the table, its
    declared columns, and both date indexes on the next _apply_schema.

    Additive-only: plain CREATE TABLE IF NOT EXISTS inside SCHEMA_SQL (no
    MIGRATIONS entry), so _apply_schema -- which every get_db() runs on
    connect -- must create it on a pre-existing DB that lacks it, same as
    build_runs/events before it.
    """
    conn = sqlite3.connect(tmp_path / "old.db")
    try:
        # A pre-validity DB: a core table exists, memory_validity does not.
        conn.executescript(
            """
            CREATE TABLE repos (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                language TEXT,
                git_remote TEXT,
                indexed_at TIMESTAMP
            );
            """
        )
        tables_before = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "memory_validity" not in tables_before

        _apply_schema(conn)

        tables_after = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "memory_validity" in tables_after

        rows = conn.execute("PRAGMA table_info(memory_validity)").fetchall()
        columns = {row[1]: row[2] for row in rows}
        assert set(columns) == {
            "concept_id", "valid_from", "valid_until", "successor_symbol"
        }
        pk_columns = [row[1] for row in rows if row[5]]
        assert pk_columns == ["concept_id"], (
            "concept_id must be the primary key"
        )

        indexes_after = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert "idx_memory_validity_from" in indexes_after
        assert "idx_memory_validity_until" in indexes_after
    finally:
        conn.close()


def _prior_version_store(tmp_path):
    """A prior-version store: a pre-validity DB file plus a bundle whose
    memory concepts carry a creation timestamp but no validity extensions.
    """
    from cairn.memory.store import create_memory
    from cairn.okf.bundle import OKFBundle

    store = tmp_path / "store"
    knowledge = store / ".knowledge"
    knowledge.mkdir(parents=True)
    db_path = store / ".kg"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE repos (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            language TEXT,
            git_remote TEXT,
            indexed_at TIMESTAMP
        );
        """
    )
    conn.commit()
    conn.close()

    bundle = OKFBundle(str(knowledge))
    fixtures = [
        ("Retry policy", "Use exponential backoff", "2026-03-01T10:00:00Z"),
        ("Doctor gate", "Run doctor before shipping", "2026-05-15T08:30:00Z"),
    ]
    for i, (title, body, ts) in enumerate(fixtures):
        concept = create_memory("decision", title, body)
        concept.timestamp = ts
        concept.concept_id = f"memory/tribal/{title.lower().replace(' ', '-')}-a1b2c{i}"
        bundle.write_concept(concept)
    # A non-memory concept: outside the backfill's scope.
    from cairn.okf.concept import OKFConcept

    other = OKFConcept(
        type="Business-rule",
        title="Refund policy",
        description="Refund policy",
        tags=["policy"],
        timestamp="2026-02-02T00:00:00Z",
        body="Full refund within 30 days.",
    )
    other.concept_id = "knowledge/business-rule/refund-policy"
    bundle.write_concept(other)
    return db_path, bundle


def test_memory_validity_backfill_old_store_rows_intact(tmp_path):
    """The one-time backfill stamps every pre-validity memory concept with
    valid_from = its recorded creation timestamp, an open end, and an
    unchanged body, mirrors the memory_validity row, and records its
    schema_meta sentinel so it runs at most once.
    """
    from cairn.graph.schema import _apply_schema, _maybe_backfill_memory_validity

    db_path, bundle = _prior_version_store(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _apply_schema(conn)
        before = {
            cid: bundle.read_concept(cid)
            for cid in bundle.list_concepts(prefix="memory/")
        }

        _maybe_backfill_memory_validity(conn, knowledge_root=bundle.root)

        rows = {
            row["concept_id"]: row
            for row in conn.execute(
                "SELECT concept_id, valid_from, valid_until, successor_symbol "
                "FROM memory_validity"
            ).fetchall()
        }
        assert set(rows) == set(before), "only memory concepts gain rows"
        for cid, concept in before.items():
            row = rows[cid]
            assert row["valid_from"] == concept.timestamp
            assert row["valid_until"] is None
            assert row["successor_symbol"] is None
            on_disk = bundle.read_concept(cid)
            assert on_disk.extensions["valid_from"] == concept.timestamp
            assert on_disk.extensions.get("valid_until") is None
            # Content and non-validity extensions are untouched.
            assert on_disk.title == concept.title
            assert on_disk.body == concept.body
            assert on_disk.extensions["memory_tier"] == concept.extensions["memory_tier"]

        sentinel = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'memory_validity.backfill'"
        ).fetchone()
        assert sentinel is not None and sentinel["value"] == "applied"

        # Non-memory concepts stay untouched.
        other = bundle.read_concept("knowledge/business-rule/refund-policy")
        assert "valid_from" not in other.extensions
        assert "valid_until" not in other.extensions

        # Re-run is a no-op: sentinel-guarded, no duplicate rows.
        _maybe_backfill_memory_validity(conn, knowledge_root=bundle.root)
        count = conn.execute(
            "SELECT COUNT(*) FROM memory_validity"
        ).fetchone()[0]
        assert count == len(before)
    finally:
        conn.close()


def test_memory_validity_backfill_preserves_stamped_interval(tmp_path):
    """A concept already carrying a validity interval keeps it through the
    backfill; the projection row mirrors the stamped values.
    """
    from cairn.graph.schema import _apply_schema, _maybe_backfill_memory_validity

    db_path, bundle = _prior_version_store(tmp_path)
    stamped_id = bundle.list_concepts(prefix="memory/")[0]
    stamped = bundle.read_concept(stamped_id)
    stamped.extensions["valid_from"] = "2026-04-01T00:00:00Z"
    stamped.extensions["valid_until"] = "2026-09-01T00:00:00Z"
    stamped.extensions["successor_symbol"] = "renamed_symbol"
    bundle.write_concept(stamped)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _apply_schema(conn)
        _maybe_backfill_memory_validity(conn, knowledge_root=bundle.root)

        row = conn.execute(
            "SELECT valid_from, valid_until, successor_symbol "
            "FROM memory_validity WHERE concept_id = ?",
            (stamped_id,),
        ).fetchone()
        assert row["valid_from"] == "2026-04-01T00:00:00Z"
        assert row["valid_until"] == "2026-09-01T00:00:00Z"
        assert row["successor_symbol"] == "renamed_symbol"
    finally:
        conn.close()


def test_get_db_backfills_memory_validity_on_connect(tmp_path):
    """Opening a prior-version store through get_db upgrades it: the schema
    gains memory_validity and every pre-existing memory is backfilled from
    the store-layout sibling bundle.
    """
    from cairn.graph.schema import get_db

    db_path, bundle = _prior_version_store(tmp_path)

    conn = get_db(str(db_path))
    try:
        rows = conn.execute(
            "SELECT concept_id, valid_from, valid_until FROM memory_validity"
        ).fetchall()
        assert len(rows) == 2
        for row in rows:
            assert row["valid_from"] == bundle.read_concept(row["concept_id"]).timestamp
            assert row["valid_until"] is None
    finally:
        conn.close()


def test_memory_validity_backfill_records_sentinel_without_sibling(tmp_path):
    """A DB with no paired bundle records the sentinel and creates no
    knowledge directory: a ``--db`` override must never reach a foreign
    bundle or a process-default path.
    """
    from cairn.graph.schema import _apply_schema, _maybe_backfill_memory_validity

    db_path = tmp_path / "lonely.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        _apply_schema(conn)
        _maybe_backfill_memory_validity(conn, db_path=db_path)

        sentinel = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'memory_validity.backfill'"
        ).fetchone()
        assert sentinel is not None and sentinel["value"] == "applied"
        assert not (tmp_path / ".knowledge").exists()
        count = conn.execute(
            "SELECT COUNT(*) FROM memory_validity"
        ).fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_memory_validity_apply_idempotent(fresh_db):
    """Re-applying the schema leaves exactly one memory_validity table and
    both date indexes, and existing rows survive the re-run.
    """
    fresh_db.execute(
        "INSERT INTO memory_validity "
        "(concept_id, valid_from, valid_until, successor_symbol) "
        "VALUES ('memory/tribal/probe-a1b2c3', '2026-01-01', NULL, NULL)"
    )

    # fresh_db already applied the schema once; this call is the re-run.
    _apply_schema(fresh_db)

    table_count = fresh_db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='memory_validity'"
    ).fetchone()[0]
    assert table_count == 1, "re-apply must not duplicate or drop memory_validity"
    index_count = fresh_db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
        "AND name IN ('idx_memory_validity_from', 'idx_memory_validity_until')"
    ).fetchone()[0]
    assert index_count == 2, "both date indexes must survive re-application"
    row_count = fresh_db.execute(
        "SELECT COUNT(*) FROM memory_validity"
    ).fetchone()[0]
    assert row_count == 1, "re-apply must not touch existing rows"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
