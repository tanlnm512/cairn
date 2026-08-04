"""Tests for the benchmark module (src/cairn/bench/).

Three layers:
1. Unit tests for the timing primitives (percentiles, time_call, peak_memory)
   — deterministic, fast, no graph/build dependency.
2. Tests for the corpus generator (determinism, scanner-recognition, buildable).
3. Smoke tests for the two suites + the cairn bench CLI — run end-to-end on a tiny
   corpus, assert report shape (not specific timings, which are machine-dependent).
"""
from __future__ import annotations

import json
import os


from cairn.bench import (
    generate_corpus,
    corpus_stats,
    run_perf_suite,
    run_scaling_suite,
    compare_reports,
    percentiles,
    time_call,
    peak_memory,
    TimingResult,
    MemoryResult,
)


# --- timing primitives ---------------------------------------------------

class TestPercentiles:
    def test_empty(self):
        p = percentiles([])
        assert p == {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "min": 0.0, "max": 0.0}

    def test_single_sample(self):
        p = percentiles([0.5])
        assert p["p50"] == 0.5 and p["max"] == 0.5

    def test_monotonic_percentiles(self):
        """p50 <= p95 <= p99 for a spread sample."""
        samples = list(range(1, 101))  # 1..100
        p = percentiles(samples)
        assert p["p50"] <= p["p95"] <= p["p99"]
        assert p["min"] == 1 and p["max"] == 100


class TestTimeCall:
    def test_warmup_discarded(self):
        """Warmup runs don't appear in samples."""
        calls = {"n": 0}
        def fn():
            calls["n"] += 1
            return calls["n"]
        result, last = time_call(fn, name="probe", warmup=2, repeats=3)
        assert len(result.samples) == 3  # only repeats, not warmup
        assert result.name == "probe"
        # last value comes from a repeat (>= warmup+1)
        assert last >= 3

    def test_returns_timing_result(self):
        result, _ = time_call(lambda: None, warmup=0, repeats=2)
        assert isinstance(result, TimingResult)
        assert len(result.samples) == 2
        assert all(s >= 0 for s in result.samples)


class TestPeakMemory:
    def test_captures_peak(self):
        mem, val = peak_memory(lambda: "x" * 1000)
        assert isinstance(mem, MemoryResult)
        assert mem.peak_bytes >= 0
        assert val == "x" * 1000

    def test_stops_tracing(self):
        """tracemalloc is stopped after the call (no leaked tracing state)."""
        import tracemalloc
        peak_memory(lambda: None)
        assert not tracemalloc.is_tracing()


# --- corpus generator ----------------------------------------------------

class TestCorpus:
    def test_generates_n_files(self, tmp_path):
        repo = generate_corpus(tmp_path, 10)
        py_files = list(repo.glob("module_*.py"))
        assert len(py_files) == 10
        assert (repo / ".git").exists()  # scanner marker

    def test_deterministic(self, tmp_path):
        """Same seed + n_files → identical content."""
        a = generate_corpus(tmp_path / "a", 5, seed=42)
        b = generate_corpus(tmp_path / "b", 5, seed=42)
        fa = sorted(p.name for p in a.glob("*.py"))
        fb = sorted(p.name for p in b.glob("*.py"))
        assert fa == fb
        # File contents identical too.
        for name in fa:
            assert (a / name).read_text() == (b / name).read_text()

    def test_corpus_stats(self, tmp_path):
        repo = generate_corpus(tmp_path, 5)
        stats = corpus_stats(repo)
        assert stats["files"] >= 6  # 5 modules + __init__.py
        assert stats["lines"] > 0
        assert stats["bytes"] > 0

    def test_generated_corpus_is_buildable(self, tmp_path):
        """The generated source must actually parse + build without errors."""
        from cairn.graph.builder import build_graph
        repo = generate_corpus(tmp_path, 8, complexity="low")
        db = str(tmp_path / "bench.db")
        os.environ["CAIRN_DB"] = db
        stats = build_graph(workspace=str(repo), db_path=db)
        assert stats["symbols"] > 0
        assert stats["edges"] >= 0  # low complexity may have few edges


# --- suite smoke tests (tiny corpus, assert shape not timings) -----------

class TestPerfSuite:
    def test_runs_and_returns_well_formed_report(self, tmp_path, monkeypatch):
        repo = generate_corpus(tmp_path, 6, complexity="low")
        db = str(tmp_path / "perf.db")
        monkeypatch.setenv("CAIRN_DB", db)
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
        report = run_perf_suite(
            str(repo), db, embed_backend="hash",
            warmup=0, repeats=1, query_repeats=2,
        )
        assert report.symbols > 0
        assert len(report.ops) >= 2  # at least build + embed
        op_names = [op.name for op in report.ops]
        assert any("build" in n for n in op_names)
        assert "embed_all" in op_names
        # Every op has a non-negative median.
        assert all(op.timing.median >= 0 for op in report.ops)
        # JSON serializes cleanly (CI path).
        payload = json.loads(report.to_json())
        assert "ops" in payload and payload["symbols"] > 0


class TestScalingSuite:
    def test_runs_over_tiny_sizes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
        report = run_scaling_suite(
            tmp_path, sizes=(5, 15), complexity="low", embed_backend="hash",
        )
        assert len(report.points) == 2
        p0, p1 = report.points
        assert p0.n_files == 5 and p1.n_files == 15
        # Bigger corpus => more symbols (the basic scaling invariant).
        assert p1.symbols >= p0.symbols
        # Times are non-negative.
        assert p0.build_seconds >= 0 and p1.embed_seconds >= 0


# --- report comparison (regression detection) ---------------------------

class TestCompareReports:
    def test_flags_regression(self):
        baseline = {"ops": [{"name": "build", "median_ms": 100.0}]}
        current = {"ops": [{"name": "build", "median_ms": 130.0}]}
        deltas = compare_reports(baseline, current, threshold=0.15)
        assert "build" in deltas
        assert deltas["build"]["regressed"] is True  # +30% > 15%
        assert deltas["build"]["delta_pct"] == 30.0

    def test_no_regression_under_threshold(self):
        baseline = {"ops": [{"name": "embed", "median_ms": 100.0}]}
        current = {"ops": [{"name": "embed", "median_ms": 110.0}]}
        deltas = compare_reports(baseline, current, threshold=0.15)
        assert deltas["embed"]["regressed"] is False  # +10% < 15%

    def test_missing_op_skipped(self):
        baseline = {"ops": [{"name": "build", "median_ms": 100.0}]}
        current = {"ops": [{"name": "embed", "median_ms": 200.0}]}  # different op
        deltas = compare_reports(baseline, current, threshold=0.15)
        assert "embed" not in deltas  # baseline had no embed to compare


# --- CLI registration ----------------------------------------------------

def test_cg_bench_help_registered():
    """cairn bench --help lists the suite options (command is registered)."""
    from click.testing import CliRunner
    from cairn.cli import main
    runner = CliRunner()
    result = runner.invoke(main, ["bench", "--help"])
    assert result.exit_code == 0
    assert "--suite" in result.output
    assert "--save" in result.output
    assert "--compare" in result.output
