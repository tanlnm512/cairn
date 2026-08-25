"""Upgrade CLI: version, upgrade, install-method detection."""
from __future__ import annotations

import click
import subprocess
import sys

from .main import main


# --- Version helpers -------------------------------------------------------
#
# Version strings are PEP 440. Naive `==` comparison breaks across pre/post/
# local segments (e.g. local "0.6.0" vs PyPI "0.6.0.post1", or "0.6.0" vs
# "0.6.0rc1"). Use packaging.version.parse when available; degrade to string
# equality if the import ever fails -- the upgrade command must never crash on
# a version parse.


def _is_up_to_date(current: str, latest: str) -> bool:
    """True if ``current`` >= ``latest`` under PEP 440, else string equality."""
    try:
        from packaging.version import parse
        return parse(current) >= parse(latest)
    except Exception:
        return current == latest


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


def _reinstall(method: str, version: str) -> bool:
    """Re-install cairn-intel using the detected method. True on success.

    The installer runs behind the shared quiet progress helper (the same
    seam `embed --install-deps` uses): one live progress line, with the
    installer's output drained silently and shown only on failure. Raw
    `subprocess.run` with inherited stdout used to dump 50+ lines of
    pipx/uv/pip noise (venv creation, every Collecting/Downloading/
    already-satisfied line) straight into the terminal.
    """
    from . import display
    from ..graph.embeddings import _run_subprocess_with_progress

    spec = f"cairn-intel=={version}"
    if method == "uv":
        cmd = ["uv", "tool", "install", "--force", spec]
    elif method == "pipx":
        cmd = ["pipx", "install", "--force", spec]
    elif method == "pip":
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", spec]
    else:
        display.warning(
            f"Cannot auto-upgrade (unknown install method). "
            f"Run manually: pip install --upgrade {spec}"
        )
        return False
    display.dim(f"Running: {' '.join(cmd)}")
    try:
        _run_subprocess_with_progress(cmd, f"Upgrading cairn-intel to {version}")
    except subprocess.CalledProcessError:
        # The helper already printed the installer's captured output above.
        display.warning(
            f"Upgrade failed. The full installer output is above; "
            f"to retry manually: {' '.join(cmd)}"
        )
        return False
    display.success(f"Upgraded cairn-intel -> {version}")
    return True


# --- Commands --------------------------------------------------------------


@main.command()
def version():
    """Print the installed cairn version."""
    from . import display
    try:
        from importlib.metadata import version as _v
        display.info(f"cairn-intel {_v('cairn-intel')}")
    except Exception:
        from cairn import __version__
        display.info(f"cairn-intel {__version__} (source checkout)")


@main.command()
@click.option("--check", is_flag=True, help="Only check, don't upgrade")
def upgrade(check):
    """Upgrade cairn. Detects install method and updates in place."""
    from . import display
    current = _installed_version()
    latest = _pypi_latest()

    if latest is None:
        if check:
            display.info(f"cairn-intel {current} (could not reach PyPI)")
        else:
            display.warning(
                "Cannot check for upgrades (PyPI unreachable). "
                "Install manually: pip install --upgrade cairn-intel"
            )
        return

    if check:
        display.info(f"cairn-intel {current} (latest: {latest})")
        return

    if _is_up_to_date(current, latest):
        display.success(f"cairn-intel {current} (already up to date)")
        return

    method = _detect_install_method()
    display.info(f"Upgrading cairn-intel {current} -> {latest} (via {method})")
    if not _reinstall(method, latest):
        sys.exit(1)
