"""Tests for the workflow-audit fixes (incremental derived indexes, incoming-edge
repair, single-repo build lock, transactional reindex, error surfacing).

These lock in the behaviors added in fix/workflow-audit-findings so a future
change can't silently regress them.
"""
from __future__ import annotations

import pytest

from cairn.graph.builder import build_graph
from cairn.graph.incremental import incremental_update, reindex_paths
from cairn.graph.schema import build_lock, get_db


# ---------------------------------------------------------------------------
# Fixtures: a tiny workspace with two files so caller/callee span files.
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path):
    """A single-repo workspace with a.kt calling b.kt's symbol."""
    ws = tmp_path / "ws"
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)
    (repo / "a.kt").write_text(
        "class Caller {\n"
        "  fun go() {\n"
        "    val r = Callee()\n"
        "    r.target()\n"
        "  }\n"
        "}\n"
    )
    (repo / "b.kt").write_text(
        "class Callee {\n"
        "  fun target() {}\n"
        "}\n"
    )
    return ws


def _build(workspace, db_path):
    """Build the graph and return an open connection."""
    build_graph(workspace=str(workspace), db_path=str(db_path))
    return get_db(str(db_path))


# ---------------------------------------------------------------------------
# #2 — incremental re-resolves INCOMING edges after a file is re-indexed.
# ---------------------------------------------------------------------------

