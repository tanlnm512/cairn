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
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_QUEUE: collections.deque = collections.deque(maxlen=500)
_LOCK = threading.Lock()
_FLUSHER_STARTED = False
_FLUSH_INTERVAL = 15.0  # seconds
# Consecutive flush-failure count (mutated only by the single flusher thread).
# Failures are expected to be transient (lock contention, a model hiccup), so
# the batch is retried indefinitely rather than dropped -- but a CHRONIC
# failure (e.g. a broken embed model) would otherwise retry invisibly at debug
# level every 15s forever. After _WARN_AFTER consecutive failures we escalate
# to WARNING so the broken flusher is observable. Reset to 0 on the first
# success.
_FAILURES = 0
_WARN_AFTER = 4
# Whether the embed_flush_stalled telemetry event already fired for the
# CURRENT failure streak (F6): the durable signal is emitted once per streak,
# not once per failing 15s tick, mirroring the once-per-degradation doctrine.
# Reset together with _FAILURES on the first successful flush, so a later
# separate outage emits again.
_STALL_EVENT_SENT = False


def _failures_bucket(n: int) -> str:
    """Collapse a consecutive-failure count into a bounded cardinality bucket.

    Buckets are the telemetry cardinality mechanism for numeric attrs (spec
    §6.4): an unbucketed count would grow a distinct value per tick. The lower
    edge is the escalation threshold (_WARN_AFTER) -- the event only fires at
    or above it.
    """
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
        # Lock contention or a transient error -- leave the batch queued for
        # the next flush attempt rather than dropping it (a poison concept_id
        # is already skipped per-cid inside embed_memory_concepts, so a failure
        # here is environmental, not a bad cid). Escalate to WARNING once the
        # failure is chronic so a broken embed model doesn't fail silently,
        # and record one durable embed_flush_stalled event per failure streak
        # (F6) so `cairn metrics`/doctor can see what a WARNING log can't.
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
