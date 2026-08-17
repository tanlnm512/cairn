"""Tests for the persisted term_df DF table (spec retrieval-quality-v2 T011).

FR-003/D-005: ``term_df(token, symbol_df, n_symbols)`` is a per-corpus
document-frequency table built from the ``symbols_fts`` FTS5 vocabulary
(row-mode fts5vocab; aggregate-scan fallback), refreshed on the embed pass.
The builder must be a pure function of the DB contents (TC-014):
deterministic, hermetic, no env/network/time dependence.
"""
from __future__ import annotations

import sqlite3

import pytest

import cairn.graph.embeddings as emb
import cairn.graph.schema as schema
from cairn.graph.schema import _apply_schema, init_db, rebuild_term_df

# Two seeded symbols. unicode61 case-folds and splits on non-alphanumerics,
# so per-symbol token sets are:
#   s1 (parse_url / m.parse_url / "Parse an encoded URL string")
#       -> {parse, url, m, an, encoded, string}
#   s2 (fetch_url / m.fetch_url / "Fetch URL")
#       -> {fetch, url, m}
# 'URL' in the docstrings folds onto the same 'url' token as the names -- the
# case-folding pitfall FR-003 calls out for the lookup key.
SEEDED = [
    ("s1", "parse_url", "m.parse_url", "Parse an encoded URL string"),
    ("s2", "fetch_url", "m.fetch_url", "Fetch URL"),
]
EXPECTED = {
    "an": (1, 2),
    "encoded": (1, 2),
    "fetch": (1, 2),
    "m": (2, 2),
    "parse": (1, 2),
    "string": (1, 2),
    "url": (2, 2),
}


