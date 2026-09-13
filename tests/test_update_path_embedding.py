"""FR-002 (specs/indexing-exact-rate): update-path embedding.

`reindex_paths` is the common entry for `cairn update`, sync, and the
watcher. This module pins the AC3 invariant: after a changed file is
re-indexed with a semantic backend available, the file's re-created
symbols carry embeddings under the current model -- no manual
`cairn embed` needed -- and the return dict reports how many symbols
were embedded.
"""
from __future__ import annotations

import logging
import pytest

from cairn.graph import embeddings as emb
from cairn.graph.builder import build_graph
from cairn.graph.incremental import reindex_paths
from cairn.graph.schema import get_db


# ---------------------------------------------------------------------------
# Fixture: a tiny workspace with two files so caller/callee span files.
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


def _file_symbol_ids(conn, rel_path):
    return {
        r["id"]
        for r in conn.execute(
            "SELECT id FROM symbols WHERE file_id = "
            "(SELECT id FROM files WHERE path = ?)",
            (rel_path,),
        )
    }


# ---------------------------------------------------------------------------
# FR-002 / AC3 -- reindex re-embeds a changed file's symbols.
# ---------------------------------------------------------------------------

def test_reindex_embeds_changed_file_symbols(workspace, tmp_path, hash_backend):
    """Re-indexing a changed file must embed its re-created symbols.

    Before the fix: reindex deletes the changed file's embeddings
    (including the vec0 rows) and never re-embeds -- so changed symbols
    stay unembedded until a manual `cairn embed`, and the return dict
    exposes no embedded_symbols count.
    """
    db = str(tmp_path / "embed.db")
    conn = _build(workspace, db)
    try:
        model = emb.current_model()

        # Baseline: a prior wholesale `cairn embed` covered the corpus.
        assert emb.embed_all(conn)["embedded"] > 0
        pre_ids = _file_symbol_ids(conn, "b.kt")
        assert pre_ids, "expected b.kt symbols after build"
        pre_embedded = {
            r["symbol_id"]
            for r in conn.execute(
                "SELECT symbol_id FROM embeddings WHERE model = ?", (model,)
            )
        }
        assert pre_ids <= pre_embedded, "baseline embed pass missed b.kt symbols"

        # Change b.kt and reindex it.
        (workspace / "demo" / "b.kt").write_text(
            "class Callee {\n"
            "  fun target() {}\n"
            "  fun extra() {}\n"  # new symbol created by the edit
            "}\n"
        )
        result = reindex_paths(conn, str(workspace), [str(workspace / "demo" / "b.kt")])
        assert result["errors"] == []

        # The re-created file's symbols (new ids, embeddings deleted by the
        # reindex delete leg) must be embedded again under the current model.
        new_ids = _file_symbol_ids(conn, "b.kt")
        assert len(new_ids) >= 2, "expected the edit to re-create symbols"
        embedded_ids = {
            r["symbol_id"]
            for r in conn.execute(
                "SELECT symbol_id FROM embeddings WHERE model = ?", (model,)
            )
        }
        missing = sorted(new_ids - embedded_ids)
        assert not missing, (
            "changed file's symbols must carry embeddings under the current "
            f"model after reindex_paths; unembedded: {missing}"
        )
        assert result.get("embedded_symbols") == len(new_ids), (
            "reindex_paths must report the embedded_symbols count on its "
            f"return dict; got {result.get('embedded_symbols')!r} for "
            f"{len(new_ids)} re-created symbols"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# FR-003 / AC4 -- no backend available: the update still succeeds and the
# deferral is observable (degrade-not-fail, never the embed CLI's hard exit).
# ---------------------------------------------------------------------------

@pytest.fixture
def no_backend(monkeypatch):
    """Select the server backend against a dead endpoint so the availability
    probe fails: ``embeddings_available()`` is False for the test -- the
    no-backend condition, with no hash fallback (the backend was chosen, it
    just cannot be reached). Entry+exit cache reset mirrors ``hash_backend``.
    """
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "server")
    monkeypatch.setenv("CAIRN_EMBED_BASE_URL", "http://127.0.0.1:9/v1")
    emb.reset_backend_cache()
    yield
    emb.reset_backend_cache()


def test_reindex_without_backend_succeeds_and_defers_observably(
    workspace, tmp_path, no_backend, caplog
):
    """Re-indexing with no backend available must succeed and make the
    deferral observable: one WARN per pass carrying the deferred count, plus
    a deferred_embeds count on the return dict.

    Before the fix: the embeddings_available() gate silently skips the embed
    leg -- the update succeeds but nothing reports the deferred embeds.
    """
    db = str(tmp_path / "defer.db")
    conn = _build(workspace, db)
    try:
        assert emb.embeddings_available() is False, (
            "fixture must present a machine with no usable backend"
        )

        # Change b.kt so the reindex re-creates its symbols.
        (workspace / "demo" / "b.kt").write_text(
            "class Callee {\n"
            "  fun target() {}\n"
            "  fun extra() {}\n"
            "}\n"
        )
        with caplog.at_level(logging.WARNING):
            result = reindex_paths(
                conn, str(workspace), [str(workspace / "demo" / "b.kt")]
            )

        # Degrade-not-fail: the update itself succeeds (never a hard exit).
        assert result["errors"] == [], (
            "a missing backend must never fail the update"
        )

        new_ids = _file_symbol_ids(conn, "b.kt")
        assert new_ids, "expected b.kt symbols after the reindex"
        assert result.get("deferred_embeds") == len(new_ids), (
            "reindex_paths must report the deferred_embeds count when no "
            f"backend is available; got {result.get('deferred_embeds')!r} "
            f"for {len(new_ids)} unembedded symbols"
        )

        warns = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and r.name.startswith("cairn.")
        ]
        assert len(warns) == 1, (
            f"expected one WARN per deferred pass, got {len(warns)}: "
            f"{[r.getMessage() for r in warns]}"
        )
        assert str(len(new_ids)) in warns[0].getMessage(), (
            "the WARN must carry the deferred count"
        )
    finally:
        conn.close()
