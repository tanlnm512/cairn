"""Load and evaluate perf-suite p95 latency budgets."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .report import PerfReport

BUDGET_SCHEMA = "cairn-perf-budgets/1"
PROVENANCE_KEYS = ("timestamp", "dataset", "cairn_version", "machine_profile")


@dataclass(frozen=True)
class Breach:
    """One operation whose measured p95 exceeded its budget."""

    tool: str
    measured_ms: float
    budget_ms: float


def _validate_budgets(budgets: Mapping[str, object]) -> dict[str, float]:
    if not isinstance(budgets, Mapping):
        raise ValueError("budgets must be an object keyed by operation name")
    validated: dict[str, float] = {}
    for tool, budget in budgets.items():
        if not isinstance(tool, str) or not tool:
            raise ValueError("budget operation names must be non-empty strings")
        if isinstance(budget, bool) or not isinstance(budget, (int, float)):
            raise ValueError(f"budget for {tool} must be a number of milliseconds")
        value = float(budget)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"budget for {tool} must be finite and positive")
        validated[tool] = value
    return validated


def load_budgets(path: Path | str) -> dict[str, float]:
    """Load and validate a schema-tagged p95 budget JSON file."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("budget file must contain a JSON object")
    if document.get("schema") != BUDGET_SCHEMA:
        raise ValueError(f"unsupported budget schema: {document.get('schema')!r}")
    missing_provenance = [key for key in PROVENANCE_KEYS if key not in document]
    if missing_provenance:
        raise ValueError(f"budget file missing provenance: {', '.join(missing_provenance)}")
    factor = document.get("factor")
    if isinstance(factor, bool) or not isinstance(factor, (int, float)):
        raise ValueError("budget factor must be a number")
    if not math.isfinite(float(factor)) or factor < 10:
        raise ValueError("budget factor must be finite and at least 10")
    raw_budgets = document.get("budgets")
    if not isinstance(raw_budgets, dict):
        raise ValueError("budget file must contain a budgets object")
    return _validate_budgets(raw_budgets)


def evaluate_budgets(
    report: PerfReport, budgets: Mapping[str, object]
) -> list[Breach]:
    """Return p95 breaches for every budgeted operation in ``report``."""
    validated = _validate_budgets(budgets)
    measurements: dict[str, float] = {}
    for op in report.ops:
        if op.name in measurements:
            raise ValueError(f"perf report contains duplicate operation: {op.name}")
        measured_ms = op.timing.p95 * 1000
        if not math.isfinite(measured_ms) or measured_ms < 0:
            raise ValueError(f"perf report has invalid p95 for {op.name}")
        measurements[op.name] = measured_ms

    missing = sorted(set(validated) - set(measurements))
    if missing:
        raise ValueError(f"perf report missing budgeted operations: {', '.join(missing)}")
    return [
        Breach(
            tool=tool,
            measured_ms=measurements[tool],
            budget_ms=budget_ms,
        )
        for tool, budget_ms in validated.items()
        if measurements[tool] > budget_ms
    ]
