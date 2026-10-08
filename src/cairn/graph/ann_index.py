"""Native ANN index for semantic_search, via the sqlite-vec extension."""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import List, Optional, Tuple

from .schema import note_contention, _is_lock_contention

_logger = logging.getLogger(__name__)


def configured_backend() -> str:
    """The resolved ``CAIRN_ANN_BACKEND`` value; an empty env value stays
    empty (an explicit opt-out, not the default)."""
    import os

    return os.environ.get("CAIRN_ANN_BACKEND", "sqlite-vec").strip().lower()


def ann_backend_enabled() -> bool:
    val = configured_backend()
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
    """Emit one ANN-fallback warning per process."""
    global _ANN_FALLBACK_WARNED
    if _ANN_FALLBACK_WARNED:
        return

    val = configured_backend()
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
    # Durable event with an enum reason for doctor/metrics aggregation; the
    # WARNING below keeps the human-readable detail. Map the human reason to
    # a bounded enum so the attr value domain is fixed.
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


# Each embedding source owns a per-model rowid-keyed vec0 index.
_SOURCE_PREFIX = {"embeddings": "vec_", "embeddings_mv": "vecmv_"}


def _table_name(model: str, source: str = "embeddings") -> str:
    """Sanitize a model name into a valid SQLite identifier for ``source``."""
    if source not in _SOURCE_PREFIX:
        raise ValueError(f"unknown source: {source!r}")
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", model)
    return f"{_SOURCE_PREFIX[source]}{safe}"


def try_load(conn: sqlite3.Connection) -> bool:
    """Attempt to load the sqlite-vec extension into `conn`. Never raises."""
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
    """Whether the vec0 table for ``model`` (and ``source``) exists."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_table_name(model, source),),
    ).fetchone()
    return row is not None


def rebuild_index(conn: sqlite3.Connection, model: str, source: str = "embeddings") -> dict:
    """Wholesale rebuild of the vec0 table for `model` from `source`."""
    # Validate source BEFORE it reaches any SQL (closed set -- see
    # _table_name; a near-miss like "embeddings_mv " must not execute).
    table = _table_name(model, source)
    if not try_load(conn):
        return {"model": model, "indexed": 0, "skipped": "sqlite-vec unavailable"}

    row = conn.execute(
        f"SELECT dim FROM {source} WHERE model = ? LIMIT 1", (model,)
    ).fetchone()
    if row is None:
        # Keep the base-source reason string byte-identical
        # (it is user-visible CLI output and asserted in tests).
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
    # Source rowids only need to stay stable within one rebuild generation.
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
    """Incrementally sync one vec0 row to an embeddings upsert (no commit)."""
    if not ann_backend_enabled() or not index_exists(conn, model):
        return False
    if not try_load(conn):
        return False
    table = _table_name(model)
    try:
        # vec0 has no replace semantics; delete first and reinsert.
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
    """Delete the vec0 rows for embeddings rowids being removed (no commit)."""
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
    """ANN cosine search against the vec0 table for `model` over `source`."""
    if not try_load(conn):
        return None
    table = _table_name(model, source)
    if not index_exists(conn, model, source):
        # A missing index is recoverable but must surface once.
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
        # Only locked or busy errors represent cross-process contention.
        if _is_lock_contention(e):
            note_contention("ann_index.ann_query", error=e)
        else:
            warn_ann_fallback_once(
                _logger, context="ann_index.ann_query", reason="query error"
            )
        return None
    return [(r["symbol_id"], 1.0 - float(r["distance"])) for r in rows]


def index_row_count(conn: sqlite3.Connection, model: str) -> Optional[int]:
    """Row count of the vec0 table for `model`; None when no index exists."""
    if not index_exists(conn, model):
        return None
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {_table_name(model)}").fetchone()[0])
    except sqlite3.Error:
        return None
