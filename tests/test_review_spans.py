"""Comment-to-symbol span mapping contracts (fixture index)."""

from __future__ import annotations

import sqlite3

import pytest

from cairn.graph.schema import get_build_db
from cairn.review.spans import resolve_comment_symbols


def _index() -> sqlite3.Connection:
    """In-memory fixture index: one file, nested spans, a span-less symbol."""
    conn = get_build_db()
    conn.execute(
        "INSERT INTO repos (id, name, path, language) "
        "VALUES ('demo', 'demo', 'demo', 'python')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language, line_count) "
        "VALUES ('f1', 'demo', 'src/pkg/mod.py', 'python', 30)"
    )
    conn.executemany(
        "INSERT INTO symbols "
        "(id, file_id, name, qualified_name, kind, line_start, line_end) "
        "VALUES (?, 'f1', ?, ?, ?, ?, ?)",
        [
            ("s1", "outer", "mod.outer", "class", 1, 20),
            ("s2", "inner", "mod.outer.inner", "method", 5, 8),
            ("s3", "standalone", "mod.standalone", "function", 22, 24),
            ("s4", "no_span", "mod.no_span", "variable", None, None),
        ],
    )
    return conn


def test_nested_spans_return_innermost_first():
    conn = _index()
    try:
        result = resolve_comment_symbols(conn, "src/pkg/mod.py", 6)
    finally:
        conn.close()

    assert not result.file_level
    assert [s.name for s in result.symbols] == ["inner", "outer"]
    assert result.symbols[0].qualified_name == "mod.outer.inner"
    assert result.symbols[0].kind == "method"
    assert result.symbols[0].line_start == 5
    assert result.symbols[0].line_end == 8


def test_line_in_outer_only_returns_single_span():
    conn = _index()
    try:
        result = resolve_comment_symbols(conn, "src/pkg/mod.py", 15)
    finally:
        conn.close()

    assert [s.name for s in result.symbols] == ["outer"]


def test_line_in_unnested_symbol_returns_it():
    conn = _index()
    try:
        result = resolve_comment_symbols(conn, "src/pkg/mod.py", 23)
    finally:
        conn.close()

    assert [s.name for s in result.symbols] == ["standalone"]


@pytest.mark.parametrize("line", [5, 8])
def test_span_bounds_are_inclusive(line):
    conn = _index()
    try:
        result = resolve_comment_symbols(conn, "src/pkg/mod.py", line)
    finally:
        conn.close()

    assert [s.name for s in result.symbols] == ["inner", "outer"]


@pytest.mark.parametrize("line", [21, 26, 999])
def test_line_outside_every_span_degrades_to_file_level(line):
    conn = _index()
    try:
        result = resolve_comment_symbols(conn, "src/pkg/mod.py", line)
    finally:
        conn.close()

    assert result.file_level
    assert result.symbols == ()
    assert result.file_path == "src/pkg/mod.py"


def test_file_level_comment_without_line_degrades_to_file_level():
    conn = _index()
    try:
        result = resolve_comment_symbols(conn, "src/pkg/mod.py", None)
    finally:
        conn.close()

    assert result.file_level
    assert result.symbols == ()


def test_unindexed_file_degrades_to_file_level():
    conn = _index()
    try:
        result = resolve_comment_symbols(conn, "src/pkg/other.py", 3)
    finally:
        conn.close()

    assert result.file_level
    assert result.symbols == ()
    assert result.file_path == "src/pkg/other.py"


@pytest.mark.parametrize("raw", ["./src/pkg/mod.py", "src\\pkg\\mod.py"])
def test_comment_path_is_normalized_before_matching(raw):
    conn = _index()
    try:
        result = resolve_comment_symbols(conn, raw, 23)
    finally:
        conn.close()

    assert [s.name for s in result.symbols] == ["standalone"]
    assert result.file_path == "src/pkg/mod.py"


@pytest.mark.parametrize(
    ("file_path", "line"),
    [
        ("src/pkg/mod.py", 0),
        ("src/pkg/mod.py", -3),
        ("src/pkg/mod.py", "6"),
        ("src/pkg/mod.py", True),
        ("", 1),
        ("   ", 1),
    ],
)
def test_invalid_locations_raise_value_error(file_path, line):
    conn = _index()
    try:
        with pytest.raises(ValueError):
            resolve_comment_symbols(conn, file_path, line)
    finally:
        conn.close()
