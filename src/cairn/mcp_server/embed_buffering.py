"""Background buffered embedding for captured/evolved memory concepts.

Embedding a memory needs a genuinely writable SQL connection (an INSERT into
memory_embeddings), which would contend with a concurrent `cairn build`/
`update` if done synchronously inside the MCP tool call under the read-only
SSE daemon. Buffering + a background flush thread (same pattern as
metric_buffering.py) removes that write from the tool call's hot path:
capture/evolve enqueue a concept_id, a flusher thread drains the queue on its
own writable connection periodically, retrying on failure instead of raising.

Unlike metric_buffering, this does NOT skip under CAIRN_READ_ONLY -- a
captured memory should still get embedded on the read-only SSE daemon (that
is the deployment mode this exists for); the injected conn_factory is
responsible for returning a writable connection regardless of the server's
read-only mode (see server.py's wiring via `_rw_conn`).
"""
from __future__ import annotations

import atexit
import collections
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_QUEUE: collections.deque = collections.deque(maxlen=500)
_LOCK = threading.Lock()
_FLUSHER_STARTED = False
_FLUSH_INTERVAL = 15.0  # seconds

_conn_factory: Optional[Callable[[], "object"]] = None
_bundle_factory: Optional[Callable[[], "object"]] = None


def configure(conn_factory: Callable[[], "object"], bundle_factory: Callable[[], "object"]) -> None:
    """Inject the writable-conn and bundle factories. Called once at server boot."""
    global _conn_factory, _bundle_factory
    _conn_factory = conn_factory
    _bundle_factory = bundle_factory


def enqueue(concept_id: str) -> None:
    """Queue a memory concept_id for (re)embedding. Non-blocking, no I/O."""
    if not concept_id:
        return
    with _LOCK:
        _QUEUE.append(concept_id)
    _start_flusher()


def _flush() -> None:
    with _LOCK:
        if not _QUEUE:
            return
        batch = list(_QUEUE)
    if _conn_factory is None or _bundle_factory is None:
        return
    from cairn.graph import embeddings as emb

    conn = None
    try:
        conn = _conn_factory()
        bundle = _bundle_factory()
        emb.embed_memory_concepts(conn, bundle, batch)
        conn.commit()
    except Exception:
        # Lock contention or a transient error -- leave the batch queued for
        # the next flush attempt rather than dropping it.
        logger.debug("memory embed flush failed; %d concept(s) remain queued", len(batch), exc_info=True)
        return
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    with _LOCK:
        for cid in batch:
            try:
                _QUEUE.remove(cid)
            except ValueError:
                pass


def _start_flusher() -> None:
    global _FLUSHER_STARTED
    if _FLUSHER_STARTED:
        return
    with _LOCK:
        if _FLUSHER_STARTED:
            return
        _FLUSHER_STARTED = True

    def _loop():
        while True:
            time.sleep(_FLUSH_INTERVAL)
            _flush()

    t = threading.Thread(target=_loop, name="cairn-memory-embed-flusher", daemon=True)
    t.start()
    atexit.register(_flush)
