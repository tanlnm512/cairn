"""`cairn communities` sidecar: seeded Louvain partition persisted to the store's communities tables."""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from cairn import communities as mod
from cairn.communities import (
    DEFAULT_TOP_K,
    LOUVAIN_SEED,
    compute_communities,
    refresh_communities,
)

pytest.importorskip("networkx", reason="graph-analytics extra not installed")

# The engine-owned tables the sidecar reads and refreshes (cairn-store schema).
_SCHEMA = """
CREATE TABLE repos (id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL, language TEXT);
CREATE TABLE files (id TEXT PRIMARY KEY, repo_id TEXT NOT NULL, path TEXT NOT NULL, language TEXT NOT NULL);
CREATE TABLE symbols (id TEXT PRIMARY KEY, file_id TEXT NOT NULL, name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL);
CREATE TABLE edges (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT, target_name TEXT, kind TEXT NOT NULL);
CREATE TABLE communities (id INTEGER PRIMARY KEY, label TEXT NOT NULL, size INTEGER NOT NULL);
CREATE TABLE symbol_communities (community_id INTEGER NOT NULL, symbol_id TEXT NOT NULL, structural_degree INTEGER NOT NULL);
"""


def _store(tmp_path: Path, name: str = "store.kg") -> Path:
    db = tmp_path / name
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return db


def _seed(db: Path, files: list[tuple], symbols: list[tuple], edges: list[tuple]) -> None:
    conn = sqlite3.connect(db)
    conn.executemany("INSERT INTO files (id, repo_id, path, language) VALUES (?, 'r1', ?, 'python')", files)
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?, 'function')",
        symbols,
    )
    conn.executemany("INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, ?)", edges)
    conn.commit()
    conn.close()


def _islands(tmp_path: Path) -> Path:
    """Two disconnected call pairs in one file."""
    db = _store(tmp_path)
    _seed(
        db,
        [("f1", "islands/a.py")],
        [("s1", "f1", "a", "a"), ("s2", "f1", "b", "b"), ("s3", "f1", "c", "c"), ("s4", "f1", "d", "d")],
        [("e1", "s1", "s2", "calls"), ("e2", "s3", "s4", "calls")],
    )
    return db


def _hubs(tmp_path: Path) -> Path:
    """One community whose members span distinct degrees and file segments."""
    db = _store(tmp_path, "hubs.kg")
    _seed(
        db,
        [("f1", "alpha/one.py"), ("f2", "beta/two.py"), ("f3", "gamma/three.py"), ("f4", "delta/four.py")],
        [("s1", "f1", "entry", "entry"), ("s2", "f2", "left", "left"),
         ("s3", "f3", "right", "right"), ("s4", "f4", "sink", "sink")],
        [("e1", "s1", "s2", "calls"), ("e2", "s1", "s3", "calls"), ("e3", "s1", "s4", "calls")],
    )
    return db


def _snapshot(conn: sqlite3.Connection) -> list[tuple]:
    communities = conn.execute("SELECT id, label, size FROM communities ORDER BY id").fetchall()
    members = conn.execute(
        "SELECT community_id, symbol_id, structural_degree FROM symbol_communities "
        "ORDER BY community_id, symbol_id"
    ).fetchall()
    return [tuple(r) for r in communities + members]


def test_seeded_run_populates_both_tables(tmp_path):
    """Two disconnected call pairs partition into two 2-member communities,
    every member carrying its structural degree; ids start at 1."""
    conn = mod.open_store(str(_islands(tmp_path)))
    assert compute_communities(conn) == 2
    communities = conn.execute("SELECT id, label, size FROM communities ORDER BY id").fetchall()
    assert [r["id"] for r in communities] == [1, 2]
    assert [r["size"] for r in communities] == [2, 2]
    assert [r["label"] for r in communities] == ["islands", "islands"]
    members = conn.execute(
        "SELECT community_id, symbol_id, structural_degree FROM symbol_communities"
    ).fetchall()
    assert {r["community_id"] for r in members} == {1, 2}
    assert {r["symbol_id"] for r in members} == {"s1", "s2", "s3", "s4"}
    assert all(r["structural_degree"] == 1 for r in members)
    conn.close()


def test_double_run_is_byte_equal(tmp_path):
    """Re-running on an unchanged graph reproduces identical table rows."""
    assert LOUVAIN_SEED == 0
    conn = mod.open_store(str(_islands(tmp_path)))
    assert compute_communities(conn) == 2
    first = _snapshot(conn)
    assert compute_communities(conn) == 2
    assert _snapshot(conn) == first
    conn.close()


