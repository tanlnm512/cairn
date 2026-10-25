"""Engine binary discovery for the PyPI shim: CAIRN_RUST_BIN, then the bundled package binary, then PATH."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

RUST_BIN_ENV = "CAIRN_RUST_BIN"

# Package-data dir for the injected engine binary: release/wheel builds copy
# the platform `cairn` binary in immediately before packaging; a source
# checkout ships none (gitignored) and discovery falls through to PATH.
BUNDLED_BIN_DIR = Path(__file__).resolve().parent / "_bin"


def bundled_binary() -> Path | None:
    """The bundled `cairn` engine binary inside the package tree, or None when absent."""
    cand = BUNDLED_BIN_DIR / "cairn"
    return cand if cand.exists() else None


def shutil_which(name: str) -> str | None:
    return shutil.which(name)


def _is_windows_host() -> bool:
    return os.name == "nt"


def _is_native_bin(path: Path) -> bool:
    """True for a native executable; excludes the #!-scripted console script also named `cairn`."""
    try:
        with open(path, "rb") as f:
            return f.read(2) != b"#!"
    except OSError:
        return False


def rust_bin() -> str | None:
    """Engine binary path: CAIRN_RUST_BIN, then the bundled package binary, then a native `cairn` on PATH or ~/.local/bin (non-Windows); None when absent."""
    cand = os.environ.get(RUST_BIN_ENV)
    if cand and Path(cand).exists():
        return cand
    bundled = bundled_binary()
    if bundled is not None:
        return str(bundled)
    # No Windows engine ships: pip's console launcher is a native cairn.exe that
    # passes the #!-only native check, so PATH there can never prove an engine.
    if _is_windows_host():
        return None
    for cand in (shutil_which("cairn"), str(Path.home() / ".local" / "bin" / "cairn")):
        if cand and Path(cand).exists() and _is_native_bin(Path(cand)):
            return cand
    return None
