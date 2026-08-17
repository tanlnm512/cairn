"""Native ANN index for semantic_search, via the sqlite-vec extension.

`sqlite-vec` (https://github.com/asg017/sqlite-vec) provides a `vec0` virtual
table in the *same* `.db` file as `embeddings`, loaded as a SQLite extension.
Keeping vectors in the same file avoids the crash-consistency hazard of a
sidecar index whose writes can fall out of the SQLite transaction.

Two maintenance paths keep the vec0 table aligned with `embeddings`: a
wholesale rebuild (:func:`rebuild_index` -- the end of every ``cairn embed``
bulk pass) and a per-upsert sync (:func:`sync_index_row` /
:func:`delete_index_rows` -- embeddings' single-symbol paths). vec0 has no
"replace" semantics: a plain INSERT on an existing rowid raises ``UNIQUE
constraint failed`` even under ``INSERT OR REPLACE`` (see sync_index_row's
spike notes), so updates are always DELETE by rowid + re-INSERT inside the
caller's transaction. Bulk paths stay on the wholesale rebuild -- per-row
sync costs ~9x more per row than the rebuild's INSERT ... SELECT.

On by default: `CAIRN_ANN_BACKEND` unset resolves to `sqlite-vec`. Set it to
`off` to force the brute-force cosine scan. Any load failure degrades to the
brute-force scan.

Scope: this index covers ONLY the code-corpus ``embeddings`` table (the path
that ``graph.semantic.semantic_search`` and ``explore`` consume). The
``knowledge_embeddings`` and ``memory_embeddings`` tables intentionally have
no vec0 index: those corpora are small and curated (dozens to low hundreds of
rows), so a brute-force ``cosine_scan`` is sub-millisecond and not worth the
per-write vec0 sync cost. If either corpus ever grows large, the pattern here
(rebuild from the source table) is the template for adding one.

The FR-005 multi-vector table ``embeddings_mv`` gets its own ``vecmv_<safe-
model>`` vec0 index through the additive ``source`` parameter on
:func:`rebuild_index` / :func:`ann_query` (D-007): a separate table, because
the base ``vec_<model>`` rowid contract must never be shared with a table
whose row population differs (``embeddings_mv`` holds up to one row per
vector kind per symbol). Every existing caller passes no ``source`` and gets
the ``embeddings``/``vec_`` pair exactly as before.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import List, Optional, Tuple

from .schema import note_contention, _is_lock_contention

_logger = logging.getLogger(__name__)


def ann_backend_enabled() -> bool:
    import os

    val = os.environ.get("CAIRN_ANN_BACKEND", "sqlite-vec").strip().lower()
    if val != "sqlite-vec":
        # Explicit opt-out (e.g. "off"): stay disabled regardless of whether
        # sqlite_vec happens to be importable (e.g. pulled in transitively).
        return False
    try:
        import sqlite_vec  # noqa: F401
        return True
    except ImportError:
        return False


# Process-global guard so the one-time warning fires at most once per process.
_ANN_FALLBACK_WARNED: bool = False


def warn_ann_fallback_once(logger, context: str = "", reason: str = "") -> None:
    """Emit one ANN-fallback warning per process.

    Mirrors ``embeddings.warn_hash_fallback_once``: a process-global guard
    ensures the degradation surfaces at most once, so repeated brute-force
    scans don't spam the log.

    No-op when the brute-force backend was explicitly chosen
    (``CAIRN_ANN_BACKEND`` set to anything other than ``sqlite-vec``, e.g.
    ``off``): that is an informed user decision, not a silent degradation, so
    it must not warn. Only fires when sqlite-vec was *expected* (env unset or
    ``=sqlite-vec``) but is unavailable or failed to load.

    ``context`` is a short string identifying the calling path (e.g.
    ``"semantic_search"``); ``reason`` classifies why ANN is unavailable -- if
    omitted, it is inferred cheaply from whether ``sqlite_vec`` imports.
    """
    global _ANN_FALLBACK_WARNED
    if _ANN_FALLBACK_WARNED:
        return
    import os

    val = os.environ.get("CAIRN_ANN_BACKEND", "sqlite-vec").strip().lower()
    if val != "sqlite-vec":
        # Explicit opt-out (e.g. "off"): an intentional choice, not a
        # degradation -- stay silent (mirrors the rationale in
        # ann_backend_enabled()).
        return
    if not reason:
        try:
            import sqlite_vec  # noqa: F401

            reason = "load failed or no index built"
        except ImportError:
            reason = "sqlite-vec not installed"
    # Durable event (spec §6.4) with an enum reason for doctor/metrics
    # aggregation; the WARNING below keeps the human-readable detail. Map the
    # human reason to the spec's bounded enum so the attr value domain is fixed.
    _REASON_ENUM = {
        "sqlite-vec not installed": "not_installed",
        "load failed": "load_failed",
        "load failed or no index built": "load_failed",
        "no index built": "no_index",
        "query error": "query_error",
    }
    enum_reason = _REASON_ENUM.get(reason, "load_failed")
    try:
        from cairn.telemetry import ANN_FALLBACK, emit as _emit

        _emit(ANN_FALLBACK, reason=enum_reason)
    except Exception:
        pass
    # Tailor the remediation to the reason class: a missing/broken *index*
    # needs a rebuild, not a package install.
    if enum_reason in ("no_index", "query_error"):
        hint = "run `cairn embed` to (re)build the vec0 index"
    else:
        hint = (
            "install sqlite-vec with `cairn embed --install-deps` "
            "(CAIRN_ANN_BACKEND=sqlite-vec is the default)"
        )
    suffix = f" [{context}]" if context else ""
    logger.warning(
        "Semantic search is using the brute-force cosine scan instead of the "
        "native sqlite-vec ANN index (%s). Results stay correct but slower for "
        "large corpora. %s.%s",
        reason,
        hint,
        suffix,
    )
    _ANN_FALLBACK_WARNED = True


# vec0 sources (D-007): each source table gets its OWN per-model vec0 index
# (rowid-keyed), so the base vec_<model> contract is never shared with a
# table whose row population differs. Additive: callers that pass no source
# get the embeddings/vec_ pair exactly as before.
_SOURCE_PREFIX = {"embeddings": "vec_", "embeddings_mv": "vecmv_"}


def _table_name(model: str, source: str = "embeddings") -> str:
    """Sanitize a model name into a valid SQLite identifier for ``source``.

    Model names come from HF repo ids ('all-MiniLM-L6-v2',
    'jinaai/jina-embeddings-v2-base-code') or the hash/openai stamps -- none
    of those are safe as a bare identifier, so this is NOT just cosmetic.

    ``source`` selects the indexed table: ``"embeddings"`` (the default,
    table ``vec_<safe-model>``) or ``"embeddings_mv"`` (the FR-005
    multi-vector parallel table, table ``vecmv_<safe-model>``). Any other
    value raises ``ValueError`` -- sources are a closed set, never a
    free-form table name (SQL-injection surface).
    """
    if source not in _SOURCE_PREFIX:
        raise ValueError(f"unknown source: {source!r}")
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", model)
    return f"{_SOURCE_PREFIX[source]}{safe}"


def try_load(conn: sqlite3.Connection) -> bool:
    """Attempt to load the sqlite-vec extension into `conn`. Never raises.

    Returns False (instead of raising) for every failure mode this needs to
    survive: the package isn't installed, this Python's sqlite3 wasn't built
    with extension-loading support (`enable_load_extension` raising
    AttributeError/NotSupportedError), or the shared library fails to load on
    this platform.
    """
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except ImportError:
        # Package not installed -- the most common degradation. Gated by the
        # explicit-opt-out check inside the helper, so CAIRN_ANN_BACKEND=off
        # stays silent.
        warn_ann_fallback_once(
            _logger, context="ann_index.try_load", reason="sqlite-vec not installed"
        )
        return False
    except Exception:
        # sqlite-vec is importable but the extension won't load into this
        # connection (e.g. this Python wasn't built with extension-loading
        # support, or a platform shared-library load failure).
        warn_ann_fallback_once(
            _logger, context="ann_index.try_load", reason="load failed"
        )
        return False


def index_exists(conn: sqlite3.Connection, model: str, source: str = "embeddings") -> bool:
    """Whether the vec0 table for ``model`` (and ``source``) exists.

    ``source`` is additive (D-007): default probes ``vec_<safe-model>``,
    ``"embeddings_mv"`` probes ``vecmv_<safe-model>``. Existing callers pass
    two args and are unaffected.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_table_name(model, source),),
    ).fetchone()
    return row is not None


