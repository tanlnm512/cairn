"""T018 (FR-005): the multi-vector query path in ``semantic_search``.

Covers ``RetrievalParams.multivector``:
- flag-off is byte-identical (TC-020's query half): params=None,
  RetrievalParams(), and multivector=False return identical result lists
  and never touch ``embeddings_mv`` in SQL, even when mv rows exist;
- flag-on brute leg UNIONs ``embeddings`` + ``embeddings_mv`` rows (same
  model) and the candidate-dict construction dedups per symbol by MAX
  cosine, carrying the winning row's chunk;
- flag-on ANN leg queries ``vec_`` AND ``vecmv_`` and merges the candidate
  lists (each symbol once, best score); a missing vecmv index leaves the
  base candidates unchanged;
- TC-021: a telegraphic name-style query and a prose docstring-style query
  each surface a symbol through the vector kind that matches (the pole the
  single-vector build misses);
- TC-023: no result list ever contains a symbol twice, including under the
  default fusion-on path;
- an empty ``embeddings_mv`` behaves exactly single-vector.

Vectors are handcrafted 4-dim basis blends with ``embed_query`` pinned to
a deterministic query vector; the corpus-level tests use the real hash
embedder via ``embed_all(multivector=True)``.
"""
from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("hash_backend")

_DIM = 4


def _vec(*comps: float) -> bytes:
    """Pack handcrafted components as a float32-LE embedding blob."""
    return struct.pack(f"<{len(comps)}f", *comps)


def _unit(axis: int) -> bytes:
    """One-hot basis vector: cosine against it is exactly that axis's component."""
    comps = [0.0] * _DIM
    comps[axis] = 1.0
    return _vec(*comps)


def _fix_query(monkeypatch, blob: bytes) -> None:
    """Pin ``embed_query`` to one handcrafted vector (deterministic dense leg)."""
    from cairn.graph import embeddings as emb

    monkeypatch.setattr(emb, "embed_query", lambda text: (blob, _DIM))


def _seed_symbols(conn: sqlite3.Connection) -> None:
    """Three functions: '1' and '3' have docstrings, '2' does not.

    Mirrors tests/test_ann_vecmv.py's corpus.
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


def _insert_base(conn: sqlite3.Connection, sid: str, vec: bytes, chunk: str) -> None:
    from cairn.graph import embeddings as emb

    conn.execute(
        "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk) VALUES (?, ?, ?, ?, ?)",
        (sid, emb.current_model(), _DIM, vec, chunk),
    )


def _insert_mv(
    conn: sqlite3.Connection,
    sid: str,
    kind: str,
    vec: bytes,
    chunk: str,
    model: str | None = None,
) -> None:
    from cairn.graph import embeddings as emb

    conn.execute(
        "INSERT INTO embeddings_mv (symbol_id, model, vector_kind, dim, vec, chunk) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sid, model or emb.current_model(), kind, _DIM, vec, chunk),
    )


def _seed_max_corpus(conn: sqlite3.Connection) -> None:
    """Handcrafted max-over-vectors corpus for a query pinned to e0:

    * '1' -- base e2 (cos 0.0), name e0 (1.0), doc e3: surfaces ONLY
      through the name vector; flag-off drops it entirely (0.0 < 0.3);
    * '2' -- base e0 (1.0), name e1: the MAX comes from the BASE row;
    * '3' -- base 0.6-mix, name 0.8-mix: the MAX is the name vector's
      0.8, neither the base's 0.6 nor an arbitrary row's.
    """
    _seed_symbols(conn)
    _insert_base(conn, "1", _unit(2), "base:safeApiCall")
    _insert_base(conn, "2", _unit(0), "base:parseHeader")
    _insert_base(conn, "3", _vec(0.6, 0.0, 0.8, 0.0), "base:loadConfig")
    _insert_mv(conn, "1", "name", _unit(0), "name:safeApiCall")
    _insert_mv(conn, "1", "docstring", _unit(3), "doc:safeApiCall")
    _insert_mv(conn, "2", "name", _unit(1), "name:parseHeader")
    _insert_mv(conn, "3", "name", _vec(0.8, 0.0, 0.6, 0.0), "name:loadConfig")
    _insert_mv(conn, "3", "docstring", _unit(2), "doc:loadConfig")
    conn.commit()


class _StatementRecorder:
    """Collect every SQL statement this connection executes."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.statements: list[str] = []

    def __enter__(self):
        self.conn.set_trace_callback(self.statements.append)
        return self

    def __exit__(self, *exc):
        self.conn.set_trace_callback(None)

    def touching(self, fragment: str) -> list[str]:
        return [s for s in self.statements if fragment in s]


