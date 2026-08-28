"""Background warm-up of the semantic-path ML models at server boot.

The first ``semantic_search`` in a fresh MCP server process paid ~9.4s
(measured) because both the sentence-transformers embedder and the
CrossEncoder reranker load lazily *inside* the query. Part of that is
unavoidable weight-loading, but ~5s of it is HuggingFace Hub httpx metadata
round-trips that fire even when the weights are already in the local cache.
Loading both models once in a daemon thread at boot moves the whole cost off
the first query: by the time the user asks anything, the in-process caches
(``embeddings._MODEL_CACHE`` / ``reranker._RERANKER_CACHE``) are populated
and the query pays only the encode/predict.

Constraints that shape this module:

* **Never raise into boot.** Warm-up is pure optimization: every step is
  individually guarded and downgrades to a single ``cairn``-logger warning,
  so a missing model, an evicted HF cache, or an import error costs one log
  line, not the server.
* **Never load what the query path wouldn't load -- and never download.**
  The embedder is warmed only for the backend the query path would use:
  ``local`` only when its weights are already in the local HF cache
  (``hash``/``openai`` have no in-process model); the server family only
  when the shared availability probe is healthy, in which case one tiny
  ``/v1/embeddings`` POST triggers the server-side lazy load (it makes no
  HF calls and downloads nothing); the reranker only when the full gate
  sequence of ``reranker.rerank()`` passes -- ``rerank_enabled()``
  (env ``CAIRN_RERANK`` or the download-reranker marker),
  ``reranker_available()``, and ``reranker_model_is_cached()``. The cache
  gates mirror the query paths' proactive guards deliberately: warm-up is
  a *warm* path, not an install path -- a first-download user's first query
  downloads exactly as before (no surprise multi-GB fetch at boot), and a
  hermetic test environment with an empty HF cache makes warm-up a no-op
  instead of a network client.
* **stdout is the JSON-RPC channel** under the stdio transport. This module
  never prints; its one diagnostic goes through
  ``logging.getLogger("cairn")``, which the server configures to stderr.
* **Idempotent.** ``warm_models_in_background`` uses double-checked locking
  over a module-level flag, so repeated calls (double-wired boot paths,
  tests) never start duplicate loads.
* **Never inside an in-process test boot.** A model load takes seconds, so
  the warm thread routinely outlives the ``run()`` call that started it.
  That is correct for a real server (the process lives on) but poison for
  tests that call ``server.run()`` in-process: the thread crosses test
  boundaries and collides with the next test's monkeypatched loaders
  (observed as TestModelCacheRace flakiness in test_server_robustness).
  ``PYTEST_CURRENT_TEST`` is therefore a hard no-start guard; production
  boots (subprocess or daemon) never have it set.

Offline-mode handling (the ~5s metadata tax): both call sites verified the
weights are in the local HF cache before loading, so loads run under
``HF_HUB_OFFLINE=1`` / ``TRANSFORMERS_OFFLINE=1`` and huggingface_hub skips
its httpx metadata checks entirely. cairn's cached loaders
(``_get_local_model`` / ``_get_reranker``) do not forward constructor
kwargs, so ``SentenceTransformer(local_files_only=True)`` cannot be used
without bypassing them -- and bypassing them would leave the process-level
caches cold, which is exactly what warm-up exists to fill. The env vars are
therefore set process-wide but only *inside the warm step*, restored in a
``finally``, and a load that fails under them retries once online (the
cache-presence check can false-positive on partial snapshots) before the
step guard gives up. Documented limitation: mutating ``os.environ`` is
process-global, so a query that races the warm step sees offline mode too;
that window is boot-only and requires weights we just verified are cached,
so a racing load of the same weights would succeed offline anyway.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable

_LOGGER = logging.getLogger("cairn")

# Env vars that make huggingface_hub / transformers skip their Hub metadata
# round-trips and read purely from the local cache.
_ENV_OFFLINE_VARS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")

# Once-per-process guard (double-checked locking in warm_models_in_background).
# Holds the started thread even after it finishes: warm-up is a boot-time
# concern, so a later call must not restart loads in a long-lived server.
_WARM_LOCK = threading.Lock()
_WARM_THREAD: threading.Thread | None = None


def warm_models_in_background() -> threading.Thread | None:
    """Start the warm-up daemon thread (non-blocking, idempotent).

    Returns the thread performing the warm-up, the already-started thread on
    a repeat call, or ``None`` when warm-up will not run: disabled via the
    ``CAIRN_WARM_MODELS`` kill switch (``0``/``false``/``no``; unset or
    anything else means enabled) or invoked inside a pytest test
    (``PYTEST_CURRENT_TEST`` set -- see module docstring: a seconds-long
    background load must not leak across test boundaries). Boot must never
    join the thread -- the whole point is that serving starts while weights
    load. The thread is a daemon so it can never block process exit, and it
    writes nothing to stdout (stdio transport carries JSON-RPC there).
    """
    if _warm_disabled() or _inside_pytest():
        return None
    global _WARM_THREAD
    with _WARM_LOCK:
        if _WARM_THREAD is not None:
            return _WARM_THREAD
        thread = threading.Thread(
            target=warm_models, name="cairn-model-warmup", daemon=True
        )
        _WARM_THREAD = thread
        thread.start()
        return thread


def warm_models() -> None:
    """Synchronous warm-up body: load every *enabled* model, best-effort.

    This is what the background thread runs; it is exposed (and called
    directly by tests) so the load sequence is deterministic without
    joining threads. Each step is independently guarded -- one model
    failing to load must not skip the other, and neither may raise into
    the caller (boot or test).
    """
    try:
        _warm_embedder()
    except Exception as exc:
        _LOGGER.warning(
            "model warm-up: embedding model load failed (non-fatal): %s",
            exc,
            exc_info=True,
        )
    try:
        _warm_reranker()
    except Exception as exc:
        _LOGGER.warning(
            "model warm-up: reranker load failed (non-fatal): %s",
            exc,
            exc_info=True,
        )


def _warm_disabled() -> bool:
    """True when CAIRN_WARM_MODELS is an explicit off value (kill switch)."""
    value = (os.environ.get("CAIRN_WARM_MODELS") or "").strip().lower()
    return value in ("0", "false", "no")


def _inside_pytest() -> bool:
    """True while running under a pytest test (hard no-start guard).

    Pytest sets ``PYTEST_CURRENT_TEST`` for the duration of every test --
    including tests that call ``server.run()`` in-process, which is exactly
    the boot path that must NOT spawn a seconds-long background load here.
    Kept as a tiny named function (rather than an inline env read) so tests
    can patch it deterministically; pytest re-sets the env var at each test
    phase boundary, which makes deleting it mid-test unreliable.
    """
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _warm_embedder() -> None:
    """Warm the active embedding backend: local weights or a server model.

    ``_effective_backend()`` (not the raw ``CAIRN_EMBED_BACKEND`` value) is
    the right gate: a ``local`` config without sentence-transformers
    installed resolves to ``hash``, which has no weights to warm, and the
    server family (server/omlx/ollama) resolves to ``server``. The local
    path keeps the ``model_is_cached()`` gate (mirroring rerank()'s
    proactive cache guard) so warm-up can never *download*: a
    first-download user's first query fetches the weights exactly as it
    would have without this module. Since passing the gate means the
    weights are verifiably local, that load runs under the HF offline env
    vars (see ``_load_with_offline_guard``).

    The server path cannot warm in-process weights -- the model lives in
    the server -- so the warm step is one tiny ``/v1/embeddings`` POST
    (``_embed_server``, the query-path client) that makes the server
    lazy-load the configured model before the first query. Its availability
    verdict comes from ``_server_probe_available()``, the same per-process
    cache the query path gates on, so a boot that already probed consumes
    the verdict without a second ``/v1/models`` round-trip. No HF offline
    vars are set (a server arm makes no HF calls) and nothing is
    downloaded; an unhealthy probe or a failed POST raises into
    ``warm_models``' step guard, which downgrades it to the single
    non-fatal warning.
    """
    # Lazy import keeps importing this module weightless and mirrors the
    # lazy style of reranker/embeddings' own heavy imports.
    from . import embeddings

    backend = embeddings._effective_backend()
    if backend == "local":
        if not embeddings.model_is_cached():
            return
        _load_with_offline_guard(embeddings._get_local_model)
        return
    if backend == "server":
        # Consumes (or, on a cold cache, populates) the FR-002 probe verdict
        # shared with embeddings_available() -- never a duplicate probe.
        if not embeddings._server_probe_available():
            raise RuntimeError(
                "embedding server probe failed; server model not warmed"
            )
        # The response is discarded: warm-up only needs the model resident
        # server-side; the single-text input is the tiniest valid batch.
        embeddings._embed_server(["warmup"])


def _warm_reranker() -> None:
    """Populate ``reranker._RERANKER_CACHE`` when reranking is live.

    Mirrors the gate sequence of ``reranker.rerank()`` exactly -- enabled
    (``CAIRN_RERANK`` truthy/falsy wins over the download marker),
    installed, and proactively cached -- so warm-up can never enable,
    download, or otherwise force a reranker the query path wouldn't use.
    Passing the cache gate means the load below runs under offline env vars
    against weights that are verifiably local.
    """
    from . import reranker

    if not (
        reranker.rerank_enabled()
        and reranker.reranker_available()
        and reranker.reranker_model_is_cached()
    ):
        return
    _load_with_offline_guard(reranker._get_reranker)


def _load_with_offline_guard(load: Callable[[], object]) -> None:
    """Call ``load()`` under the HF offline env vars, retrying once online.

    Both call sites verified the weights are in the local HF cache before
    reaching this function, so the load can run with
    ``HF_HUB_OFFLINE``/``TRANSFORMERS_OFFLINE`` set -- skipping the ~5s of
    httpx Hub metadata round-trips that fire even on fully cached weights.
    The vars are saved/restored around the call so nothing leaks into the
    process after the step, and a load that fails *with* them retries once
    without them -- ``try_to_load_from_cache`` can report a partial snapshot
    as complete, and a stale verdict must not leave the model cold for the
    whole process lifetime. A retry that also fails raises to the step
    guard in ``warm_models`` (single warning, boot continues).
    """
    saved = {var: os.environ.get(var) for var in _ENV_OFFLINE_VARS}
    try:
        for var in _ENV_OFFLINE_VARS:
            os.environ[var] = "1"
        try:
            load()
        except Exception:
            # Restore the originals BEFORE retrying so the retry genuinely
            # runs online, not still under the vars the load just failed on.
            # A second failure raises to the step guard in warm_models.
            _restore_env(saved)
            load()
    finally:
        _restore_env(saved)  # idempotent; no-ops when the retry path restored


def _restore_env(saved: dict) -> None:
    """Put os.environ back exactly as ``_load_with_offline_guard`` found it."""
    for var, previous in saved.items():
        if previous is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = previous


def _reset_warmup_state() -> None:
    """Clear the once-per-process warm-up flag. Test isolation only."""
    global _WARM_THREAD
    with _WARM_LOCK:
        _WARM_THREAD = None
