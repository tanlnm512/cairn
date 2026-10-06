"""Tests for scripts/mint_perf_budgets.py -- perf budget recalibration."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from cairn.bench.budgets import BUDGET_SCHEMA, PROVENANCE_KEYS, load_budgets

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "mint_perf_budgets.py"

BUDGETED_TOOLS = {
    "find_definition",
    "get_callers",
    "impact_analysis",
    "search_symbols",
    "semantic_search",
    "explore",
}

STAMP = {
    "timestamp": "2026-10-05T00:00:00+00:00",
    "dataset": {"name": "benchmark-datasource", "version": "DS-test"},
    "cairn_version": "0.21.2",
    "machine_profile": {"runner_class": "ci-github-actions-arm64"},
}

P95S = {
    "find_definition": 1.23,
    "get_callers": 0.45,
    "impact_analysis": 0.89,
    "search_symbols": 0.31,
    "semantic_search": 201.67,
    "explore": 513.73,
}


def _write_run(path: Path, *, drop_tools=(), p95_overrides=None, drop_stamp=()) -> Path:
    p95s = {name: value for name, value in P95S.items() if name not in drop_tools}
    p95s.update(p95_overrides or {})
    ops = [{"name": "build (total)", "p95_ms": 1736.78}]
    ops += [{"name": name, "p95_ms": value} for name, value in p95s.items()]
    run = {"ops": ops}
    run.update({key: value for key, value in STAMP.items() if key not in drop_stamp})
    path.write_text(json.dumps(run), encoding="utf-8")
    return path


def _run_script(run_path: Path, out_path: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--from", str(run_path), "--out", str(out_path), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def test_mints_ceiled_budgets_with_provenance(tmp_path):
    run_path = _write_run(tmp_path / "run.json")
    out_path = tmp_path / "budgets.json"
    proc = _run_script(run_path, out_path)
    assert proc.returncode == 0, proc.stderr

    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["schema"] == BUDGET_SCHEMA
    for key in PROVENANCE_KEYS:
        assert data[key] == STAMP[key]
    assert data["factor"] == 10.0

    budgets = data["budgets"]
    assert set(budgets) == BUDGETED_TOOLS
    for name, budget in budgets.items():
        assert budget >= P95S[name] * 10
    assert budgets["find_definition"] == 12.3
    assert "build (total)" not in budgets
    assert load_budgets(out_path) == budgets


def test_fractional_factor_rounds_up_never_below_the_multiple(tmp_path):
    run_path = _write_run(
        tmp_path / "run.json", p95_overrides={"find_definition": 0.125}
    )
    out_path = tmp_path / "budgets.json"
    proc = _run_script(run_path, out_path, "--factor", "10.5")
    assert proc.returncode == 0, proc.stderr
    budgets = json.loads(out_path.read_text(encoding="utf-8"))["budgets"]
    assert budgets["find_definition"] == 1.32  # 0.125 * 10.5 = 1.3125


def test_rejects_factor_below_the_catastrophe_floor(tmp_path):
    run_path = _write_run(tmp_path / "run.json")
    out_path = tmp_path / "budgets.json"
    proc = _run_script(run_path, out_path, "--factor", "9")
    assert proc.returncode == 2
    assert "--factor must be >= 10" in proc.stderr
    assert not out_path.exists()


def test_rejects_run_missing_a_budgeted_tool(tmp_path):
    run_path = _write_run(tmp_path / "run.json", drop_tools=("semantic_search",))
    out_path = tmp_path / "budgets.json"
    proc = _run_script(run_path, out_path)
    assert proc.returncode == 1
    assert "semantic_search" in proc.stderr
    assert not out_path.exists()


def test_rejects_invalid_p95_or_missing_provenance(tmp_path):
    bad_p95 = _write_run(tmp_path / "bad-p95.json", p95_overrides={"explore": 0})
    proc = _run_script(bad_p95, tmp_path / "out1.json")
    assert proc.returncode == 1
    assert "explore" in proc.stderr

    unstamped = _write_run(tmp_path / "unstamped.json", drop_stamp=("machine_profile",))
    proc = _run_script(unstamped, tmp_path / "out2.json")
    assert proc.returncode == 1
    assert "machine_profile" in proc.stderr
