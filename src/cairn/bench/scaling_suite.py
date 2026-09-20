"""Scaling benchmark: how build/embed cost grows with corpus size."""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Sequence

from .corpus import generate_corpus
from .report import ScalingPoint, ScalingReport
from .timing import peak_memory

# Closure-op budget at the 1000-file gate point: wall seconds and peak traced
# memory (MB) the transitive-closure build must stay within at the multiplied
# structural-edge volume.
CLOSURE_BUDGET_WALL_SECONDS = 60.0
CLOSURE_BUDGET_PEAK_MB = 512.0
CLOSURE_EDGE_FACTOR = 5


def synthesize_structural_edges(
    conn: sqlite3.Connection, *, factor: int = CLOSURE_EDGE_FACTOR
) -> int:
    """Insert ``factor - 1`` replicas per structural edge, each with a fresh id
    and its target cyclically shifted by the replica index over the sorted
    symbol ids (name-only targets shift over the sorted symbol names), then
    return the number of rows inserted."""
    if factor < 2:
        return 0
    from ..graph.traversal import STRUCTURAL_EDGE_KINDS

    cur = conn.cursor()
    id_to_name = dict(cur.execute("SELECT id, name FROM symbols").fetchall())
    if not id_to_name:
        return 0
    ids = sorted(id_to_name)
    id_pos = {sid: i for i, sid in enumerate(ids)}
    name_domain = sorted(set(id_to_name.values()))
    name_pos = {n: i for i, n in enumerate(name_domain)}
    kind_ph = ",".join("?" for _ in STRUCTURAL_EDGE_KINDS)
    rows = []
    for eid, src, tgt, tname, kind, line, col in cur.execute(
        f"SELECT id, source_id, target_id, target_name, kind, line, column"
        f" FROM edges WHERE kind IN ({kind_ph})",
        STRUCTURAL_EDGE_KINDS,
    ):
        for k in range(1, factor):
            new_tgt, new_name = tgt, tname
            if tgt in id_pos:
                shifted = ids[(id_pos[tgt] + k) % len(ids)]
                new_tgt, new_name = shifted, id_to_name[shifted]
            elif tname:
                new_name = name_domain[(name_pos[tname] + k) % len(name_domain)]
            rows.append((f"{eid}~synth{k}", src, new_tgt, new_name, kind, line, col))
    cur.executemany(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, column)"
        " VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


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
    build the graph into a throwaway DB, embed it, multiply the structural
    edges to ``CLOSURE_EDGE_FACTOR`` volume, and record one
    :class:`ScalingPoint` carrying the timed + memory-traced closure build.
    Each size gets its own DB and corpus so there's no carryover between
    samples.

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
        from cairn.graph.dataflow import build_transitive_closure
        from cairn.graph.schema import get_db
        from cairn.graph.traversal import STRUCTURAL_EDGE_KINDS

        emb.reset_backend_cache()

        # Build + embed under a single memory trace so peak_memory reflects
        # the full per-size cost; each phase is timed inline with perf_counter.
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
                # multivector pinned off: embed is a timed op here.
                embed_stats = emb.embed_all(c, reap_orphans=False, multivector=False)
            finally:
                c.close()
            embed_s = _t.perf_counter() - _t0

        mem, _ = peak_memory(_build_and_embed)

        # Closure op: multiply structural-edge volume deterministically, then
        # time + memory-trace one transitive-closure build over it.
        conn = get_db(db_path)
        try:
            synthesize_structural_edges(conn)
            kind_ph = ",".join("?" for _ in STRUCTURAL_EDGE_KINDS)
            structural_edges = conn.execute(
                f"SELECT COUNT(*) FROM edges WHERE kind IN ({kind_ph})",
                STRUCTURAL_EDGE_KINDS,
            ).fetchone()[0]

            def _closure_op() -> float:
                _t0 = _t.perf_counter()
                build_transitive_closure(conn)
                return _t.perf_counter() - _t0

            closure_mem, closure_s = peak_memory(_closure_op)
        finally:
            conn.close()

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
            closure_seconds=closure_s,
            closure_peak_memory_mb=closure_mem.peak_mb,
            closure_edges=structural_edges,
        )
        report.points.append(point)
        if progress:
            progress("size_done", n=n, symbols=symbols,
                     build_s=round(build_s, 3), embed_s=round(embed_s, 3),
                     closure_s=round(closure_s, 3))

        # Clean up this size's DB to keep disk usage bounded across the sweep.
        try:
            os.remove(db_path)
        except OSError:
            pass

    if progress:
        progress("scaling_done", points=len(report.points))
    return report