def rebuild_index(conn: sqlite3.Connection, model: str, source: str = "embeddings") -> dict:
    """Wholesale rebuild of the vec0 table for `model` from `source`.

    ``source`` (additive, D-007) selects the indexed table: ``"embeddings"``
    (the default -- vec0 table ``vec_<safe-model>``, byte-identical to the
    pre-FR-005 behavior) or ``"embeddings_mv"`` (the multi-vector parallel
    table -- its own ``vecmv_<safe-model>`` table, same DELETE+INSERT
    rowid-keyed contract over that table's rows: every mv vector kind goes
    in, the per-kind distinction lives in ``embeddings_mv`` itself).

    Returns {"model", "indexed", "dim"} or {"model", "indexed": 0, "skipped":
    reason} if sqlite-vec can't be loaded or there's nothing to index. Safe
    to call repeatedly (drops and recreates), and safe to call from a process
    that hasn't loaded the extension yet -- loads it itself via try_load().
    """
    # Validate source BEFORE it reaches any SQL (closed set -- see
    # _table_name; a near-miss like "embeddings_mv " must not execute).
    table = _table_name(model, source)
    if not try_load(conn):
        return {"model": model, "indexed": 0, "skipped": "sqlite-vec unavailable"}

    row = conn.execute(
        f"SELECT dim FROM {source} WHERE model = ? LIMIT 1", (model,)
    ).fetchone()
    if row is None:
        # Keep the base-source reason byte-identical to the pre-FR-005
        # string (it is user-visible CLI output and asserted in tests).
        reason = (
            "no embeddings for model"
            if source == "embeddings"
            else f"no {source} rows for model"
        )
        return {"model": model, "indexed": 0, "skipped": reason}
    dim = row["dim"] if hasattr(row, "keys") else row[0]

    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(
        f"CREATE VIRTUAL TABLE {table} USING vec0(embedding float[{int(dim)}] distance_metric=cosine)"
    )
    # rowid here is the source table's own hidden rowid -- stable for the
    # lifetime of this rebuild pass, which is all sync_index/ann_query need
    # (they always join back to <source>.rowid within the same rebuild
    # generation).
    conn.execute(
        f"INSERT INTO {table}(rowid, embedding) "
        f"SELECT rowid, vec FROM {source} WHERE model = ?",
        (model,),
    )
    conn.commit()
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return {"model": model, "indexed": count, "dim": dim}


