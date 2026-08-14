"""Native ANN index for semantic_search, via the sqlite-vec extension.

`sqlite-vec` (https://github.com/asg017/sqlite-vec) provides a `vec0` virtual
table in the *same* `.db` file as `embeddings`, loaded as a SQLite extension.
Keeping vectors in the same file avoids the crash-consistency hazard of a
sidecar index whose writes can fall out of the SQLite transaction.

This module does a wholesale rebuild from the `embeddings` table rather than
keeping the vec0 table incrementally in sync with individual `INSERT OR
REPLACE`s (vec0 has no "replace" semantics; embeddings' hidden rowid can be
reused by delete+reinsert).

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
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import List, Optional, Tuple

from .schema import note_contention

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


def _table_name(model: str) -> str:
    """Sanitize a model name into a valid SQLite identifier.

    Model names come from HF repo ids ('all-MiniLM-L6-v2',
    'jinaai/jina-embeddings-v2-base-code') or the hash/openai stamps -- none
    of those are safe as a bare identifier, so this is NOT just cosmetic.
    """
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", model)
    return f"vec_{safe}"


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


def index_exists(conn: sqlite3.Connection, model: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_table_name(model),),
    ).fetchone()
    return row is not None


def rebuild_index(conn: sqlite3.Connection, model: str) -> dict:
    """Wholesale rebuild of the vec0 table for `model` from `embeddings`.

    Returns {"model", "indexed", "dim"} or {"model", "indexed": 0, "skipped":
    reason} if sqlite-vec can't be loaded or there's nothing to index. Safe
    to call repeatedly (drops and recreates), and safe to call from a process
    that hasn't loaded the extension yet -- loads it itself via try_load().
    """
    if not try_load(conn):
        return {"model": model, "indexed": 0, "skipped": "sqlite-vec unavailable"}

    row = conn.execute(
        "SELECT dim FROM embeddings WHERE model = ? LIMIT 1", (model,)
    ).fetchone()
    if row is None:
        return {"model": model, "indexed": 0, "skipped": "no embeddings for model"}
    dim = row["dim"] if hasattr(row, "keys") else row[0]

    table = _table_name(model)
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(
        f"CREATE VIRTUAL TABLE {table} USING vec0(embedding float[{int(dim)}] distance_metric=cosine)"
    )
    # rowid here is embeddings' own hidden rowid -- stable for the lifetime of
    # this rebuild pass, which is all sync_index/ann_query need (they always
    # join back to embeddings.rowid within the same rebuild generation).
    conn.execute(
        f"INSERT INTO {table}(rowid, embedding) "
        "SELECT rowid, vec FROM embeddings WHERE model = ?",
        (model,),
    )
    conn.commit()
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return {"model": model, "indexed": count, "dim": dim}


def ann_query(
    conn: sqlite3.Connection, model: str, q_blob: bytes, k: int
) -> Optional[List[Tuple[str, float]]]:
    """ANN cosine search against the vec0 table for `model`.

    Returns a list of ``(symbol_id, score)`` (score = 1 - cosine distance, so
    higher is more similar, matching the brute-force scan's score semantics)
    or ``None`` if the ANN path isn't usable right now (extension won't load,
    or no index built yet for this model) -- callers must fall back to the
    brute-force scan on ``None``, not treat it as "zero results".
    """
    if not try_load(conn):
        return None
    table = _table_name(model)
    if not index_exists(conn, model):
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
            f"FROM {table} v JOIN embeddings e ON e.rowid = v.rowid "
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
        msg = str(e).lower()
        if "locked" in msg or "busy" in msg:
            note_contention("ann_index.ann_query")
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
