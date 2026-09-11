"""Persistence of parser-side enrichment signals (imports.local_alias, symbols.arity).

Both columns are additive and nullable: a parser that cannot produce a signal
writes NULL (abstain-safe), never a guess. This pins the write path end to
end — what a ParsedFile carries must land in the DB row through both insert
paths (full build's insert_parsed_file, and incremental reindex_paths which
delegates to it), and a legacy DB must gain the columns in place at connect
time (additive ALTER; legacy DBs age out, no data migration).
"""
from __future__ import annotations

import sqlite3

import pytest

from cairn.graph.builder import get_parser, insert_parsed_file
from cairn.graph.incremental import reindex_paths
from cairn.graph.schema import _apply_schema, get_db
from cairn.parsers.base import Edge, Import, ParsedFile, Symbol


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    yield conn
    conn.close()


def _parsed_file() -> ParsedFile:
    pf = ParsedFile(path="/ws/repo/a.py", language="python", hash="h1", line_count=8)
    pf.symbols.append(
        Symbol(
            name="dumps_two",
            kind="function",
            line_start=3,
            line_end=5,
            qualified_name="dumps_two",
            arity=2,
        )
    )
    pf.symbols.append(
        Symbol(name="plain", kind="function", line_start=7, line_end=8, qualified_name="plain")
    )
    pf.imports.append(Import(imported_path="json", line=1, local_alias="j"))
    pf.imports.append(Import(imported_path="os", line=2))
    pf.edges.append(
        Edge(source_name="dumps_two", kind="calls", target_name="j.dumps", line=4, column=11)
    )
    return pf


def test_insert_parsed_file_persists_signals(db):
    """What ParsedFile carries must land in the row; absent signals stay NULL."""
    n_sym, n_edge, n_imp = insert_parsed_file(
        db, "repo", "a.py", "/ws/repo/a.py", "python", "h1", _parsed_file(), {}, {}
    )
    assert (n_sym, n_imp) == (3, 2)  # 2 declared symbols + module row; 2 imports

    imports = {
        r["imported_path"]: r["local_alias"]
        for r in db.execute("SELECT imported_path, local_alias FROM imports")
    }
    assert imports == {"json": "j", "os": None}

    arity = {
        r["name"]: r["arity"] for r in db.execute("SELECT name, arity FROM symbols")
    }
    assert arity["dumps_two"] == 2
    assert arity["plain"] is None
    assert arity["a"] is None  # synthesized module row never carries arity


def test_reindex_paths_persists_signals(tmp_path):
    """The incremental write path (reindex_paths -> insert_parsed_file) persists
    exactly what the language parser produced for the re-parsed file."""
    ws = tmp_path / "ws"
    repo = ws / "r"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    src = repo / "a.py"
    src.write_text("import json as j\n\n\ndef use(x):\n    return j.dumps(x)\n")

    conn = get_db(str(tmp_path / "inc.db"))
    # reindex_paths' files insert targets this repo; the row must pre-exist.
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('r', 'r', ?)", (str(repo),)
    )
    conn.commit()
    try:
        result = reindex_paths(conn, str(ws), [str(src)])
        assert result == {"reindexed": 1, "deleted": 0, "errors": []}

        pf = get_parser("python").parse(str(src))
        expected_imports = {i.imported_path: i.local_alias for i in pf.imports}
        assert expected_imports, "python parser must emit at least one import for the probe file"
        got_imports = {
            r["imported_path"]: r["local_alias"]
            for r in conn.execute("SELECT imported_path, local_alias FROM imports")
        }
        assert got_imports == expected_imports

        for sym in pf.symbols:
            row = conn.execute(
                "SELECT arity FROM symbols WHERE qualified_name = ?", (sym.qualified_name,)
            ).fetchone()
            assert row is not None, f"symbol {sym.qualified_name} missing after reindex"
            assert row["arity"] == sym.arity
    finally:
        conn.close()


# Pre-change table shapes (matching SCHEMA_SQL's CREATE TABLE, which stays
# minimal — every later column rides an additive ALTER migration).
LEGACY_SYMBOLS_DDL = """
CREATE TABLE symbols (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES files(id),
    name TEXT NOT NULL,
    qualified_name TEXT,
    kind TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    column_start INTEGER,
    column_end INTEGER,
    docstring TEXT,
    modifiers TEXT
)
"""
LEGACY_IMPORTS_DDL = """
CREATE TABLE imports (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES files(id),
    imported_path TEXT NOT NULL,
    resolved_symbol_id TEXT REFERENCES symbols(id),
    line INTEGER
)
"""


def test_legacy_db_gains_signal_columns_in_place(tmp_path):
    """A DB written before these columns existed upgrades in place at connect
    time via the additive-ALTER migrations (no rebuild migration)."""
    db_path = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(db_path)
    raw.execute(LEGACY_SYMBOLS_DDL)
    raw.execute(LEGACY_IMPORTS_DDL)
    raw.commit()
    raw.close()

    conn = get_db(db_path)
    try:
        sym_cols = {r[1] for r in conn.execute("PRAGMA table_info(symbols)")}
        imp_cols = {r[1] for r in conn.execute("PRAGMA table_info(imports)")}
        assert "arity" in sym_cols
        assert "local_alias" in imp_cols
    finally:
        conn.close()