def sync_index_row(
    conn: sqlite3.Connection, model: str, rowid: int, blob: bytes
) -> bool:
    """Incrementally sync one vec0 row to an embeddings upsert (no commit).

    Spike findings (sqlite-vec 0.1.9, SQLite 3.53.1, PERF-4 P4.1) that shape
    this helper:

    * ``INSERT INTO <vec_tbl>(rowid, embedding)`` with an already-present
      rowid raises ``OperationalError: UNIQUE constraint failed on <tbl>
      primary key`` -- and so do ``INSERT OR REPLACE`` and ``INSERT OR
      IGNORE`` (vec0 enforces its PK inside the virtual-table module, where
      SQLite's conflict-resolution clauses can't reach). There is no
      replace/upsert idiom; DELETE + re-INSERT is the only update path.
    * ``DELETE FROM <vec_tbl> WHERE rowid = ?`` behaves like an ordinary
      table delete (rowcount 1), and deleting a rowid that isn't there is a
      silent no-op -- so the DELETE below needs no existence probe.
    * vec0 writes fully participate in SQLite transactions: the delete +
      insert can share the caller's transaction with the embeddings-row
      upsert (crash-atomic together), and a ROLLBACK undoes them cleanly.
      This helper therefore does NOT commit; the caller owns the boundary.
    * Cost at a 20k-row index: ~27 us per delete+insert+commit cycle vs
      ~3 us/row for the wholesale rebuild -- why single upserts pay this
      (microseconds, and the alternative is drift until the next ``cairn
      embed``) while bulk passes stay on ``rebuild_index``.

    The ``blob`` is the embeddings row's float32-LE ``vec`` verbatim --
    exactly what ``rebuild_index`` copies via ``INSERT ... SELECT``.

    Pure no-op (returns False, writes nothing) when the ANN backend is
    disabled (``CAIRN_ANN_BACKEND=off``), no vec0 table exists yet for
    ``model`` (only ``cairn embed``'s wholesale rebuild creates one), or the
    extension won't load -- an embeddings upsert must never fail because
    derived index state is absent. Best-effort on vec0 errors too (e.g. a
    dim change under a pinned model name): logs and returns False, leaving
    the drift for ``cairn doctor``'s staleness probe to flag and ``cairn
    embed`` to heal.
    """
    if not ann_backend_enabled() or not index_exists(conn, model):
        return False
    if not try_load(conn):
        return False
    table = _table_name(model)
    try:
        # Delete-first is required (no replace semantics) and safe: a missing
        # rowid deletes nothing, and a failed INSERT after a successful DELETE
        # leaves the vec row *gone* -- a recall miss the join drops cleanly,
        # never a stale vector paired with a reused rowid.
        conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))
        conn.execute(
            f"INSERT INTO {table}(rowid, embedding) VALUES (?, ?)", (rowid, blob)
        )
    except sqlite3.Error as e:
        _logger.warning(
            "vec0 sync failed for model '%s' rowid %s (%s); index left stale "
            "for `cairn doctor` to flag",
            model,
            rowid,
            e,
        )
        return False
    return True


