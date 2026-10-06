#!/usr/bin/env python3
"""Mint catastrophe-class perf p95 budgets from a saved bench run payload."""
from __future__ import annotations

import argparse
import json
import math
import sys
from decimal import ROUND_UP, Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from cairn.bench.budgets import BUDGET_SCHEMA, PROVENANCE_KEYS, load_budgets

# Catastrophe-class floor: budgets below 10x observed p95 would trip on
# hosted-runner noise, which FR-003's gate exists to survive.
MIN_FACTOR = Decimal("10")
CENT = Decimal("0.01")

BUDGETED_TOOLS = (
    "find_definition",
    "get_callers",
    "impact_analysis",
    "search_symbols",
    "semantic_search",
    "explore",
)


def _budget_ms(observed_ms: float, factor: Decimal) -> float:
    exact = Decimal(str(observed_ms)) * factor
    return float(exact.quantize(CENT, rounding=ROUND_UP))


def mint_budgets(run: dict, *, factor: Decimal) -> dict:
    """Build a budgets payload from a perf-suite run dict, or exit 1 on a malformed run."""
    ops = run.get("ops")
    if not isinstance(ops, list):
        raise SystemExit(
            "run payload has no 'ops' list -- pass a `cairn bench --suite perf --json` run"
        )
    p95s: dict[str, object] = {}
    for op in ops:
        if isinstance(op, dict) and isinstance(op.get("name"), str):
            p95s[op["name"]] = op.get("p95_ms")
    missing_tools = [tool for tool in BUDGETED_TOOLS if tool not in p95s]
    if missing_tools:
        raise SystemExit(
            f"run is missing budgeted tool(s): {', '.join(missing_tools)}"
        )
    missing_stamp = [key for key in PROVENANCE_KEYS if key not in run]
    if missing_stamp:
        raise SystemExit(
            f"run payload is missing provenance key(s): {', '.join(missing_stamp)}"
        )
    budgets: dict[str, float] = {}
    for tool in BUDGETED_TOOLS:
        value = p95s[tool]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SystemExit(
                f"{tool}: p95_ms must be a finite positive number, got {value!r}"
            )
        if not math.isfinite(value) or value <= 0:
            raise SystemExit(
                f"{tool}: p95_ms must be a finite positive number, got {value!r}"
            )
        budgets[tool] = _budget_ms(value, factor)
    return {
        "schema": BUDGET_SCHEMA,
        **{key: run[key] for key in PROVENANCE_KEYS},
        "factor": float(factor),
        "budgets": budgets,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--from",
        dest="from_run",
        required=True,
        help="saved `cairn bench --suite perf --json` payload",
    )
    parser.add_argument(
        "--factor",
        type=float,
        default=10.0,
        help="budget multiple of observed p95, >= 10 (default: 10)",
    )
    parser.add_argument("--out", required=True, help="output budgets JSON path")
    args = parser.parse_args(argv)

    factor = Decimal(str(args.factor))
    if factor < MIN_FACTOR:
        parser.error(f"--factor must be >= {MIN_FACTOR} (catastrophe-class budgets)")

    try:
        run = json.loads(Path(args.from_run).read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"cannot read run payload {args.from_run}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"run payload is not valid JSON: {args.from_run}: {exc}") from exc
    if not isinstance(run, dict):
        raise SystemExit("run payload must be a JSON object")

    payload = mint_budgets(run, factor=factor)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        load_budgets(out)
    except ValueError as exc:
        out.unlink(missing_ok=True)
        raise SystemExit(f"minted file failed its own load contract: {exc}") from exc
    for tool in BUDGETED_TOOLS:
        print(f"[mint] {tool}: budget {payload['budgets'][tool]}ms")
    print(f"[mint] wrote {out} (factor {args.factor}, schema {BUDGET_SCHEMA})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
