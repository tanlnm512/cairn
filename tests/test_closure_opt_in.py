"""Closure opt-in: default build/import-scip leave transitive_edges empty, --with-closure restores it byte-identically."""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.graph.builder import build_graph
from cairn.graph.dataflow import build_dataflow_index, build_transitive_closure
from cairn.graph.incremental import incremental_update
from cairn.graph.schema import get_db
from cairn.parsers.scip_importer import scip_available

SCIP_FIXTURES = Path(__file__).parent / "fixtures" / "scip-indexing"
needs_scip = pytest.mark.skipif(not scip_available(), reason="[scip] extra not installed")


@pytest.fixture(autouse=True)
def _scip_fixture_git_markers():
    """The committed scip fixture workspaces carry no .git dir in git (uncommittable); the scanner needs the marker."""
    for ws in (SCIP_FIXTURES / "covered", SCIP_FIXTURES / "covered-off"):
        (ws / ".git").mkdir(exist_ok=True)



@pytest.fixture(autouse=True)
def _hash_embedder(hash_backend):
    """incremental_update re-embeds changed symbols; pin the dep-free embedder."""


def _write(path: Path, text: str, tick: float) -> None:
    """Write a file and pin its mtime to ``tick`` (stat-fallback change detection)."""
    path.write_text(text, encoding="utf-8")
    os.utime(path, (tick, tick))


def _write_ws(tmp_path: Path) -> tuple[Path, float]:
    """One-repo workspace (``.git`` marker, no HEAD) where a_fn calls b_fn."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").mkdir()
    tick = time.time() + 86400
    _write(ws / "b.py", "def b_fn():\n    return 1\n", tick)
    _write(ws / "a.py", "from b import b_fn\n\n\ndef a_fn():\n    return b_fn()\n", tick + 2)
    return ws, tick + 4


def _build(ws: Path, db: Path, *extra: str):
    from cairn.cli import main

    return CliRunner().invoke(
        main, ["build", *extra, "--workspace", str(ws), "--db", str(db)],
        catch_exceptions=False,
    )


def _import_scip(scip_file: Path, ws: Path, db: Path, *extra: str):
    from cairn.cli import main

    return CliRunner().invoke(
        main,
        ["import-scip", *extra, str(scip_file), "--workspace", str(ws), "--db", str(db)],
        catch_exceptions=False,
    )


def _closure_count(db: Path) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM transitive_edges").fetchone()[0]
    finally:
        conn.close()


def _closure_labels(db: Path) -> set[tuple]:
    """Closure rows as stable label tuples (symbol ids differ across builds)."""
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            """
            SELECT s.name, f.path, t.target_name, ts.name, tf.path, t.distance
            FROM transitive_edges t
            JOIN symbols s ON s.id = t.source_id
            JOIN files f ON s.file_id = f.id
            LEFT JOIN symbols ts ON ts.id = t.target_id
            LEFT JOIN files tf ON tf.id = ts.file_id
            """
        ).fetchall()
        return {tuple(r) for r in rows}
    finally:
        conn.close()


def test_default_build_materializes_no_closure(tmp_path):
    """A default build leaves transitive_edges with zero rows."""
    ws, _ = _write_ws(tmp_path)
    db = tmp_path / "default.kg"

    result = _build(ws, db)

    assert result.exit_code == 0, result.output
    assert _closure_count(db) == 0


def test_with_closure_build_matches_direct_closure_build(tmp_path):
    """--with-closure produces the row set a direct build_transitive_closure run yields."""
    ws, _ = _write_ws(tmp_path)
    flag_db = tmp_path / "flag.kg"

    result = _build(ws, flag_db, "--with-closure")

    assert result.exit_code == 0, result.output
    assert _closure_count(flag_db) > 0

    direct_db = tmp_path / "direct.kg"
    build_graph(workspace=str(ws), db_path=str(direct_db))
    conn = get_db(direct_db)
    try:
        build_transitive_closure(conn)
    finally:
        conn.close()

    assert _closure_labels(flag_db) == _closure_labels(direct_db)


@needs_scip
def test_default_import_scip_materializes_no_closure(tmp_path):
    """A default import-scip leaves transitive_edges with zero rows."""
    ws = SCIP_FIXTURES / "covered-off"
    index = SCIP_FIXTURES / "covered" / "index.scip"
    db = tmp_path / "import.kg"

    result = _build(ws, db)
    assert result.exit_code == 0, result.output
    result = _import_scip(index, ws, db)
    assert result.exit_code == 0, result.output

    assert _closure_count(db) == 0


@needs_scip
def test_with_closure_import_rebuilds_closure(tmp_path):
    """--with-closure on import-scip materializes the closure."""
    ws = SCIP_FIXTURES / "covered-off"
    index = SCIP_FIXTURES / "covered" / "index.scip"
    db = tmp_path / "import-closure.kg"

    result = _build(ws, db)
    assert result.exit_code == 0, result.output
    result = _import_scip(index, ws, db, "--with-closure")
    assert result.exit_code == 0, result.output

    assert _closure_count(db) > 0


def _append_caller(ws: Path, tick: float) -> None:
    """Add c_fn -> a_fn reachability so an update has closure work to do."""
    text = (ws / "a.py").read_text(encoding="utf-8")
    _write(ws / "a.py", text + "\n\ndef c_fn():\n    return a_fn()\n", tick)


def test_update_maintains_present_closure(tmp_path):
    """After a --with-closure build, an update grows the closure with new reachability."""
    ws, tick = _write_ws(tmp_path)
    db = tmp_path / "maintain.kg"

    result = _build(ws, db, "--with-closure")
    assert result.exit_code == 0, result.output
    before = _closure_count(db)
    assert before > 0

    _append_caller(ws, tick)
    update = incremental_update(workspace=str(ws), db_path=str(db))
    assert update["errors"] == [], update["errors"]
    assert update["files_reindexed"] >= 1, update

    assert _closure_count(db) > before


def test_update_skips_absent_closure(tmp_path, monkeypatch):
    """After a default build, an update maintains dataflow incrementally and
    leaves transitive_edges empty -- no full-rebuild leg may fire."""
    from cairn.graph import dataflow as dataflow_mod

    ws, tick = _write_ws(tmp_path)
    db = tmp_path / "skip.kg"

    result = _build(ws, db)
    assert result.exit_code == 0, result.output
    assert _closure_count(db) == 0

    called = {"closure": 0, "dataflow": 0}

    def _spy_closure(conn, *a, **k):
        called["closure"] += 1
        return build_transitive_closure(conn, *a, **k)

    def _spy_dataflow(conn, *a, **k):
        called["dataflow"] += 1
        return build_dataflow_index(conn, *a, **k)

    monkeypatch.setattr(dataflow_mod, "build_transitive_closure", _spy_closure)
    monkeypatch.setattr(dataflow_mod, "build_dataflow_index", _spy_dataflow)

    _append_caller(ws, tick)
    update = incremental_update(workspace=str(ws), db_path=str(db))
    assert update["errors"] == [], update["errors"]
    assert update["files_reindexed"] >= 1, update
    assert called == {"closure": 0, "dataflow": 0}, (
        f"update fell back to the full rebuild: {called}"
    )

    assert _closure_count(db) == 0
