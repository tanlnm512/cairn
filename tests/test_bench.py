"""Tests for the benchmark module (src/cairn/bench).

Three layers:
1. Unit tests for the timing primitives (percentiles, time_call, peak_memory)
   deterministic, fast, no graph/build dependency.
2. Tests for the corpus generator (determinism, scanner-recognition, buildable).
3. Smoke tests for the two suites + the cairn bench CLI — run end-to-end on a tiny
   corpus, assert report shape (not specific timings, which are machine-dependent).
"""
from __future__ import annotations

import json
import os
import sys


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
import pytest

pytestmark = pytest.mark.infra


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

    def test_nonpositive_repeats_rejected(self):
        """repeats < 1 is a ValueError, never a silent single-run clamp."""
        with pytest.raises(ValueError, match="repeats"):
            time_call(lambda: None, warmup=1, repeats=0)


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

    def test_corpus_stats_tolerates_non_utf8(self, tmp_path):
        """A non-UTF8 .py file counts with replacement chars, like the parser."""
        repo = generate_corpus(tmp_path, 2)
        (repo / "binary.py").write_bytes(b"\xff\xfe\x00def x():\n    pass\n")
        stats = corpus_stats(repo)
        assert stats["files"] == 4
        assert stats["lines"] > 0

    def test_generated_corpus_is_buildable(self, tmp_path):
        """The generated source must actually parse + build without errors."""
        from cairn.graph.builder import build_graph
        repo = generate_corpus(tmp_path, 8, complexity="low")
        db = str(tmp_path / "bench.db")
        os.environ["CAIRN_DB"] = db
        stats = build_graph(workspace=str(repo), db_path=db)
        assert stats["symbols"] > 0
        assert stats["edges"] >= 0  # low complexity may have few edges

    def test_unknown_complexity_rejected(self, tmp_path):
        """An unknown complexity is an error, never a silent medium fallback."""
        with pytest.raises(ValueError, match="complexity"):
            generate_corpus(tmp_path, 5, complexity="bogus")


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

    def test_env_restored_when_suite_raises(self, tmp_path, monkeypatch):
        """Env restoration covers failure paths, not only the success return."""
        monkeypatch.setenv("CAIRN_DB", "pre.db")
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "sentinel")

        import cairn.graph.builder as builder

        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(builder, "build_graph", _boom)
        with pytest.raises(RuntimeError, match="boom"):
            run_perf_suite(
                str(tmp_path), str(tmp_path / "x.db"),
                embed_backend="hash", warmup=0, repeats=1,
            )
        assert os.environ["CAIRN_DB"] == "pre.db"
        assert os.environ["CAIRN_EMBED_BACKEND"] == "sentinel"

    def test_rerank_pinned_off_for_timed_queries(self, tmp_path, monkeypatch):
        """The perf suite pins CAIRN_RERANK off for the query battery and
        restores the ambient value, so p95s cannot drift with reranker state."""
        monkeypatch.setenv("CAIRN_RERANK", "1")
        seen = {}
        import cairn.graph.semantic as semantic_mod
        from cairn.graph.semantic import semantic_search as real

        def _spy(conn, query, **kwargs):
            seen["rerank"] = os.environ.get("CAIRN_RERANK")
            return real(conn, query, **kwargs)

        # Patch the source module: setattr on the lazy queries proxy would
        # pin the attribute at teardown and shadow __getattr__ for the rest
        # of the session.
        monkeypatch.setattr(semantic_mod, "semantic_search", _spy)
        repo = generate_corpus(tmp_path, 4, complexity="low")
        run_perf_suite(
            str(repo), str(tmp_path / "rr.db"), embed_backend="hash",
            warmup=0, repeats=1, query_repeats=1,
        )
        assert seen.get("rerank") == "0"
        assert os.environ["CAIRN_RERANK"] == "1"

    def test_build_total_includes_persist_phase(self, tmp_path, monkeypatch):
        """'build (total)' covers the real persist-to-disk window: build.persist
        is recorded and never exceeds the total."""
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "p.db"))
        repo = generate_corpus(tmp_path, 4, complexity="low")
        report = run_perf_suite(
            str(repo), str(tmp_path / "p.db"), embed_backend="hash",
            warmup=0, repeats=1, query_repeats=1,
        )
        by_name = {op.name: op for op in report.ops}
        assert "build.persist" in by_name
        assert (
            by_name["build (total)"].timing.median
            >= by_name["build.persist"].timing.median
        )

    def test_scan_walk_recorded_as_phase(self, tmp_path, monkeypatch):
        """The workspace walk before the builder's first event is recorded as
        the scan_walk phase, not silently dropped from the phase sum."""
        import time as _t

        import cairn.graph.builder as builder_mod
        from cairn.bench.perf_suite import _phase_timings

        def _fake_build(*args, progress=None, **kwargs):
            _t.sleep(0.05)  # stand-in for the pre-event workspace walk
            if progress:
                progress("scan", files=1, skips=0)
            return {"symbols": 1, "edges": 0}

        monkeypatch.setattr(builder_mod, "build_graph", _fake_build)
        _, phase_seconds = _phase_timings(str(tmp_path), str(tmp_path / "x.db"))
        assert phase_seconds["scan_walk"] >= 0.04
        assert phase_seconds["scan"] >= 0
        assert sum(phase_seconds.values()) >= 0.04


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

    def test_env_restored_after_suite(self, tmp_path, monkeypatch):
        """The suite restores the env vars it pins, like the other suites."""
        monkeypatch.setenv("CAIRN_DB", "pre.db")
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "sentinel")
        run_scaling_suite(tmp_path, sizes=(3,), complexity="low", embed_backend="hash")
        assert os.environ["CAIRN_DB"] == "pre.db"
        assert os.environ["CAIRN_EMBED_BACKEND"] == "sentinel"

    def test_missing_analytics_extra_still_records_zeroes(self, tmp_path, monkeypatch):
        """networkx absent: communities are recorded as zero and the sweep
        completes."""
        # Stub the whole chain: a cached networkx.algorithms.community would
        # otherwise let the probe short-circuit past the halted parent.
        for name in ("networkx", "networkx.algorithms", "networkx.algorithms.community"):
            monkeypatch.setitem(sys.modules, name, None)
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "nx.db"))
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
        report = run_scaling_suite(
            tmp_path, sizes=(3,), complexity="low", embed_backend="hash",
        )
        assert report.points[0].communities_seconds == 0.0
        assert report.points[0].communities_count == 0

    def test_internal_import_error_not_masked_as_zeroes(self, tmp_path, monkeypatch):
        """An ImportError raised inside a communities run propagates instead of
        being recorded as a zero-cost, zero-community point."""
        pytest.importorskip("networkx")
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "boom.db"))
        monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
        import cairn.graph.communities as communities_mod

        def _boom(conn):
            raise ImportError("genuine internal failure")

        monkeypatch.setattr(communities_mod, "compute_communities", _boom)
        with pytest.raises(ImportError, match="genuine internal failure"):
            run_scaling_suite(
                tmp_path, sizes=(3,), complexity="low", embed_backend="hash",
            )


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

    def test_missing_op_surfaced_unbaselined(self):
        """An op the baseline lacks is surfaced unbaselined, never silently skipped."""
        baseline = {"ops": [{"name": "build", "median_ms": 100.0}]}
        current = {"ops": [{"name": "embed", "median_ms": 200.0}]}  # different op
        deltas = compare_reports(baseline, current, threshold=0.15)
        assert deltas["embed"]["unbaselined"] is True
        assert deltas["embed"]["regressed"] is False
        assert deltas["embed"]["baseline_ms"] is None
        assert deltas["embed"]["delta_pct"] is None


