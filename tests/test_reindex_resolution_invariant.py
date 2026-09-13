"""Reindexing a file whose target symbols disappear keeps the resolver
invariant: no edge ends up resolution='exact' with target_id=NULL, and the
caller's edges survive as unresolved with their target names backfilled.
"""

from cairn.graph.incremental import reindex_paths


def test_reindex_deleted_target_stays_unresolved(caller_callee_db, caller_callee_ws, tmp_path, hash_backend):
    """Reindexing a b.kt whose target symbol is gone must null the caller's
    edge and mark it unresolved -- never exact with a dangling NULL."""
    conn = caller_callee_db(str(tmp_path / "inv.db"))
    try:
        (caller_callee_ws / "demo" / "b.kt").write_text(
            "class Unrelated {\n  fun other() {}\n}\n"
        )
        result = reindex_paths(
            conn, str(caller_callee_ws), [str(caller_callee_ws / "demo" / "b.kt")]
        )
        assert result["errors"] == []

        dangling = conn.execute(
            "SELECT COUNT(*) AS c FROM edges "
            "WHERE resolution = 'exact' AND target_id IS NULL"
        ).fetchone()["c"]
        assert dangling == 0

        rows = conn.execute(
            "SELECT target_id, target_name, resolution FROM edges "
            "WHERE target_name IN ('target', 'Callee')"
        ).fetchall()
        assert rows, "the caller's edges to the removed symbols must survive"
        for r in rows:
            assert r["target_id"] is None
            assert r["resolution"] == "unresolved"
    finally:
        conn.close()
