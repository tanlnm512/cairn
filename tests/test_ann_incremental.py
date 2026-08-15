"""PERF-4: incremental vec0 maintenance (sync-on-write + drift recovery).

sqlite-vec IS installed in this environment (spiked -- v0.1.9), so these
tests exercise the real extension against real vec0 tables, using the hash
embedder (CAIRN_EMBED_BACKEND=hash) so no model download is needed.

Covers the P4 contract:
1. P4.1 spike regression -- vec0 really has no replace semantics (a plain
   INSERT on an existing rowid raises), which is why sync is delete+insert.
2. P4.2 sync-on-write -- embed_symbols (the single-upsert seam) makes a
   new/changed embedding visible to ann_query WITHOUT any rebuild, under the
   same transaction, while preserving the embeddings rowid the vec0 table
   keys on. The bulk embed_all path stays exempt (wholesale rebuild only).
   Deletions (orphan reap) remove the vec row too.
3. P4.3 drift detection -- the doctor's ANN check reports row-count drift
   with counts in both directions (missing vs stale vec rows).
4. No-op guards -- CAIRN_ANN_BACKEND=off and no-index-built are pure no-ops,
   and a successful sync emits no fallback warning/telemetry.
"""
from __future__ import annotations

import logging
import sqlite3
import struct

import pytest

sqlite_vec = pytest.importorskip(
    "sqlite_vec", reason="sqlite-vec not installed -- ANN tests need the real extension"
)

pytestmark = pytest.mark.usefixtures("hash_backend")


def _seed_symbols(conn: sqlite3.Connection, n_seed: int = 2) -> None:
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/Api.kt', 'kotlin')"
    )
    docs = {
        1: "Retries a network call with backoff.",
        2: "Formats a date for display.",
        3: "Parses ISO-8601 timestamp strings into datetime objects.",
    }
    for i in range(1, n_seed + 1):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
            f"VALUES ({i}, 1, 'sym{i}', 'function', 'xyz.sym{i}', '{docs[i]}', {i}, {i + 9})"
        )
    conn.commit()


def _add_symbol(conn: sqlite3.Connection, sid: int, name: str, doc: str) -> None:
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        f"VALUES ({sid}, 1, '{name}', 'function', 'xyz.{name}', '{doc}', {sid}, {sid + 9})"
    )
    conn.commit()


def _built_index(conn, n_seed: int = 2):
    """embed + wholesale-rebuild a baseline index over n_seed symbols."""
    from cairn.graph import ann_index as ann, embeddings as emb

    _seed_symbols(conn, n_seed)
    emb.embed_all(conn)
    model = emb.current_model()
    ann.rebuild_index(conn, model)
    return model


def _emb_rowid(conn, model: str, sid: str) -> int:
    return conn.execute(
        "SELECT rowid FROM embeddings WHERE symbol_id = ? AND model = ?", (sid, model)
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# P4.1 -- spike regression: vec0 has no replace semantics
# ---------------------------------------------------------------------------


def test_vec0_insert_on_existing_rowid_raises(fresh_db):
    """Documents the P4.1 spike as a regression canary: on the pinned
    sqlite-vec, INSERTing a rowid that's already in the vec0 table raises
    (even under OR REPLACE / OR IGNORE -- the PK lives inside the vtab
    module). sync_index_row's delete+insert idiom is the ONLY update path;
    if a future sqlite-vec gains true replace semantics, revisit it (and
    this test)."""
    from cairn.graph import ann_index as ann

    assert ann.try_load(fresh_db), "sqlite-vec loads in this environment"
    table = ann._table_name("spike-model")
    fresh_db.execute(
        f"CREATE VIRTUAL TABLE {table} USING vec0(embedding float[4] distance_metric=cosine)"
    )
    vec = struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)
    fresh_db.execute(f"INSERT INTO {table}(rowid, embedding) VALUES (1, ?)", (vec,))
    fresh_db.commit()

    with pytest.raises(sqlite3.OperationalError):
        fresh_db.execute(
            f"INSERT INTO {table}(rowid, embedding) VALUES (1, ?)", (vec,)
        )