def delete_index_rows(conn: sqlite3.Connection, model: str, rowids) -> int:
    """Delete the vec0 rows for embeddings rowids being removed (no commit).

    Deletion-side companion to :func:`sync_index_row`: whenever an
    ``embeddings`` row is deleted (the orphan reap today), its vec0 entry
    must go too, or the stale entry survives keyed on a rowid SQLite may
    later REUSE for a different embedding -- the ``ann_query`` join would
    then pair a fresh embeddings row with an unrelated vector (wrong
    results, strictly worse than the missing row it replaces). Deleting a
    rowid that isn't in the table is a silent no-op (spiked), so passing
    rowids that never had a vec entry is harmless.

    Pure no-op (returns 0) when the backend is off, no table exists for
    ``model``, or the extension won't load; best-effort on vec0 errors (logs,
    returns rows removed so far). Does NOT commit -- the caller owns the
    transaction so the embeddings DELETE and this land atomically together.
    """
    ids = list(rowids)
    if not ids or not ann_backend_enabled() or not index_exists(conn, model):
        return 0
    if not try_load(conn):
        return 0
    table = _table_name(model)
    removed = 0
    try:
        # Chunked IN-lists: stay well under any SQLite variable bound even
        # for a mass reap.
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            placeholders = ",".join("?" for _ in chunk)
            cur = conn.execute(
                f"DELETE FROM {table} WHERE rowid IN ({placeholders})", chunk
            )
            if cur.rowcount and cur.rowcount > 0:
                removed += cur.rowcount
    except sqlite3.Error as e:
        _logger.warning(
            "vec0 delete failed for model '%s' (%s); index left stale for "
            "`cairn doctor` to flag",
            model,
            e,
        )
    return removed


