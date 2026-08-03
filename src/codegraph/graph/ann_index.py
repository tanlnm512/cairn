"""Native ANN index for semantic_search, via the sqlite-vec extension.

`sqlite-vec` (https://github.com/asg017/sqlite-vec) provides a `vec0` virtual
table living in the *same* `.db` file as `embeddings`, loaded as a normal
SQLite extension (`conn.enable_load_extension` + `sqlite_vec.load(conn)`).
Keeping vectors in the same file as the rows they reference avoids the
crash-consistency hazard of a sidecar index whose writes can fall out of the
SQLite transaction.

Scope decision: rather than trying to keep the vec0 table incrementally in
sync with individual `INSERT OR REPLACE`s on `embeddings` (fragile --
`embeddings`'s hidden rowid can be reused by `INSERT OR REPLACE`'s
delete+reinsert, and vec0 has no equivalent "replace" semantics), this module
does a wholesale rebuild from the `embeddings` table. That is cheap enough at
codegraph's scale (one bulk SELECT + bulk INSERT) to run after every `cg embed`.

On by default: `CODEGRAPH_ANN_BACKEND` unset resolves to `sqlite-vec`. Set it
to `off` (or any value other than `sqlite-vec`) to force the brute-force
cosine scan instead. Any load failure (extension loading disabled in this
Python build, package not installed, wheel unavailable for the platform, etc.)
degrades to the brute-force scan exactly like a missing table would.
"""
from __future__ import annotations

import re
import sqlite3
from typing import List, Optional, Tuple


def ann_backend_enabled() -> bool:
    import os

    val = os.environ.get("CODEGRAPH_ANN_BACKEND", "sqlite-vec").strip().lower()
    if val != "sqlite-vec":
        # Explicit opt-out (e.g. "off"): stay disabled regardless of whether
        # sqlite_vec happens to be importable (e.g. pulled in transitively).
        return False
    try:
        import sqlite_vec  # noqa: F401
        return True
    except ImportError:
        return False


def install_hint() -> str:
    return (
        "The sqlite-vec ANN backend requires the 'sqlite-vec' package "
        "(pip install sqlite-vec) and a Python sqlite3 build with extension "
        "loading enabled. Falling back to the brute-force cosine scan."
    )


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
    except Exception:
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
        return None
    try:
        rows = conn.execute(
            f"SELECT e.symbol_id AS symbol_id, v.distance AS distance "
            f"FROM {table} v JOIN embeddings e ON e.rowid = v.rowid "
            f"WHERE v.embedding MATCH ? AND v.k = ? "
            "ORDER BY v.distance",
            (q_blob, k),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    return [(r["symbol_id"], 1.0 - float(r["distance"])) for r in rows]