# ---------------------------------------------------------------------------
# P4.2 -- sync-on-write: upsert -> visible without rebuild
# ---------------------------------------------------------------------------


def test_embed_symbols_new_symbol_visible_without_rebuild(fresh_db):
    """The core PERF-4 win: after a single upsert via embed_symbols, the new
    symbol is returned by ann_query immediately -- no rebuild_index call."""
    from cairn.graph import ann_index as ann, embeddings as emb

    model = _built_index(fresh_db, n_seed=2)
    assert ann.index_row_count(fresh_db, model) == 2

    _add_symbol(fresh_db, 3, "parseIsoTimestamp", "Parses ISO-8601 timestamp strings.")
    summary = emb.embed_symbols(fresh_db, ["3"])

    assert summary["embedded"] == 1
    assert summary["ann_synced"] == 1, "vec0 row written in the same transaction"
    assert ann.index_row_count(fresh_db, model) == 3, "index grew without a rebuild"

    q_blob, _ = emb.embed_query("parseIsoTimestamp")
    hits = ann.ann_query(fresh_db, model, q_blob, k=5)
    assert hits is not None
    ids = [sid for sid, _score in hits]
    assert "3" in ids, "the just-upserted symbol is ANN-visible"


def test_embed_symbols_updates_existing_vec_row(fresh_db):
    """Re-embedding a CHANGED symbol replaces its vec0 entry in place: the
    query vector now matches the new text, the row count stays flat, and the
    embeddings rowid (the vec0 key) is preserved."""
    from cairn.graph import ann_index as ann, embeddings as emb

    model = _built_index(fresh_db, n_seed=2)
    rid_before = _emb_rowid(fresh_db, model, "2")

    # Change symbol 2's content so the chunk hash no longer matches.
    fresh_db.execute(
        "UPDATE symbols SET docstring = 'Parses ISO-8601 timestamp strings.' "
        "WHERE id = '2'"
    )
    fresh_db.commit()

    summary = emb.embed_symbols(fresh_db, ["2"])
    assert summary["embedded"] == 1
    assert summary["ann_synced"] == 1

    # Rowid-preservation contract: ON CONFLICT DO UPDATE kept the rowid the
    # vec0 table keys on (INSERT OR REPLACE would have reassigned it).
    assert _emb_rowid(fresh_db, model, "2") == rid_before
    # Update, not append: still exactly 2 vec rows.
    assert ann.index_row_count(fresh_db, model) == 2

    # The vec row now carries the NEW vector: querying the new text returns
    # symbol 2 as the top hit (it was the *old* formatDate text before).
    q_blob, _ = emb.embed_query("ISO-8601 timestamp")
    hits = ann.ann_query(fresh_db, model, q_blob, k=2)
    assert hits is not None
    assert hits[0][0] == "2", f"updated vec row ranks first, got {hits}"


def test_embed_symbols_idempotent_skip_does_not_sync(fresh_db):
    """An unchanged symbol (content_hash still matches) is skipped: no
    embedding write, no vec0 write, ann_synced == 0."""
    from cairn.graph import embeddings as emb

    model = _built_index(fresh_db, n_seed=2)
    before = _emb_rowid(fresh_db, model, "1")

    summary = emb.embed_symbols(fresh_db, ["1"])
    assert summary["embedded"] == 0
    assert summary["skipped"] == 1
    assert summary["ann_synced"] == 0
    assert _emb_rowid(fresh_db, model, "1") == before


