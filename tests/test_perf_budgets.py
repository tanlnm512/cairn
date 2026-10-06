from __future__ import annotations

import json
from pathlib import Path

import pytest

from cairn.bench.budgets import evaluate_budgets, load_budgets
from cairn.bench.report import OpTiming, PerfReport
from cairn.bench.timing import TimingResult


BUDGET_FILE = Path("benchmarks/baselines/perf_p95_budgets.json")
BASELINE_FILE = Path("benchmarks/baselines/DS-v1/perf.json")
REQUIRED_TOOLS = {
    "find_definition",
    "get_callers",
    "impact_analysis",
    "search_symbols",
    "semantic_search",
    "explore",
}


def _report(p95_ms_by_tool: dict[str, float]) -> PerfReport:
    return PerfReport(
        ops=[
            OpTiming(name=name, timing=TimingResult(name=name, p95=ms / 1000))
            for name, ms in p95_ms_by_tool.items()
        ]
    )


def test_checked_in_budgets_are_schema_tagged_and_catastrophe_sized():
    budgets = load_budgets(BUDGET_FILE)
    raw = json.loads(BUDGET_FILE.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    observed = {op["name"]: op["p95_ms"] for op in baseline["ops"]}

    assert REQUIRED_TOOLS <= budgets.keys()
    assert raw["schema"] == "cairn-perf-budgets/1"
    assert raw["timestamp"] == baseline["timestamp"]
    assert raw["dataset"] == baseline["dataset"]
    assert raw["cairn_version"] == baseline["cairn_version"]
    assert raw["machine_profile"] == baseline["machine_profile"]
    assert raw["factor"] >= 10
    assert all(budgets[tool] >= 10 * observed[tool] for tool in REQUIRED_TOOLS)


def test_evaluate_budgets_reports_only_over_budget_tools():
    report = _report({
        "find_definition": 4,
        "get_callers": 5,
        "impact_analysis": 5,
        "search_symbols": 5,
        "semantic_search": 5,
        "explore": 6,
    })
    budgets = {tool: 5.0 for tool in REQUIRED_TOOLS}

    breaches = evaluate_budgets(report, budgets)

    assert [(b.tool, b.measured_ms, b.budget_ms) for b in breaches] == [
        ("explore", 6.0, 5.0)
    ]


def test_evaluate_budgets_requires_every_budgeted_measurement():
    report = _report({"find_definition": 1.0})

    with pytest.raises(ValueError, match="get_callers"):
        evaluate_budgets(report, {"find_definition": 5.0, "get_callers": 5.0})


@pytest.mark.parametrize(
    ("schema", "budgets"),
    [
        ("wrong-schema", {"find_definition": 5.0}),
        ("cairn-perf-budgets/1", {"find_definition": 0.0}),
    ],
)
def test_load_budgets_rejects_invalid_files(tmp_path, schema, budgets):
    path = tmp_path / "budgets.json"
    document = {
        "schema": schema,
        "timestamp": "2026-10-05T00:00:00+00:00",
        "dataset": {"version": "DS-test"},
        "cairn_version": "0.21.2",
        "machine_profile": {"runner_class": "ci-test"},
        "factor": 10,
        "budgets": budgets,
    }
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError):
        load_budgets(path)
