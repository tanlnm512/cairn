"""Incremental reindex keeps a file's rationale rows matching its current comments."""

from __future__ import annotations

from cairn.graph.builder import build_graph
from cairn.graph.incremental import reindex_paths
from cairn.graph.schema import get_db


def test_reindex_drops_stale_rationale_and_rederives(tmp_path, hash_backend):
    """A reindexed file's rationale rows are exactly what its current comments
    imply: the pre-edit records are gone, the current ones present."""
    ws = tmp_path / "ws"
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)
    src = repo / "a.py"
    src.write_text("# NOTE: stale note\n\n\ndef f():\n    return 1\n")

    db_path = str(tmp_path / "inc.db")
    build_graph(workspace=str(ws), db_path=db_path, verbose=False)

    src.write_text("def f():\n    # WHY: fresh note\n    return 1\n")
    conn = get_db(db_path)
    try:
        result = reindex_paths(conn, str(ws), [str(src)])
        assert result["errors"] == []
        rows = conn.execute(
            "SELECT line, kind, text FROM rationale ORDER BY line"
        ).fetchall()
        assert [(r["line"], r["kind"], r["text"]) for r in rows] == [
            (2, "why", "fresh note")
        ]
    finally:
        conn.close()
