"""FR-005 (T017): the parallel ``embeddings_mv`` multi-vector table.

Covers the ship gate (TC-020) and the producer/staleness contract:
- Flag OFF (the default): zero ``embeddings_mv`` writes and the
  ``embeddings`` table flow byte-identical to a flag-off run (D-006).
- Flag ON: both kinds ('name', 'docstring') populated with kind-specific
  texts, each with its OWN per-kind content-hash staleness, and the
  producers stay OUT of ``CHUNK_VARIANTS`` (TC-008's identity-floor test
  iterates that tuple).

Uses CAIRN_EMBED_BACKEND=hash so no torch/model download is needed.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest
from click.testing import CliRunner

from cairn.graph.schema import init_db

pytestmark = pytest.mark.usefixtures("hash_backend")


def _seed_corpus(conn: sqlite3.Connection) -> None:
    """Three symbols: '1' and '3' have docstrings, '2' does not.

    File paths deliberately do NOT exist on disk, so no signature line is
    read and the name-kind text is exactly ``"<kind> <qualified_name>"``.
    """
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/Api.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES ('1', 1, 'safeApiCall', 'function', 'xyz.safeApiCall', 'Handles retries with backoff.', 1, 10)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('2', 1, 'parseHeader', 'function', 'xyz.parseHeader', 12, 20)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES ('3', 1, 'loadConfig', 'function', 'xyz.loadConfig', 'Loads the config file.', 22, 30)"
    )
    conn.commit()


def _mv_rows(conn: sqlite3.Connection) -> dict:
    """{(symbol_id, vector_kind): (chunk, content_hash, embedded_at)}."""
    return {
        (r[0], r[1]): (r[2], r[3], r[4])
        for r in conn.execute(
            "SELECT symbol_id, vector_kind, chunk, content_hash, embedded_at "
            "FROM embeddings_mv ORDER BY symbol_id, vector_kind"
        )
    }


def _embeddings_snapshot(conn: sqlite3.Connection):
    """Base-table rows minus embedded_at (the timestamp is run-specific)."""
    return conn.execute(
        "SELECT symbol_id, model, dim, vec, chunk, content_hash "
        "FROM embeddings ORDER BY symbol_id"
    ).fetchall()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_mv_table_exists_on_fresh_init():
    """Fresh init_db must carry embeddings_mv with the 3-column PK."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "fresh.db")
        conn = init_db(path)
        try:
            info = conn.execute("PRAGMA table_info(embeddings_mv)").fetchall()
            cols = [r[1] for r in info]
            assert cols == [
                "symbol_id", "model", "vector_kind",
                "dim", "vec", "chunk", "content_hash", "embedded_at",
            ]
            pk_cols = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
            assert pk_cols == ["symbol_id", "model", "vector_kind"]
        finally:
            conn.close()


