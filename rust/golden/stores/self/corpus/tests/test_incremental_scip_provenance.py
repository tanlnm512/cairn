"""SCIP overlay provenance across the update cycle: `cairn update` re-imports
the configured index (existing files only, never invoking an indexer), a
missing index degrades to tree-sitter with a scip_index_missing record, and
the next full build regenerates the index exactly once.

The update/ fixture carries a committed index covering tracked.py. A full
build gives tracked.py index-sourced exact edges; editing the file and
running incremental_update re-imports the index so the file's call/reference
rows go back to source='scip' exact (stub indexer on PATH never invoked);
with the index missing, update keeps tree-sitter edges and records the skip;
a full build with the index missing regenerates it via the stub and restores
source='scip'.

Skipped when the optional ``[scip]`` extra isn't installed.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from cairn.graph.builder import build_graph
from cairn.graph.incremental import incremental_update
from cairn.parsers.scip_importer import scip_available

# scip_importer must import without the protobuf runtime (the availability
# probe is its contract); only _scip_pb2 stays lazy inside scip_available.
pytestmark = pytest.mark.skipif(not scip_available(), reason="[scip] extra not installed")

FIXTURE = Path(__file__).parent / "fixtures" / "scip-indexing" / "update"

_TRACKED_SQL = (
    "from edges e join symbols s on e.source_id=s.id "
    "join files f on s.file_id=f.id where f.path like '%tracked.py'"
)


@pytest.fixture(autouse=True)
def _hash_embedder(hash_backend):
    """incremental_update re-embeds changed symbols; pin the dep-free hash
    embedder so runs are deterministic and need no model download."""


def _materialize(root: Path) -> Path:
    """Copy the fixture to tmp with a bare repo marker; git is unusable in
    the copy, so update's change detection takes the size/mtime fallback."""
    ws = root / "update"
    shutil.copytree(FIXTURE, ws, ignore=shutil.ignore_patterns(".git"))
    (ws / ".git").mkdir()
    return ws


def _count(db_path: str, where: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(f"select count(*) {_TRACKED_SQL} and {where}").fetchone()
        return row[0]
    finally:
        conn.close()


def _fetch(db_path: str, sql: str) -> list:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _edit_tracked(ws: Path) -> None:
    with open(ws / "tracked.py", "a", encoding="utf-8") as fh:
        fh.write("\ndef added_for_update_probe():\n    return 7\n")


def _stub_on_path(ws: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", f"{ws / 'bin'}{os.pathsep}{os.environ['PATH']}")


def test_update_reapplies_index_edges_when_index_present(tmp_path, monkeypatch):
    """Editing a covered file then updating with the index present re-imports
    it: scip exact edges survive, tree-sitter callrefs are replaced, and the
    stub indexer is never invoked."""
    ws = _materialize(tmp_path)
    db = str(tmp_path / "keep.db")
    build_graph(workspace=str(ws), db_path=db)

    assert _count(db, "e.source='scip' and e.resolution='exact'") > 0

    calls_log = ws / "bin" / "calls.log"
    calls_log.unlink(missing_ok=True)
    _edit_tracked(ws)
    _stub_on_path(ws, monkeypatch)

    result = incremental_update(workspace=str(ws), db_path=db)

    assert result["files_reindexed"] >= 1
    assert result["errors"] == []
    assert not calls_log.exists()
    assert _count(db, "e.source='scip' and e.resolution='exact'") > 0
    assert (
        _count(db, "e.kind in ('calls','references') and coalesce(e.source,'') != 'scip'")
        == 0
    )


def test_update_without_index_skips_overlay_and_records_scip_index_missing(
    tmp_path, monkeypatch
):
    """A missing index on update never triggers generation: tree-sitter edges
    remain and a scip_index_missing skip row names the configured path."""
    ws = _materialize(tmp_path)
    db = str(tmp_path / "missing.db")
    build_graph(workspace=str(ws), db_path=db)

    assert _count(db, "e.source='scip' and e.resolution='exact'") > 0

    (ws / "index.scip").unlink()
    calls_log = ws / "bin" / "calls.log"
    calls_log.unlink(missing_ok=True)
    _edit_tracked(ws)
    _stub_on_path(ws, monkeypatch)

    result = incremental_update(workspace=str(ws), db_path=db)

    assert result["files_reindexed"] >= 1
    assert result["errors"] == []
    assert not calls_log.exists()
    assert _count(db, "e.source='scip'") == 0
    assert _count(db, "e.source='tree_sitter' and e.kind in ('calls','references')") > 0
    rows = _fetch(
        db,
        "select repo_id, path from skipped_files where reason = 'scip_index_missing'",
    )
    assert rows and rows[0][0] and rows[0][1] == "index.scip"

    # A repeated update under the same missing index replaces the skip row
    # instead of accumulating one per update.
    _edit_tracked(ws)
    incremental_update(workspace=str(ws), db_path=db)
    rows = _fetch(
        db,
        "select repo_id, path from skipped_files where reason = 'scip_index_missing'",
    )
    assert len(rows) == 1


def test_update_without_scip_config_records_nothing(tmp_path, monkeypatch):
    """No scip config means the update's overlay hook is a no-op: no skip
    rows, no errors, and no indexer invocation even with the stub on PATH."""
    ws = _materialize(tmp_path)
    (ws / "cairn.json").unlink()
    db = str(tmp_path / "nocfg.db")
    build_graph(workspace=str(ws), db_path=db)

    _edit_tracked(ws)
    _stub_on_path(ws, monkeypatch)

    result = incremental_update(workspace=str(ws), db_path=db)

    assert result["files_reindexed"] >= 1
    assert result["errors"] == []
    assert not (ws / "bin" / "calls.log").exists()
    assert _count(db, "e.source='scip'") == 0
    rows = _fetch(db, "select count(*) from skipped_files where reason like 'scip%'")
    assert rows[0][0] == 0


def test_full_build_restores_index_edges_after_update(tmp_path, monkeypatch):
    """A full build after the update regenerates the missing index via the
    stub (exactly one invocation) and restores index-sourced exact edges."""
    ws = _materialize(tmp_path)
    db = str(tmp_path / "restore.db")
    build_graph(workspace=str(ws), db_path=db)

    (ws / "index.scip").unlink()
    _edit_tracked(ws)
    incremental_update(workspace=str(ws), db_path=db)

    _stub_on_path(ws, monkeypatch)
    build_graph(workspace=str(ws), db_path=db)

    calls_log = ws / "bin" / "calls.log"
    assert calls_log.exists()
    assert len(calls_log.read_text(encoding="utf-8").splitlines()) == 1
    assert _count(db, "e.source='scip' and e.resolution='exact'") > 0
