"""cairn benchmark module: performance + scalability suites."""
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
