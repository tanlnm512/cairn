"""PyPI console-script entry: exec the cairn engine binary with the original argv."""
from __future__ import annotations

import os
import sys

from . import paths
from .paths import RUST_BIN_ENV, rust_bin

# Tells the engine which interpreter carries the sidecar extras ([ingest]).
_SIDECAR_PYTHON_ENV = "CAIRN_SIDECAR_PYTHON"

_NO_ENGINE = (
    "cairn: no engine binary found. This package launches the cairn binary: install a "
    "wheel built for your platform or the standalone binary (Homebrew tap, cargo binstall "
    f"cairn-cli), or point {RUST_BIN_ENV} at one."
)
_NO_ENGINE_WINDOWS = (
    "cairn: no engine binary found. No native Windows engine ships yet: run the "
    f"standalone Linux binary under WSL, or point {RUST_BIN_ENV} at one."
)


def main() -> None:
    """Exec the discovered engine binary (rust_bin() order) with the original argv."""
    argv = sys.argv[1:]
    bin_path = rust_bin()
    if bin_path is None:
        message = _NO_ENGINE_WINDOWS if paths._is_windows_host() else _NO_ENGINE
        print(message, file=sys.stderr)
        sys.exit(1)
    os.environ.setdefault(_SIDECAR_PYTHON_ENV, sys.executable)
    try:
        os.execv(bin_path, [bin_path, *argv])
    except OSError as exc:
        print(f"cairn: cannot exec {bin_path}: {exc}", file=sys.stderr)
        sys.exit(1)
