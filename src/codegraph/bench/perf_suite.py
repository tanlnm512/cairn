"""Perf benchmark: latency of build, embed, and the query battery.

Answers "did my change make build/embed/query faster or slower?". Each stage
runs with discarded warmup + repeated timing so the percentile distribution
is stable (a single shot is contaminated by import/JIT/disk-cold costs).

Build timing splits the phases (scan/parse/insert/resolve) via ``build_graph``'s
``progress`` event callback — the total is reported alongside the phase
breakdown so a regression can be localized ("resolve got slower" rather than
"build got slower").
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

from .corpus import generate_corpus, corpus_stats
from .report import OpTiming, PerfReport
from .timing import time_call


def _phase_timings(workspace: str, db_path: str) -> tuple[dict, dict]:
    """Build once, capturing per-phase wall-clock time from progress events.

    Returns ``(build_stats, phase_seconds)`` where ``phase_seconds`` maps
    phase names (scan/parse/insert/resolve/persist) to their elapsed time.

    The build emits events in this order: ``scan`` (once), ``parse_progress``
    (per file), ``parse_done`` (once), ``insert_progress`` (per batch),
    ``resolve_start``/``resolve_done`` (per repo), ``persist`` (once). We map
    each to a phase by tracking transitions: a phase starts at its first event
    and ends at the first event of the next phase.
    """
    from codegraph.graph.builder import build_graph

    PHASE_OF = {
        "scan": "scan",
        "parse_progress": "parse",
        "parse_done": "parse",
        "insert_progress": "insert",
        "resolve_start": "resolve",
        "resolve_done": "resolve",
        "persist": "persist",
    }
    marks: list[tuple[str, float]] = []  # (phase, timestamp) ordered log
    import time as _t

    def _progress(phase: str, **kw):
        ph = PHASE_OF.get(phase)
        if ph is None:
            return
        marks.append((ph, _t.perf_counter()))

    stats = build_graph(workspace=workspace, db_path=db_path, progress=_progress)

    # Collapse the ordered marks into per-phase elapsed time. Each phase's
    # duration is from its FIRST mark to the first mark of any LATER phase.
    phase_seconds: dict[str, float] = {}
    seen_phases: list[str] = []
    first_ts: dict[str, float] = {}
    for ph, ts in marks:
        if ph not in first_ts:
            first_ts[ph] = ts
            seen_phases.append(ph)
    # Phase end = start of the next phase in seen order (or last mark overall).
    for i, ph in enumerate(seen_phases):
        start = first_ts[ph]
        end = first_ts[seen_phases[i + 1]] if i + 1 < len(seen_phases) else marks[-1][1]
        if end > start:
            phase_seconds[ph] = end - start
    return stats, phase_seconds


def run_perf_suite(
    workspace: str,
    db_path: str,
    *,
    embed_backend: str = "hash",
    warmup: int = 1,
    repeats: int = 3,
    query_repeats: int = 5,
    progress=None,
) -> PerfReport:
    """Run the perf benchmark against ``workspace``.

    Assumes the corpus already exists at ``workspace`` (use
    :func:`generate_corpus` first, or point at a real repo). The graph is built
    fresh into ``db_path`` each run.

    ``embed_backend`` defaults to ``hash`` (dep-free, deterministic). Set to a
    real backend only if you've installed the ``[semantic]`` extra — the perf
    suite is otherwise self-contained.

    ``query_repeats`` is higher than ``repeats`` because queries are cheap and
    need more samples for a stable p95.
    """
    report = PerfReport(db_path=db_path)
    report.corpus = corpus_stats(Path(workspace))

    # This suite mutates two process-global env vars to point the build/embed
    # at the bench DB and the chosen backend. Snapshot them so callers (e.g.
    # tests reusing one process) aren't left with the bench's values after the
    # run. Restored in the `finally` around the return.
    _saved_db = os.environ.get("CODEGRAPH_DB")
    _saved_embed_backend = os.environ.get("CODEGRAPH_EMBED_BACKEND")
    _had_db = "CODEGRAPH_DB" in os.environ
    _had_embed_backend = "CODEGRAPH_EMBED_BACKEND" in os.environ

    def _restore_env() -> None:
        if _had_db:
            os.environ["CODEGRAPH_DB"] = _saved_db  # type: ignore[assignment]
        else:
            os.environ.pop("CODEGRAPH_DB", None)
        if _had_embed_backend:
            os.environ["CODEGRAPH_EMBED_BACKEND"] = _saved_embed_backend  # type: ignore[assignment]
        else:
            os.environ.pop("CODEGRAPH_EMBED_BACKEND", None)

    # --- Build phase ------------------------------------------------------
    # Build timing is single-shot (the phase-event split is the signal, and a
    # second build into the same DB is incremental, not a clean rebuild).
    os.environ["CODEGRAPH_DB"] = db_path
    build_stats, phase_seconds = _phase_timings(workspace, db_path)
    report.symbols = build_stats.get("symbols", 0)
    report.edges = build_stats.get("edges", 0)
    if progress:
        progress("build_done", symbols=report.symbols, edges=report.edges)

    from .timing import TimingResult
    # Total build from the phase sum (more stable than a single wall-clock).
    total_build = sum(phase_seconds.values())
    report.ops.append(OpTiming(
        name="build (total)",
        timing=TimingResult(name="build (total)", samples=[total_build],
                            median=total_build, p50=total_build, p95=total_build,
                            p99=total_build, mean=total_build,
                            minimum=total_build, maximum=total_build),
    ))
    for phase, secs in sorted(phase_seconds.items()):
        if secs > 0:
            report.ops.append(OpTiming(
                name=f"build.{phase}",
                timing=TimingResult(name=phase, samples=[secs], median=secs,
                                    p50=secs, p95=secs, p99=secs, mean=secs,
                                    minimum=secs, maximum=secs),
            ))

    # --- Embed phase ------------------------------------------------------
    from codegraph.graph import embeddings as emb
    from codegraph.graph.schema import get_db

    os.environ["CODEGRAPH_EMBED_BACKEND"] = embed_backend
    emb.reset_backend_cache()

    conn = get_db(db_path)
    try:
        def _do_embed():
            c = get_db(db_path)
            try:
                return emb.embed_all(c, reap_orphans=False)
            finally:
                c.close()

        embed_timing, embed_result = time_call(
            _do_embed, name="embed_all", warmup=warmup, repeats=repeats
        )
        report.ops.append(OpTiming(name="embed_all", timing=embed_timing))
    finally:
        conn.close()

    # DB size after build + embed.
    report.db_size_mb = Path(db_path).stat().st_size / (1024 * 1024)

    # --- Query battery ----------------------------------------------------
    # Each query runs warmup + query_repeats against the freshly built graph.
    # The query set mirrors the operations users actually feel latency on.
    from codegraph.graph import queries as q

    conn = get_db(db_path)
    try:
        # Pick real symbols to query — the first few from the graph.
        sample_rows = conn.execute(
            "SELECT name FROM symbols WHERE name != '' LIMIT 5"
        ).fetchall()
        sample_names = [r["name"] for r in sample_rows] if sample_rows else ["main"]
        query_target = sample_names[0] if sample_names else "main"

        query_ops = [
            ("find_definition", lambda: q.find_definition(conn, query_target, limit=5)),
            ("search_symbols", lambda: q.search_symbols(conn, query_target[:4] + "*", limit=20)),
            ("get_callers", lambda: q.get_callers(conn, query_target, limit=50)),
            ("get_callees", lambda: q.get_callees(conn, query_target, limit=50)),
            ("impact_analysis", lambda: q.impact_analysis(conn, query_target, max_depth=3, limit=100)),
        ]
        # semantic_search + explore may need embeddings; guard with try.
        try:
            query_ops.append(("semantic_search", lambda: q.semantic_search(conn, query_target, limit=10)))
        except Exception:
            pass
        try:
            query_ops.append(("explore", lambda: q.explore(conn, query_target)))
        except Exception:
            pass

        for name, fn in query_ops:
            timing, _ = time_call(fn, name=name, warmup=1, repeats=query_repeats)
            report.ops.append(OpTiming(name=name, timing=timing))
    finally:
        conn.close()

    if progress:
        progress("perf_done", ops=len(report.ops))
    try:
        return report
    finally:
        _restore_env()
