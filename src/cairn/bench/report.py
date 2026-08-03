"""Result dataclasses + rendering for the benchmark suites.

``PerfReport`` holds per-operation ``TimingResult``s from the perf suite;
``ScalingReport`` holds the size→time/DB-size curve from the scaling suite.
Both render to a rich table (via ``cli/display.print_table``) for humans and
to a JSON-serialisable dict for CI / baseline diffing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

from .timing import TimingResult


def _fmt_ms(seconds: float) -> str:
    """Render a seconds value as a readable millisecond string."""
    return f"{seconds * 1000:.1f} ms" if seconds < 1 else f"{seconds:.3f} s"


@dataclass
class OpTiming:
    """One operation's timing in the perf suite (name + its TimingResult)."""

    name: str
    timing: TimingResult

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "median_ms": round(self.timing.median * 1000, 2),
            "p50_ms": round(self.timing.p50 * 1000, 2),
            "p95_ms": round(self.timing.p95 * 1000, 2),
            "p99_ms": round(self.timing.p99 * 1000, 2),
            "ops_per_sec": round(self.timing.ops_per_sec, 2),
        }


@dataclass
class PerfReport:
    """Results of a perf-suite run: build, embed, and per-query timings."""

    corpus: dict = field(default_factory=dict)
    db_path: str = ""
    db_size_mb: float = 0.0
    symbols: int = 0
    edges: int = 0
    ops: List[OpTiming] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "corpus": self.corpus,
            "db_path": self.db_path,
            "db_size_mb": round(self.db_size_mb, 2),
            "symbols": self.symbols,
            "edges": self.edges,
            "ops": [op.to_dict() for op in self.ops],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_table(self) -> str:
        """Render the report as a rich table via cli.display.

        Returns a string summary (also prints the table when a TTY is
        available). Kept as a method so the CLI layer is a thin caller.
        """
        from ..cli.display import print_table

        rows = []
        for op in self.ops:
            t = op.timing
            rows.append([
                op.name,
                _fmt_ms(t.median),
                _fmt_ms(t.p95),
                f"{t.ops_per_sec:.1f}",
            ])
        print_table(
            f"cairn perf benchmark  ({self.symbols:,} symbols, {self.edges:,} edges, {self.db_size_mb:.1f} MB DB)",
            columns=["operation", "median", "p95", "ops/sec"],
            rows=rows,
        )
        return self.to_json()


@dataclass
class ScalingPoint:
    """One (corpus-size, measurement) sample on the scaling curve."""

    n_files: int
    symbols: int
    build_seconds: float
    embed_seconds: float
    db_size_mb: float
    resolve_rate: float
    peak_memory_mb: float = 0.0

    def to_dict(self) -> dict:
        return {
            "n_files": self.n_files,
            "symbols": self.symbols,
            "build_s": round(self.build_seconds, 3),
            "embed_s": round(self.embed_seconds, 3),
            "db_mb": round(self.db_size_mb, 2),
            "resolve_rate": round(self.resolve_rate, 3),
            "peak_mem_mb": round(self.peak_memory_mb, 2),
        }


@dataclass
class ScalingReport:
    """The size→cost curve from the scaling suite."""

    points: List[ScalingPoint] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"points": [p.to_dict() for p in self.points]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_table(self) -> str:
        from ..cli.display import print_table

        rows = [
            [
                f"{p.n_files:,}",
                f"{p.symbols:,}",
                _fmt_ms(p.build_seconds),
                _fmt_ms(p.embed_seconds),
                f"{p.db_size_mb:.1f}",
                f"{p.resolve_rate:.0%}",
                f"{p.peak_memory_mb:.0f}" if p.peak_memory_mb else "-",
            ]
            for p in self.points
        ]
        print_table(
            "cairn scaling benchmark",
            columns=["files", "symbols", "build", "embed", "DB MB", "resolve", "peak MB"],
            rows=rows,
        )
        return self.to_json()


def compare_reports(baseline: dict, current: dict, threshold: float = 0.15) -> dict:
    """Compare two perf reports and flag regressions beyond ``threshold``.

    ``baseline``/``current`` are the dicts from ``PerfReport.to_dict()``.
    Returns a dict mapping operation name → {baseline_ms, current_ms, delta_pct,
    regressed(bool)}. An op is "regressed" if current median is more than
    ``threshold`` (default 15%) slower than baseline.
    """
    base_ops = {op["name"]: op["median_ms"] for op in baseline.get("ops", [])}
    cur_ops = {op["name"]: op["median_ms"] for op in current.get("ops", [])}
    result = {}
    for name, cur_ms in cur_ops.items():
        base_ms = base_ops.get(name)
        if base_ms is None or base_ms == 0:
            continue
        delta = (cur_ms - base_ms) / base_ms
        result[name] = {
            "baseline_ms": round(base_ms, 2),
            "current_ms": round(cur_ms, 2),
            "delta_pct": round(delta * 100, 1),
            "regressed": delta > threshold,
        }
    return result
