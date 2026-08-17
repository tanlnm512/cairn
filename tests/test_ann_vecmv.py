"""T019 (FR-005, D-007): the dedicated ``vecmv_<safe-model>`` vec0 index.

Covers the additive ``source`` parameter on ``rebuild_index``/``ann_query``:
- default source ("embeddings") is byte-identical to the pre-T019 call shape
  and never creates a vecmv table (TC-020's flag-off storage guarantee);
- source="embeddings_mv" builds a SEPARATE vec0 table beside ``vec_`` with
  the same rowid-keyed wholesale-rebuild contract (every mv vector kind goes
  in), and ``ann_query`` round-trips through ``embeddings_mv``'s own rows;
- ``_candidates_from_ann_hits`` dedups per symbol by MAX score -- one entry
  per symbol at its best score (the ANN half of TC-023's "one symbol, one
  entry"), a no-op at one row per symbol where results are byte-identical
  to the previous last-wins comprehension;
- CLI: ``cairn embed --multivector`` rebuilds BOTH indexes when the ANN
  backend is on; the flag-off build creates only the base ``vec_`` table.

Uses CAIRN_EMBED_BACKEND=hash so no torch/model download is needed.
"""
from __future__ import annotations

import sqlite3

import pytest

sqlite_vec = pytest.importorskip(
    "sqlite_vec", reason="sqlite-vec not installed -- ANN tests need the real extension"
)

pytestmark = pytest.mark.usefixtures("hash_backend")


def _seed_corpus(conn: sqlite3.Connection) -> None:
    """Three symbols: '1' and '3' have docstrings, '2' does not.

    Mirrors tests/test_embeddings_mv.py's corpus: under multivector that is
    3 base rows, 3 name rows + 2 docstring rows = 5 mv rows.
    """
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/Api.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES ('1', 1, 'safeApiCall', 'function', 'xyz.safeApiCall', 'Handles retries with backoff.', 1, 10)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('2', 1, 'parseHeader', 'function', 'xyz.parseHeader', 12, 20)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES ('3', 1, 'loadConfig', 'function', 'xyz.loadConfig', 'Loads the config file.', 22, 30)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Default source -- byte-identical to the pre-T019 call shape (TC-020)
# ---------------------------------------------------------------------------


def test_default_source_param_equivalent_to_legacy_call(fresh_db, monkeypatch):
    """No-source == source='embeddings' for both rebuild and query, exactly."""
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    from cairn.graph import ann_index as ann, embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db)
    model = emb.current_model()

    legacy = ann.rebuild_index(fresh_db, model)
    explicit = ann.rebuild_index(fresh_db, model, source="embeddings")
    assert legacy["indexed"] == explicit["indexed"] == 3
    assert legacy["dim"] == explicit["dim"]
    # One and the same table (the explicit-source rebuild dropped/recreated it).
    assert ann._table_name(model) == ann._table_name(model, "embeddings")
    assert ann._table_name(model).startswith("vec_")

    q_blob, _ = emb.embed_query("safeApiCall")
    legacy_hits = ann.ann_query(fresh_db, model, q_blob, k=5)
    explicit_hits = ann.ann_query(fresh_db, model, q_blob, k=5, source="embeddings")
    assert legacy_hits is not None and explicit_hits is not None
    assert legacy_hits == explicit_hits, "default-source query must be byte-identical"


def test_default_build_never_creates_vecmv_table(fresh_db, monkeypatch):
    """A flag-off build + default rebuild must not leave a vecmv table
    behind -- flag-off storage is byte-identical (TC-020)."""
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    from cairn.graph import ann_index as ann, embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db)  # multivector off: the default
    model = emb.current_model()

    summary = ann.rebuild_index(fresh_db, model)
    assert summary["indexed"] == 3
    assert ann.index_exists(fresh_db, model)
    assert not ann.index_exists(fresh_db, model, "embeddings_mv")
    vecmv_tables = fresh_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'vecmv_%'"
    ).fetchall()
    assert vecmv_tables == []


# ---------------------------------------------------------------------------
# vecmv_<safe-model>: separate table, same contract
# ---------------------------------------------------------------------------


