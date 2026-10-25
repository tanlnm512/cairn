"""PyPI console-script entry: exec the cairn engine binary; `communities` runs the networkx sidecar in-process."""
from __future__ import annotations

import os
import sys

from .paths import RUST_BIN_ENV, rust_bin

# Louvain stays the Python [graph-analytics] sidecar; the binary only has a stub arm for it.
_SIDECAR_COMMANDS = frozenset({"communities"})
# Tells the engine which interpreter carries the sidecar extras ([ingest]).
_SIDECAR_PYTHON_ENV = "CAIRN_SIDECAR_PYTHON"

_NO_ENGINE = (
    "cairn: no engine binary found. This package launches the cairn binary: install a "
    "wheel built for your platform or the standalone binary (Homebrew tap, cargo binstall "
    f"cairn-cli), or point {RUST_BIN_ENV} at one."
)


def _split_sidecar(argv: list[str]) -> tuple[str | None, list[str]]:
    """(command, its argv) when argv names a sidecar command past the group-level -v/--verbose; else (None, argv)."""
    for i, arg in enumerate(argv):
        if arg in ("-v", "--verbose"):
            continue
        if arg in _SIDECAR_COMMANDS:
            return arg, argv[i + 1:]
        return None, argv
    return None, argv


def main() -> None:
    """Exec the discovered engine binary (rust_bin() order) with the original argv."""
    argv = sys.argv[1:]
    command, rest = _split_sidecar(argv)
    if command == "communities":
        from .communities import main as run_communities

        sys.exit(run_communities(rest))
    bin_path = rust_bin()
    if bin_path is None:
        print(_NO_ENGINE, file=sys.stderr)
        sys.exit(1)
    os.environ.setdefault(_SIDECAR_PYTHON_ENV, sys.executable)
    try:
        os.execv(bin_path, [bin_path, *argv])
    except OSError as exc:
        print(f"cairn: cannot exec {bin_path}: {exc}", file=sys.stderr)
        sys.exit(1)
