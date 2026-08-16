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


# --- CLI JSON output (CI path: timestamp + timings) ----------------------

def _run_bench_cli(extra_args):
    """Invoke `cairn bench` on a tiny corpus; return the CliRunner result."""
    from click.testing import CliRunner
    from cairn.cli import main
    runner = CliRunner()
    return runner.invoke(main, [
        "bench", "--suite", "perf",
        "--n-files", "5", "--complexity", "low",
        "--embed-backend", "hash", "--repeats", "1",
        *extra_args,
    ])


class TestBenchCliJson:
    def test_json_payload_has_timestamp_and_timings(self, tmp_path, monkeypatch):
        """--json emits a machine-readable payload: ISO timestamp + per-op timings.

        This is the shape the CI bench job uploads as an artifact and compares
        against the rolling baseline.
        """
        from datetime import datetime

        # Pin CAIRN_DB: the perf suite restores it to whatever it saw on entry
        # (a value the CLI sets to its own temp DB), so without a pin the
        # leaked path would outlive this test inside the shared process.
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "bench_cli.db"))
        result = _run_bench_cli(["--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert "timestamp" in payload
        datetime.fromisoformat(payload["timestamp"])  # valid ISO 8601
        assert payload["symbols"] > 0
        op_names = [op["name"] for op in payload["ops"]]
        assert any("build" in n for n in op_names)
        assert all("median_ms" in op for op in payload["ops"])

    def test_default_output_stays_human(self, tmp_path, monkeypatch):
        """Without --json the command prints the rich table, not a JSON dump."""
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "bench_cli.db"))
        result = _run_bench_cli([])
        assert result.exit_code == 0, result.output
        assert not result.stdout.lstrip().startswith("{")


# --- CLI --baseline resolution (T014, FR-004/AC1, TC-007..TC-012) ----------


def _synthetic_perf_report(median_ms=100.0):
    """A PerfReport whose compare payload is fully deterministic.

    The compare path only reads ops[].{name, median_ms}, so one op with a
    fixed median makes regression/improvement scenarios exact -- no timing
    noise, no real graph build.
    """
    from cairn.bench.report import OpTiming, PerfReport
    from cairn.bench.timing import TimingResult

    return PerfReport(
        symbols=10,
        edges=5,
        ops=[OpTiming(
            name="build",
            timing=TimingResult(name="build", median=median_ms / 1000.0),
        )],
    )


def _patch_perf_suite(monkeypatch, median_ms=100.0):
    """Swap the real perf suite for the synthetic report (speed: no build)."""
    monkeypatch.setattr(
        "cairn.bench.run_perf_suite",
        lambda *args, **kwargs: _synthetic_perf_report(median_ms),
    )


def _write_committed_baseline(
    tmp_path, monkeypatch, *, version="DS-v1", name="perf",
    median_ms=100.0, profile="match", payload=None,
):
    """Write a throwaway committed-baseline fixture and chdir onto its repo.

    Mirrors the tree T015 will mint (benchmarks/baselines/<DS-version>/
    <suite>.json) under tmp_path -- never under the repo's real benchmarks/
    -- and chdirs so the CLI's cwd-first resolution finds it.

    profile: "match" stamps the exact current machine_profile (no warning);
    a dict stamps that profile verbatim; None omits the key entirely
    (pre-T013 unstamped baseline shape).
    """
    from cairn.bench.datasource import machine_profile

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("RUNNER_NAME", raising=False)
    if payload is None:
        stamped = machine_profile() if profile == "match" else profile
        payload = {
            "ops": [{"name": "build", "median_ms": median_ms}],
            "dataset": {
                "name": "benchmark-datasource",
                "version": version,
                "tree_hash": "f" * 64,
            },
            "cairn_version": "0.11.0",
        }
        if stamped is not None:
            payload["machine_profile"] = stamped
    root = tmp_path / "benchmarks" / "baselines" / version
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return root