def test_zero_edge_store_persists_nothing(tmp_path):
    """Symbols without structural edges get no membership; a full refresh
    still clears stale rows and reports zero communities."""
    db = _store(tmp_path)
    _seed(db, [("f1", "a.py")], [("s1", "f1", "solo", "solo")], [])
    conn = mod.open_store(str(db))
    conn.execute("INSERT INTO communities (id, label, size) VALUES (1, 'stale', 1)")
    conn.execute("INSERT INTO symbol_communities VALUES (1, 's1', 0)")
    conn.commit()

    assert compute_communities(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM communities").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM symbol_communities").fetchone()[0] == 0
    conn.close()


def test_label_falls_back_to_top_hub_without_dominant_segment(tmp_path):
    """Paths spread across segments (no share above the threshold) label the
    community with its highest-degree hub, prefixed to stay distinguishable."""
    conn = mod.open_store(str(_hubs(tmp_path)))
    assert compute_communities(conn) == 1
    assert conn.execute("SELECT label FROM communities").fetchone()[0] == "hub:entry"
    conn.close()


def test_missing_networkx_hints_without_opening_store(monkeypatch, tmp_path):
    """A failed probe raises the install hint before any store open."""
    opened: list[str] = []
    monkeypatch.setattr(mod, "open_store", lambda *a, **k: opened.append(a) or None)
    monkeypatch.setitem(
        sys.modules, "networkx.algorithms.community", types.ModuleType("networkx.algorithms.community")
    )

    with pytest.raises(ImportError, match=r"cairn-intel\[graph-analytics\]"):
        refresh_communities(str(tmp_path / "probe.kg"))
    assert opened == []


def test_cli_prints_count_sizes_and_hubs(tmp_path, capsys):
    """Output is the count, the global hub block, then one hub block per
    community in persisted order; hubs rank by (-degree, path, name) under --top-k."""
    assert DEFAULT_TOP_K == 10
    db = _hubs(tmp_path)

    assert mod.main(["--db", str(db), "--top-k", "2"]) == 0
    assert capsys.readouterr().out == (
        "1 communities\n"
        "Global hubs:\n"
        "  entry (degree 3)\n"
        "  left (degree 1)\n"
        "Communities:\n"
        "  1: hub:entry (4 symbols)\n"
        "    entry (degree 3)\n"
        "    left (degree 1)\n"
    )


def test_cli_double_run_output_is_byte_identical(tmp_path, capsys):
    db = _islands(tmp_path)
    assert mod.main(["--db", str(db)]) == 0
    first = capsys.readouterr().out
    assert mod.main(["--db", str(db)]) == 0
    assert first and capsys.readouterr().out == first


def test_cli_rejects_non_positive_top_k(tmp_path):
    with pytest.raises(SystemExit) as exc:
        mod.main(["--db", str(_islands(tmp_path)), "--top-k", "0"])
    assert exc.value.code == 2


def test_cli_without_extra_hints_and_leaves_store_unchanged(monkeypatch, tmp_path, capsys):
    """A failed probe exits non-zero with the install hint and the store file
    is byte-unchanged (the probe runs before any writable open)."""
    db = _islands(tmp_path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    monkeypatch.setitem(
        sys.modules, "networkx.algorithms.community", types.ModuleType("networkx.algorithms.community")
    )

    assert mod.main(["--db", str(db)]) == 1
    assert "cairn-intel[graph-analytics]" in capsys.readouterr().err
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_cli_zero_edge_store_reports_none(tmp_path, capsys):
    db = _store(tmp_path)
    assert mod.main(["--db", str(db)]) == 0
    assert "no communities found" in capsys.readouterr().out


def test_cli_missing_store_is_an_error_not_a_new_file(tmp_path, capsys):
    """The engine owns store creation: a missing --db is reported, never created."""
    db = tmp_path / "absent.kg"
    assert mod.main(["--db", str(db)]) == 1
    assert str(db) in capsys.readouterr().err
    assert not db.exists()


def test_default_db_comes_from_the_engine(monkeypatch, tmp_path):
    """Without --db the store path is whatever `cairn config --db` resolves."""
    fake = tmp_path / "cairn"
    fake.write_text("#!/bin/sh\necho /resolved/store.kg\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(mod, "rust_bin", lambda: str(fake))
    assert mod.default_db_path() == "/resolved/store.kg"

    monkeypatch.setattr(mod, "rust_bin", lambda: None)
    with pytest.raises(SystemExit, match="pass --db"):
        mod.default_db_path()
