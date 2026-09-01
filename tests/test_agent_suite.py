"""Tests for the agent-effort benchmark suite (src/cairn/bench/agent_suite.py).

Hermetic by construction: tiny generated corpus (6 modules + ``__init__.py``
<= 9 files), hash embed backend, no network, no LLM. Asserts *shape and
determinism*, not specific timings or ratios (machine-dependent).
"""
from __future__ import annotations

import json

from cairn.bench.agent_suite import (
    CHARS_PER_TOKEN,
    AgentReport,
    TaskEffort,
    ArmEffort,
    compare_agent_reports,
    run_agent_suite,
)
from cairn.bench.corpus import generate_corpus
import pytest

pytestmark = pytest.mark.infra

# 6 modules + __init__.py = 7 files -- small enough that build+embed+tasks
# stay well under a second, large enough that every task finds its target.
N_FILES = 6
RUNS = 2


def _run_suite(tmp_path, name="agent", seed=0xC0DE, runs=RUNS):
    """Generate a tiny corpus and run the suite against a fresh DB."""
    repo = generate_corpus(tmp_path / name, N_FILES, complexity="low")
    db = str(tmp_path / f"{name}.db")
    return run_agent_suite(str(repo), db, runs=runs, seed=seed)


# --- suite shape ------------------------------------------------------------