def test_vecmv_built_beside_vec_with_same_contract(fresh_db, monkeypatch):
    """source='embeddings_mv' builds its OWN vec0 table beside vec_, wholesale
    (DROP+CREATE+INSERT...SELECT), rowid-keyed over embeddings_mv's rows --
    every mv vector kind goes in (3 name + 2 docstring = 5 here)."""
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    from cairn.graph import ann_index as ann, embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db, multivector=True)
    model = emb.current_model()

    base = ann.rebuild_index(fresh_db, model)
    mv = ann.rebuild_index(fresh_db, model, source="embeddings_mv")

    assert base["indexed"] == 3, "base index still covers exactly the embeddings rows"
    assert mv["indexed"] == 5, "mv index covers every mv row (name + docstring kinds)"
    assert base["dim"] == mv["dim"], "same embedder -> same dimensionality"

    vec_tbl, mv_tbl = ann._table_name(model), ann._table_name(model, "embeddings_mv")
    assert vec_tbl != mv_tbl and mv_tbl.startswith("vecmv_")
    assert ann.index_exists(fresh_db, model)
    assert ann.index_exists(fresh_db, model, "embeddings_mv")

    # Rowid-keyed contract: each index's rowids are exactly its source
    # table's rowids (the join back to <source>.rowid is total).
    src_rowids = {
        r[0] for r in fresh_db.execute("SELECT rowid FROM embeddings_mv WHERE model = ?", (model,))
    }
    vecmv_rowids = {r[0] for r in fresh_db.execute(f"SELECT rowid FROM {mv_tbl}")}
    assert vecmv_rowids == src_rowids

    # Safe to call repeatedly (wholesale DROP+CREATE), same counts.
    again = ann.rebuild_index(fresh_db, model, source="embeddings_mv")
    assert again["indexed"] == 5


def test_ann_query_mv_round_trip(fresh_db, monkeypatch):
    """Querying the mv index returns rows joined through embeddings_mv: every
    hit's symbol has an mv row, and kind-specific texts are the matching
    surface (query the exact name-kind / docstring-kind chunk -> top hit)."""
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    from cairn.graph import ann_index as ann, embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db, multivector=True)
    model = emb.current_model()
    ann.rebuild_index(fresh_db, model)
    ann.rebuild_index(fresh_db, model, source="embeddings_mv")

    mv_symbols = {
        r[0] for r in fresh_db.execute("SELECT DISTINCT symbol_id FROM embeddings_mv")
    }

    # Name-kind text of symbol '1' ("function xyz.safeApiCall").
    q_blob, _ = emb.embed_query("function xyz.safeApiCall")
    hits = ann.ann_query(fresh_db, model, q_blob, k=5, source="embeddings_mv")
    assert hits is not None
    assert {sid for sid, _ in hits} <= mv_symbols, "hits join through embeddings_mv"
    assert hits[0][0] == "1", "the exact name-kind chunk is the top match"

    # Docstring-kind text of symbol '3'.
    q_blob, _ = emb.embed_query("Loads the config file.")
    hits = ann.ann_query(fresh_db, model, q_blob, k=5, source="embeddings_mv")
    assert hits is not None and hits[0][0] == "3"
    # A symbol with several mv rows can appear once per kind -- dedup is the
    # caller's contract (semantic._candidates_from_ann_hits), asserted below.

    # The base index still answers through embeddings on the default source.
    q_blob, _ = emb.embed_query("safeApiCall")
    base_hits = ann.ann_query(fresh_db, model, q_blob, k=5)
    assert base_hits is not None and {sid for sid, _ in base_hits} == {"1", "2", "3"}


def test_ann_query_mv_without_index_returns_none(fresh_db, monkeypatch):
    """No vecmv rebuild -> mv-source query is None (fall back, never empty)."""
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    from cairn.graph import ann_index as ann, embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db, multivector=True)
    model = emb.current_model()
    ann.rebuild_index(fresh_db, model)  # base only

    q_blob, _ = emb.embed_query("function xyz.safeApiCall")
    assert ann.ann_query(fresh_db, model, q_blob, k=5, source="embeddings_mv") is None


def test_unknown_source_rejected(fresh_db):
    """source is a closed set, never a free-form table name."""
    from cairn.graph import ann_index as ann

    with pytest.raises(ValueError):
        ann._table_name("m", source="symbols; DROP TABLE embeddings")
    with pytest.raises(ValueError):
        ann.rebuild_index(fresh_db, "m", source="embeddings_mv ")
    with pytest.raises(ValueError):
        ann.ann_query(fresh_db, "m", b"\x00" * 16, 5, source="other")
    with pytest.raises(ValueError):
        ann.index_exists(fresh_db, "m", source="other")


# ---------------------------------------------------------------------------
# _candidates_from_ann_hits: last-wins -> max (FR-005, TC-023's ANN half)
# ---------------------------------------------------------------------------


def _candidates(fresh_db, ann_hits, threshold):
    """Seed + embed (hash backend), then run the ANN-candidate path.

    _candidates_from_ann_hits joins `embeddings` for chunk/metadata, so the
    corpus must actually be embedded before the helper can resolve a hit.
    """
    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import _candidates_from_ann_hits

    if not fresh_db.execute("SELECT 1 FROM symbols LIMIT 1").fetchone():
        _seed_corpus(fresh_db)  # idempotent across repeat calls in one test
    emb.embed_all(fresh_db)
    return _candidates_from_ann_hits(fresh_db, ann_hits, threshold)