def _disable_rerank(monkeypatch) -> None:
    """No CAIRN_RERANK env, marker probing pointed at a nonexistent path."""
    from cairn.graph import reranker as rrk

    monkeypatch.setattr(
        rrk, "_rerank_marker_path", lambda: Path("/nonexistent/cairn-t018-marker")
    )
    monkeypatch.delenv("CAIRN_RERANK", raising=False)
    monkeypatch.delenv("CAIRN_RERANK_MIN_MARGIN", raising=False)


@pytest.fixture()
def brute_env(fresh_db, monkeypatch):
    """Brute scan forced, fusion OFF (raw cosine scores), rerank off."""
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    monkeypatch.setenv("CAIRN_FUSION", "0")
    _disable_rerank(monkeypatch)
    return fresh_db


@pytest.fixture()
def fused_env(fresh_db, monkeypatch):
    """Brute scan forced, fusion at its ON default (the fused result path)."""
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    monkeypatch.delenv("CAIRN_FUSION", raising=False)
    _disable_rerank(monkeypatch)
    return fresh_db


@pytest.fixture()
def ann_env(fresh_db, monkeypatch):
    """sqlite-vec ANN backend on, fusion OFF (raw scores), rerank off."""
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")
    monkeypatch.setenv("CAIRN_FUSION", "0")
    _disable_rerank(monkeypatch)
    return fresh_db


_PROBES = [
    "safeApiCall",
    "Handles retries with backoff.",
    "parseHeader",
    "Loads the config file.",
]


# ---------------------------------------------------------------------------
# Flag-off equivalence (TC-020's query half): byte-identical, mv never read
# ---------------------------------------------------------------------------


@pytest.fixture()
def mv_corpus_db(brute_env):
    """The 3-symbol corpus embedded WITH multivector: 3 base rows + 5 mv rows
    exist in the DB -- the flag-off contract must hold in their PRESENCE."""
    from cairn.graph import embeddings as emb

    _seed_symbols(brute_env)
    emb.embed_all(brute_env, multivector=True)
    n_mv = brute_env.execute("SELECT COUNT(*) FROM embeddings_mv").fetchone()[0]
    assert n_mv == 5, "fixture sanity: name+docstring mv rows exist"
    return brute_env


@pytest.mark.parametrize("probe", _PROBES)
def test_flag_off_param_shapes_byte_identical(mv_corpus_db, probe):
    from cairn.graph.semantic import RetrievalParams, semantic_search

    kwargs = dict(limit=5, threshold=0.1)

    baseline = semantic_search(mv_corpus_db, probe, **kwargs)
    assert baseline, "fixture sanity: the probe returns results at all"
    for params in (RetrievalParams(), RetrievalParams(multivector=False)):
        assert semantic_search(mv_corpus_db, probe, params=params, **kwargs) == baseline


