"""Background buffered embedding for captured/evolved memory concepts."""
from __future__ import annotations

import atexit
import collections
import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_QUEUE: collections.deque = collections.deque(maxlen=500)
_LOCK = threading.Lock()
_FLUSHER_STARTED = False
_FLUSH_INTERVAL = 15.0  # seconds
# Consecutive flush failures, retried indefinitely; after _WARN_AFTER they
# escalate to WARNING so a chronic failure stays observable.
_FAILURES = 0
_WARN_AFTER = 4
# embed_flush_stalled fires once per failure streak, not per tick, and resets
# with _FAILURES on the first successful flush.
_STALL_EVENT_SENT = False


def _failures_bucket(n: int) -> str:
    """Collapse a consecutive-failure count into a bounded-cardinality bucket."""
    for bound, label in ((10, "4-10"), (100, "11-100")):
        if n <= bound:
            return label
    return ">100"


_conn_factory: Optional[Callable[[], "Any"]] = None
_bundle_factory: Optional[Callable[[], "Any"]] = None


def configure(conn_factory: Callable[[], "Any"], bundle_factory: Callable[[], "Any"]) -> None:
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
    global _FAILURES, _STALL_EVENT_SENT
    try:
        conn = _conn_factory()
        bundle = _bundle_factory()
        emb.embed_memory_concepts(conn, bundle, batch)
        conn.commit()
    except Exception:
        # Environmental failure: keep the batch queued for the next attempt,
        # escalate once chronic, and emit one durable stalled event per streak.
        _FAILURES += 1
        if _FAILURES >= _WARN_AFTER:
            logger.warning(
                "memory embed flush has failed %d consecutive times; "
                "%d concept(s) remain queued. Check the embed model / DB.",
                _FAILURES, len(batch), exc_info=True,
            )
            if not _STALL_EVENT_SENT:
                _STALL_EVENT_SENT = True
                try:
                    from cairn.telemetry import EMBED_FLUSH_STALLED, emit as _emit

                    _emit(EMBED_FLUSH_STALLED, failures=_failures_bucket(_FAILURES))
                except Exception:
                    pass
        else:
            logger.debug(
                "memory embed flush failed (%d); %d concept(s) remain queued",
                _FAILURES, len(batch), exc_info=True,
            )
        return
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    _FAILURES = 0
    _STALL_EVENT_SENT = False
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
