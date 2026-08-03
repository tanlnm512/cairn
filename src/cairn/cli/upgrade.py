"""Upgrade CLI: version, upgrade, install-method detection."""
from __future__ import annotations

import click
import json
import os
import subprocess
import sys
from pathlib import Path

from .main import DEFAULT_DB_PATH, DEFAULT_KNOWLEDGE_PATH, builder, get_db, main, queries, scanner_mod
from ._helpers import _human_bytes, _mods, _shorten  # noqa: F401


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


@main.command()
def version():
    """Print the installed cairn version."""
    try:
        from importlib.metadata import version as _v
        click.echo(f"cairn-intel {_v('cairn-intel')}")
    except Exception:
        from cairn import __version__
        click.echo(f"cairn-intel {__version__} (source checkout)")


def _installed_version() -> str:
    """Return the currently installed version string."""
    try:
        from importlib.metadata import version as _v
        return _v("cairn-intel")
    except Exception:
        from cairn import __version__
        return __version__


def _pypi_latest() -> str | None:
    """Query PyPI for the latest published version. Returns None on failure."""
    try:
        import urllib.request
        import json
        url = "https://pypi.org/pypi/cairn-intel/json"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data["info"]["version"]
    except Exception:
        return None


def _detect_install_method() -> str:
    """Detect how cairn-intel was installed: 'uv', 'pipx', 'pip', or 'unknown'."""
    exe = sys.executable
    # Check uv tool installations first (uv tool list is fast).
    try:
        r = subprocess.run(
            ["uv", "tool", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if "cairn-intel" in r.stdout:
            return "uv"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Check pipx.
    try:
        r = subprocess.run(
            ["pipx", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if "cairn-intel" in r.stdout:
            return "pipx"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # If running inside a venv that looks like a pip install, say pip.
    if "venv" in exe or "virtualenv" in exe or ".local" in exe:
        return "pip"
    return "unknown"


def _reinstall(method: str, version: str):
    """Re-install cairn-intel using the detected method."""
    spec = f"cairn-intel=={version}"
    if method == "uv":
        cmd = ["uv", "tool", "install", "--force", spec]
    elif method == "pipx":
        cmd = ["pipx", "install", "--force", spec]
    elif method == "pip":
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", spec]
    else:
        click.echo(
            f"Cannot auto-upgrade (unknown install method). "
            f"Run manually: pip install --upgrade {spec}"
        )
        return
    click.echo(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=False)


@main.command()
@click.option("--check", is_flag=True, help="Only check, don't upgrade")
def upgrade(check):
    """Upgrade cairn. Detects install method and updates in place."""
    current = _installed_version()
    latest = _pypi_latest()

    if latest is None:
        if check:
            click.echo(f"cairn-intel {current} (could not reach PyPI)")
        else:
            click.echo(
                "Cannot check for upgrades (PyPI unreachable). "
                "Install manually: pip install --upgrade cairn-intel"
            )
        return

    if check:
        click.echo(f"cairn-intel {current} (latest: {latest})")
        return

    if current == latest:
        click.echo(f"cairn-intel {current} (already up to date)")
        return

    method = _detect_install_method()
    click.echo(f"Upgrading cairn-intel {current} -> {latest} (via {method})")
    _reinstall(method, latest)


