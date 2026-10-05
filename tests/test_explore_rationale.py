"""Tests for explore's gated Rationale section.

Covers: the section renders one line per rationale record attributed to the
matched seeds' symbols, is omitted entirely when none exist, and the Tribal
memory section keeps its place ahead of it.
"""
from __future__ import annotations

import sqlite3

import pytest

from cairn.graph.schema import _apply_schema
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Schema'd file DB with one indexed symbol + knowledge dir, wired
    through CAIRN_DB/CAIRN_KNOWLEDGE so no test touches the real ~/.cairn."""
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    db_path = tmp_path / "graph.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '.')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'r1', 'src/loader.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (1, 1, 'numpy_loader', 'function', 'loader.numpy_loader', 1, 10)"
    )
    conn.commit()
    conn.close()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setenv("CAIRN_DB", str(db_path))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(knowledge))
    return db_path


def _add_rationale(db_path, rows):
    """Insert attributed rationale rows for the fixture symbol (id 1, file 1)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO rationale (id, file_id, symbol_id, line, kind, text) "
            "VALUES (?, 1, 1, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _write_tribal(bundle: OKFBundle, title: str, slug: str) -> None:
    bundle.write_concept(
        OKFConcept(
            type="Tribal-mistake",
            title=title,
            description=title,
            body=f"Why: prose.\nHow to apply: {title.lower()}.",
            concept_id=f"memory/tribal/{slug}",
            extensions={"memory_tier": "tribal", "memory_type": "mistake"},
        )
    )


def _section_lines(out: str, header: str) -> list[str]:
    """Body lines of the `=== <header>` section (header excluded, next
    `=== ` header terminates)."""
    lines = out.splitlines()
    start = next(
        i for i, ln in enumerate(lines) if ln.startswith(f"=== {header}")
    )
    end = start + 1
    while end < len(lines) and not lines[end].startswith("=== "):
        end += 1
    return lines[start + 1 : end]


# ---------------------------------------------------------------------------
# records render for the matched symbol, ordered by line
# ---------------------------------------------------------------------------


def test_explore_renders_rationale_for_matched_symbol(env):
    from cairn.mcp_server import tools_graph

    _add_rationale(
        env,
        [
            ("r-7", 7, "why", "eviction breaks C extensions"),
            ("r-3", 3, "note", "loader holds the only numpy import"),
        ],
    )
    out = tools_graph.explore("numpy_loader")
    assert "=== Rationale (2) ===" in out
    assert _section_lines(out, "Rationale") == [
        "  loader.py:3 [note] loader holds the only numpy import",
        "  loader.py:7 [why] eviction breaks C extensions",
    ]


# ---------------------------------------------------------------------------
# no records -> no section at all
# ---------------------------------------------------------------------------


def test_explore_omits_rationale_section_when_no_records(env):
    from cairn.mcp_server import tools_graph

    out = tools_graph.explore("numpy_loader")
    assert "=== Rationale" not in out


# ---------------------------------------------------------------------------
# tribal memory ordering preserved: its section stays ahead of Rationale
# ---------------------------------------------------------------------------


def test_explore_keeps_tribal_section_before_rationale(env):
    from cairn.mcp_server import tools_graph

    db_path = env
    _add_rationale(env, [("r-3", 3, "note", "loader holds the only numpy import")])

    knowledge = db_path.parent / "knowledge"
    _write_tribal(OKFBundle(str(knowledge)), "Never evict numpy mid-run", "never-evict-numpy")

    out = tools_graph.explore("numpy_loader")
    lines = out.splitlines()
    tribal_idx = next(
        i for i, ln in enumerate(lines) if ln.startswith("=== Tribal memory")
    )
    rationale_idx = next(
        i for i, ln in enumerate(lines) if ln.startswith("=== Rationale")
    )
    assert tribal_idx < rationale_idx
    assert _section_lines(out, "Tribal memory") == [
        "  Never evict numpy mid-run",
        "    How to apply: never evict numpy mid-run.",
    ]
