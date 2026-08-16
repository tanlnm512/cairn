"""T2 datasource build+query smoke: the TC-mapped contract layer (FR-002, AC3).

T008 ships this as a thin TC-mapped layer beside T005's snapshot sanity test
(``tests/test_t2_snapshot.py``: build counts, URL findable, URL callers
non-empty). No assertion overlap by design: where T005 checks presence, this
file pins *identity*, against the upstream commit pinned in
``benchmarks/datasource/t2/provenance.json`` (dddcb82):

* TC-013 -- the known-symbol query returns THAT symbol: exactly one
  class-kind ``URL`` in ``yarl/_url.py`` (class defined at _url.py:356).
* TC-014 -- the snapshot carries source in two languages (Python modules
  plus the Cython ``yarl/_quoting_c.pyx``), and a precise
  ``get_callers("split_url")`` returns ``encode_url``: caller defined in
  ``yarl/_url.py`` (call site ``_url.py:230``), callee in
  ``yarl/_parse.py:24`` -- a cross-file caller pair pinned by construction,
  not just a non-empty count.

Hermetic by construction: the tree is copied to tmp and the *copy*, not the
committed tree, gets the ``.git`` scanner marker exactly as
``generate_corpus`` does for T1 (``bench/corpus.py:50-52``: ``(repo /
".git").mkdir(exist_ok=True)``) -- git does not track empty dirs, so a
committed marker dir would not survive the clone anyway (tech-spec pitfall).
The graph lands in a throwaway DB inside the test sandbox, and the build
fixture is function-scoped on purpose: the suite-wide autouse
``_hermetic_env`` fixture pins HOME/CAIRN_* per test, and a module-scoped
build would run before that patch applies. A build measures ~0.3 s, so two
tests each building once stay well under a second.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from cairn.graph.builder import build_graph
from cairn.graph.traversal import find_definition, get_callers

T2_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "datasource" / "t2"
SNAPSHOT = T2_DIR / "yarl"


@pytest.fixture()
def t2_graph_db(tmp_path):
    """Build a graph over a test-private copy of the vendored snapshot."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copytree(SNAPSHOT, workspace / "yarl")
    # Scanner marker, same idiom as generate_corpus (bench/corpus.py:50-52).
    (workspace / "yarl" / ".git").mkdir(exist_ok=True)
    db_path = str(tmp_path / "graph.db")
    summary = build_graph(workspace=str(workspace), db_path=db_path, verbose=False)
    assert summary["repos"] == 1, summary  # marker recognized, nothing else
    assert summary["parse_errors"] == 0, summary
    return db_path


def test_t2_tc013_known_symbol_query_returns_identity(t2_graph_db):
    """TC-013: a query for a symbol known to exist returns that symbol."""
    conn = sqlite3.connect(t2_graph_db)
    conn.row_factory = sqlite3.Row
    try:
        defs = find_definition(conn, "URL")
        exact = [
            row for row in defs if row["name"] == "URL" and row["kind"] == "class"
        ]
        # Identity, not just presence: exactly one URL class, in _url.py.
        assert len(exact) == 1, [dict(row) for row in defs]
        url = exact[0]
        assert url["name"] == "URL"
        assert url["kind"] == "class"
        assert url["file_path"] == "yarl/_url.py"
    finally:
        conn.close()


def test_t2_tc014_cross_file_callers_query(t2_graph_db):
    """TC-014: a precise callers query returns a caller from another file."""
    # TC-014's tree clause: genuinely multi-language source is vendored --
    # Python modules plus the Cython source _quoting_c.pyx.
    exts = {p.suffix for p in SNAPSHOT.rglob("*") if p.is_file()}
    assert {".py", ".pyx"} <= exts, sorted(exts)

    conn = sqlite3.connect(t2_graph_db)
    conn.row_factory = sqlite3.Row
    try:
        # Callee identity: split_url is _parse.py's module-level splitter.
        defs = [row for row in find_definition(conn, "split_url")]
        assert len(defs) == 1, [dict(row) for row in defs]
        callee = defs[0]
        assert callee["name"] == "split_url"
        assert callee["kind"] == "function"
        assert callee["file_path"] == "yarl/_parse.py"

        # Pinned pair (upstream dddcb82): encode_url (yarl/_url.py:226)
        # calls split_url(url_str) at _url.py:230 via the explicit import
        # ``from ._parse import (... split_url ...)`` (_url.py:25-33), so
        # the precise resolver pins this edge.
        callers = get_callers(conn, "split_url")  # precise: resolved targets only
        pinned = [
            row
            for row in callers
            if row["caller_name"] == "encode_url" and row["file_path"] == "yarl/_url.py"
        ]
        assert pinned, [(r["caller_name"], r["file_path"]) for r in callers]
        edge = pinned[0]
        assert edge["caller_kind"] == "function"
        assert edge["resolution"] == "exact"
        # The TC-014 observable: the caller lives in a different file.
        assert edge["file_path"] != callee["file_path"]
    finally:
        conn.close()
