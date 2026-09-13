"""update-path embedding.

`reindex_paths` is the common entry for `cairn update`, sync, and the
watcher. This module pins the AC3 invariant: after a changed file is
re-indexed with a semantic backend available, the file's re-created
symbols carry embeddings under the current model -- no manual
`cairn embed` needed -- and the return dict reports how many symbols
were embedded.
"""
from __future__ import annotations

import logging
import pytest

from cairn.graph import embeddings as emb
from cairn.graph.incremental import reindex_paths


def _file_symbol_ids(conn, rel_path):
    return {
        r["id"]
        for r in conn.execute(
            "SELECT id FROM symbols WHERE file_id = "
            "(SELECT id FROM files WHERE path = ?)",
            (rel_path,),
        )
    }


# ---------------------------------------------------------------------------
# AC3 -- reindex re-embeds a changed file's symbols.
# ---------------------------------------------------------------------------

def test_reindex_embeds_changed_file_symbols(caller_callee_db, caller_callee_ws, tmp_path, hash_backend):
    """Re-indexing a changed file must embed its re-created symbols:
    the old embedding rows (including the vec0 rows) are deleted and the
    re-created symbols are re-embedded, with the embedded count on the
    return dict.
    """
    db = str(tmp_path / "embed.db")
    conn = caller_callee_db(db)
    try:
        model = emb.current_model()

        # Baseline: a prior wholesale `cairn embed` covered the corpus.
        assert emb.embed_all(conn)["embedded"] > 0
        pre_ids = _file_symbol_ids(conn, "b.kt")
        assert pre_ids, "expected b.kt symbols after build"
        pre_embedded = {
            r["symbol_id"]
            for r in conn.execute(
                "SELECT symbol_id FROM embeddings WHERE model = ?", (model,)
            )
        }
        assert pre_ids <= pre_embedded, "baseline embed pass missed b.kt symbols"

        # Change b.kt and reindex it.
        (caller_callee_ws / "demo" / "b.kt").write_text(
            "class Callee {\n"
            "  fun target() {}\n"
            "  fun extra() {}\n"  # new symbol created by the edit
            "}\n"
        )
        result = reindex_paths(conn, str(caller_callee_ws), [str(caller_callee_ws / "demo" / "b.kt")])
        assert result["errors"] == []

        # The re-created file's symbols (new ids, embeddings deleted by the
        # reindex delete leg) must be embedded again under the current model.
        new_ids = _file_symbol_ids(conn, "b.kt")
        assert len(new_ids) >= 2, "expected the edit to re-create symbols"
        embedded_ids = {
            r["symbol_id"]
            for r in conn.execute(
                "SELECT symbol_id FROM embeddings WHERE model = ?", (model,)
            )
        }
        missing = sorted(new_ids - embedded_ids)
        assert not missing, (
            "changed file's symbols must carry embeddings under the current "
            f"model after reindex_paths; unembedded: {missing}"
        )
        assert result.get("embedded_symbols") == len(new_ids), (
            "reindex_paths must report the embedded_symbols count on its "
            f"return dict; got {result.get('embedded_symbols')!r} for "
            f"{len(new_ids)} re-created symbols"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# embed_symbols' commit-contention degradation must not poison the pass:
# a batch left buffered on an open transaction is settled before the next
# file leg's BEGIN.
# ---------------------------------------------------------------------------

def test_reindex_settles_embed_symbols_open_transaction(
    caller_callee_db, caller_callee_ws, tmp_path, hash_backend, monkeypatch
):
    """embed_symbols' commit-failure path (lock contention) returns with the
    batch buffered on an open transaction and embedded=0. reindex_paths must
    settle that transaction per file leg, or the next leg's BEGIN fails and
    that leg's rollback records a spurious parse error for a parseable file.
    """

    def buffered_batch(conn, symbol_ids):
        # The degradation contract: transaction left open, embedded=0.
        conn.execute("BEGIN")
        return {
            "model": emb.current_model(),
            "embedded": 0,
            "skipped": len(symbol_ids),
            "ann_synced": 0,
        }

    monkeypatch.setattr(emb, "embed_symbols", buffered_batch)

    db = str(tmp_path / "settle.db")
    conn = caller_callee_db(db)
    try:
        result = reindex_paths(
            conn,
            str(caller_callee_ws),
            [str(caller_callee_ws / "demo" / "a.kt"), str(caller_callee_ws / "demo" / "b.kt")],
        )
        assert result["reindexed"] == 2, "both files must reindex cleanly"
        assert result["errors"] == [], (
            "an embed commit-contention degradation must not fail the "
            f"following file legs: {result['errors']}"
        )
        assert result["deferred_embeds"] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC4 -- no backend available: the update still succeeds and the
# deferral is observable (degrade-not-fail, never the embed CLI's hard exit).
# ---------------------------------------------------------------------------

@pytest.fixture
def no_backend(monkeypatch):
    """Select the server backend against a dead endpoint so the availability
    probe fails: ``embeddings_available()`` is False for the test -- the
    no-backend condition, with no hash fallback (the backend was chosen, it
    just cannot be reached). Entry+exit cache reset mirrors ``hash_backend``.
    """
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "http://127.0.0.1:9/v1")
    emb.reset_backend_cache()
    yield
    emb.reset_backend_cache()


def test_reindex_without_backend_succeeds_and_defers_observably(
    caller_callee_db, caller_callee_ws, tmp_path, no_backend, caplog
):
    """Re-indexing with no backend available must succeed and make the
    deferral observable: one WARN per pass carrying the deferred count, plus
    a deferred_embeds count on the return dict -- never a silent skip of the
    embed leg.
    """
    db = str(tmp_path / "defer.db")
    conn = caller_callee_db(db)
    try:
        assert emb.embeddings_available() is False, (
            "fixture must present a machine with no usable backend"
        )

        # Change b.kt so the reindex re-creates its symbols.
        (caller_callee_ws / "demo" / "b.kt").write_text(
            "class Callee {\n"
            "  fun target() {}\n"
            "  fun extra() {}\n"
            "}\n"
        )
        with caplog.at_level(logging.WARNING):
            result = reindex_paths(
                conn, str(caller_callee_ws), [str(caller_callee_ws / "demo" / "b.kt")]
            )

        # Degrade-not-fail: the update itself succeeds (never a hard exit).
        assert result["errors"] == [], (
            "a missing backend must never fail the update"
        )

        new_ids = _file_symbol_ids(conn, "b.kt")
        assert new_ids, "expected b.kt symbols after the reindex"
        assert result.get("deferred_embeds") == len(new_ids), (
            "reindex_paths must report the deferred_embeds count when no "
            f"backend is available; got {result.get('deferred_embeds')!r} "
            f"for {len(new_ids)} unembedded symbols"
        )

        warns = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and r.name.startswith("cairn.")
        ]
        assert len(warns) == 1, (
            f"expected one WARN per deferred pass, got {len(warns)}: "
            f"{[r.getMessage() for r in warns]}"
        )
        assert str(len(new_ids)) in warns[0].getMessage(), (
            "the WARN must carry the deferred count"
        )
    finally:
        conn.close()
