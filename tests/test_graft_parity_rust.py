"""Rust generic-tier indexing contracts."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from cairn.graph.builder import build_graph


SERVICE_SOURCE = """\
pub struct Engine {
    pub ready: bool,
}

impl Engine {
    pub fn start(&self) -> bool {
        self.ready
    }
}
"""

CALLER_SOURCE = """\
pub fn launch() -> bool {
    let engine = Engine { ready: true };
    engine.start()
}
"""

DECLARATIONS_SOURCE = """\
pub struct Engine;
pub enum Mode { On, Off }
pub trait Runnable { fn run(&self); }
impl Runnable for Engine { fn run(&self) {} }
pub mod runtime { pub fn start() {} }
pub type Id = u32;
pub const LIMIT: usize = 1;
pub static NAME: &str = "cairn";
macro_rules! squared { ($x:expr) => { $x * $x }; }
pub fn launch() -> bool { true }
"""


def _make_workspace(tmp_path: Path, name: str, sources: dict[str, str]) -> str:
    workspace = tmp_path / name
    repo = workspace / "demo"
    (repo / ".git").mkdir(parents=True)
    for filename, source in sources.items():
        (repo / filename).write_text(source, encoding="utf-8")
    return str(workspace)


def _open_graph(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def rust_db(tmp_path: Path):
    workspace = _make_workspace(
        tmp_path,
        "rust_indexed",
        {
            "service.rs": SERVICE_SOURCE,
            "caller.rs": CALLER_SOURCE,
            "declarations.rs": DECLARATIONS_SOURCE,
        },
    )
    db_path = tmp_path / "rust-indexed.db"
    build_graph(workspace=workspace, db_path=str(db_path), verbose=False)
    conn = _open_graph(db_path)
    try:
        yield conn
    finally:
        conn.close()


def test_rust_definitions_have_source_spans(rust_db: sqlite3.Connection) -> None:
    rows = rust_db.execute(
        """
        SELECT f.path, s.name, s.line_start, s.line_end,
               s.column_start, s.column_end
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE f.language = 'rust'
          AND f.path IN ('service.rs', 'caller.rs')
          AND s.name IN ('start', 'launch')
          AND s.kind != 'module'
        """
    ).fetchall()
    spans = {(row["path"], row["name"]): tuple(row)[2:] for row in rows}

    assert spans == {
        ("service.rs", "start"): (6, 8, 4, 5),
        ("caller.rs", "launch"): (1, 4, 0, 1),
    }


def test_rust_declaration_kinds_are_indexed(
    rust_db: sqlite3.Connection,
) -> None:
    rows = rust_db.execute(
        """
        SELECT s.name, s.kind
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE f.language = 'rust'
          AND f.path = 'declarations.rs'
          AND s.name != 'declarations'
        """
    ).fetchall()
    kinds = {row["name"]: row["kind"] for row in rows}

    assert kinds == {
        "Engine": "class",
        "Mode": "enum",
        "Runnable": "interface",
        "impl Engine": "implementation",
        "run": "method",
        "runtime": "module",
        "start": "function",
        "Id": "class",
        "LIMIT": "constant",
        "NAME": "constant",
        "squared": "macro",
        "launch": "function",
    }


def test_rust_calls_are_name_based_and_never_exact(
    rust_db: sqlite3.Connection,
) -> None:
    edge = rust_db.execute(
        """
        SELECT e.target_name, e.line, e.resolution
        FROM edges e
        JOIN symbols src ON src.id = e.source_id
        JOIN files f ON f.id = src.file_id
        WHERE f.language = 'rust'
          AND f.path = 'caller.rs'
          AND src.name = 'launch'
          AND e.kind = 'calls'
          AND e.target_name = 'start'
        """
    ).fetchone()

    assert edge is not None
    assert (edge["target_name"], edge["line"], edge["resolution"]) == (
        "start",
        3,
        "unresolved",
    )
    assert rust_db.execute(
        """
        SELECT COUNT(*)
        FROM edges e
        JOIN symbols src ON src.id = e.source_id
        JOIN files f ON f.id = src.file_id
        WHERE f.language = 'rust'
          AND e.kind = 'calls'
          AND e.resolution = 'exact'
        """
    ).fetchone()[0] == 0


def test_unavailable_rust_grammar_is_recorded_as_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "tree_sitter_rust", None)
    workspace = _make_workspace(
        tmp_path, "rust_unavailable", {"unavailable.rs": "fn setup() {}\n"}
    )
    db_path = tmp_path / "rust-unavailable.db"

    summary = build_graph(workspace=workspace, db_path=str(db_path), verbose=False)

    assert summary["files"] == 0
    assert summary["parse_errors"] == 0
    assert summary["skipped"] == 1
    conn = _open_graph(db_path)
    try:
        rows = conn.execute(
            "SELECT path, reason FROM skipped_files ORDER BY path"
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(row) for row in rows] == [
        ("unavailable.rs", "parser_unavailable")
    ]
