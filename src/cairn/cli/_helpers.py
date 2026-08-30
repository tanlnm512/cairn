"""Shared CLI helpers used across multiple command modules."""
from __future__ import annotations

import json


def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _mods(modifiers_json: str) -> list:
    if not modifiers_json:
        return []
    try:
        return json.loads(modifiers_json)
    except (json.JSONDecodeError, TypeError):
        return []


def _shorten(path: str) -> str:
    """Shorten an absolute path for display by stripping the workspace root.

    Uses the resolved workspace from the central store; falls back to the
    basename if the workspace can't be determined (e.g. no store yet).
    """
    try:
        from ..paths import resolve_workspace
        ws = str(resolve_workspace())
        if path.startswith(ws):
            rel = path[len(ws):].lstrip("/")
            return rel if rel else path
    except Exception:
        pass
    return path