def test_incremental_repairs_incoming_edges(workspace, tmp_path):
    """Re-indexing b.kt must re-resolve a.kt's edge that points at Callee.target.

    Before the fix: reindex deletes b.kt's symbols (nulling a.kt's edge to
    'unresolved') and re-creates them with new ids, but never re-resolves the
    incoming edge -- so precise callers of `target` dropped until a full rebuild.
    """
    db = str(tmp_path / "inc.db")
    conn = _build(workspace, db)
    try:
        # Initially there is an exact edge from a.kt -> Callee.target.
        before = conn.execute(
            "SELECT COUNT(*) AS c FROM edges WHERE target_name IS NULL "
            "AND target_id IN (SELECT id FROM symbols WHERE name = 'target')"
        ).fetchone()
        assert before["c"] >= 1, "expected a resolved edge to 'target' after build"

        # Touch b.kt (the callee's file) and reindex.
        (workspace / "demo" / "b.kt").write_text(
            "class Callee {\n"
            "  fun target() {}\n"
            "  fun extra() {}\n"  # harmless change to alter the file
            "}\n"
        )
        reindex_paths(conn, str(workspace), [str(workspace / "demo" / "b.kt")])

        # The incoming edge from a.kt should be re-resolved to the NEW symbol id.
        # Before the fix this stayed resolution='unresolved' with target_id NULL.
        # At least one edge into the new 'target' symbol is exact again.
        exact_into_target = conn.execute(
            "SELECT COUNT(*) AS c FROM edges WHERE resolution = 'exact' "
            "AND target_id IN (SELECT id FROM symbols WHERE name = 'target')"
        ).fetchone()
        assert exact_into_target["c"] >= 1, (
            "incoming edge to 'target' should be re-resolved to 'exact' after "
            "incremental repair; got no exact edges"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# #1 — incremental_update rebuilds the derived indexes (transitive_edges).
# ---------------------------------------------------------------------------

def test_incremental_rebuilds_derived_indexes(workspace, tmp_path):
    """incremental_update must refresh transitive_edges after a change.

    Before the fix: only `cairn build` rebuilt derived indexes, so after
    `cairn update` the transitive closure was stale.
    """
    db = str(tmp_path / "derived.db")
    conn = _build(workspace, db)
    try:
        # Seed the transitive table so a stale state is detectable.
        from cairn.graph.dataflow import build_transitive_closure
        build_transitive_closure(conn)
        before = conn.execute("SELECT COUNT(*) AS c FROM transitive_edges").fetchone()[0]
        assert before >= 1

        # Corrupt the table to simulate staleness, then run incremental_update.
        conn.execute("DELETE FROM transitive_edges")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM transitive_edges").fetchone()[0] == 0

        # Edit a file so there's a change to pick up.
        (workspace / "demo" / "b.kt").write_text(
            "class Callee {\n  fun target() {}\n  fun newMethod() {}\n}\n"
        )
    finally:
        conn.close()

    incremental_update(workspace=str(workspace), db_path=db)
    conn = get_db(db)
    try:
        # transitive_edges should be repopulated by incremental_update now.
        after = conn.execute("SELECT COUNT(*) FROM transitive_edges").fetchone()[0]
        assert after >= 1, (
            "incremental_update should rebuild transitive_edges; table stayed empty"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# #4 — incremental_update returns errors instead of swallowing them.
# ---------------------------------------------------------------------------

def test_incremental_update_returns_errors_key(workspace, tmp_path):
    """The return dict must include an 'errors' list (possibly empty)."""
    db = str(tmp_path / "errs.db")
    _build(workspace, db)
    result = incremental_update(workspace=str(workspace), db_path=db)
    assert "errors" in result, "incremental_update must surface an 'errors' list"
    assert isinstance(result["errors"], list)


# ---------------------------------------------------------------------------
# #3 — single-repo build takes the advisory build lock.
# ---------------------------------------------------------------------------

def test_single_repo_build_takes_lock(workspace, tmp_path, monkeypatch):
    """Two concurrent single-repo builds must not both proceed.

    Holding the lock from one and attempting another should raise RuntimeError.
    """
    db = str(tmp_path / "lock.db")

    # Hold the lock manually, then attempt a single-repo build -> should raise.
    with build_lock(db):
        with pytest.raises(RuntimeError, match="another build"):
            build_graph(
                workspace=str(workspace),
                repo_filter="demo",
                db_path=db,
            )


# ---------------------------------------------------------------------------
# #5 — transactional reindex: a failed re-parse keeps the old rows.
# ---------------------------------------------------------------------------

def test_reindex_failure_keeps_old_rows(workspace, tmp_path, monkeypatch):
    """If re-parse fails after the delete, the old symbols must be restored.

    Before the fix: delete -> commit, then re-parse failure left a gap (old
    deleted, new not written). Now the whole delete+reinsert is one transaction.
    """
    db = str(tmp_path / "tx.db")
    conn = _build(workspace, db)
    try:
        symbols_before = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        assert symbols_before >= 2
    finally:
        conn.close()

    # Make the parser fail by patching the kotlin parser's parse to raise. Use a
    # wrapper that delegates to the real parser for non-target calls so we don't
    # mutate the shared cached instance (which would leak into other tests).
    import cairn.graph.builder as builder_mod

    real_get_parser = builder_mod.get_parser

    # The worker calls get_parser then parser.parse(path). Wrap the returned
    # parser so only .parse raises, only for kotlin (b.kt).
    class _FailingParserProxy:
        def __init__(self, real):
            self._real = real

        def parse(self, path):
            raise RuntimeError("simulated parse failure")

        def __getattr__(self, name):
            return getattr(self._real, name)

    def failing_get_parser(language):
        p = real_get_parser(language)
        if language == "kotlin":
            return _FailingParserProxy(p)
        return p

    monkeypatch.setattr(builder_mod, "get_parser", failing_get_parser)

    b = workspace / "demo" / "b.kt"
    b.write_text("class Callee {\n  fun target() {}\n}\n")  # touch
    conn = get_db(db)
    try:
        result = reindex_paths(conn, str(workspace), [str(b)])
    finally:
        conn.close()

    conn = get_db(db)
    try:
        # The old symbols must still be present (rollback restored them).
        symbols_after = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        assert symbols_after == symbols_before, (
            f"a failed re-parse should leave old rows intact via rollback; "
            f"had {symbols_before}, now {symbols_after}"
        )
        # And the failure should be reported in the errors list.
        assert any("simulated parse failure" in e for e in result["errors"]), (
            "reindex failure should be surfaced in errors"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# #7 — read-only mode skips metric writes (table stays empty).
# ---------------------------------------------------------------------------

def test_read_only_skips_metric_logging(monkeypatch):
    """_log_metric is a no-op when CAIRN_READ_ONLY is set."""
    import cairn.mcp_server.metric_buffering as mb

    monkeypatch.setenv("CAIRN_READ_ONLY", "1")
    mb._METRIC_BUFFER.clear()
    mb._log_metric("some_tool", 12.3, "ok")
    assert len(mb._METRIC_BUFFER) == 0, (
        "metric logging should be skipped in read-only mode"
    )

    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    mb._log_metric("some_tool", 12.3, "ok")
    assert len(mb._METRIC_BUFFER) == 1, (
        "metric logging should run when not read-only"
    )


# ---------------------------------------------------------------------------
# #12 — _clear_repo deletes embeddings.
# ---------------------------------------------------------------------------

def test_clear_repo_deletes_embeddings(workspace, tmp_path):
    """A repo rebuild should not leave orphaned embedding rows."""
    from cairn.graph.builder import _clear_repo

    db = str(tmp_path / "emb.db")
    conn = _build(workspace, db)
    try:
        # Manually insert embedding rows for a symbol (the semantic extra isn't
        # installed in the test env, so we simulate the rows).
        sym = conn.execute("SELECT id FROM symbols LIMIT 1").fetchone()
        conn.execute(
            "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk, embedded_at) "
            "VALUES (?, 'test', 4, X'00000000', 'chunk', 0)",
            (sym["id"],),
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 1

        _clear_repo(conn, "demo")
        conn.commit()

        orphans = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        assert orphans == 0, (
            f"_clear_repo should delete embeddings; {orphans} orphaned rows remain"
        )
    finally:
        conn.close()
