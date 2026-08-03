"""Timing primitives for the benchmark suites.

Stdlib-only (``time.perf_counter`` + ``tracemalloc``). The one shared core
every suite calls: warmup-and-repeat timing with percentile distribution, plus
a lightweight peak-memory wrapper. No external benchmarking dependency.
"""
from __future__ import annotations

import statistics
import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Callable, List, Optional, TypeVar

T = TypeVar("T")


@dataclass
class TimingResult:
    """Percentile distribution of a repeated timing sample.

    ``median``/``p50``/``p95``/``p99`` are the fields a regression report reads;
    ``samples`` is kept for deeper analysis. All times are seconds.
    """

    name: str
    samples: List[float] = field(default_factory=list)
    median: float = 0.0
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    mean: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0

    @property
    def ops_per_sec(self) -> float:
        """Reciprocal of the median — throughput for a repeated operation."""
        return 1.0 / self.median if self.median > 0 else 0.0


def percentiles(samples: List[float]) -> dict:
    """Return p50/p95/p99/mean/min/max for a list of values.

    Uses ``statistics.quantiles`` (Python 3.8+) with ``n=100`` so the values
    are true percentiles. For very small sample sets (fewer than 100 values)
    the higher percentiles collapse toward the max, which is the correct
    behavior — there isn't enough data to distinguish p95 from p99.
    """
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "min": 0.0, "max": 0.0}
    if len(samples) == 1:
        v = samples[0]
        return {"p50": v, "p95": v, "p99": v, "mean": v, "min": v, "max": v}
    qs = statistics.quantiles(samples, n=100, method="inclusive")
    return {
        "p50": qs[49],
        "p95": qs[94],
        "p99": qs[98],
        "mean": statistics.fmean(samples),
        "min": min(samples),
        "max": max(samples),
    }


def time_call(
    fn: Callable[[], T],
    *,
    name: str = "",
    warmup: int = 1,
    repeats: int = 5,
) -> tuple[TimingResult, T]:
    """Time ``fn`` with discarded warmup, then ``repeats`` measured runs.

    Returns ``(TimingResult, last_return_value)`` so a caller can assert the
    timed work actually produced something (e.g. a non-empty result set),
    not just that it ran. Warmup is essential: the first build embeds/writes,
    the first query may pay import/JIT costs — those contaminate a single shot.
    """
    last: Optional[T] = None
    for _ in range(max(0, warmup)):
        last = fn()
    samples: List[float] = []
    for _ in range(max(1, repeats)):
        start = time.perf_counter()
        last = fn()
        samples.append(time.perf_counter() - start)
    stats = percentiles(samples)
    result = TimingResult(
        name=name or getattr(fn, "__name__", "call"),
        samples=samples,
        median=statistics.median(samples),
        p50=stats["p50"],
        p95=stats["p95"],
        p99=stats["p99"],
        mean=stats["mean"],
        minimum=stats["min"],
        maximum=stats["max"],
    )
    return result, last  # type: ignore[return-value]


@dataclass
class MemoryResult:
    """Peak memory recorded by tracemalloc around a call."""

    peak_bytes: int = 0

    @property
    def peak_mb(self) -> float:
        return self.peak_bytes / (1024 * 1024)


def peak_memory(fn: Callable[[], T]) -> tuple[MemoryResult, T]:
    """Run ``fn`` under tracemalloc and return (peak-RSS-bytes, return value).

    tracemalloc tracks Python-allocated memory, not C-extension buffers (torch
    tensors, native tree-sitter nodes). For the build/embed paths — which are
    Python-dominated — it's a faithful RSS proxy and has no dependency cost.
    """
    tracemalloc.start()
    try:
        result = fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return MemoryResult(peak_bytes=peak), result
