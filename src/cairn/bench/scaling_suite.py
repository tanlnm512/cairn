"""Scaling benchmark: how build/embed cost grows with corpus size.

Answers "will cairn scale to a large monorepo?". Generates a synthetic
corpus at each size in ``sizes``, builds + embeds it, and records build-time,
embed-time, DB-size, resolve-rate, and peak memory. The resulting curve shows
where the cost becomes superlinear — the same shape of finding the ROADMAP's
"global symbol index rebuild per resolve" wall would surface as.

Each size is single-shot (scaling is about the curve shape, not precise p95);
memory is captured via tracemalloc around the whole build+embed.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Sequence

from .corpus import generate_corpus
from .report import ScalingPoint, ScalingReport
from .timing import peak_memory


def _resolve_rate(build_stats: dict) -> float:
    """Fraction of edges that resolved to a definition (exact + ambiguous)."""
    resolution = build_stats.get("resolution", {}) or {}
    total = sum(resolution.values())
    if not total:
        return 0.0
    resolved = resolution.get("exact", 0) + resolution.get("ambiguous", 0)
    return resolved / total


def run_scaling_suite(
    root: Path,
    *,
    sizes: Sequence[int] = (100, 500, 1000, 5000),
    complexity: str = "medium",
    embed_backend: str = "hash",
    progress=None,
) -> ScalingReport:
    """Run the scaling benchmark over the given corpus sizes.

    For each size in ``sizes``: generate a fresh corpus under ``root/<size>/``,
    build the graph into a throwaway DB, embed it, and record one
    :class:`ScalingPoint`. Each size gets its own DB and corpus so there's no
    carryover between samples.

    ``root`` should be a temp directory; this function creates and removes
    subdirectories under it.
    """
    report = ScalingReport()

    for n in sizes:
        size_root = root / f"size_{n}"
        if size_root.exists():
            shutil.rmtree(size_root)
        size_root.mkdir(parents=True)

        corpus = generate_corpus(size_root, n, complexity=complexity)
        db_path = str(size_root / "bench.db")
        if os.path.exists(db_path):
            os.remove(db_path)
        os.environ["CAIRN_DB"] = db_path
        os.environ["CAIRN_EMBED_BACKEND"] = embed_backend

        from cairn.graph import embeddings as emb
        from cairn.graph.builder import build_graph
        from cairn.graph.schema import get_db

        emb.reset_backend_cache()

        # Build + embed under a single memory trace so peak_memory reflects
        # the full per-size cost a user would pay. Each phase is timed inline
        # with perf_counter so we get both the memory footprint and the
        # build/embed split from one pass (no double build).
        import time as _t

        build_stats: dict = {}
        embed_stats: dict = {}
        build_s: float = 0.0
        embed_s: float = 0.0

        def _build_and_embed():
            nonlocal build_stats, embed_stats, build_s, embed_s
            _t0 = _t.perf_counter()
            build_stats = build_graph(workspace=str(corpus), db_path=db_path)
            build_s = _t.perf_counter() - _t0

            emb.reset_backend_cache()
            _t0 = _t.perf_counter()
            c = get_db(db_path)
            try:
                embed_stats = emb.embed_all(c, reap_orphans=False)
            finally:
                c.close()
            embed_s = _t.perf_counter() - _t0

        mem, _ = peak_memory(_build_and_embed)

        symbols = build_stats.get("symbols", 0)
        db_mb = Path(db_path).stat().st_size / (1024 * 1024) if os.path.exists(db_path) else 0.0

        point = ScalingPoint(
            n_files=n,
            symbols=symbols,
            build_seconds=build_s,
            embed_seconds=embed_s,
            db_size_mb=db_mb,
            resolve_rate=_resolve_rate(build_stats),
            peak_memory_mb=mem.peak_mb,
        )
        report.points.append(point)
        if progress:
            progress("size_done", n=n, symbols=symbols,
                     build_s=round(build_s, 3), embed_s=round(embed_s, 3))

        # Clean up this size's DB to keep disk usage bounded across the sweep.
        try:
            os.remove(db_path)
        except OSError:
            pass

    if progress:
        progress("scaling_done", points=len(report.points))
    return report