def test_flag_off_never_reads_embeddings_mv(mv_corpus_db):
    """params=None/()/False issue zero embeddings_mv SQL; flag-on does."""
    from cairn.graph.semantic import RetrievalParams, semantic_search

    with _StatementRecorder(mv_corpus_db) as rec:
        semantic_search(mv_corpus_db, "safeApiCall", limit=5, threshold=0.1)
        semantic_search(
            mv_corpus_db, "safeApiCall", params=RetrievalParams(), limit=5, threshold=0.1
        )
        semantic_search(
            mv_corpus_db,
            "safeApiCall",
            params=RetrievalParams(multivector=False),
            limit=5,
            threshold=0.1,
        )
    assert rec.touching("embeddings_mv") == [], "flag-off must never read embeddings_mv"

    with _StatementRecorder(mv_corpus_db) as rec:
        semantic_search(
            mv_corpus_db,
            "safeApiCall",
            params=RetrievalParams(multivector=True),
            limit=5,
            threshold=0.1,
        )
    assert rec.touching("embeddings_mv"), "flag-on brute leg UNIONs the mv table"


# ---------------------------------------------------------------------------
# Brute leg: UNION + max-dedup (TC-023 brute half)
# ---------------------------------------------------------------------------


def test_brute_union_max_dedup(brute_env, monkeypatch):
    """Flag-on surfaces each symbol ONCE at its MAX cosine, carrying the
    winning vector's row (score AND chunk); flag-off keeps the base-only
    picture (the mv-only symbol absent, the base score for the rest)."""
    from cairn.graph.semantic import RetrievalParams, semantic_search

    _seed_max_corpus(brute_env)
    _fix_query(monkeypatch, _unit(0))

    on = semantic_search(
        brute_env, "safeApiCall", params=RetrievalParams(multivector=True), limit=10
    )
    by_id = {}
    for c in on:
        assert c["id"] not in by_id, f"symbol {c['id']} appears twice flag-on"
        by_id[c["id"]] = c

    # '1' qualifies ONLY through its name vector (base cosine 0.0 < 0.3).
    assert by_id["1"]["score"] == 1.0
    assert by_id["1"]["chunk"] == "name:safeApiCall"
    # '2''s max is the BASE row.
    assert by_id["2"]["score"] == 1.0
    assert by_id["2"]["chunk"] == "base:parseHeader"
    # '3''s max is the name vector's 0.8 -- not the base's 0.6.
    assert by_id["3"]["score"] == 0.8
    assert by_id["3"]["chunk"] == "name:loadConfig"

    off = semantic_search(brute_env, "safeApiCall", limit=10)
    off_by_id = {c["id"]: c for c in off}
    assert "1" not in off_by_id, "base cosine 0.0 must not surface flag-off"
    assert off_by_id["3"]["score"] == 0.6, "flag-off sees only the base vector"


# ---------------------------------------------------------------------------
# TC-021: both mismatch poles surface through the matching vector kind
# ---------------------------------------------------------------------------


def test_tc021_telegraphic_and_prose_poles(brute_env, monkeypatch):
    """A name-style query reaches '1' through its NAME vector and a prose
    query through its DOCSTRING vector -- the two poles the single-vector
    build misses (base vector is orthogonal to both) -- and repeats return
    identical results."""
    from cairn.graph.semantic import RetrievalParams, semantic_search

    _seed_max_corpus(brute_env)

    # Telegraphic pole: the query is the bare function name; pinned e0.
    _fix_query(monkeypatch, _unit(0))
    on_name = semantic_search(
        brute_env, "safeApiCall", params=RetrievalParams(multivector=True), limit=10
    )
    off_name = semantic_search(brute_env, "safeApiCall", limit=10)
    hit = next(c for c in on_name if c["id"] == "1")
    assert hit["score"] == 1.0 and hit["chunk"] == "name:safeApiCall"
    assert "1" not in {c["id"] for c in off_name}, "the pole that previously missed"

    # Prose pole: a describes-the-behavior sentence; pinned e3 (the
    # docstring-kind axis in this corpus).
    _fix_query(monkeypatch, _unit(3))
    on_doc = semantic_search(
        brute_env,
        "Handles retries with backoff.",
        params=RetrievalParams(multivector=True),
        limit=10,
    )
    off_doc = semantic_search(brute_env, "Handles retries with backoff.", limit=10)
    hit = next(c for c in on_doc if c["id"] == "1")
    assert hit["score"] == 1.0 and hit["chunk"] == "doc:safeApiCall"
    assert "1" not in {c["id"] for c in off_doc}

    # Repeats return identical results (run each probe twice, diff empty).
    _fix_query(monkeypatch, _unit(0))
    again = semantic_search(
        brute_env, "safeApiCall", params=RetrievalParams(multivector=True), limit=10
    )
    assert again == on_name