def _invoke_perf_cli(extra_args, tmp_path, monkeypatch):
    """Invoke `cairn bench --suite perf` on a tiny corpus with fast options."""
    from click.testing import CliRunner
    from cairn.cli import main

    # Pin CAIRN_DB: the CLI writes its own temp DB path into os.environ and
    # only monkeypatch restores the pre-test value (same reason as above).
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "baseline_cli.db"))
    return CliRunner().invoke(main, [
        "bench", "--suite", "perf",
        "--n-files", "3", "--complexity", "low",
        "--embed-backend", "hash", "--repeats", "1",
        *extra_args,
    ])


class TestBenchCliBaseline:
    def test_header_renders_version_and_stamp_facts(self, tmp_path, monkeypatch):
        """TC-007: --baseline resolves the committed dir, headers it with its
        stamp facts (dataset version, cairn version, runner class), and the
        comparison names the requested version -- clean run exits 0."""
        from cairn import __version__

        _write_committed_baseline(tmp_path, monkeypatch)  # median 100, match
        _patch_perf_suite(monkeypatch, median_ms=100.0)  # current == baseline
        result = _invoke_perf_cli(["--baseline", "DS-v1"], tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        assert "Baseline DS-v1" in result.output  # dataset-version header
        assert "benchmark-datasource" in result.output  # ...and its stamp facts:
        assert __version__ in result.output
        assert "reference-local" in result.output  # runner class from the file
        assert "vs baseline DS-v1" in result.output  # version in the comparison

    def test_mismatch_warning_names_exactly_differing_fields(
        self, tmp_path, monkeypatch
    ):
        """TC-009/TC-011: every differing profile field is named with both
        values; matching fields are NOT; the mismatch never gates (exit 0)."""
        import platform

        from cairn.bench.datasource import machine_profile

        current = machine_profile()  # exactly what the CLI's stamp computes
        stamped = dict(current, runner_class="ci-ubuntu-latest", arch="x86_64")
        _write_committed_baseline(tmp_path, monkeypatch, profile=stamped)
        _patch_perf_suite(monkeypatch, median_ms=100.0)  # clean comparison
        result = _invoke_perf_cli(["--baseline", "DS-v1"], tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output  # advisory: warn, not gate
        assert "MACHINE-PROFILE MISMATCH" in result.output
        lines = [l for l in result.output.splitlines() if " vs current " in l]
        assert len(lines) == 2  # exactly the two differing fields
        named = "\n".join(lines)
        assert "runner_class" in named and "arch" in named
        assert "cpu_count" not in named and "cpu" not in named and " os" not in named
        # Both sides of each mismatch are visible.
        assert "ci-ubuntu-latest" in result.output  # baseline runner_class
        assert "reference-local" in result.output  # current runner_class
        assert "x86_64" in result.output  # baseline arch
        assert platform.machine() in result.output  # current arch

    def test_matching_profile_prints_no_mismatch_warning(self, tmp_path, monkeypatch):
        """TC-010: an exact profile match renders no mismatch marker."""
        _write_committed_baseline(tmp_path, monkeypatch)  # profile matches
        _patch_perf_suite(monkeypatch, median_ms=100.0)
        result = _invoke_perf_cli(["--baseline", "DS-v1"], tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        assert "MACHINE-PROFILE MISMATCH" not in result.output
        assert " vs current " not in result.output

    def test_unstamped_baseline_notes_unknown_not_mismatch(self, tmp_path, monkeypatch):
        """A pre-T013 baseline (no machine_profile key) is 'unknown', not
        'mismatched': noted without the MISMATCH marker, compare proceeds."""
        _write_committed_baseline(
            tmp_path, monkeypatch, profile=None,
            payload={"ops": [{"name": "build", "median_ms": 100.0}]},
        )
        _patch_perf_suite(monkeypatch, median_ms=100.0)
        result = _invoke_perf_cli(["--baseline", "DS-v1"], tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        assert "MACHINE-PROFILE MISMATCH" not in result.output
        assert "no machine_profile stamp" in result.output

    def test_unknown_version_fails_promptly(self, tmp_path, monkeypatch):
        """TC-008: unknown version exits 1 naming the version (and the ones
        that exist), renders no comparison, and never runs the suite."""
        def _boom(*args, **kwargs):
            raise AssertionError("suite must not run for an unknown version")

        _write_committed_baseline(tmp_path, monkeypatch)  # only DS-v1 exists
        monkeypatch.setattr("cairn.bench.run_perf_suite", _boom)
        monkeypatch.setattr("cairn.bench.generate_corpus", _boom)
        result = _invoke_perf_cli(["--baseline", "does-not-exist"], tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert not isinstance(result.exception, AssertionError)  # failed pre-suite
        assert "does-not-exist" in result.output  # missing version named
        assert "DS-v1" in result.output  # ...and the versions that do exist
        assert "Unknown baseline" in result.output
        assert "vs baseline" not in result.output  # no partial comparison

    def test_version_without_suite_artifact_names_it(self, tmp_path, monkeypatch):
        """A committed version missing this suite's file names suite + path."""
        from cairn.bench.datasource import machine_profile

        payload = {
            "tasks": [{"label": "t", "cairn": {"est_tokens": 1}}],
            "machine_profile": machine_profile(),
        }
        _write_committed_baseline(
            tmp_path, monkeypatch, name="agent", payload=payload,
        )
        result = _invoke_perf_cli(["--baseline", "DS-v1"], tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert "has no perf suite result" in result.output
        assert "agent.json" in result.output  # hints at what IS committed

    def test_baseline_and_compare_are_mutually_exclusive(self, tmp_path, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("suite must not run for a usage error")

        _write_committed_baseline(tmp_path, monkeypatch)
        monkeypatch.setattr("cairn.bench.run_perf_suite", _boom)
        result = _invoke_perf_cli(
            ["--baseline", "DS-v1", "--compare", str(tmp_path / "b.json")],
            tmp_path, monkeypatch,
        )
        assert result.exit_code == 1
        assert not isinstance(result.exception, AssertionError)
        assert "mutually exclusive" in result.output

    def test_regression_exit_2_preserved_with_baseline(self, tmp_path, monkeypatch):
        """sys.exit(2) regression semantics carry over to --baseline."""
        _write_committed_baseline(tmp_path, monkeypatch, median_ms=100.0)
        _patch_perf_suite(monkeypatch, median_ms=150.0)  # +50% > 15% threshold
        result = _invoke_perf_cli(["--baseline", "DS-v1"], tmp_path, monkeypatch)
        assert result.exit_code == 2
        assert "REGRESSED" in result.output

    def test_compare_explicit_file_still_works(self, tmp_path, monkeypatch):
        """--compare <file> (no --baseline) keeps its exact prior behavior:
        exit 2 on regression, no header (it is not a dataset version)."""
        explicit = tmp_path / "explicit.json"
        explicit.write_text(
            json.dumps({"ops": [{"name": "build", "median_ms": 100.0}]}),
            encoding="utf-8",
        )
        _patch_perf_suite(monkeypatch, median_ms=150.0)
        result = _invoke_perf_cli(["--compare", str(explicit)], tmp_path, monkeypatch)
        assert result.exit_code == 2, result.output
        assert "REGRESSED" in result.output
        assert "vs baseline" in result.output
        assert "Baseline DS-v1" not in result.output  # no version header

    def test_compare_missing_file_error_unchanged(self, tmp_path, monkeypatch):
        _patch_perf_suite(monkeypatch, median_ms=100.0)
        result = _invoke_perf_cli(
            ["--compare", str(tmp_path / "nope.json")], tmp_path, monkeypatch
        )
        assert result.exit_code == 1
        assert "Baseline file not found" in result.output