def _seed(conn, symbols=SEEDED):
    """Insert one repo/file plus (id, name, qualified_name, docstring) rows."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/r')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES ('f1', 'r', 'a.py', 'python')"
    )
    for sid, name, qname, doc in symbols:
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind, docstring) "
            "VALUES (?, ?, ?, ?, 'function', ?)",
            (sid, "f1", name, qname, doc),
        )
    conn.commit()


def _rows(conn):
    """term_df contents as {token: (symbol_df, n_symbols)}, key order stable."""
    return {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            "SELECT token, symbol_df, n_symbols FROM term_df ORDER BY token"
        )
    }


def test_fresh_init_has_term_df():
    """Fresh init_db: term_df exists with exactly (token PK, symbol_df, n_symbols)."""
    conn = init_db(":memory:")
    try:
        info = list(conn.execute("PRAGMA table_info(term_df)"))
        assert [r[1] for r in info] == ["token", "symbol_df", "n_symbols"]
        assert [r[5] for r in info] == [1, 0, 0], "token must be the PRIMARY KEY"
    finally:
        conn.close()


def test_builder_expected_rows(fresh_db):
    """Seeded symbols -> builder maps each token to (distinct-symbol df, n)."""
    _seed(fresh_db)
    written = rebuild_term_df(fresh_db)
    assert written == len(EXPECTED)
    assert _rows(fresh_db) == EXPECTED


def test_refresh_tracks_symbol_changes(fresh_db):
    """A rebuild after symbol deletions updates df AND the n_symbols denominator."""
    _seed(fresh_db)
    rebuild_term_df(fresh_db)
    assert _rows(fresh_db) == EXPECTED

    fresh_db.execute("DELETE FROM symbols WHERE id = 's2'")
    fresh_db.commit()
    rebuild_term_df(fresh_db)
    rows = _rows(fresh_db)
    assert rows["url"] == (1, 1)
    assert "fetch" not in rows
    assert all(n == 1 for _df, n in rows.values())


def test_rebuild_deterministic(fresh_db):
    """Build twice -> identical table contents; same on an identically-seeded DB."""
    _seed(fresh_db)
    rebuild_term_df(fresh_db)
    first = _rows(fresh_db)

    rebuild_term_df(fresh_db)
    assert _rows(fresh_db) == first

    # Hermetic (TC-014): a separate, identically-seeded DB builds the same
    # table -- the result depends only on DB contents, not process state.
    other = sqlite3.connect(":memory:")
    other.row_factory = sqlite3.Row
    _apply_schema(other)
    try:
        _seed(other)
        rebuild_term_df(other)
        assert _rows(other) == first
    finally:
        other.close()


def test_fallback_scan_matches_vocab(fresh_db, monkeypatch):
    """fts5vocab unusable -> one aggregate scan produces the same rows."""
    _seed(fresh_db)
    monkeypatch.setattr(schema, "_rebuild_term_df_vocab", lambda conn, n: None)
    written = rebuild_term_df(fresh_db)
    assert written == len(EXPECTED)
    assert _rows(fresh_db) == EXPECTED


def test_vocab_table_not_left_behind(fresh_db):
    """The transient fts5vocab temp table is dropped after the rebuild."""
    _seed(fresh_db)
    rebuild_term_df(fresh_db)
    for master in ("sqlite_master", "sqlite_temp_master"):
        leftover = fresh_db.execute(
            f"SELECT COUNT(*) FROM {master} WHERE name = 'term_df_vocab'"
        ).fetchone()[0]
        assert leftover == 0, f"term_df_vocab left behind in {master}"


def test_empty_corpus_builds_empty_table(fresh_db):
    """No symbols -> zero rows, zero written (not an error)."""
    assert rebuild_term_df(fresh_db) == 0
    assert _rows(fresh_db) == {}


def test_migration_old_db_gains_term_df(tmp_path):
    """A pre-term_df DB upgrades in place on the next open, data intact.

    Mirrors the additive pattern test for build_runs/events: plain CREATE
    TABLE IF NOT EXISTS in SCHEMA_SQL, applied by every get_db() connect.
    """
    db_path = str(tmp_path / "old.db")
    old = sqlite3.connect(db_path)
    try:
        old.executescript(
            """
            CREATE TABLE repos (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
                language TEXT, git_remote TEXT, indexed_at TIMESTAMP
            );
            CREATE TABLE files (
                id TEXT PRIMARY KEY, repo_id TEXT NOT NULL, path TEXT NOT NULL,
                language TEXT NOT NULL, hash TEXT, line_count INTEGER,
                indexed_at TIMESTAMP, UNIQUE(repo_id, path)
            );
            CREATE TABLE symbols (
                id TEXT PRIMARY KEY, file_id TEXT NOT NULL, name TEXT NOT NULL,
                qualified_name TEXT, kind TEXT NOT NULL, line_start INTEGER,
                line_end INTEGER, column_start INTEGER, column_end INTEGER,
                docstring TEXT, modifiers TEXT
            );
            INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/r');
            INSERT INTO files (id, repo_id, path, language)
                VALUES ('f1', 'r', 'a.py', 'python');
            INSERT INTO symbols (id, file_id, name, qualified_name, kind, docstring)
                VALUES ('legacy', 'f1', 'old_thing', 'm.old_thing', 'class', 'Legacy');
            """
        )
        old.commit()
    finally:
        old.close()

    conn = schema.get_db(db_path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(term_df)")]
        assert cols == ["token", "symbol_df", "n_symbols"]
        # The pre-existing row survived the in-place upgrade.
        assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == 1
        # And the upgraded DB can build term_df immediately.
        rebuild_term_df(conn)
        assert _rows(conn)["old"] == (1, 1)
    finally:
        conn.close()


def test_embed_pass_refreshes_term_df(fresh_db, hash_backend):
    """D-005: a `cairn embed`-driven build (embed_all) leaves term_df current."""
    _seed(fresh_db)
    assert _rows(fresh_db) == {}, "no DF rows before any embed pass"

    summary = emb.embed_all(fresh_db)
    assert summary["embedded"] == 2, "hash backend must embed both seeded symbols"
    assert _rows(fresh_db) == EXPECTED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