# ---------------------------------------------------------------------------
# TC-023: no duplicate symbols in any result list (fused path included)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "probe",
    _PROBES + ["retries backoff", "function xyz.safeApiCall", "xyz"],
)
def test_fused_result_lists_duplicate_free(fused_env, probe):
    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import RetrievalParams, semantic_search

    _seed_symbols(fused_env)
    emb.embed_all(fused_env, multivector=True)

    results = semantic_search(
        fused_env, probe, params=RetrievalParams(multivector=True), limit=5, threshold=0.1
    )
    assert results, "fixture sanity: non-empty list to inspect"
    ids = [r["id"] for r in results]
    assert len(ids) == len(set(ids)), "one symbol, one entry (TC-023)"
    assert len(results) <= 5, "list length stays within the requested top-k"


# ---------------------------------------------------------------------------
# Empty embeddings_mv behaves exactly single-vector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("probe", _PROBES)
def test_empty_mv_table_flag_on_equals_flag_off(brute_env, probe):
    from cairn.graph import embeddings as emb
    from cairn.graph.semantic import RetrievalParams, semantic_search

    _seed_symbols(brute_env)
    emb.embed_all(brute_env)  # default: no mv rows
    assert brute_env.execute("SELECT COUNT(*) FROM embeddings_mv").fetchone()[0] == 0

    kwargs = dict(limit=5, threshold=0.1)
    off = semantic_search(brute_env, probe, **kwargs)
    assert off, "fixture sanity"
    on = semantic_search(
        brute_env, probe, params=RetrievalParams(multivector=True), **kwargs
    )
    assert on == off, "empty embeddings_mv must behave single-vector"


def test_mv_rows_under_foreign_model_excluded(brute_env, monkeypatch):
    """The UNION's mv arm filters by the SAME model stamp as the base arm:
    a foreign-model mv row never surfaces its symbol."""
    from cairn.graph.semantic import RetrievalParams, semantic_search

    _seed_symbols(brute_env)
    _insert_base(brute_env, "1", _unit(2), "base:safeApiCall")
    _insert_base(brute_env, "2", _unit(2), "base:parseHeader")
    _insert_mv(brute_env, "1", "name", _unit(0), "name:safeApiCall", model="other-model/v1")
    _insert_mv(brute_env, "2", "name", _unit(0), "name:parseHeader")
    brute_env.commit()
    _fix_query(monkeypatch, _unit(0))

    on = semantic_search(
        brute_env, "retries", params=RetrievalParams(multivector=True), limit=10
    )
    ids = {c["id"] for c in on}
    assert "1" not in ids, "foreign-model mv row must be ignored"
    assert "2" in ids, "same-model mv row still surfaces"


# ---------------------------------------------------------------------------
# ANN leg: dual-index query + merge (requires the real sqlite-vec extension)
# ---------------------------------------------------------------------------

sqlite_vec = pytest.importorskip(
    "sqlite_vec", reason="sqlite-vec not installed -- ANN tests need the real extension"
)


def _rebuild_both(conn) -> None:
    from cairn.graph import ann_index as ann, embeddings as emb

    model = emb.current_model()
    assert ann.rebuild_index(conn, model)["indexed"] == 3
    assert ann.rebuild_index(conn, model, source="embeddings_mv")["indexed"] == 5


