"""PyPI console-script entry: exec the rust engine binary when discoverable, else the python CLI."""
from __future__ import annotations

import os
import sys

# Sidecar rulings: prs/bench/eval (dev workflow) and communities
# (graph-analytics) keep serving from the Python package; the binary only has
# stub arms for them.
_PYPI_ONLY_COMMANDS = frozenset({"prs", "bench", "eval", "communities"})


def _pypi_only(argv: list[str]) -> bool:
    """True when argv names a sidecar-ruled command, skipping the group-level -v/--verbose."""
    for arg in argv:
        if arg in ("-v", "--verbose"):
            continue
        return arg in _PYPI_ONLY_COMMANDS
    return False


def main() -> None:
    """Exec the discovered rust binary (rust_bin() order) with the original argv; python CLI when none is found."""
    argv = sys.argv[1:]
    if not _pypi_only(argv):
        from .mcp_server.lifecycle import rust_bin

        bin_path = rust_bin()
        if bin_path:
            try:
                os.execv(bin_path, [bin_path, *argv])
            except OSError as exc:
                print(
                    f"cairn: cannot exec {bin_path}: {exc}; using the python CLI",
                    file=sys.stderr,
                )
    from .cli import main as python_main

    python_main(argv)
