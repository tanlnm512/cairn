"""SC-2 / TC-010: instrumented recording stays off the tool hot path.

Median latency of an instrumented call must stay within 5% of the bare
call (buffered recording, O(1) size capture — tech-spec D-005).
"""
from __future__ import annotations

import statistics
import time

from cairn.mcp_server.metric_buffering import instrument


def _work(n: int = 12000) -> str:
    # Stand-in for a real tool body at realistic scale (~1-2ms: one sqlite
    # round-trip + JSON serialization). The wrapper's bookkeeping is a fixed
    # ~microsecond cost; the ratio budget only stays meaningful against a
    # body of real-tool size, not a trivial one.
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
    # Best-of-4 medians keeps the ratio stable under scheduler noise.
    bare = min(_median_us(lambda: _work()) for _ in range(4))
    wrapped = min(_median_us(lambda: _bench_tool("overhead-probe")) for _ in range(4))
    ratio = wrapped / bare
    added = wrapped - bare
    # The ceiling catches structural regressions (a synchronous write per
    # call would add ms-scale); it deliberately leaves headroom for
    # interpreter/allocator variance on slow runners, which the ratio below
    # is the authoritative guard for.
    assert added < 500, (
        f"recording adds {added:.1f}us/call — buffering is on the hot path "
        f"(bare {bare:.1f}us, wrapped {wrapped:.1f}us)"
    )
    assert ratio < 1.05, f"recording overhead {ratio:.3f}x exceeds the 1.05 budget (bare {bare:.1f}us, wrapped {wrapped:.1f}us)"