# --- budget file validation ------------------------------------------------


class TestBudgetValidation:
    def test_huge_int_budget_rejected_as_value_error(self):
        """A huge integer budget is a ValueError, never an OverflowError."""
        from cairn.bench.budgets import _validate_budgets

        with pytest.raises(ValueError, match="must be a number of milliseconds"):
            _validate_budgets({"find_definition": 10**400})

    def test_huge_int_factor_rejected_as_value_error(self, tmp_path):
        """A huge integer factor is a ValueError, never an OverflowError."""
        from cairn.bench.budgets import load_budgets

        path = tmp_path / "huge-factor.json"
        path.write_text(json.dumps({
            "schema": "cairn-perf-budgets/1",
            "timestamp": "2026-10-16T00:00:00+00:00",
            "dataset": {"version": "DS-test"},
            "cairn_version": "test",
            "machine_profile": {},
            "factor": 10**400,
            "budgets": {"find_definition": 5.0},
        }), encoding="utf-8")
        with pytest.raises(ValueError, match="factor"):
            load_budgets(path)


# --- CLI registration ----------------------------------------------------

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

        # Pin CAIRN_DB so this test's env state is deterministic regardless
        # of the suite/CLI env restore discipline.
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


class TestBenchCliCairnDbEnv:
    """The CLI's CAIRN_DB pin must not outlive the ``cairn bench`` run."""

    def test_cli_unsets_cairn_db_it_pinned(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CAIRN_DB", raising=False)
        result = _run_bench_cli(["--json"])
        assert result.exit_code == 0, result.output
        assert "CAIRN_DB" not in os.environ

    def test_cli_restores_caller_cairn_db(self, tmp_path, monkeypatch):
        caller_db = str(tmp_path / "callers.db")
        monkeypatch.setenv("CAIRN_DB", caller_db)
        result = _run_bench_cli(["--json"])
        assert result.exit_code == 0, result.output
        assert os.environ["CAIRN_DB"] == caller_db


# --- CLI --baseline resolution (/AC1, .. ) ----------


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
    """    Write a throwaway committed-baseline fixture under tmp_path and chdir onto
    its repo.

    Mirrors the tree the mint will use (benchmarks/baselines/<DS-version>/
    <suite>.json), never the repo's real benchmarks/; chdirs so the CLI's
    cwd-first resolution finds it.
    profile: "match" stamps the exact current machine_profile (no warning); a
    dict stamps that profile verbatim; None omits the key entirely
    (pre-unstamped baseline shape)."""
    from cairn import __version__
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
            # The stamp carries whatever version minted it -- dynamic, so the
            # header assertion (current __version__ in output) never rots at
            # a version bump.
            "cairn_version": __version__,
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

    # Pin CAIRN_DB so this test's env state is deterministic regardless of
    # the CLI env restore discipline.
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "baseline_cli.db"))
    return CliRunner().invoke(main, [
        "bench", "--suite", "perf",
        "--n-files", "3", "--complexity", "low",
        "--embed-backend", "hash", "--repeats", "1",
        *extra_args,
    ])


