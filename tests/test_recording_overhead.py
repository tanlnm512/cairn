"""SC-2 / TC-010: instrumented recording stays off the tool hot path.

Median latency of an instrumented call must stay within 5% of the bare
call (buffered recording, O(1) size capture — tech-spec D-005).
"""
from __future__ import annotations

import statistics
import time

from cairn.mcp_server.metric_buffering import instrument


def _work(n: int = 2000) -> str:
    # Stand-in for a real tool body: enough work (~100us) that the wrapper's
    # bookkeeping is a small fraction, like real MCP tools.
    parts = []
    for i in range(n):
        parts.append(f"{i}-{'x' * 12}")
    return ",".join(parts)


@instrument
def _bench_tool(query: str, limit: int = 10) -> str:
    return _work() + query[:limit]


def _median_us(fn, calls: int = 120) -> float:
    for _ in range(10):  # warmup
        fn()
    samples = []
    for _ in range(calls):
        t0 = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - t0) / 1000.0)
    return statistics.median(samples)


def test_recording_overhead_under_5_percent():
    # Best-of-3 medians keeps the ratio stable under scheduler noise.
    bare = min(_median_us(lambda: _work()) for _ in range(3))
    wrapped = min(_median_us(lambda: _bench_tool("overhead-probe")) for _ in range(3))
    ratio = wrapped / bare
    assert ratio < 1.05, f"recording overhead {ratio:.3f}x exceeds the 1.05 budget (bare {bare:.1f}us, wrapped {wrapped:.1f}us)"