def test_existing_db_gains_mv_table_on_reopen():
    """A DB created before the table (simulated by DROP) regains it on reopen."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "old.db")
        conn = init_db(path)
        conn.execute("DROP TABLE embeddings_mv")
        conn.commit()
        conn.close()

        conn = init_db(path)  # executescript re-applies CREATE IF NOT EXISTS
        try:
            assert conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'embeddings_mv'"
            ).fetchone() is not None
        finally:
            conn.close()


def test_base_embeddings_pk_unchanged():
    """D-006: the base table's PK stays (symbol_id, model) -- never re-PK'd."""
    with tempfile.TemporaryDirectory() as tmp:
        conn = init_db(os.path.join(tmp, "pk.db"))
        try:
            info = conn.execute("PRAGMA table_info(embeddings)").fetchall()
            pk_cols = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
            assert pk_cols == ["symbol_id", "model"]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Producers are NOT chunk variants (TC-008 identity floor)
# ---------------------------------------------------------------------------


def test_kinds_are_not_chunk_variants():
    """The mv kinds must not enter CHUNK_VARIANTS (identity-floor tests
    iterate that tuple; joining it would break the floor for minimal texts)."""
    from cairn.graph.embeddings import CHUNK_VARIANTS, MV_KINDS

    assert MV_KINDS == ("name", "docstring")
    assert not set(MV_KINDS) & set(CHUNK_VARIANTS)
    # The pre-FR-005 variant tuple is exactly unchanged.
    assert CHUNK_VARIANTS == (
        "A", "B", "C",
        "B_NO_SCOPE", "B_NO_SIG", "B_IDENTITIES", "C_TRIM",
    )


# ---------------------------------------------------------------------------
# Flag OFF (default) -- TC-020
# ---------------------------------------------------------------------------


def test_flag_off_writes_zero_mv_rows_and_keeps_summary_shape(fresh_db):
    """Default embed_all: no mv writes, no 'mv_embedded' key, base rows exist."""
    from cairn.graph import embeddings as emb

    _seed_corpus(fresh_db)
    summary = emb.embed_all(fresh_db)  # no multivector kwarg: legacy call shape

    assert summary["embedded"] == 3
    assert "mv_embedded" not in summary, "flag-off summary must keep its prior shape"
    assert fresh_db.execute("SELECT COUNT(*) c FROM embeddings_mv").fetchone()["c"] == 0
    assert emb.embed_count(fresh_db) == 3

    # A second (idempotent) flag-off run still writes nothing to the mv table.
    emb.embed_all(fresh_db)
    assert fresh_db.execute("SELECT COUNT(*) c FROM embeddings_mv").fetchone()["c"] == 0


def test_flag_off_base_table_identical_to_flag_on_base_table(fresh_db):
    """The base `embeddings` rows are byte-identical whether or not the mv
    flag is on: the flag must never perturb the single-vector flow."""
    from cairn.graph import embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db, multivector=False)
    flag_off_snapshot = [tuple(r) for r in _embeddings_snapshot(fresh_db)]
    assert len(flag_off_snapshot) == 3

    # Fresh identical corpus, embedded WITH the flag: base rows must match.
    other = sqlite3.connect(":memory:")
    other.row_factory = sqlite3.Row
    from cairn.graph.schema import _apply_schema

    _apply_schema(other)
    _seed_corpus(other)
    emb.embed_all(other, multivector=True)
    flag_on_snapshot = [tuple(r) for r in _embeddings_snapshot(other)]
    other.close()

    assert flag_on_snapshot == flag_off_snapshot


# ---------------------------------------------------------------------------
# Flag ON -- population + kind texts
# ---------------------------------------------------------------------------


def test_flag_on_populates_both_kinds_with_kind_specific_texts(fresh_db):
    """--multivector: name rows carry kind+qname (+sig line), docstring rows
    carry the bare docstring; a docstring-less symbol gets name rows only."""
    from cairn.graph import embeddings as emb

    _seed_corpus(fresh_db)
    summary = emb.embed_all(fresh_db, multivector=True)

    rows = _mv_rows(fresh_db)
    # 3 name rows + 2 docstring rows (symbol '2' has no docstring).
    assert summary["mv_embedded"] == 5
    assert len(rows) == 5
    assert set(rows) == {
        ("1", "name"), ("1", "docstring"),
        ("2", "name"),
        ("3", "name"), ("3", "docstring"),
    }
    # Kind-specific texts.
    assert rows[("1", "name")][0] == "function xyz.safeApiCall"
    assert rows[("1", "docstring")][0] == "Handles retries with backoff."
    assert rows[("2", "name")][0] == "function xyz.parseHeader"
    assert ("2", "docstring") not in rows, "docstring-less symbol gets no docstring row"
    # Per-kind hashes differ from each other and from the base chunk hash.
    base = fresh_db.execute(
        "SELECT content_hash FROM embeddings WHERE symbol_id = '1'"
    ).fetchone()[0]
    assert rows[("1", "name")][1] != rows[("1", "docstring")][1] != base


def test_name_text_includes_signature_line_when_file_exists(fresh_db, tmp_path):
    """The name-kind recipe is kind + qualified name + signature line."""
    from cairn.graph import embeddings as emb

    src = tmp_path / "Api.kt"
    src.write_text("class Api {\n    fun safeApiCall(retries: Int): Result<Response> {\n}\n}\n")
    fresh_db.execute(
        "INSERT INTO repos (id, name, path) VALUES ('test', 'test', ?)",
        (str(tmp_path),),
    )
    fresh_db.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', ?, 'kotlin')",
        (str(src),),
    )
    fresh_db.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('1', 1, 'safeApiCall', 'function', 'xyz.Api.safeApiCall', 2, 3)"
    )
    fresh_db.commit()
    emb.embed_all(fresh_db, multivector=True)

    rows = _mv_rows(fresh_db)
    assert rows[("1", "name")][0] == (
        "function xyz.Api.safeApiCall\n"
        "fun safeApiCall(retries: Int): Result<Response> {"
    )


def test_mv_idempotent_second_run(fresh_db):
    """Second flag-on run with no edits embeds 0 mv rows."""
    from cairn.graph import embeddings as emb

    _seed_corpus(fresh_db)
    first = emb.embed_all(fresh_db, multivector=True)
    second = emb.embed_all(fresh_db, multivector=True)

    assert first["mv_embedded"] == 5
    assert second["mv_embedded"] == 0
    assert len(_mv_rows(fresh_db)) == 5


# ---------------------------------------------------------------------------
# Per-kind staleness
# ---------------------------------------------------------------------------


def test_per_kind_staleness_refreshes_only_the_changed_kind(fresh_db):
    """Editing one field refreshes ONLY that kind's row for that symbol."""
    from cairn.graph import embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db, multivector=True)
    before = _mv_rows(fresh_db)

    # '1': docstring edited -> docstring row refreshes, name row must NOT.
    fresh_db.execute(
        "UPDATE symbols SET docstring = 'Handles retries with backoff and jitter.' "
        "WHERE id = '1'"
    )
    # '2': identity edited -> name row refreshes (it has no docstring row).
    fresh_db.execute(
        "UPDATE symbols SET name = 'parseHeaderStrict', qualified_name = 'xyz.parseHeaderStrict' "
        "WHERE id = '2'"
    )
    # '3': untouched -> BOTH rows must stay frozen.
    fresh_db.commit()

    summary = emb.embed_all(fresh_db, multivector=True)
    after = _mv_rows(fresh_db)

    assert summary["mv_embedded"] == 2, "only '1'/docstring and '2'/name are stale"
    assert after[("1", "docstring")] != before[("1", "docstring")]
    assert after[("1", "docstring")][0] == "Handles retries with backoff and jitter."
    assert after[("1", "name")] == before[("1", "name")], "name text unchanged -> row frozen"
    assert after[("2", "name")] != before[("2", "name")]
    assert after[("2", "name")][0] == "function xyz.parseHeaderStrict"
    assert after[("3", "name")] == before[("3", "name")]
    assert after[("3", "docstring")] == before[("3", "docstring")]


