"""Shared helpers for the Open Knowledge Format (OKF) layer."""
from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """URL-safe slug for OKF concept ids.

    Lowercases, replaces runs of non-alphanumeric characters with a single
    hyphen, strips leading/trailing hyphens, truncates to 60 chars. Returns
    ``""`` for all-symbol/empty input.
    """
    return _NON_ALNUM.sub("-", text.lower()).strip("-")[:60]
