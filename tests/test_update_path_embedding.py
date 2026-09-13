"""Update-path embedding: reindex_paths re-embeds changed files' symbols and
reports embedded/deferred counts on its return dict.
"""
from __future__ import annotations

import logging
import pytest

from cairn.graph import embeddings as emb
from cairn.graph.incremental import reindex_paths

# The b.kt edit that re-creates its symbols (one extra symbol).
_EDITED_B_KT = "class Callee {\n  fun target() {}\n  fun extra() {}\n}\n"


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
    """Reindexing a changed file deletes its old embedding rows and re-embeds
    the re-created symbols under the current model, reporting embedded_symbols.
    """
    db = str(tmp_path / "embed.db")
    conn = caller_callee_db(db)
    try:
        model = emb.current_model()

        # Baseline embed so the reindex exercises delete + re-embed, not first embed.
        assert emb.embed_all(conn)["embedded"] > 0
        pre_ids = _file_symbol_ids(conn, "b.kt")
        pre_embedded = {
            r["symbol_id"]
            for r in conn.execute(
                "SELECT symbol_id FROM embeddings WHERE model = ?", (model,)
            )
        }
        assert pre_ids and pre_ids <= pre_embedded, "baseline embed missed b.kt"

        (caller_callee_ws / "demo" / "b.kt").write_text(_EDITED_B_KT)
        result = reindex_paths(conn, str(caller_callee_ws), [str(caller_callee_ws / "demo" / "b.kt")])
        assert result["errors"] == []

        new_ids = _file_symbol_ids(conn, "b.kt")
        assert len(new_ids) >= 2, "expected the edit to re-create symbols"
        embedded_ids = {
            r["symbol_id"]
            for r in conn.execute(
                "SELECT symbol_id FROM embeddings WHERE model = ?", (model,)
            )
        }
        assert not (new_ids - embedded_ids), "re-created symbols must be embedded"
        assert result.get("embedded_symbols") == len(new_ids)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# embed_symbols' buffered-batch degradation must not poison the next file leg.
# ---------------------------------------------------------------------------

def test_reindex_settles_embed_symbols_open_transaction(
    caller_callee_db, caller_callee_ws, tmp_path, hash_backend, monkeypatch
):
    """A commit-contention degradation (batch buffered, transaction left open,
    embedded=0) from embed_symbols must be settled before the next file leg's
    BEGIN, or that leg fails and its rollback drops the buffered rows.
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
            f"the degradation must not fail following file legs: {result['errors']}"
        )
        assert result["deferred_embeds"] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC4 -- no backend: the update succeeds and the deferral is observable.
# ---------------------------------------------------------------------------

@pytest.fixture
def no_backend(monkeypatch):
    """Server backend against a dead endpoint: embeddings_available() is False
    with no hash fallback. Cache reset mirrors hash_backend."""
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "http://127.0.0.1:9/v1")
    emb.reset_backend_cache()
    yield
    emb.reset_backend_cache()


def test_reindex_without_backend_succeeds_and_defers_observably(
    caller_callee_db, caller_callee_ws, tmp_path, no_backend, caplog
):
    """No backend: the update succeeds and the deferral is observable -- one
    WARN carrying the count, plus deferred_embeds on the return dict.
    """
    db = str(tmp_path / "defer.db")
    conn = caller_callee_db(db)
    try:
        assert emb.embeddings_available() is False

        (caller_callee_ws / "demo" / "b.kt").write_text(_EDITED_B_KT)
        with caplog.at_level(logging.WARNING):
            result = reindex_paths(
                conn, str(caller_callee_ws), [str(caller_callee_ws / "demo" / "b.kt")]
            )

        assert result["errors"] == [], "a missing backend must never fail the update"

        new_ids = _file_symbol_ids(conn, "b.kt")
        assert new_ids, "expected b.kt symbols after the reindex"
        assert result.get("deferred_embeds") == len(new_ids)

        warns = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and r.name.startswith("cairn.")
        ]
        assert len(warns) == 1, f"expected one WARN per pass, got {len(warns)}"
        assert str(len(new_ids)) in warns[0].getMessage(), "WARN must carry the count"
    finally:
        conn.close()
