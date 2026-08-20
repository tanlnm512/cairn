"""Token estimation for the dashboard.

Two modes: exact counting through the optional ``[semantic]`` extra's
transformers tokenizer when it is importable and the embed model's tokenizer
is cached locally (never fetched from the network), or the zero-dependency
chars/4 heuristic — the same ``CHARS_PER_TOKEN`` constant the bench suite
uses, so bench and dashboard numbers stay comparable. The active mode is
resolved once per process; ``reset_tokenizer_mode()`` forces a re-probe.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Optional

from cairn.bench.agent_suite import CHARS_PER_TOKEN

HEURISTIC_MODE = "heuristic (chars/4)"

_mode: Optional[str] = None
_tokenizer: Optional[Any] = None
_lock = threading.Lock()


def _tokenizer_model() -> str:
    from cairn.graph.embeddings import DEFAULT_LOCAL_MODEL

    return (os.environ.get("CAIRN_EMBED_LOCAL_MODEL") or DEFAULT_LOCAL_MODEL).strip()


def _probe_tokenizer() -> Optional[Any]:
    """The locally cached tokenizer, or None when unavailable.

    Unavailability is normal: the semantic extra not installed, or the
    tokenizer not yet cached, both mean the heuristic mode.
    """
    try:
        import cairn.paths  # noqa: F401  (injects ~/.cairn/lib into sys.path)
        from transformers import AutoTokenizer
    except ImportError:
        return None
    try:
        return AutoTokenizer.from_pretrained(_tokenizer_model(), local_files_only=True)
    except Exception:
        return None


def _resolve_mode() -> str:
    global _mode, _tokenizer
    with _lock:
        if _mode is None:
            _tokenizer = _probe_tokenizer()
            _mode = HEURISTIC_MODE if _tokenizer is None else f"exact ({_tokenizer_model()})"
        return _mode


def active_tokenizer_mode() -> str:
    """The active estimation mode's stable display name."""
    return _resolve_mode()


def estimate_tokens(text: str) -> int:
    """Token count for ``text`` under the active mode: exact tokenizer when
    available, ``len(text) // CHARS_PER_TOKEN`` otherwise."""
    _resolve_mode()
    if _tokenizer is None:
        return len(text) // CHARS_PER_TOKEN
    return len(_tokenizer.encode(text, add_special_tokens=False))


def reset_tokenizer_mode() -> None:
    """Clear the cached mode and tokenizer so the next call re-probes."""
    global _mode, _tokenizer
    with _lock:
        _mode = None
        _tokenizer = None