def test_reap_deletes_vec_rows(fresh_db):
    """Deleting a symbol (then reaping its orphaned embedding) must remove
    the vec0 row too -- not just leave the join to filter it out: a stale
    entry keyed on a rowid SQLite may REUSE would mis-pair a future
    embedding with an unrelated vector."""
    from cairn.graph import ann_index as ann, embeddings as emb

    model = _built_index(fresh_db, n_seed=2)
    rid2 = _emb_rowid(fresh_db, model, "2")
    table = ann._table_name(model)

    fresh_db.execute("DELETE FROM symbols WHERE id = '2'")
    fresh_db.commit()
    reaped = emb.reap_orphaned_embeddings(fresh_db)

    assert reaped == 1
    assert ann.index_row_count(fresh_db, model) == 1
    stale = fresh_db.execute(
        f"SELECT COUNT(*) FROM {table} WHERE rowid = ?", (rid2,)
    ).fetchone()[0]
    assert stale == 0, "the reaped row's vec0 entry is physically gone"

    # The survivor is still queryable.
    q_blob, _ = emb.embed_query("sym1")
    hits = ann.ann_query(fresh_db, model, q_blob, k=5)
    assert hits is not None and "1" in [sid for sid, _ in hits]


# ---------------------------------------------------------------------------
# P4.2 -- bulk path stays on the wholesale rebuild
# ---------------------------------------------------------------------------


def test_bulk_embed_all_leaves_vec0_to_wholesale_rebuild(fresh_db):
    """embed_all (the bulk pass over the whole corpus) must NOT pay per-row
    vec sync: new embeddings land, the vec0 table stays at its old count,
    and rebuild_index remains the bulk seam that realigns it."""
    from cairn.graph import ann_index as ann, embeddings as emb

    model = _built_index(fresh_db, n_seed=2)
    assert ann.index_row_count(fresh_db, model) == 2

    _add_symbol(fresh_db, 3, "parseIsoTimestamp", "Parses ISO-8601 timestamp strings.")
    summary = emb.embed_all(fresh_db)

    assert summary["embedded"] == 1, "the new symbol was embedded"
    assert emb.embed_count(fresh_db) == 3
    assert ann.index_row_count(fresh_db, model) == 2, (
        "bulk path made no per-row vec0 writes (wholesale rebuild is its seam)"
    )

    # And the wholesale rebuild still realigns everything.
    ann.rebuild_index(fresh_db, model)
    assert ann.index_row_count(fresh_db, model) == 3


# ---------------------------------------------------------------------------
# P4.3 -- doctor drift detection with counts
# ---------------------------------------------------------------------------


def _doctor_ann_row(conn):
    from cairn.cli.system import _FAIL, _PASS, _WARN, _check_ann

    row = _check_ann(conn)
    assert row["status"] in (_PASS, _WARN, _FAIL)
    return row


def test_doctor_flags_missing_vec_rows_drift(fresh_db):
    """emb_n > idx_n direction: an embeddings row landed without its vec0
    sync (here simulated by a direct insert that bypasses the seam) ->
    WARN 'ANN index stale' with BOTH counts and the `cairn embed` heal hint."""
    model = _built_index(fresh_db, n_seed=2)
    # Bypass embed_symbols to force drift, exactly like a pre-PERF-4 write
    # path (or a crash between the upsert and its sync) would leave behind.
    fresh_db.execute(
        "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk, content_hash, embedded_at) "
        "VALUES ('3', ?, 4, ?, 'bypassed the sync seam', 'x', NULL)",
        (model, struct.pack("<4f", 0.1, 0.2, 0.3, 0.4)),
    )
    fresh_db.commit()

    row = _doctor_ann_row(fresh_db)
    assert row["status"] == "WARN"
    assert "stale" in row["detail"].lower()
    assert "3 embedding(s) vs 2 indexed" in row["detail"]
    assert "1 unindexed" in row["detail"]
    assert "cairn embed" in (row.get("hint") or "")


def test_doctor_flags_stale_vec_rows_drift(fresh_db):
    """idx_n > emb_n direction: an embeddings row was removed without its
    vec0 entry following (the mis-pairing hazard) -> WARN with counts and
    the 'stale vector(s)' phrasing."""
    _built_index(fresh_db, n_seed=2)
    # Bypass the reap seam: delete the embeddings row directly.
    fresh_db.execute("DELETE FROM embeddings WHERE symbol_id = '2'")
    fresh_db.commit()

    row = _doctor_ann_row(fresh_db)
    assert row["status"] == "WARN"
    assert "stale" in row["detail"].lower()
    assert "2 indexed vs 1 embedding(s)" in row["detail"]
    assert "1 stale vector(s)" in row["detail"]
    assert "cairn embed" in (row.get("hint") or "")