class TestBenchCliBaseline:
    def test_header_renders_version_and_stamp_facts(self, tmp_path, monkeypatch):
        """--baseline resolves the committed dir, headers it with its
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
        """Every differing profile field is named with both
        values; matching fields are NOT; the mismatch never gates (exit 0)."""
        import platform

        from cairn.bench.datasource import machine_profile

        current = machine_profile()  # exactly what the CLI's stamp computes
        # arch must differ on EVERY machine class (x86_64 collides with CI's
        # real arch, collapsing the 2-field mismatch to 1) -- use a fake.
        from cairn.bench.datasource import runner_class as _rc

        stamped = dict(
            current,
            runner_class="ci-ubuntu-latest",
            arch="benchmark-fake-arch-not-a-machine",
        )
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
        assert _rc() in result.output  # current runner_class (env-dependent)
        assert "benchmark-fake-arch-not-a-machine" in result.output  # baseline arch
        assert platform.machine() in result.output  # current arch

    def test_matching_profile_prints_no_mismatch_warning(self, tmp_path, monkeypatch):
        """An exact profile match renders no mismatch marker."""
        _write_committed_baseline(tmp_path, monkeypatch)  # profile matches
        _patch_perf_suite(monkeypatch, median_ms=100.0)
        result = _invoke_perf_cli(["--baseline", "DS-v1"], tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        assert "MACHINE-PROFILE MISMATCH" not in result.output
        assert " vs current " not in result.output

    def test_same_hosted_pool_instances_do_not_mismatch(
        self, tmp_path, monkeypatch
    ):
        """Rolling-baseline support: two instances of GitHub's hosted pool
        (ci-github-actions-<N>, drifting N) are the same CLASS -- no mismatch
        marker for the runner_class field, so a main-minted rolling baseline
        compared PR-over-PR renders clean."""
        from cairn.bench.datasource import machine_profile

        # Make the CURRENT run stamp as a hosted-pool instance, then have
        # the baseline carry a different instance number of the same pool.
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("RUNNER_NAME", "GitHub Actions 1000001128")
        current = machine_profile()
        stamped = dict(
            current,
            runner_class="ci-github-actions-12",
        )
        _write_committed_baseline(tmp_path, monkeypatch, profile=stamped)
        # (the fixture delenvs GITHUB_ACTIONS/RUNNER_NAME -- re-arm so the
        # CLI's own stamp computes the hosted-pool profile)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("RUNNER_NAME", "GitHub Actions 1000001128")
        _patch_perf_suite(monkeypatch, median_ms=100.0)
        result = _invoke_perf_cli(["--baseline", "DS-v1"], tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        assert "MACHINE-PROFILE MISMATCH" not in result.output

    def test_different_hosted_runner_names_still_mismatch(
        self, tmp_path, monkeypatch
    ):
        """Class bucketing only folds the instance suffix: a genuinely
        different runner name (ci-github-actions-12 vs ci-self-hosted-7)
        still warns, and reference-local never buckets with ci-*."""
        from cairn.bench.datasource import machine_profile

        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("RUNNER_NAME", "GitHub Actions 1000001128")
        current = machine_profile()
        stamped = dict(current, runner_class="ci-self-hosted-7")
        _write_committed_baseline(tmp_path, monkeypatch, profile=stamped)
        _patch_perf_suite(monkeypatch, median_ms=100.0)
        result = _invoke_perf_cli(["--baseline", "DS-v1"], tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        assert "MACHINE-PROFILE MISMATCH" in result.output
        assert "runner_class: baseline ci-self-hosted-7 vs current " in result.output

    def test_unstamped_baseline_notes_unknown_not_mismatch(self, tmp_path, monkeypatch):
        """A pre- baseline (no machine_profile key) is 'unknown', not
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
        """Unknown version exits 1 naming the version (and the ones
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

    def test_invalid_json_baseline_exits_with_message(self, tmp_path, monkeypatch):
        """A corrupt --compare file reports the JSON error instead of raising."""
        _patch_perf_suite(monkeypatch, median_ms=100.0)
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        result = _invoke_perf_cli(["--compare", str(bad)], tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert "not valid JSON" in result.output

    def test_save_write_failure_exits_with_message(self, tmp_path, monkeypatch):
        """An unwritable --save path reports the write error instead of raising."""
        _patch_perf_suite(monkeypatch, median_ms=100.0)
        target_dir = tmp_path / "savedir"
        target_dir.mkdir()
        result = _invoke_perf_cli(["--save", str(target_dir)], tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert "Baseline save failed" in result.output

    def test_bad_sizes_exits_with_message_not_traceback(self, tmp_path, monkeypatch):
        """Non-integer --sizes is a usage error, not an uncaught ValueError."""
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "sizes.db"))
        from click.testing import CliRunner
        from cairn.cli import main

        result = CliRunner().invoke(
            main, ["bench", "--suite", "scaling", "--sizes", "abc"]
        )
        assert result.exit_code == 1
        assert "--sizes expects comma-separated integers" in result.output
        assert "Traceback" not in result.output

    def test_empty_sizes_exits_with_message(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "sizes2.db"))
        from click.testing import CliRunner
        from cairn.cli import main

        result = CliRunner().invoke(
            main, ["bench", "--suite", "scaling", "--sizes", ",,,"]
        )
        assert result.exit_code == 1
        assert "selects no corpus sizes" in result.output


# --- CLI --baseline on the scaling suite (points-shaped comparison) --------


class TestBenchCliScalingBaseline:
    def _write_scaling_baseline(self, tmp_path, points):
        from cairn.bench.datasource import machine_profile

        root = tmp_path / "benchmarks" / "baselines" / "DS-sc"
        root.mkdir(parents=True)
        (root / "scaling.json").write_text(
            json.dumps({"points": points, "machine_profile": machine_profile()}),
            encoding="utf-8",
        )

    def _scaling_report(self, n_files, build_s):
        from cairn.bench.report import ScalingPoint, ScalingReport

        return ScalingReport(points=[ScalingPoint(
            n_files=n_files, symbols=100,
            build_seconds=build_s, embed_seconds=1.0,
            db_size_mb=1.0, resolve_rate=1.0, peak_memory_mb=10.0,
        )])

    def _invoke(self, tmp_path, monkeypatch, extra_args):
        from click.testing import CliRunner
        from cairn.cli import main

        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "sc.db"))
        return CliRunner().invoke(main, [
            "bench", "--suite", "scaling", "--sizes", "5", *extra_args,
        ])

    def test_scaling_baseline_compares_points_and_exits_2_on_regression(
        self, tmp_path, monkeypatch
    ):
        """The scaling comparison reads the points shape: a slower build_s
        gates with exit 2 instead of the silent no-comparable success."""
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.delenv("RUNNER_NAME", raising=False)
        monkeypatch.chdir(tmp_path)
        self._write_scaling_baseline(tmp_path, [
            {"n_files": 5, "symbols": 100, "build_s": 1.0, "embed_s": 1.0,
             "db_mb": 1.0, "resolve_rate": 1.0, "peak_mem_mb": 10.0},
        ])
        monkeypatch.setattr(
            "cairn.bench.run_scaling_suite",
            lambda *a, **k: self._scaling_report(5, 2.0),
        )
        result = self._invoke(tmp_path, monkeypatch, ["--baseline", "DS-sc"])
        assert result.exit_code == 2, result.output
        assert "REGRESSED" in result.output
        assert "n=5 build_s" in result.output
        assert "No comparable operations" not in result.output

    def test_scaling_point_missing_from_baseline_is_flagged_not_silent(
        self, tmp_path, monkeypatch
    ):
        """A current point with no baseline counterpart renders '(no baseline)'
        rows instead of an empty comparison and a clean exit."""
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.delenv("RUNNER_NAME", raising=False)
        monkeypatch.chdir(tmp_path)
        self._write_scaling_baseline(tmp_path, [
            {"n_files": 5, "symbols": 100, "build_s": 1.0, "embed_s": 1.0,
             "db_mb": 1.0, "resolve_rate": 1.0, "peak_mem_mb": 10.0},
        ])
        monkeypatch.setattr(
            "cairn.bench.run_scaling_suite",
            lambda *a, **k: self._scaling_report(9, 1.0),
        )
        result = self._invoke(tmp_path, monkeypatch, ["--baseline", "DS-sc"])
        assert result.exit_code == 0, result.output
        assert "(no baseline)" in result.output
        assert "No comparable operations" not in result.output


# --- CLI p95 budget gate (--budgets / --budget-mode) ----------------------

_BUDGETED_TOOLS = (
    "find_definition",
    "get_callers",
    "impact_analysis",
    "search_symbols",
    "semantic_search",
    "explore",
)


def _budget_measurements(**overrides):
    """Fixed p95 (ms) per budgeted tool; defaults are within budget."""
    return {tool: 1.0 for tool in _BUDGETED_TOOLS} | overrides


def _patch_budgeted_perf_suite(monkeypatch, p95_ms_by_tool):
    """Swap the perf suite for fixed-p95 ops (deterministic budget scenarios)."""
    from cairn.bench.report import OpTiming, PerfReport
    from cairn.bench.timing import TimingResult

    report = PerfReport(
        symbols=10,
        edges=5,
        ops=[
            OpTiming(
                name=name,
                timing=TimingResult(
                    name=name, median=ms / 1000, p95=ms / 1000
                ),
            )
            for name, ms in p95_ms_by_tool.items()
        ],
    )
    monkeypatch.setattr(
        "cairn.bench.run_perf_suite", lambda *args, **kwargs: report
    )


def _write_budget_file(tmp_path, budgets):
    from cairn import __version__

    path = tmp_path / "budgets.json"
    path.write_text(
        json.dumps({
            "schema": "cairn-perf-budgets/1",
            "timestamp": "2026-10-05T00:00:00+00:00",
            "dataset": {"version": "DS-test"},
            "cairn_version": __version__,
            "machine_profile": {"runner_class": "ci-test"},
            "factor": 10,
            "budgets": budgets,
        }),
        encoding="utf-8",
    )
    return path


class TestBenchCliBudgets:
    def _budgets_at_5ms(self, tmp_path):
        return _write_budget_file(tmp_path, {tool: 5.0 for tool in _BUDGETED_TOOLS})

    def test_no_budget_flags_preserve_today_payload(self, tmp_path, monkeypatch):
        _patch_budgeted_perf_suite(monkeypatch, _budget_measurements())
        result = _invoke_perf_cli(["--json"], tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        assert "budgets" not in json.loads(result.stdout)

    def test_advise_prints_trend_lines_without_gating(self, tmp_path, monkeypatch):
        _patch_budgeted_perf_suite(
            monkeypatch, _budget_measurements(explore=6.0)
        )
        budgets = self._budgets_at_5ms(tmp_path)
        result = _invoke_perf_cli(["--budgets", str(budgets)], tmp_path, monkeypatch)
        assert result.exit_code == 0, result.output
        assert "p95 budgets (advise)" in result.output
        assert "EXCEEDS" in result.output
        assert "within" in result.output
        assert "6.0" in result.output and "5.0" in result.output

    def test_advise_json_carries_budgets_block(self, tmp_path, monkeypatch):
        _patch_budgeted_perf_suite(
            monkeypatch, _budget_measurements(explore=6.0)
        )
        budgets = self._budgets_at_5ms(tmp_path)
        result = _invoke_perf_cli(
            ["--budgets", str(budgets), "--json"], tmp_path, monkeypatch
        )
        assert result.exit_code == 0, result.output
        block = json.loads(result.stdout)["budgets"]
        assert block["mode"] == "advise"
        tools = {t["tool"]: t for t in block["tools"]}
        assert tools["explore"] == {
            "tool": "explore",
            "p95_ms": 6.0,
            "budget_ms": 5.0,
            "over": True,
        }
        assert tools["find_definition"]["over"] is False

    def test_enforce_breach_exits_3_with_pinned_message(
        self, tmp_path, monkeypatch
    ):
        _patch_budgeted_perf_suite(
            monkeypatch, _budget_measurements(explore=6.0)
        )
        budgets = self._budgets_at_5ms(tmp_path)
        result = _invoke_perf_cli(
            ["--budgets", str(budgets), "--budget-mode", "enforce"],
            tmp_path,
            monkeypatch,
        )
        assert result.exit_code == 3
        assert "tool explore: p95 6.0ms exceeds budget 5.0ms" in result.output
        assert "within" in result.output  # non-breached tools still identified

    def test_enforce_json_keeps_stdout_parseable(self, tmp_path, monkeypatch):
        _patch_budgeted_perf_suite(
            monkeypatch, _budget_measurements(explore=6.0)
        )
        budgets = self._budgets_at_5ms(tmp_path)
        result = _invoke_perf_cli(
            [
                "--budgets", str(budgets),
                "--budget-mode", "enforce",
                "--json",
            ],
            tmp_path,
            monkeypatch,
        )
        assert result.exit_code == 3
        payload = json.loads(result.stdout)
        assert payload["budgets"]["mode"] == "enforce"
        assert "tool explore: p95 6.0ms exceeds budget 5.0ms" in result.stderr

    def test_enforce_within_budget_exits_0(self, tmp_path, monkeypatch):
        _patch_budgeted_perf_suite(monkeypatch, _budget_measurements())
        budgets = self._budgets_at_5ms(tmp_path)
        result = _invoke_perf_cli(
            ["--budgets", str(budgets), "--budget-mode", "enforce"],
            tmp_path,
            monkeypatch,
        )
        assert result.exit_code == 0, result.output
        assert "EXCEEDS" not in result.output

    def test_enforce_breach_takes_precedence_over_regression_exit_2(
        self, tmp_path, monkeypatch
    ):
        _patch_budgeted_perf_suite(
            monkeypatch,
            _budget_measurements(find_definition=1.5, explore=6.0),
        )
        budgets = self._budgets_at_5ms(tmp_path)
        baseline = tmp_path / "baseline.json"
        baseline.write_text(
            json.dumps({"ops": [{"name": "find_definition", "median_ms": 0.5}]}),
            encoding="utf-8",
        )
        result = _invoke_perf_cli(
            [
                "--budgets", str(budgets),
                "--budget-mode", "enforce",
                "--compare", str(baseline),
            ],
            tmp_path,
            monkeypatch,
        )
        assert result.exit_code == 3
        assert "REGRESSED" in result.output

    def test_missing_budget_file_fails_promptly(self, tmp_path, monkeypatch):
        called = []
        monkeypatch.setattr(
            "cairn.bench.run_perf_suite", lambda *a, **k: called.append(1)
        )
        result = _invoke_perf_cli(
            ["--budgets", str(tmp_path / "nope.json")], tmp_path, monkeypatch
        )
        assert result.exit_code == 1
        assert "nope.json" in result.output
        assert called == []

    def test_budgets_rejected_off_perf_suite(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from cairn.cli import main

        called = []
        monkeypatch.setattr(
            "cairn.bench.run_scaling_suite", lambda *a, **k: called.append(1)
        )
        budgets = self._budgets_at_5ms(tmp_path)
        result = CliRunner().invoke(main, [
            "bench", "--suite", "scaling", "--sizes", "2",
            "--budgets", str(budgets),
        ])
        assert result.exit_code == 1
        assert "perf suite only" in result.output
        assert called == []

    def test_enforce_without_budgets_is_a_usage_error(self, tmp_path, monkeypatch):
        _patch_budgeted_perf_suite(monkeypatch, _budget_measurements())
        result = _invoke_perf_cli(["--budget-mode", "enforce"], tmp_path, monkeypatch)
        assert result.exit_code == 1
        assert "requires --budgets" in result.output

    def test_budget_mismatch_emits_run_results_before_exit(
        self, tmp_path, monkeypatch
    ):
        """A budget naming an unmeasured op fails AFTER the completed run's
        results are emitted, so the measurements are not discarded."""
        _patch_budgeted_perf_suite(monkeypatch, {"find_definition": 1.0})
        budgets = self._budgets_at_5ms(tmp_path)  # requires all six tools
        result = _invoke_perf_cli(
            ["--budgets", str(budgets), "--json"], tmp_path, monkeypatch
        )
        assert result.exit_code == 1
        payload = json.loads(result.stdout)
        assert "ops" in payload
        assert "Budget evaluation failed" in result.stderr
        assert "missing budgeted operations" in result.stderr


class TestProfileClassBucketing:
    """Unit tests for _profile_class (the rolling-baseline class rule)."""

    def test_runner_class_strips_instance_suffix(self):
        from cairn.cli.bench import _profile_class

        assert (
            _profile_class("runner_class", "ci-github-actions-1000001128")
            == _profile_class("runner_class", "ci-github-actions-12")
            == "ci-github-actions"
        )
        # Different runner NAMES stay distinct; reference-local is exact.
        assert _profile_class("runner_class", "ci-self-hosted-7") == "ci-self-hosted"
        assert _profile_class("runner_class", "reference-local") == "reference-local"
        # A suffix that is part of the name (letters) is not stripped.
        assert _profile_class("runner_class", "ci-runner-x86") == "ci-runner-x86"

    def test_os_buckets_linux_kernels_only(self):
        from cairn.cli.bench import _profile_class

        assert _profile_class(
            "os", "Linux-6.17.0-1022-azure-x86_64-with-glibc2.39"
        ) == _profile_class("os", "Linux-6.20.1-1234-azure-x86_64") == "Linux"
        # Non-Linux values (macOS point releases) stay exact: the reference
        # minter's OS delta is a real comparability fact.
        assert _profile_class("os", "macOS-26.5.2-arm64") == "macOS-26.5.2-arm64"

    def test_other_fields_and_unstamped_pass_through(self):
        from cairn.cli.bench import _UNSTAMPED, _profile_class

        assert _profile_class("arch", "x86_64") == "x86_64"
        assert _profile_class("cpu_count", 4) == "4"
        assert _profile_class("runner_class", _UNSTAMPED) is _UNSTAMPED


class TestSweBenchWorkspaceValidation:
    """_swe_bench_workspaces rejects malformed repo/base_commit row fields."""

    @pytest.mark.parametrize(
        "repo, sha",
        [
            ("ok/repo", "../../outside"),  # traversal via base_commit
            ("../escape", "c" * 40),  # traversal via repo
        ],
    )
    def test_malformed_row_fields_rejected(self, tmp_path, repo, sha):
        from pathlib import Path

        from cairn.cli.bench import _swe_bench_workspaces

        cache_root = tmp_path / "cache"
        task = {"instance_id": "t", "repo": repo, "base_commit": sha}
        # The fields resolve to an existing warm-cache entry (outside the
        # cache root for the traversal case), so the lookup never reaches
        # the clone branch and no network is touched.
        target = Path(os.path.normpath(cache_root / f"{repo.replace('/', '__')}-{sha}"))
        (target / ".git").mkdir(parents=True)
        with pytest.raises(ValueError):
            _swe_bench_workspaces([task], cache_root=cache_root, quiet=True)

    def test_valid_fields_warm_cache_hit(self, tmp_path):
        from cairn.cli.bench import _swe_bench_workspaces

        cache_root = tmp_path / "cache"
        sha = "c" * 40
        target = cache_root / f"ok__repo-{sha}"
        (target / ".git").mkdir(parents=True)
        task = {"instance_id": "t", "repo": "ok/repo", "base_commit": sha}
        workspaces = _swe_bench_workspaces([task], cache_root=cache_root, quiet=True)
        assert workspaces == {"t": str(target)}

    def test_replace_fallback_rejects_invalid_target(self, tmp_path, monkeypatch):
        """The os.replace fallback only adopts a concurrent-run target that is
        itself a valid checkout, never a tree that failed the .git check."""
        from cairn.cli.bench import _swe_bench_workspaces

        cache_root = tmp_path / "cache"
        sha = "c" * 40
        target = cache_root / f"ok__repo-{sha}"
        target.mkdir(parents=True)
        (target / "stray.txt").write_text("junk", encoding="utf-8")
        task = {"instance_id": "t", "repo": "ok/repo", "base_commit": sha}
        monkeypatch.setattr("cairn.cli.bench.subprocess.run", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="not a valid checkout"):
            _swe_bench_workspaces([task], cache_root=cache_root, quiet=True)

    def test_failed_clone_leaves_no_staging_dir(self, tmp_path, monkeypatch):
        """A failed clone/checkout removes its .tmp staging tree from the
        persistent shared cache."""
        import subprocess as sp

        from cairn.cli.bench import _swe_bench_workspaces

        cache_root = tmp_path / "cache"
        task = {"instance_id": "t", "repo": "ok/repo", "base_commit": "c" * 40}
        from pathlib import Path

        def _fake_run(cmd, *args, **kwargs):
            if "clone" in cmd:
                Path(cmd[-1]).mkdir(parents=True)  # git's partial staging tree
                return None
            raise sp.CalledProcessError(
                128, "git", stderr="fatal: unknown revision"
            )

        monkeypatch.setattr("cairn.cli.bench.subprocess.run", _fake_run)
        with pytest.raises(RuntimeError, match="checkout of ok/repo"):
            _swe_bench_workspaces([task], cache_root=cache_root, quiet=True)
        assert not list(cache_root.glob(".tmp-*"))


# --- CLI option validation (--runs / --repeats bounds) ----------------------


class TestBenchCliRunValidation:
    def test_runs_below_one_rejected_at_parse_time(self, tmp_path, monkeypatch):
        """--runs 0 is a usage error (exit 2), never an empty-median crash."""
        def _boom(*args, **kwargs):
            raise AssertionError("suite must not run for an invalid --runs")

        monkeypatch.setattr("cairn.bench.agent_suite.run_agent_suite", _boom)
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "runs.db"))
        from click.testing import CliRunner
        from cairn.cli import main

        result = CliRunner().invoke(main, [
            "bench", "--suite", "agent", "--runs", "0",
            "--n-files", "3", "--complexity", "low",
            "--embed-backend", "hash",
        ])
        assert result.exit_code == 2
        assert not isinstance(result.exception, AssertionError)
        assert "runs" in result.output

    def test_repeats_below_one_rejected_at_parse_time(self, tmp_path, monkeypatch):
        """--repeats 0 is a usage error (exit 2), never a silent single run."""
        def _boom(*args, **kwargs):
            raise AssertionError("suite must not run for an invalid --repeats")

        monkeypatch.setattr("cairn.bench.run_perf_suite", _boom)
        monkeypatch.setenv("CAIRN_DB", str(tmp_path / "repeats.db"))
        from click.testing import CliRunner
        from cairn.cli import main

        result = CliRunner().invoke(main, [
            "bench", "--suite", "perf", "--repeats", "0",
            "--n-files", "3", "--complexity", "low",
            "--embed-backend", "hash",
        ])
        assert result.exit_code == 2
        assert not isinstance(result.exception, AssertionError)
        assert "repeats" in result.output