class TestAgentSuite:
    def test_six_tasks_produce_effort_in_both_arms(self, tmp_path):
        report = _run_suite(tmp_path)
        labels = [t.label for t in report.tasks]
        assert labels == [
            "definition-lookup",
            "caller-enumeration",
            "blast-radius-depth3",
            "entry-to-leaf-flow",
            "concept-search",
            "common-name-impact",
        ]
        for task in report.tasks:
            assert task.question  # human-readable question bound to a target
            for arm in (task.cairn, task.control):
                assert arm.tool_calls >= 1
                assert arm.chars >= 0
                # The documented token proxy: chars / 4.
                assert arm.est_tokens == arm.chars // CHARS_PER_TOKEN
                assert arm.wall_seconds >= 0

    def test_control_arm_overreads_on_common_name(self, tmp_path):
        """The collision task is where grep must over-read: control reads
        (nearly) the whole corpus while cairn stays at 2 scripted calls."""
        report = _run_suite(tmp_path)
        task = next(t for t in report.tasks if t.label == "common-name-impact")
        assert task.cairn.tool_calls == 2  # precise impact + fuzzy escalation
        # Every generated file mentions method_N (defines or calls it).
        assert task.control.tool_calls > N_FILES

    def test_medians_over_runs(self, tmp_path):
        """With runs=3 the reported effort is the median sample, not a mean
        or a last value: call counts are deterministic so the median equals
        that same value."""
        report = _run_suite(tmp_path, name="median", runs=3)
        assert report.runs == 3
        for task in report.tasks:
            # Deterministic counts -> median == the (only) observed value.
            assert task.control.tool_calls >= 1
            assert task.cairn.tool_calls >= 1

    def test_control_arm_deterministic_across_runs(self, tmp_path):
        """Two suites over identical corpora (same seed) produce identical
        control-arm call counts and chars per task."""
        first = _run_suite(tmp_path, name="det_a")
        second = _run_suite(tmp_path, name="det_b")
        assert len(first.tasks) == len(second.tasks)
        for a, b in zip(first.tasks, second.tasks):
            assert a.label == b.label
            assert a.control.tool_calls == b.control.tool_calls
            assert a.control.chars == b.control.chars
            # cairn call counts are deterministic; payload chars can drift
            # by one near-tied row across REBUILDS (symbol ids are random
            # per build, so semantic_search's limit cutoff may swap a row).
            # Allow ~1% -- far below the 15% baseline-compare gate.
            assert a.cairn.tool_calls == b.cairn.tool_calls
            assert abs(a.cairn.chars - b.cairn.chars) <= max(64, a.cairn.chars // 100)

    def test_seed_changes_targets_not_task_set(self, tmp_path):
        """A different seed may pick different targets but the six task
        labels (the payload's stable shape) are fixed."""
        a = _run_suite(tmp_path, name="seed_a", seed=1)
        b = _run_suite(tmp_path, name="seed_b", seed=2)
        assert [t.label for t in a.tasks] == [t.label for t in b.tasks]
        assert a.seed == 1 and b.seed == 2


# --- payload shape ----------------------------------------------------------

class TestAgentReportPayload:
    def test_json_payload_shape_stable(self, tmp_path):
        report = _run_suite(tmp_path)
        payload = json.loads(report.to_json())
        assert set(payload) >= {
            "corpus", "seed", "runs", "embed_backend",
            "chars_per_token", "tasks", "totals",
        }
        assert payload["chars_per_token"] == 4
        assert payload["embed_backend"] == "hash"
        assert payload["corpus"]["files"] >= N_FILES + 1  # + __init__.py
        for task in payload["tasks"]:
            assert set(task) == {"label", "question", "cairn", "control", "reduction"}
            for arm in ("cairn", "control"):
                assert set(task[arm]) == {"tool_calls", "chars", "est_tokens", "wall_ms"}
            assert set(task["reduction"]) == {"calls_pct", "tokens_pct", "time_ratio"}
        assert set(payload["totals"]) == {"cairn", "control", "reduction"}

    def test_totals_sum_task_medians(self, tmp_path):
        report = _run_suite(tmp_path)
        payload = report.to_dict()
        assert payload["totals"]["cairn"]["tool_calls"] == sum(
            t["cairn"]["tool_calls"] for t in payload["tasks"]
        )
        assert payload["totals"]["control"]["est_tokens"] == sum(
            t["control"]["est_tokens"] for t in payload["tasks"]
        )


# --- baseline comparison ----------------------------------------------------

class TestCompareAgentReports:
    def _payload(self, tokens_a, tokens_b):
        return {"tasks": [
            {"label": "t1", "cairn": {"est_tokens": tokens_a}},
            {"label": "t2", "cairn": {"est_tokens": tokens_b}},
        ]}

    def test_flags_token_regression(self):
        baseline = self._payload(100, 100)
        current = self._payload(130, 100)
        deltas = compare_agent_reports(baseline, current, threshold=0.15)
        assert deltas["t1"]["regressed"] is True  # +30% > 15%
        assert deltas["t1"]["delta_pct"] == 30.0
        assert deltas["t2"]["regressed"] is False

    def test_improvement_not_flagged(self):
        deltas = compare_agent_reports(self._payload(100, 100), self._payload(50, 90))
        assert all(not d["regressed"] for d in deltas.values())

    def test_missing_task_skipped(self):
        deltas = compare_agent_reports(
            {"tasks": [{"label": "old", "cairn": {"est_tokens": 100}}]},
            self._payload(100, 100),
        )
        assert "old" not in deltas


# --- dataclass rendering (no build needed) ----------------------------------

class TestRendering:
    def test_reduction_math(self):
        task = TaskEffort(
            label="x", question="q",
            cairn=ArmEffort(1, 400, 100, 0.01),
            control=ArmEffort(10, 4000, 1000, 0.02),
        )
        d = task.to_dict()
        assert d["reduction"]["calls_pct"] == 90.0
        assert d["reduction"]["tokens_pct"] == 90.0
        assert d["reduction"]["time_ratio"] == 2.0

    def test_empty_report_renders(self):
        """A zero-task report still serializes (shape guards in CI)."""
        payload = AgentReport().to_dict()
        assert payload["tasks"] == []
        assert payload["totals"]["cairn"]["tool_calls"] == 0


# --- CLI registration + JSON path -------------------------------------------

class TestAgentCli:
    def _invoke(self, extra):
        from click.testing import CliRunner
        from cairn.cli import main

        runner = CliRunner()
        return runner.invoke(main, [
            "bench", "--suite", "agent",
            "--n-files", "4", "--complexity", "low",
            "--embed-backend", "hash", "--runs", "1",
            *extra,
        ])

    def test_json_payload_has_timestamp_and_tasks(self, tmp_path, monkeypatch):
        # Pin CAIRN_DB: the suite restores it to whatever it saw on entry
        # (the CLI sets its own temp DB); without a pin the path would leak
        # into the shared test process for the rest of the test run.
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "cli.db"))
        result = self._invoke(["--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert "timestamp" in payload
        assert len(payload["tasks"]) == 6
        assert payload["runs"] == 1

    def test_default_output_stays_human(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "cli2.db"))
        result = self._invoke([])
        assert result.exit_code == 0, result.output
        assert not result.stdout.lstrip().startswith("{")

    def test_help_lists_agent_and_runs(self):
        from click.testing import CliRunner
        from cairn.cli import main

        result = CliRunner().invoke(main, ["bench", "--help"])
        assert result.exit_code == 0
        assert "agent" in result.output
        assert "--runs" in result.output


# --- CLI --baseline resolution (T014, FR-004/AC1, agent-suite path) --------


class TestAgentBaselineCli:
    def test_agent_baseline_headers_and_compares(self, tmp_path, monkeypatch):
        """--baseline resolves <version>/agent.json for the agent suite too:
        the dataset-version header renders and the token comparison table
        names the requested version (clean run -> exit 0)."""
        from click.testing import CliRunner

        from cairn import __version__
        from cairn.bench.datasource import machine_profile
        from cairn.cli import main

        # Deterministic synthetic report (no build, no timing noise): one
        # task whose est_tokens exactly matches the baseline -> delta 0.
        report = AgentReport(
            tasks=[TaskEffort(
                label="definition-lookup",
                question="q",
                cairn=ArmEffort(2, 400, 100, 0.01),
                control=ArmEffort(10, 4000, 1000, 0.02),
            )],
        )
        monkeypatch.setattr(
            "cairn.bench.agent_suite.run_agent_suite", lambda *a, **k: report
        )
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.delenv("RUNNER_NAME", raising=False)
        baseline = {
            "tasks": [{"label": "definition-lookup", "cairn": {"est_tokens": 100}}],
            "dataset": {"name": "benchmark-datasource", "version": "DS-ag"},
            "cairn_version": __version__,
            "machine_profile": machine_profile(),  # matches -> no warning
        }
        root = tmp_path / "benchmarks" / "baselines" / "DS-ag"
        root.mkdir(parents=True)
        (root / "agent.json").write_text(json.dumps(baseline), encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "ag.db"))

        result = CliRunner().invoke(main, [
            "bench", "--suite", "agent", "--n-files", "3", "--complexity", "low",
            "--embed-backend", "hash", "--runs", "1", "--baseline", "DS-ag",
        ])
        assert result.exit_code == 0, result.output
        assert "Baseline DS-ag" in result.output  # dataset-version header
        assert "vs baseline DS-ag" in result.output  # version in the comparison
        assert "definition-lookup" in result.output  # agent compare table rendered
        assert "MACHINE-PROFILE MISMATCH" not in result.output  # profile matches

    def test_agent_baseline_regression_exits_2(self, tmp_path, monkeypatch):
        """The agent suite's exit-2 regression signal also carries over."""
        from click.testing import CliRunner

        from cairn.bench.datasource import machine_profile
        from cairn.cli import main

        report = AgentReport(
            tasks=[TaskEffort(
                label="definition-lookup",
                question="q",
                cairn=ArmEffort(2, 800, 200, 0.01),  # est_tokens 200 vs 100
                control=ArmEffort(10, 4000, 1000, 0.02),
            )],
        )
        monkeypatch.setattr(
            "cairn.bench.agent_suite.run_agent_suite", lambda *a, **k: report
        )
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        baseline = {
            "tasks": [{"label": "definition-lookup", "cairn": {"est_tokens": 100}}],
            "machine_profile": machine_profile(),
        }
        root = tmp_path / "benchmarks" / "baselines" / "DS-ag"
        root.mkdir(parents=True)
        (root / "agent.json").write_text(json.dumps(baseline), encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "ag.db"))

        result = CliRunner().invoke(main, [
            "bench", "--suite", "agent", "--n-files", "3", "--complexity", "low",
            "--embed-backend", "hash", "--runs", "1", "--baseline", "DS-ag",
        ])
        assert result.exit_code == 2, result.output
        assert "REGRESSED" in result.output
