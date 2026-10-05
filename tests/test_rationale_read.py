"""Rationale read helpers: records for symbol ids and for a file, ordered by line."""

from __future__ import annotations

import sqlite3

import pytest

from cairn.graph.builder import insert_parsed_file
from cairn.graph.rationale import records_for_file, records_for_symbol_ids
from cairn.graph.schema import _apply_schema
from cairn.parsers.base import ParsedFile, RationaleRecord, Symbol


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    yield conn
    conn.close()


def _pf(path: str, records: list[tuple[int, str, str]]) -> ParsedFile:
    pf = ParsedFile(path=path, language="python", hash="h-" + path, line_count=20)
    pf.symbols.append(
        Symbol(name="fn", kind="function", line_start=5, line_end=10, qualified_name="fn")
    )
    pf.symbols.append(
        Symbol(name="other", kind="method", line_start=12, line_end=20, qualified_name="other")
    )
    pf.rationale.extend(RationaleRecord(*r) for r in records)
    return pf


def _insert(db, rel_path, records):
    insert_parsed_file(
        db, "repo", rel_path, f"/ws/{rel_path}", "python", "h-" + rel_path,
        _pf(rel_path, records), {}, {},
    )


def test_records_for_file_ordered_by_line(db):
    _insert(db, "a.py", [(9, "hack", "later"), (2, "note", "early"), (6, "why", "inside")])
    file_id = db.execute("SELECT id FROM files").fetchone()["id"]
    rows = records_for_file(db, file_id)
    assert [(r["line"], r["kind"], r["text"]) for r in rows] == [
        (2, "note", "early"),
        (6, "why", "inside"),
        (9, "hack", "later"),
    ]


def test_records_for_symbol_ids_filter_and_order(db):
    _insert(db, "a.py", [(6, "why", "in fn"), (2, "note", "file level")])
    _insert(db, "b.py", [(15, "hack", "in other")])
    file_ids = {
        r["path"]: r["id"] for r in db.execute("SELECT id, path FROM files")
    }
    fn_id = db.execute(
        "SELECT id FROM symbols WHERE file_id = ? AND name = 'fn'",
        (file_ids["a.py"],),
    ).fetchone()["id"]
    other_id = db.execute(
        "SELECT id FROM symbols WHERE file_id = ? AND name = 'other'",
        (file_ids["b.py"],),
    ).fetchone()["id"]
    rows = records_for_symbol_ids(db, [fn_id, other_id, "missing-id"])
    assert [(r["line"], r["kind"], r["text"], r["symbol_id"]) for r in rows] == [
        (6, "why", "in fn", fn_id),
        (15, "hack", "in other", other_id),
    ]
    # File-level rows (NULL symbol_id) never ride the symbol query.
    assert all(r["text"] != "file level" for r in rows)


def test_records_for_symbol_ids_empty_input(db):
    assert records_for_symbol_ids(db, []) == []
