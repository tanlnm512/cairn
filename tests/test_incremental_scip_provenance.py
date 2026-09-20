"""FR-010: cairn update flips a changed covered file to tree-sitter edge
provenance without invoking any indexer; the next full build restores the
index-sourced edges.

The update/ fixture carries a committed index covering tracked.py. A full
build gives tracked.py index-sourced exact edges; editing the file and
running incremental_update deletes + re-parses its rows through tree-sitter
(edges.source='tree_sitter', stub indexer on PATH never invoked); a full
build with the index missing regenerates it via the stub and restores
source='scip' exact edges.

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


def _build_with_stub_on_path(ws: Path, db_path: str, monkeypatch) -> None:
    monkeypatch.setenv("PATH", f"{ws / 'bin'}{os.pathsep}{os.environ['PATH']}")
    build_graph(workspace=str(ws), db_path=db_path)


def _edit_tracked(ws: Path) -> None:
    with open(ws / "tracked.py", "a", encoding="utf-8") as fh:
        fh.write("\ndef added_for_update_probe():\n    return 7\n")


def test_update_flips_covered_file_to_tree_sitter(tmp_path, monkeypatch):
    """Editing a covered file then updating leaves only tree-sitter edges for
    it and never invokes the stub indexer."""
    ws = _materialize(tmp_path)
    db = str(tmp_path / "flip.db")
    build_graph(workspace=str(ws), db_path=db)

    assert _count(db, "e.source='scip' and e.resolution='exact'") > 0

    (ws / "index.scip").unlink()
    calls_log = ws / "bin" / "calls.log"
    calls_log.unlink(missing_ok=True)
    _edit_tracked(ws)
    monkeypatch.setenv("PATH", f"{ws / 'bin'}{os.pathsep}{os.environ['PATH']}")

    result = incremental_update(workspace=str(ws), db_path=db)

    assert result["files_reindexed"] >= 1
    assert not calls_log.exists()
    assert _count(db, "e.source='scip'") == 0
    assert _count(db, "e.source='tree_sitter' and e.kind in ('calls','references')") > 0


def test_full_build_restores_index_edges_after_update(tmp_path, monkeypatch):
    """A full build after the update regenerates the missing index via the
    stub (exactly one invocation) and restores index-sourced exact edges."""
    ws = _materialize(tmp_path)
    db = str(tmp_path / "restore.db")
    build_graph(workspace=str(ws), db_path=db)

    (ws / "index.scip").unlink()
    _edit_tracked(ws)
    incremental_update(workspace=str(ws), db_path=db)

    _build_with_stub_on_path(ws, db, monkeypatch)

    calls_log = ws / "bin" / "calls.log"
    assert calls_log.exists()
    assert len(calls_log.read_text(encoding="utf-8").splitlines()) == 1
    assert _count(db, "e.source='scip' and e.resolution='exact'") > 0
