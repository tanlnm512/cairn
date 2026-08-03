"""cairn benchmark module: performance + scalability suites.

Two complementary benchmarks, both stdlib-only (no pytest-benchmark/pyinstrument
dependency):

- :func:`run_perf_suite` — per-operation latency (build / embed / query
  battery) with warmup + percentile distribution. Answers "did my change
  regress?".
- :func:`run_scaling_suite` — build/embed cost vs corpus-size curve. Answers
  "will this scale to a large monorepo?".

Both are exposed via the ``cairn bench`` CLI command (see ``cli/bench.py``).
"""
from .corpus import generate_corpus, corpus_stats
from .perf_suite import run_perf_suite
from .report import PerfReport, ScalingReport, ScalingPoint, compare_reports
from .scaling_suite import run_scaling_suite
from .timing import TimingResult, MemoryResult, time_call, percentiles, peak_memory

__all__ = [
    "generate_corpus",
    "corpus_stats",
    "run_perf_suite",
    "run_scaling_suite",
    "PerfReport",
    "ScalingReport",
    "ScalingPoint",
    "compare_reports",
    "TimingResult",
    "MemoryResult",
    "time_call",
    "percentiles",
    "peak_memory",
]