def test_doctor_passes_when_index_in_sync(fresh_db):
    """After sync-on-write, a store that only used the incremental seams has
    equal counts -> the ANN check stays PASS (no drift false-positive)."""
    from cairn.graph import ann_index as ann, embeddings as emb

    model = _built_index(fresh_db, n_seed=2)
    _add_symbol(fresh_db, 3, "parseIsoTimestamp", "Parses ISO-8601 timestamp strings.")
    emb.embed_symbols(fresh_db, ["3"])

    row = _doctor_ann_row(fresh_db)
    assert row["status"] == "PASS"
    assert "3 vector(s) indexed" in row["detail"]
    assert ann.index_row_count(fresh_db, model) == 3


# ---------------------------------------------------------------------------
# No-op guards -- CAIRN_ANN_BACKEND=off / no index built
# ---------------------------------------------------------------------------


def test_sync_noop_when_ann_backend_off(monkeypatch, fresh_db, caplog):
    """CAIRN_ANN_BACKEND=off: embed_symbols still writes the embedding, but
    performs no vec0 work (ann_synced == 0) and stays silent -- an explicit
    opt-out must not warn (mirrors warn_ann_fallback_once)."""
    from cairn.graph import ann_index as ann, embeddings as emb

    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    caplog.set_level(logging.WARNING, logger="cairn.graph.ann_index")

    _seed_symbols(fresh_db, n_seed=2)
    summary = emb.embed_symbols(fresh_db, ["1"])

    assert summary["embedded"] == 1
    assert summary["ann_synced"] == 0
    assert emb.embed_count(fresh_db) == 1, "the embedding itself still landed"
    assert not ann.index_exists(fresh_db, emb.current_model())
    # Direct helper call is equally a pure no-op.
    assert ann.sync_index_row(fresh_db, emb.current_model(), 1, b"\x00" * 1024) is False
    assert ann.delete_index_rows(fresh_db, emb.current_model(), [1]) == 0
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_sync_noop_when_no_index_built(monkeypatch, fresh_db):
    """ANN enabled but no vec0 table exists yet (pre-`cairn embed` store):
    embed_symbols is a pure no-op on the index side -- it must NOT lazily
    create a vec0 table (that stays the wholesale rebuild's job), and
    ann_query keeps returning None for the caller's brute-force fallback."""
    from cairn.graph import ann_index as ann, embeddings as emb

    monkeypatch.delenv("CAIRN_ANN_BACKEND", raising=False)
    _seed_symbols(fresh_db, n_seed=2)

    summary = emb.embed_symbols(fresh_db, ["1"])
    assert summary["embedded"] == 1
    assert summary["ann_synced"] == 0
    assert not ann.index_exists(fresh_db, emb.current_model()), (
        "single upserts never create the vec0 table"
    )

    q_blob, _ = emb.embed_query("sym1")
    assert ann.ann_query(fresh_db, emb.current_model(), q_blob, 5) is None


def test_successful_sync_emits_no_fallback_warning(monkeypatch, fresh_db, caplog):
    """A successful synced upsert is silent: no ann_fallback warning fires
    from the sync path (telemetry contract unchanged -- the warning is for
    degradations, and there is none here)."""
    from cairn.graph import embeddings as emb

    monkeypatch.delenv("CAIRN_ANN_BACKEND", raising=False)
    ann = pytest.importorskip("cairn.graph.ann_index")
    ann._ANN_FALLBACK_WARNED = False
    caplog.set_level(logging.WARNING, logger="cairn.graph.ann_index")

    model = _built_index(fresh_db, n_seed=2)
    _add_symbol(fresh_db, 3, "parseIsoTimestamp", "Parses ISO-8601 timestamp strings.")
    emb.embed_symbols(fresh_db, ["3"])

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == [], "a healthy sync must not degrade-warn"
    assert ann.index_row_count(fresh_db, model) == 3
