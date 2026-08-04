#!/usr/bin/env python3
"""Run `cairn impact` and guard against name-collision blowups (Golden Rule 6).

`impact_analysis`/`cairn impact` traverses callers recursively by name. Common
or lifecycle names (`get`, `create`, `onCreate`, `render`, ...) can match
hundreds or thousands of unrelated symbols across repos, producing a total
that looks like real blast radius but is actually noise. The traversal
result already carries a `cycles` field naming the colliding symbols -- this
script checks it so you don't have to eyeball a huge dump every time.

Usage:
    impact_guard.py <symbol> [--depth N] [--threshold N] [--fuzzy]

Exit code is always 0 on a successful query (collision or not) so it's safe
to call from an agent without extra error handling; a non-zero `cairn impact`
failure (bad symbol, no graph, etc.) is passed through.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

DEFAULT_THRESHOLD = 100


def _cg_command() -> list[str]:
    """Resolve the cairn invocation.

    Mirrors agent_install.resolve_cg_command(): prefer the `cairn` binary on
    PATH; fall back to `python -m cairn.cli.main` when it isn't (e.g. an editable
    install invoked via the module path). This script ships standalone into
    the skill dir, so it cannot import resolve_cg_command directly.
    """
    if shutil.which("cairn"):
        return ["cairn"]
    return [sys.executable, "-m", "cairn.cli.main"]


def run_cg(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([*_cg_command(), *args], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        print("error: `cairn` not found on PATH", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print(f"error: `cairn {' '.join(args)}` timed out", file=sys.stderr)
        sys.exit(1)


def print_impact(result: dict) -> None:
    total = result.get("total", 0)
    print(f"Total impacted: {total}")
    by_depth: dict[int, list] = {}
    for r in result.get("impacted", []):
        by_depth.setdefault(r["depth"], []).append(r)
    for d in sorted(by_depth):
        rows = by_depth[d]
        print(f"\nDepth {d} ({len(rows)}):")
        for r in rows[:20]:
            print(f"  {r['symbol']:30} {r['file']}  ({r['repo']})")
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")


def try_dataflow_fallback(symbol: str) -> None:
    print("Trying the precomputed dataflow index instead (cached=True equivalent)...")
    df = run_cg(["dataflow", "dataflow-lookup", symbol, "--json"])
    if df.returncode != 0:
        print("  (no precomputed dataflow entry -- run `cairn dataflow build` first, "
              "or re-run impact_guard.py with a more specific qualified name)")
        return
    data = json.loads(df.stdout)
    within = data.get("within_repo", [])
    cross = data.get("cross_repo", [])
    print(f"  within-repo: {len(within)} symbols")
    for s in within[:20]:
        print(f"    {s}")
    if len(within) > 20:
        print(f"    ... and {len(within) - 20} more")
    print(f"  cross-repo consumers: {', '.join(cross) if cross else '(none)'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbol")
    parser.add_argument("--depth", type=int, default=3, help="Max traversal depth (default: 3).")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                         help=f"Impacted-symbol count above which a cycle-carrying result is "
                              f"flagged as a name collision (default: {DEFAULT_THRESHOLD}).")
    parser.add_argument("--fuzzy", action="store_true",
                         help="Also traverse unresolved name-only edges (inflates results further; "
                              "rarely what you want alongside this guard).")
    args = parser.parse_args()

    cg_args = ["impact", args.symbol, "--depth", str(args.depth), "--json"]
    if args.fuzzy:
        cg_args.append("--fuzzy")
    proc = run_cg(cg_args)
    if proc.returncode != 0:
        print(proc.stderr or proc.stdout, file=sys.stderr)
        sys.exit(proc.returncode)

    result = json.loads(proc.stdout)
    total = result.get("total", 0)
    cycles = result.get("cycles", [])

    if cycles and total > args.threshold:
        cycle_names = [c.get("symbol", c) if isinstance(c, dict) else c for c in cycles]
        shown = cycle_names[:8]
        more = f" ... and {len(cycle_names) - 8} more" if len(cycle_names) > 8 else ""
        print(f"⚠ {args.symbol}: impact_analysis reports {total} impacted symbols, but the "
              f"traversal passed through common/colliding names {shown}{more} -- this reads as "
              f"a name-collision artifact, not real coupling (Golden Rule 6). Not printing the "
              f"raw dump.")
        print()
        try_dataflow_fallback(args.symbol)
        return

    print_impact(result)


if __name__ == "__main__":
    main()
