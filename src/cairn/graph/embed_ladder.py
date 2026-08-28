"""Embedding parity sampler over stored chunks (FR-005).

``check_parity`` samples stored embedding rows under a model stamp, re-embeds
the sampled chunk texts through an embed client (default: the server client
``embeddings._embed_server``), and compares each returned vector with the
stored float32-LE blob by cosine. Shared by the embed writers' alias
preflight, ``cairn doctor``, and the dashboard parity action; the availability
ladder and its FR-013 degradation notification fan-out live in this module too.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import struct
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from . import embeddings

# Mean-cosine parity gate (D-007): one constant shared by the alias preflight,
# the ladder rungs, and doctor. Deliberately not env-overridable.
PARITY_GATE = 0.98

# Upper bound on stored chunks sampled per check (FR-005).
SAMPLE_LIMIT = 16


@dataclass
class ParityResult:
    """Outcome of one parity check.

    ``mean_cosine`` is None when no cosine was measurable (vacuous pass, dim
    mismatch, embed-count mismatch); every failure carries its measured value
    either in ``mean_cosine`` or verbatim in ``reason``.
    """

    sampled: int
    mean_cosine: Optional[float]
    dim_match: bool
    passed: bool
    reason: str


def check_parity(
    conn: sqlite3.Connection,
    stamp: str,
    embed_fn: Optional[
        Callable[[Sequence[str]], Tuple[List[bytes], int]]
    ] = None,
    sample_limit: int = SAMPLE_LIMIT,
) -> ParityResult:
    """Sample stored chunks under ``stamp`` and parity-check them.

    ``embed_fn(texts) -> (float32-LE blobs, dim)`` defaults to the T002
    server client. Contract:

    * zero stored rows under the stamp -> vacuous pass (``sampled=0``,
      ``mean_cosine=None``); ``embed_fn`` is never called (FR-005).
    * a served/stored dim mismatch fails naming both measured dims.
    * otherwise pass iff mean pairwise cosine >= PARITY_GATE; failures
      report the measured mean.

    Sampling is deterministic: first ``sample_limit`` rows by rowid.
    """
    if embed_fn is None:
        embed_fn = embeddings._embed_server

    rows = conn.execute(
        "SELECT chunk, vec FROM embeddings WHERE model = ? "
        "ORDER BY rowid LIMIT ?",
        (stamp, sample_limit),
    ).fetchall()
    if not rows:
        return ParityResult(0, None, True, True, "vacuous_no_stored_rows")

    blobs, _dim = embed_fn([row[0] for row in rows])
    if len(blobs) != len(rows):
        return ParityResult(
            len(rows),
            None,
            False,
            False,
            f"embed_count_mismatch texts={len(rows)} blobs={len(blobs)}",
        )

    cosines: List[float] = []
    for row, blob in zip(rows, blobs):
        stored = _decode_f32(row[1])
        served = _decode_f32(blob)
        if len(served) != len(stored):
            return ParityResult(
                len(rows),
                None,
                False,
                False,
                f"dim_mismatch stored={len(stored)} server={len(served)}",
            )
        cosines.append(_cosine(stored, served))

    mean = sum(cosines) / len(cosines)
    if mean >= PARITY_GATE:
        return ParityResult(len(rows), mean, True, True, "parity_ok")
    return ParityResult(
        len(rows),
        mean,
        True,
        False,
        f"mean_cosine {mean:.4f} below gate {PARITY_GATE}",
    )


def _decode_f32(blob: bytes) -> List[float]:
    """float32 little-endian BLOB -> float list (trailing bytes dropped)."""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob[: n * 4]))


def _cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity; a zero-norm vector scores 0.0 (no divide-by-zero)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Availability ladder (FR-012 / spec A2.7). Rung 1 adopts a parity-passing
# same-server candidate session-scoped through the alias mechanics; rung 2
# falls back to a cached local model on the same parity gate; rung 3 is the
# terminal BM25/FTS5-hybrid-only state. Hash is never a rung (D-003).
# Evaluated at most once per process per backend-state.
# ---------------------------------------------------------------------------


@dataclass
class LadderState:
    """Verdict of one ladder evaluation.

    ``rung`` is 1, 2, or 3. ``reason`` is the adoption kind for rungs 1-2
    (``fallback_session_alias`` / ``fallback_local``) and the trigger for
    rung 3 (``server_down`` | ``model_missing`` | ``parity_fail``) — always
    one of ``telemetry.events.EMBED_SERVER_REASONS``. ``adopted_model`` is
    the adopted model id (rungs 1-2), else None. ``active`` turns False
    when a later healthy evaluation supersedes the state.
    """

    rung: int
    reason: str
    detail: str
    adopted_model: Optional[str]
    active: bool


_LADDER_CACHE: dict = {"state": None}

# Serializes the check-then-act on _LADDER_CACHE (double-checked locking: an
# unlocked fast-path read, then re-check + evaluate under the lock) and the
# membership-check+add on _DEGRADATION_NOTIFIED below. Without it, concurrent
# MCP threads each race past the empty-cache check and double-run evaluation
# (up to 16 embeds per candidate) and double-notify. Mirrors embeddings'
# _ALIAS_GATE_LOCK discipline. notify_degradation runs OUTSIDE the lock so
# telemetry emission never holds it.
_LADDER_LOCK = threading.Lock()

# Rung-3 detail strings: short, machine-actionable, one per trigger reason.
# Each names the degraded state AND one actionable remediation (FR-013).
_RUNG3_DETAIL = {
    "server_down": (
        "embedding server unreachable; dense leg off, "
        "queries ride the bm25/fts5 hybrid; check server health and "
        "CAIRN_EMBED_BASE_URL (cairn doctor probes it)"
    ),
    "model_missing": (
        "configured model not served; dense leg off, "
        "queries ride the bm25/fts5 hybrid; serve the model or set "
        "CAIRN_EMBED_SERVER_MODEL to a served id"
    ),
    "parity_fail": (
        "no parity-verified replacement; re-embed required with `cairn embed`, "
        "queries ride the bm25/fts5 hybrid"
    ),
}


def ladder_state() -> Optional[LadderState]:
    """The cached ladder verdict, or None when never evaluated or healthy."""
    return _LADDER_CACHE["state"]


def set_session_stamp(stamp: Optional[str]) -> None:
    """Pin the rung-1 alias binding: the stamp ``current_model()`` serves.

    Holds the STORED corpus stamp (not the candidate's derived stamp) so
    reads and writes keep hitting the existing rows with zero re-embed;
    the adopted request model id moves via :func:`set_session_server_model`.
    """
    embeddings._SESSION_STAMP_OVERRIDE = stamp


def set_session_server_model(model_id: Optional[str]) -> None:
    """Pin the rung-1 adopted model id used by server embed/query requests."""
    embeddings._SESSION_SERVER_MODEL = model_id


def set_session_backend(backend: Optional[str]) -> None:
    """Pin the rung-2 session backend override (only ever 'local')."""
    embeddings._SESSION_BACKEND_OVERRIDE = backend
    embeddings._EFFECTIVE_BACKEND_CACHE["effective"] = None


def reset_cache() -> None:
    """Drop the cached verdict, every session override, and the notify
    once-set (the next degradation logs and emits again).

    Reached through ``embeddings.reset_backend_cache()`` (and directly in
    tests); also the re-evaluation seam for doctor and ``cairn embed``.
    """
    with _LADDER_LOCK:
        _LADDER_CACHE["state"] = None
        _DEGRADATION_NOTIFIED.clear()
    set_session_stamp(None)
    set_session_server_model(None)
    set_session_backend(None)


# ---------------------------------------------------------------------------
# FR-013 notification fan-out (D-010). One unit per (process, reason):
# a user-facing warn-once line on the shared 'cairn' logger plus one
# EMBED_SERVER_DEGRADED event (host+model payload only, spec A2.6). The
# logger line is deliberately NOT gated on telemetry: events.warn_once
# refuses under CAIRN_TELEMETRY=off, which would silence US3 AC3's
# unconditional surface, so this module keeps a private once-set and leaves
# the telemetry gates to emit() itself.
# ---------------------------------------------------------------------------

logger = logging.getLogger("cairn")

_DEGRADATION_NOTIFIED: set = set()

# Message body when notify_degradation is called without a LadderState
# detail; every entry names one actionable remediation.
_DEGRADATION_HINT = {
    **_RUNG3_DETAIL,
    "fallback_session_alias": (
        "candidate model adopted for this session after a parity pass; "
        "make permanent: cairn embed --adopt-server-model <model-id>"
    ),
    "fallback_local": (
        "local model adopted for this session after a parity pass; "
        "session-scoped, reverts on restart"
    ),
    "hybrid_only": (
        "dense leg off, queries ride the bm25/fts5 hybrid; restore the "
        "embedding server to bring it back"
    ),
}


def _degradation_host() -> str:
    """Netloc of the effective server base URL (host-only payload, A2.6)."""
    try:
        return urlsplit(embeddings._server_base_url()).netloc or "unresolved"
    except RuntimeError:
        return "unresolved"


def notify_degradation(reason: str, detail: str = "") -> None:
    """Fan out one degradation notification, once per (process, reason).

    ``reason`` is a ``telemetry.events.EMBED_SERVER_REASONS`` member;
    ``detail`` is the caller's actionable remediation (defaults to a
    per-reason hint). Fires as one unit: the warn-once logger line (never
    telemetry-gated, US3 AC3) and one ``EMBED_SERVER_DEGRADED`` event whose
    attrs are reason + host + model only -- never request bodies or code
    text (spec A2.6); telemetry-off/read-only suppress the event, never the
    line. Never raises.
    """
    with _LADDER_LOCK:
        if reason in _DEGRADATION_NOTIFIED:
            return
        _DEGRADATION_NOTIFIED.add(reason)
    try:
        from ..telemetry import EMBED_SERVER_DEGRADED
        from ..telemetry import emit as _emit

        _emit(
            EMBED_SERVER_DEGRADED,
            reason=reason,
            host=_degradation_host(),
            model=embeddings._server_model(),
        )
    except Exception:
        pass
    logger.warning(
        "embedding backend degraded (%s): %s",
        reason,
        detail or _DEGRADATION_HINT.get(reason, "degraded; run `cairn doctor`"),
    )


def degradation_active() -> bool:
    """True when a ladder degradation is active right now (doctor, FR-013)."""
    state = _LADDER_CACHE["state"]
    return state is not None and state.active


def _degradation_line(prefix: str) -> str:
    """The shared active-state text: rung + reason + remediation, or ''."""
    state = _LADDER_CACHE["state"]
    if state is None or not state.active:
        return ""
    body = state.detail or _DEGRADATION_HINT.get(state.reason, "")
    return f"{prefix}rung {state.rung} ({state.reason}): {body}"


def degradation_footnote() -> str:
    """The degradation footnote MCP tool results append (FR-013).

    Zero side effects; "" when no degradation is active, else one line
    naming the rung, reason, and remediation.
    """
    return _degradation_line("degraded: ")


def degradation_banner() -> str:
    """The dashboard degradation banner text (FR-013).

    Zero side effects; "" when no degradation is active, else one line
    naming the rung, reason, and remediation.
    """
    return _degradation_line("Embedding backend degraded -- ")


def evaluate_ladder(
    conn: Optional[sqlite3.Connection] = None,
    force: bool = False,
) -> Optional[LadderState]:
    """Evaluate the FR-012 fallback ladder.

    Consumers call this when the server probe fails or an embed error
    occurs; the verdict is cached for the process per backend-state and
    ``force=True`` re-evaluates (doctor / embed re-verification). No-ops —
    returns None with nothing cached — unless the effective backend is the
    server family. A healthy re-evaluation supersedes any active state
    (``active=False``, cache back to None). A state-setting evaluation
    notifies once at the end via :func:`notify_degradation` (FR-013), so a
    rung adoption is never silent.

    Thread-safe: the cache check-then-act is double-checked under
    ``_LADDER_LOCK``, so N concurrent callers produce exactly one
    ``_evaluate`` pass and one notification per reason.
    """
    cached = _LADDER_CACHE["state"]  # fast path: unlocked read
    if cached is not None and not force:
        return cached
    with _LADDER_LOCK:
        cached = _LADDER_CACHE["state"]  # re-check under the lock
        if cached is not None and not force:
            return cached
        if embeddings._effective_backend() != "server":
            return None
        state = _evaluate(conn)
        if state is None:
            if cached is not None:
                cached.active = False
            _LADDER_CACHE["state"] = None
            return None
        _LADDER_CACHE["state"] = state
    # Outside the lock: telemetry emission and logging must not hold it.
    notify_degradation(state.reason, state.detail)
    return state


def _evaluate(conn: Optional[sqlite3.Connection]) -> Optional[LadderState]:
    """One uncached ladder pass. None = server healthy (configured model
    is listed): any active state is superseded by the caller."""
    configured = embeddings._server_model()
    ids = _fetch_model_listing()
    if ids is not None and configured in ids:
        return None
    trigger = "server_down" if ids is None else "model_missing"
    parity_failed = False
    stamp = _stored_stamp()

    # Rung 1: same-server candidates. Requires a conn WITH stored rows under
    # the current stamp — parity against nothing proves nothing, so the rung
    # declines rather than trusting an unverified producer (D-009).
    if ids and conn is not None and stamp and _has_stored_rows(conn, stamp):
        for cid in ids:
            if cid == configured:
                continue
            try:
                result = check_parity(conn, stamp, embed_fn=_server_embed_fn(cid))
            except Exception:
                continue  # candidate unreachable/rejected — try the next
            if result.passed:
                set_session_stamp(stamp)
                set_session_server_model(cid)
                detail = (
                    f"adopted server model '{cid}' for this session after "
                    f"parity pass; make permanent: "
                    f"cairn embed --adopt-server-model {cid}"
                )
                return LadderState(1, "fallback_session_alias", detail, cid, True)
            parity_failed = True

    # Rung 2: cached local model on the same parity gate.
    local = _try_rung2(conn, stamp)
    if local is not None:
        set_session_backend("local")
        detail = (
            f"local model '{local}' adopted for this session after parity pass"
        )
        return LadderState(2, "fallback_local", detail, local, True)

    reason = "parity_fail" if parity_failed else trigger
    return LadderState(3, reason, _RUNG3_DETAIL[reason], None, True)


def _try_rung2(
    conn: Optional[sqlite3.Connection], stamp: Optional[str]
) -> Optional[str]:
    """The local model id rung 2 can adopt, or None.

    Gates: sentence-transformers importable, weights cached, and parity
    proven against stored rows (needs a conn with rows — same rule as
    rung 1).
    """
    if conn is None or stamp is None or not _sentence_transformers_available():
        return None
    model = _local_default_model()
    if not embeddings.model_is_cached(model):
        return None
    if not _has_stored_rows(conn, stamp):
        return None
    try:
        result = check_parity(conn, stamp, embed_fn=_local_embed_fn(model))
    except Exception:
        return None
    return model if result.passed else None


def _stored_stamp() -> Optional[str]:
    """The stamp parity samples against, or None when it cannot resolve
    (e.g. bare 'server' without CAIRN_EMBED_BASE_URL)."""
    try:
        return embeddings.current_model()
    except RuntimeError:
        return None


def _has_stored_rows(conn: sqlite3.Connection, stamp: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM embeddings WHERE model = ? LIMIT 1", (stamp,)
    ).fetchone()
    return row is not None


def _sentence_transformers_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _config_value(name: str) -> str:
    """One CAIRN_EMBED_* knob through the D-008 choke point (env > file),
    stripped, '' when unset.

    Lazy import: embeddings imports this module at load time, so the
    reverse ``_config_or_env`` reference can only resolve at call time
    (module-cycle discipline). File-persisted keys must reach the ladder's
    probes exactly as they reach the main embed path, else parity checks
    false-fail against authenticated/slow servers or the wrong local model.
    """
    from .embeddings import _config_or_env

    return (_config_or_env(name) or "").strip()


def _local_default_model() -> str:
    """The local-backend model rung 2 would fall back to (code corpus)."""
    return _config_value("CAIRN_EMBED_LOCAL_MODEL") or embeddings.DEFAULT_LOCAL_MODEL


def _fetch_model_listing() -> Optional[List[str]]:
    """GET {base}/models -> listed model ids, or None on any failure.

    Same base URL / bearer / 2 s timeout discipline as the FR-002 probe;
    never raises.
    """
    import http.client
    import json
    import urllib.request

    try:
        base = embeddings._server_base_url().rstrip("/")
    except RuntimeError:
        return None
    headers = {}
    api_key = _config_value("CAIRN_EMBED_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        req = urllib.request.Request(f"{base}/models", headers=headers)
        with urllib.request.urlopen(req, timeout=embeddings._PROBE_TIMEOUT_S) as resp:
            if resp.status != 200:
                return None
            body = resp.read()
    except (OSError, http.client.HTTPException, ValueError):
        return None
    try:
        listing = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    data = listing.get("data") if isinstance(listing, dict) else None
    if not isinstance(data, list):
        return None
    ids = [
        entry["id"]
        for entry in data
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    ]
    # Servers may list one id several times (e.g. one entry per
    # quantization); dedupe preserving order so each candidate parity
    # embed runs exactly once.
    return list(dict.fromkeys(ids))


def _embed_with_model(texts: Sequence[str], model_id: str) -> Tuple[List[bytes], int]:
    """One /v1/embeddings POST pinned to ``model_id`` (rung-1 parity).

    Single attempt, no retry ladder — a failing candidate just declines.
    """
    import json
    import urllib.request

    base = embeddings._server_base_url().rstrip("/")
    timeout_raw = _config_value("CAIRN_EMBED_TIMEOUT")
    timeout = float(timeout_raw) if timeout_raw else 30.0
    headers = {"Content-Type": "application/json"}
    api_key = _config_value("CAIRN_EMBED_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = json.dumps({"model": model_id, "input": list(texts)}).encode("utf-8")
    req = urllib.request.Request(f"{base}/embeddings", data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    data = body["data"]
    data.sort(key=lambda d: d["index"])  # preserve input order
    blobs = [embeddings._floats_to_blob(d["embedding"]) for d in data]
    return blobs, len(data[0]["embedding"])


def _server_embed_fn(model_id: str) -> Callable[[Sequence[str]], Tuple[List[bytes], int]]:
    def fn(texts: Sequence[str]) -> Tuple[List[bytes], int]:
        return _embed_with_model(texts, model_id)

    return fn


def _local_embed_fn(model_name: str) -> Callable[[Sequence[str]], Tuple[List[bytes], int]]:
    """Embed via the named local model (bypasses current_model(), which
    still resolves to the server arm during rung-2 evaluation)."""
    def fn(texts: Sequence[str]) -> Tuple[List[bytes], int]:
        model = embeddings._get_local_model(model_name)
        vecs = model.encode(
            list(texts), normalize_embeddings=True, show_progress_bar=False
        )
        blobs = [embeddings._vec_to_blob(v) for v in vecs]
        return blobs, int(vecs.shape[1])

    return fn
