"""Background warm-up of the semantic-path ML models at server boot."""
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
    """Start the warm-up daemon thread (non-blocking, idempotent)."""
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
    """Synchronous warm-up body: load every *enabled* model, best-effort."""
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
    """True while running under a pytest test (hard no-start guard)."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _warm_embedder() -> None:
    """Warm the active embedding backend: local weights or a server model."""
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
        # Consumes (or, on a cold cache, populates) the server availability
        # probe verdict
        # shared with embeddings_available() -- never a duplicate probe.
        if not embeddings._server_probe_available():
            raise RuntimeError(
                "embedding server probe failed; server model not warmed"
            )
        # The response is discarded: warm-up only needs the model resident
        # server-side; the single-text input is the tiniest valid batch.
        embeddings._embed_server(["warmup"])


def _warm_reranker() -> None:
    """Populate ``reranker._RERANKER_CACHE`` when reranking is live."""
    from . import reranker

    if not (
        reranker.rerank_enabled()
        and reranker.reranker_available()
        and reranker.reranker_model_is_cached()
    ):
        return
    _load_with_offline_guard(reranker._get_reranker)


def _load_with_offline_guard(load: Callable[[], object]) -> None:
    """Call ``load()`` under the HF offline env vars, retrying once online."""
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
