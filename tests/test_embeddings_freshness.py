"""Phase 7b: embedding freshness (content-hash invalidation) and orphan reap.

Uses CAIRN_EMBED_BACKEND=hash so the test needs no torch/model download --
same dep-free smoke-test posture as the rest of the semantic stack.

Covers two gaps closed alongside HNSW/rerank work:
1. A symbol's embedding must re-embed when its docstring changes, even though
   the model name (and therefore embeddings.model stamp) is unchanged.
2. A symbol's embedding row must be deleted once the symbol itself is gone.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

# Apply the shared hash-backend fixture to every test in this module
pytestmark = pytest.mark.usefixtures("hash_backend")


def _seed_one_symbol(conn: sqlite3.Connection, docstring: str = "Handles retries.") -> None:
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/Api.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES (1, 1, 'safeApiCall', 'function', 'xyz.safeApiCall', ?, 1, 10)",
        (docstring,),
    )
    conn.commit()


def test_unchanged_symbol_is_not_reembedded(fresh_db):
    """Re-running embed_all with no edits should embed 0 new rows the second time."""
    from cairn.graph import embeddings as emb

    _seed_one_symbol(fresh_db)
    conn = fresh_db
    first = emb.embed_all(conn)
    assert first["embedded"] == 1

    second = emb.embed_all(conn)
    assert second["embedded"] == 0, "unchanged docstring must not trigger re-embedding"


def test_edited_docstring_triggers_reembed_without_model_change(fresh_db):
    """The gap this closes: editing a docstring under the same model must re-embed."""
    from cairn.graph import embeddings as emb

    _seed_one_symbol(fresh_db, docstring="Handles retries.")
    conn = fresh_db
    emb.embed_all(conn)
    before_row = conn.execute(
        "SELECT vec, content_hash FROM embeddings WHERE symbol_id = '1'"
    ).fetchone()

    conn.execute(
        "UPDATE symbols SET docstring = 'Handles retries and backoff.' WHERE id = '1'"
    )
    conn.commit()

    summary = emb.embed_all(conn)
    assert summary["embedded"] == 1, "edited docstring must be detected as stale and re-embedded"

    after_row = conn.execute(
        "SELECT vec, content_hash FROM embeddings WHERE symbol_id = '1'"
    ).fetchone()
    assert after_row["content_hash"] != before_row["content_hash"]
    assert after_row["vec"] != before_row["vec"]


def test_orphan_reap_deletes_vectors_for_removed_symbols(fresh_db):
    """The gap this closes: a deleted symbol's embedding must not linger forever."""
    from cairn.graph import embeddings as emb

    _seed_one_symbol(fresh_db)
    conn = fresh_db
    emb.embed_all(conn)
    assert emb.embed_count(conn) == 1

    conn.execute("DELETE FROM symbols WHERE id = '1'")
    conn.commit()

    reaped = emb.reap_orphaned_embeddings(conn)
    assert reaped == 1
    assert emb.embed_count(conn) == 0


def test_embed_all_reaps_by_default(fresh_db):
    """embed_all's default reap_orphans=True should reap without a separate call."""
    from cairn.graph import embeddings as emb

    _seed_one_symbol(fresh_db)
    conn = fresh_db
    emb.embed_all(conn)

    conn.execute("DELETE FROM symbols WHERE id = '1'")
    conn.commit()

    summary = emb.embed_all(conn)
    assert summary["reaped"] == 1
    assert emb.embed_count(conn) == 0


def test_null_content_hash_from_legacy_row_is_treated_as_stale(fresh_db):
    """Rows written before the content_hash column existed must self-heal."""
    from cairn.graph import embeddings as emb

    _seed_one_symbol(fresh_db)
    conn = fresh_db
    emb.embed_all(conn)
    # Simulate a pre-migration row: blank out content_hash as if it were NULL
    # all along (mirrors a DB that had the column added but never backfilled).
    conn.execute("UPDATE embeddings SET content_hash = NULL WHERE symbol_id = '1'")
    conn.commit()

    summary = emb.embed_all(conn)
    assert summary["embedded"] == 1, "NULL content_hash must be treated as stale, not skipped"


def test_chunk_includes_declaration_line_from_disk(fresh_db):
    """The 'richer chunks' recommendation: signature line should join the chunk.

    Most symbols have no docstring, so the declaration line read from disk is
    what actually gives the embedding model real code instead of a bare
    identifier. Verifies the line is read via line_start and lands in the
    stored `chunk` column.
    """
    from cairn.graph import embeddings as emb

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, "Api.kt")
        with open(file_path, "w") as fh:
            fh.write(
                "class Api {\n"
                "    fun safeApiCall(retries: Int): Result<Response> {\n"
                "        return call()\n"
                "    }\n"
                "}\n"
            )

        conn = fresh_db
        conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', ?, 'kotlin')",
            (file_path,),
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
            "VALUES (1, 1, 'safeApiCall', 'function', 'xyz.Api.safeApiCall', 2, 4)"
        )
        conn.commit()

        emb.embed_all(conn)
        row = conn.execute("SELECT chunk FROM embeddings WHERE symbol_id = '1'").fetchone()
        assert "fun safeApiCall(retries: Int): Result<Response> {" in row["chunk"]


def test_missing_file_degrades_to_no_signature(fresh_db):
    """A moved/deleted file must not crash embed_all -- just no signature line."""
    from cairn.graph import embeddings as emb

    _seed_one_symbol(fresh_db)  # file_path '/tmp/test/Api.kt' doesn't exist
    summary = emb.embed_all(fresh_db)
    assert summary["embedded"] == 1
