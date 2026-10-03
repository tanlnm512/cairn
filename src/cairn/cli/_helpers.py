"""Shared CLI helpers used across multiple command modules."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path


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


def _pkg_root() -> Path:
    """The source-checkout root (three levels above the cairn package)."""
    spec = find_spec("cairn")
    if spec is None or spec.origin is None:
        raise RuntimeError("cannot locate the installed cairn package")
    return Path(spec.origin).resolve().parents[2]


def _detect_install_method() -> str:
    """How cairn-intel was installed: 'uv', 'pipx', 'pip', 'venv', or 'unknown'."""
    exe = sys.executable
    try:
        r = subprocess.run(
            ["uv", "tool", "list"], capture_output=True, text=True, timeout=10,
        )
        if "cairn-intel" in r.stdout:
            return "uv"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        r = subprocess.run(
            ["pipx", "list"], capture_output=True, text=True, timeout=10,
        )
        if "cairn-intel" in r.stdout:
            return "pipx"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Source checkout's in-tree venv (.venv at the package root).
    try:
        pkg_root = _pkg_root()
        if (pkg_root / ".venv").exists() and pkg_root / ".venv" in Path(exe).resolve().parents:
            return "venv"
    except Exception:
        pass
    if "venv" in exe or "virtualenv" in exe or ".local" in exe:
        return "pip"
    return "unknown"


def _shorten(path: str) -> str:
    """Shorten an absolute path for display by stripping the workspace root.

    Falls back to the path unchanged when the workspace can't be determined or
    the path lies outside it.
    """
    try:
        from ..paths import resolve_workspace
        ws = str(resolve_workspace())
        if path == ws or path.startswith(ws.rstrip(os.sep) + os.sep):
            rel = path[len(ws):].lstrip(os.sep + "/")
            return rel if rel else path
    except Exception:
        pass
    return path
