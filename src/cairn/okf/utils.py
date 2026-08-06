"""Shared helpers for the Open Knowledge Format (OKF) layer.

Small, dependency-free utilities used across OKF concept producers (memory,
knowledge, etc.) so each producer doesn't reinvent the same logic.
"""
from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """URL-safe slug for OKF concept ids.

    Lowercases, replaces each run of non-alphanumeric characters with a single
    hyphen, strips leading/trailing hyphens, and truncates to 60 chars.

    Returns ``""`` for all-symbol/empty input; callers that need a non-empty
    id should supply their own fallback, e.g. ``slugify(x) or "memory"``.

    Note: this is deliberately ASCII-only and truncating. Producers that need
    to preserve underscores/unicode word characters (e.g. memory consolidation
    paths) keep a richer local slugifier rather than using this one.
    """
    return _NON_ALNUM.sub("-", text.lower()).strip("-")[:60]