def test_candidates_dedup_max_multi_hit_symbol(fresh_db):
    """A symbol hit several times appears ONCE, at its MAX score (not the
    first hit's, not the last's), in first-occurrence order."""
    hits = [("1", 0.9), ("2", 0.5), ("1", 0.4), ("1", 0.7)]
    cands = _candidates(fresh_db, hits, threshold=0.3)

    ids = [c["id"] for c in cands]
    assert ids == ["1", "2"], "each symbol exactly once, first-occurrence order"
    assert cands[0]["score"] == 0.9, "max of {0.9, 0.4, 0.7}"
    assert cands[1]["score"] == 0.5


def test_candidates_threshold_applies_to_max_score(fresh_db):
    """Qualification rides the MAX score: a later below-threshold hit must
    not overwrite a passing one (the last-wins bug), and a symbol whose
    every hit is below threshold stays out."""
    # Last-wins would have kept 0.2 (below threshold) for '1'.
    cands = _candidates(fresh_db, [("1", 0.5), ("1", 0.2)], threshold=0.3)
    assert [c["id"] for c in cands] == ["1"]
    assert cands[0]["score"] == 0.5

    # All hits below threshold -> excluded even though a max exists.
    assert _candidates(fresh_db, [("1", 0.2), ("1", 0.1)], threshold=0.3) == []


def test_candidates_single_vector_noop_equivalence(fresh_db):
    """One hit per symbol: the result is exactly what the previous last-wins
    comprehension produced (same order, same rounded scores, same shape) --
    the dedup change is invisible on the single-vector path."""
    hits = [("2", 0.812345), ("1", 0.6), ("3", 0.31)]
    cands = _candidates(fresh_db, hits, threshold=0.3)

    assert [c["id"] for c in cands] == ["2", "1", "3"]
    assert [c["score"] for c in cands] == [0.8123, 0.6, 0.31], "round(score, 4)"
    for c, expected_name in zip(cands, ["parseHeader", "safeApiCall", "loadConfig"]):
        assert c["name"] == expected_name
        assert c["provenance"] == "semantic"
        assert c["reranked"] is False
        assert c["kind"] == "function"
        assert c["file_path"] == "/tmp/test/Api.kt"
        assert c["repo"] == "test"


# ---------------------------------------------------------------------------
# CLI wiring (TC-020's user-facing surface)
# ---------------------------------------------------------------------------


def test_cli_multivector_rebuilds_both_indexes(tmp_path, monkeypatch):
    """`cairn embed --multivector` (ANN backend on) rebuilds the base vec_
    AND the vecmv_ index; the flag-off build creates only vec_."""
    from click.testing import CliRunner

    from cairn.cli import main as cli_main
    from cairn.graph import ann_index as ann, embeddings as emb
    from cairn.graph.schema import init_db

    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    model = emb.current_model()
    runner = CliRunner()

    # Flag ON: both indexes.
    db_on = str(tmp_path / "on.db")
    conn = init_db(db_on)
    _seed_corpus(conn)
    conn.close()
    result = runner.invoke(
        cli_main, ["embed", "--db", db_on, "--multivector", "--build-index"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    conn = sqlite3.connect(db_on)
    conn.row_factory = sqlite3.Row
    try:
        # A fresh connection must load sqlite-vec before reading vec0 tables.
        assert ann.try_load(conn)
        vec_tbl = ann._table_name(model)
        mv_tbl = ann._table_name(model, "embeddings_mv")
        names = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert vec_tbl in names and mv_tbl in names
        assert conn.execute(f"SELECT COUNT(*) c FROM {vec_tbl}").fetchone()["c"] == 3
        assert conn.execute(f"SELECT COUNT(*) c FROM {mv_tbl}").fetchone()["c"] == 5
    finally:
        conn.close()

    # Flag OFF (default): only the base index, no vecmv table.
    db_off = str(tmp_path / "off.db")
    conn = init_db(db_off)
    _seed_corpus(conn)
    conn.close()
    result = runner.invoke(
        cli_main, ["embed", "--db", db_off, "--build-index"], catch_exceptions=False
    )
    assert result.exit_code == 0
    conn = sqlite3.connect(db_off)
    conn.row_factory = sqlite3.Row
    try:
        names = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert ann._table_name(model) in names
        assert not any(n.startswith("vecmv_") for n in names)
        assert conn.execute("SELECT COUNT(*) c FROM embeddings_mv").fetchone()["c"] == 0
    finally:
        conn.close()
