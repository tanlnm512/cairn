"""T2 vendored snapshot: build+query smoke test (spec FR-002, TC-015).

``benchmarks/datasource/t2/yarl/`` is a source export of yarl @ the commit
pinned in ``t2/provenance.json`` (D-002: Apache-2.0, best call depth and
docstrings per byte among the surveyed candidates). This test proves the
datasource is exercisable: cairn builds a graph over the vendored tree and
answers a known-symbol query against it.

Hermetic by construction: the tree is copied to tmp -- the *copy*, not the
committed tree, gets the ``.git`` marker the scanner requires (a GitHub
source export carries no VCS dir; ``bench/corpus.py`` uses the same marker
trick for T1), and the graph lands in a throwaway DB inside the test
sandbox, with the suite-wide ``_hermetic_env`` fixture pinning HOME and
CAIRN_* env there. Build is ~2-5 s over the 24 Python files this tree
yields; the repo's pytest config has no slow-test marker, and this test
stays well under 10 s.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from cairn.graph.builder import build_graph
from cairn.graph.traversal import find_definition, get_callers

T2_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "datasource" / "t2"
SNAPSHOT = T2_DIR / "yarl"


def test_t2_snapshot_builds_and_answers_queries(tmp_path):
    """The vendored yarl snapshot must build green and answer known queries.

    Assertions (task T005 / FR-002): build succeeds, symbol count > 500,
    ``find_definition("URL")`` returns yarl's URL class, and
    ``get_callers("URL")`` is non-empty (the vendored test suite calls
    ``URL(...)`` hundreds of times).
    """
    # Copy the vendored tree into tmp and add the .git repo marker the
    # scanner needs. The committed tree stays marker-free; only this
    # test-private copy is recognized as a repo.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copytree(SNAPSHOT, workspace / "yarl")
    (workspace / "yarl" / ".git").mkdir()

    db_path = str(tmp_path / "graph.db")
    summary = build_graph(workspace=str(workspace), db_path=db_path, verbose=False)

    # Build succeeded and the scanner recognized exactly the one marked
    # repo; the corpus is substantial (measured at this pin: ~1066 symbols
    # from 24 parsed Python files -- package plus its test suite).
    assert summary["repos"] == 1, summary
    assert summary["symbols"] > 500, summary

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Known-symbol query: yarl's URL class is defined in yarl/_url.py.
        defs = find_definition(conn, "URL")
        assert any(
            row["kind"] == "class" and row["file_path"].endswith("yarl/_url.py")
            for row in defs
        ), [dict(row) for row in defs]

        # Call-site query: precise callers must be non-empty -- the
        # vendored tests construct/compare URL objects extensively (at this
        # pin the precise result hits its default 200-row limit).
        callers = get_callers(conn, "URL")
        assert len(callers) > 0, "expected URL callers from the vendored test suite"
    finally:
        conn.close()