def test_ann_dual_index_merge(ann_env, monkeypatch):
    """Flag-on queries vec_ AND vecmv_ and merges: the mv-only symbol
    surfaces, each symbol appears once, and per-symbol scores are the MAX
    across both indexes (0.8 from vecmv_, not 0.6 from vec_)."""
    from cairn.graph.semantic import RetrievalParams, semantic_search

    _seed_max_corpus(ann_env)
    _rebuild_both(ann_env)
    _fix_query(monkeypatch, _unit(0))

    on = semantic_search(
        ann_env, "safeApiCall", params=RetrievalParams(multivector=True), limit=10
    )
    ids = [c["id"] for c in on]
    assert len(ids) == len(set(ids)), "merged list: one entry per symbol"
    by_id = {c["id"]: c for c in on}
    assert "1" in by_id, "mv-only match surfaces through the vecmv_ leg"
    assert by_id["1"]["score"] == pytest.approx(1.0, abs=1e-3)
    assert by_id["3"]["score"] == pytest.approx(0.8, abs=1e-3), "max across both legs"

    # Flag-off: the base index alone -- '1' (base cosine 0.0) absent.
    off = semantic_search(ann_env, "safeApiCall", limit=10)
    assert "1" not in {c["id"] for c in off}


def test_ann_missing_vecmv_index_equals_flag_off(ann_env, monkeypatch):
    """No vecmv rebuild -> the mv leg returns None -> base candidates
    unchanged: flag-on results are identical to flag-off, no error."""
    from cairn.graph import ann_index as ann, embeddings as emb
    from cairn.graph.semantic import RetrievalParams, semantic_search

    _seed_max_corpus(ann_env)
    ann.rebuild_index(ann_env, emb.current_model())  # base only
    _fix_query(monkeypatch, _unit(0))

    off = semantic_search(ann_env, "safeApiCall", limit=10)
    on = semantic_search(
        ann_env, "safeApiCall", params=RetrievalParams(multivector=True), limit=10
    )
    assert on == off


def test_ann_flag_off_shapes_and_sql(ann_env, monkeypatch):
    """Both indexes present, flag off: the three off-shapes are identical
    and no SQL touches vecmv_/embeddings_mv."""
    from cairn.graph.semantic import RetrievalParams, semantic_search

    _seed_max_corpus(ann_env)
    _rebuild_both(ann_env)
    _fix_query(monkeypatch, _unit(0))

    baseline = semantic_search(ann_env, "safeApiCall", limit=10)
    assert baseline, "fixture sanity"
    with _StatementRecorder(ann_env) as rec:
        assert (
            semantic_search(
                ann_env, "safeApiCall", params=RetrievalParams(), limit=10
            )
            == baseline
        )
        assert (
            semantic_search(
                ann_env,
                "safeApiCall",
                params=RetrievalParams(multivector=False),
                limit=10,
            )
            == baseline
        )
    assert rec.touching("vecmv_") == [] and rec.touching("embeddings_mv") == []


# ---------------------------------------------------------------------------
# _merge_ann_candidates unit: max + deterministic ties
# ---------------------------------------------------------------------------


def test_merge_ann_candidates_max_and_tie_determinism():
    from cairn.graph.semantic import _merge_ann_candidates

    base = [{"id": "b", "score": 0.9}, {"id": "a", "score": 0.5}]
    extra = [{"id": "a", "score": 0.8}, {"id": "c", "score": 0.4}]
    merged = _merge_ann_candidates(base, extra)
    assert [(c["id"], c["score"]) for c in merged] == [("b", 0.9), ("a", 0.8), ("c", 0.4)]

    # Exact tie: the base-leg candidate wins (stable, deterministic).
    tied = _merge_ann_candidates(
        [{"id": "x", "score": 0.7, "leg": "base"}],
        [{"id": "x", "score": 0.7, "leg": "vecmv"}],
    )
    assert tied == [{"id": "x", "score": 0.7, "leg": "base"}]