def test_docstring_removed_deletes_its_mv_row(fresh_db):
    """A docstring deleted since the last pass must not keep serving old text."""
    from cairn.graph import embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db, multivector=True)
    assert ("1", "docstring") in _mv_rows(fresh_db)

    fresh_db.execute("UPDATE symbols SET docstring = NULL WHERE id = '1'")
    fresh_db.commit()
    summary = emb.embed_all(fresh_db, multivector=True)

    rows = _mv_rows(fresh_db)
    assert ("1", "docstring") not in rows
    assert ("1", "name") in rows, "the name row survives its sibling's removal"
    assert summary["mv_embedded"] == 0


# ---------------------------------------------------------------------------
# Reaping
# ---------------------------------------------------------------------------


def test_reap_removes_orphaned_mv_rows(fresh_db):
    """Deleting a symbol reaps its base row AND both mv rows."""
    from cairn.graph import embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db, multivector=True)
    assert ("1", "name") in _mv_rows(fresh_db)

    fresh_db.execute("DELETE FROM symbols WHERE id = '1'")
    fresh_db.commit()
    reaped = emb.reap_orphaned_embeddings(fresh_db)

    assert reaped == 3, "1 base row + 2 mv rows"
    rows = _mv_rows(fresh_db)
    assert ("1", "name") not in rows and ("1", "docstring") not in rows


def test_embed_all_reaps_mv_by_default(fresh_db):
    """embed_all(multivector=True) reaps orphans in the same pass."""
    from cairn.graph import embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db, multivector=True)
    fresh_db.execute("DELETE FROM symbols WHERE id = '3'")
    fresh_db.commit()

    summary = emb.embed_all(fresh_db, multivector=True)
    assert summary["reaped"] == 3
    # Remaining mv rows: '1'/name, '1'/docstring, '2'/name.
    assert set(_mv_rows(fresh_db)) == {("1", "name"), ("1", "docstring"), ("2", "name")}


# ---------------------------------------------------------------------------
# CLI wiring (TC-020's user-facing surface)
# ---------------------------------------------------------------------------


def test_cli_multivector_flag_wires_to_embed_all(tmp_path, monkeypatch):
    """`cairn embed --multivector` populates the table; without it, empty."""
    from cairn.cli import main as cli_main

    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    runner = CliRunner()

    # Flag ON.
    db_on = str(tmp_path / "on.db")
    conn = init_db(db_on)
    _seed_corpus(conn)
    conn.close()
    result = runner.invoke(
        cli_main, ["embed", "--db", db_on, "--multivector"], catch_exceptions=False
    )
    assert result.exit_code == 0
    conn = sqlite3.connect(db_on)
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("SELECT COUNT(*) c FROM embeddings_mv").fetchone()["c"] == 5
    finally:
        conn.close()

    # Flag OFF (default): same corpus, zero mv rows, base index built.
    db_off = str(tmp_path / "off.db")
    conn = init_db(db_off)
    _seed_corpus(conn)
    conn.close()
    result = runner.invoke(cli_main, ["embed", "--db", db_off], catch_exceptions=False)
    assert result.exit_code == 0
    conn = sqlite3.connect(db_off)
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("SELECT COUNT(*) c FROM embeddings_mv").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM embeddings").fetchone()["c"] == 3
    finally:
        conn.close()