def ann_query(
    conn: sqlite3.Connection,
    model: str,
    q_blob: bytes,
    k: int,
    source: str = "embeddings",
) -> Optional[List[Tuple[str, float]]]:
    """ANN cosine search against the vec0 table for `model` over `source`.

    ``source`` (additive, D-007) selects the indexed table exactly as in
    :func:`rebuild_index`: the default joins back to ``embeddings`` through
    ``vec_<safe-model>``; ``"embeddings_mv"`` joins to the multi-vector
    table through ``vecmv_<safe-model>``. A symbol with several mv rows can
    therefore appear several times in the returned list (once per vector
    kind) -- dedup by max score is the caller's contract
    (``semantic._candidates_from_ann_hits``).

    Returns a list of ``(symbol_id, score)`` (score = 1 - cosine distance, so
    higher is more similar, matching the brute-force scan's score semantics)
    or ``None`` if the ANN path isn't usable right now (extension won't load,
    or no index built yet for this model) -- callers must fall back to the
    brute-force scan on ``None``, not treat it as "zero results".
    """
    if not try_load(conn):
        return None
    table = _table_name(model, source)
    # The default-source probe must stay on the exact 2-arg index_exists
    # call shape: concurrency tests monkeypatch it as `lambda conn, model:
    # ...` to force the vec0 MATCH path, and cli/system.py +
    # mcp_server/_server_core.py call it with two args. Only the opt-in mv
    # leg (D-007) passes its source through.
    if source == "embeddings":
        have_index = index_exists(conn, model)
    else:
        have_index = index_exists(conn, model, source)
    if not have_index:
        # No vec0 index for this model (typically: embeddings were built but
        # `cairn embed` hasn't run since, or the DB predates sqlite-vec). This
        # is a recoverable setup state, not a crash -- but it silently costs
        # the native path on EVERY query, so surface it once with the spec's
        # `no_index` reason (spec §6.4) instead of returning None invisibly.
        warn_ann_fallback_once(_logger, context="ann_index.ann_query", reason="no index built")
        return None
    try:
        rows = conn.execute(
            f"SELECT e.symbol_id AS symbol_id, v.distance AS distance "
            f"FROM {table} v JOIN {source} e ON e.rowid = v.rowid "
            f"WHERE v.embedding MATCH ? AND v.k = ? "
            "ORDER BY v.distance",
            (q_blob, k),
        ).fetchall()
    except sqlite3.OperationalError as e:
        # Discriminate before calling this contention (mirrors schema.py's
        # duplicate-column discrimination): only "locked"/"busy" errors are a
        # real cross-process lock event. Anything else (FTS/vec0 syntax error,
        # no-such-table racing a rebuild, index corruption) is a *query*
        # failure -- misattributing it to contention would send doctor's
        # concurrency check chasing a phantom lock. The spec's `query_error`
        # reason (§6.4) carries it durably instead.
        if _is_lock_contention(e):
            note_contention("ann_index.ann_query", error=e)
        else:
            warn_ann_fallback_once(
                _logger, context="ann_index.ann_query", reason="query error"
            )
        return None
    return [(r["symbol_id"], 1.0 - float(r["distance"])) for r in rows]


def index_row_count(conn: sqlite3.Connection, model: str) -> Optional[int]:
    """Row count of the vec0 table for `model`; None when no index exists.

    Companion to :func:`index_exists` for staleness probing (`cairn doctor`):
    the index is rebuilt wholesale from ``embeddings``, so a row-count
    mismatch between the two means embeddings changed since the last rebuild
    (incremental syncs add embeddings without touching the vec0 table).
    Defensive: any read failure returns None rather than raising.
    """
    if not index_exists(conn, model):
        return None
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {_table_name(model)}").fetchone()[0])
    except sqlite3.Error:
        return None
