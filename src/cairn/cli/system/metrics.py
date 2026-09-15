"""Metrics command and telemetry renderers."""
from __future__ import annotations

import logging
import click
import json
from datetime import datetime, timezone

from ..main import DEFAULT_DB_PATH, get_db, main

_log = logging.getLogger(__name__)

@main.command()
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--tool", "tool_name", default=None, help="Filter by tool name.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
@click.option("--builds", "builds_flag", is_flag=True,
              help="Recent build-run trend with the resolution mix.")
@click.option("--quality", "quality_flag", is_flag=True,
              help="Retrieval quality: empty-result rate, truncations, backend mix.")
@click.option("--contention", "contention_flag", is_flag=True,
              help="Lock-contention events grouped by site.")
@click.option("--tasks", "tasks_flag", is_flag=True,
              help="LLM task-queue lifecycle: events by kind and task type.")
def metrics(db, tool_name, as_json, builds_flag, quality_flag, contention_flag, tasks_flag):
    """Report MCP tool metrics and telemetry trends.

    With no flag, aggregates ``tool_metrics`` (calls / avg ms / errors) -- the
    original behavior, unchanged. The extension flags render from the telemetry
    tables added by spec observability-telemetry §6.5:

      --builds      recent ``build_runs`` rows with the resolution mix
      --quality     empty-result rate, truncations, semantic backend mix
      --contention  ``lock_contention`` events grouped by site
      --tasks       ``task_lifecycle`` events: counts by event (claimed /
                    completed / revised / dropped) and by task_kind

    All four accept ``--json``. Multiple flags render each section in turn
    (and, under ``--json``, a single object keyed by section name).
    """
    from .. import display

    # The default (no flag) path is the original tool_metrics aggregation. It
    # is kept verbatim in _metrics_default so its output is byte-for-byte
    # unchanged -- the new flags branch off here and never touch it.
    if not (builds_flag or quality_flag or contention_flag or tasks_flag):
        _metrics_default(db, tool_name, as_json, display)
        return

    # One connection shared across the requested sections; closed in finally.
    conn = get_db(db)
    sections: list[tuple[str, object]] = []
    try:
        if builds_flag:
            sections.append(("builds", _gather_builds(conn)))
        if quality_flag:
            sections.append(("quality", _gather_quality(conn)))
        if contention_flag:
            sections.append(("contention", _gather_contention(conn)))
        if tasks_flag:
            sections.append(("tasks", _gather_tasks(conn)))
    finally:
        conn.close()

    if as_json:
        # Single flag -> the bare value (a list for builds/contention, a dict
        # for quality/tasks), matching the per-flag spec wording. Multiple
        # flags -> one object keyed by section so a combined snapshot is
        # self-describing.
        if len(sections) == 1:
            click.echo(json.dumps(sections[0][1], indent=2, default=str))
        else:
            click.echo(json.dumps({k: v for k, v in sections}, indent=2, default=str))
        return

    for i, (kind, value) in enumerate(sections):
        if i:
            display.console.print()  # blank line between sections
        if kind == "builds":
            _render_builds(value, display)
        elif kind == "quality":
            _render_quality(value, display)
        elif kind == "tasks":
            _render_tasks(value, display)
        else:
            _render_contention(value, display)


def _metrics_default(db, tool_name, as_json, display):
    """Original tool_metrics aggregation (spec: default output unchanged).

    Body preserved verbatim from the pre-extension command so callers with no
    flag see identical output.
    """
    conn = get_db(db)
    try:
        where = "WHERE tool_name = ?" if tool_name else ""
        params = (tool_name,) if tool_name else ()
        rows = conn.execute(
            f"SELECT tool_name, COUNT(*) AS calls, "
            f"AVG(duration_ms) AS avg_ms, "
            f"SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors "
            f"FROM tool_metrics {where} "
            f"GROUP BY tool_name ORDER BY calls DESC",
            params,
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        display.info("No tool metrics recorded yet.")
        return
    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return
    table_rows = []
    for r in rows:
        err_pct = r["errors"] / r["calls"] * 100 if r["calls"] else 0
        table_rows.append([
            r["tool_name"],
            f"{r['calls']:,}",
            f"{r['avg_ms']:.1f}",
            f"{r['errors']:,}",
            f"{err_pct:.1f}%",
        ])
    display.print_table(
        title=None,
        columns=["tool", "calls", "avg ms", "errors", "err %"],
        rows=table_rows,
    )


# Telemetry gatherers return an empty result for missing or unreadable tables.
# Renderers emit human output normally and gather data verbatim in JSON mode.

# Cap on rows surfaced by --builds so a long-running store still renders a
# bounded table; the newest rows are the useful trend.
_BUILDS_LIMIT = 20


def _gather_builds(conn) -> list[dict]:
    """Recent ``build_runs`` rows (newest first) including the resolution mix."""
    try:
        rows = conn.execute(
            "SELECT kind, started_at, duration_s, repos, files, symbols, edges, "
            "resolution_exact, resolution_ambiguous, resolution_unresolved, "
            "parse_errors, skipped, workers "
            "FROM build_runs ORDER BY started_at DESC LIMIT ?",
            (_BUILDS_LIMIT,),
        ).fetchall()
    except Exception:
        _log.debug("metrics --builds: build_runs unreadable", exc_info=True)
        return []
    return [dict(r) for r in rows]


def _render_builds(rows: list[dict], display) -> None:
    if not rows:
        display.info("No build runs recorded yet.")
        return
    table_rows = []
    for r in rows:
        table_rows.append([
            r["kind"],
            _fmt_ts(r["started_at"]),
            _fmt_dur(r["duration_s"]),
            _fmt_int(r["repos"]),
            _fmt_int(r["files"]),
            _fmt_int(r["symbols"]),
            _fmt_int(r["edges"]),
            _fmt_resolution(r),
            _fmt_int(r["parse_errors"]),
            _fmt_int(r["skipped"]),
        ])
    display.print_table(
        title="Build runs",
        columns=["kind", "started", "dur", "repos", "files", "symbols",
                 "edges", "resolution", "errs", "skip"],
        rows=table_rows,
    )


def _gather_quality(conn) -> dict:
    """Aggregate retrieval-quality signals from ``events``.

    ``empty_result`` is emitted from three query kinds (semantic_search,
    explore, search_symbols -- spec §6.4). Only the ``semantic_search``
    empties share a denominator with ``semantic_backend`` (the population at
    risk of an empty result), so the rate is scoped to that kind: semantic
    empties / semantic_backend total. ``empty_by_kind`` exposes the full
    per-kind breakdown so explore/search_symbols empties stay visible without
    polluting the rate. backend mix counts ``backend`` across
    ``semantic_backend`` events; truncations is the ``truncate_result`` total
    plus a per-tool breakdown.
    """
    semantic_total = _count_events(conn, "semantic_backend")
    empty_total = _count_events(conn, "empty_result")
    empty_by_kind = _attr_counts(conn, "empty_result", "query_kind")
    empty_semantic = empty_by_kind.get("semantic_search", 0)
    truncate_total = _count_events(conn, "truncate_result")
    return {
        "empty_results": empty_total,
        "empty_by_kind": empty_by_kind,
        "semantic_total": semantic_total,
        # Scoped to the semantic kind: only semantic_search empties share a
        # denominator (semantic_backend) with a meaningful at-risk population.
        # None (rendered 'n/a') when no semantic calls have been recorded yet.
        "empty_result_rate": (empty_semantic / semantic_total) if semantic_total else None,
        "truncations": truncate_total,
        "truncations_by_tool": _attr_counts(conn, "truncate_result", "tool"),
        "backend_mix": _attr_counts(conn, "semantic_backend", "backend"),
    }


def _render_quality(data: dict, display) -> None:
    if data["semantic_total"] == 0 and data["empty_results"] == 0 and data["truncations"] == 0:
        display.info("No quality events recorded yet.")
        return
    rate = data["empty_result_rate"]
    rate_str = f"{rate * 100:.1f}%" if rate is not None else "n/a"
    display.console.print("[bold]Quality signals[/bold]")
    # Rate is scoped to the semantic kind (the only one with a denominator);
    # render that numerator explicitly so "X / Y" can't be mistaken for the
    # all-kinds empty total.
    empty_semantic = data["empty_by_kind"].get("semantic_search", 0)
    display.kv(
        "empty results (semantic)",
        f"{empty_semantic} / {data['semantic_total']} ({rate_str})",
    )
    by_kind = data["empty_by_kind"]
    if by_kind:
        ordered = sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0]))
        display.kv(
            "empty by kind",
            ", ".join(f"{k}: {v}" for k, v in ordered),
        )
    display.kv("truncations", f"{data['truncations']}")
    mix = data["backend_mix"]
    if mix:
        total = sum(mix.values())
        ordered = sorted(mix.items(), key=lambda kv: (-kv[1], kv[0]))
        display.kv("backend mix", f"{total} calls — " + ", ".join(f"{k}: {v}" for k, v in ordered))
    else:
        display.kv("backend mix", "no semantic calls recorded")


def _gather_contention(conn) -> list[dict]:
    """``lock_contention`` events grouped by site (count + most-recent ts).

    Ordered by count desc then site. An event whose ``site`` attr is missing or
    unreadable is bucketed under ``<unknown>`` so it still counts.
    """
    try:
        # ASC so the running last_ts update lands on the most-recent row.
        rows = conn.execute(
            "SELECT ts, attrs FROM events WHERE name = ? ORDER BY ts ASC",
            ("lock_contention",),
        ).fetchall()
    except Exception:
        _log.debug("metrics --contention: events unreadable", exc_info=True)
        return []
    sites: dict[str, dict] = {}
    for r in rows:
        ts = r[0]
        site = _attr_value(r[1], "site") or "<unknown>"
        entry = sites.setdefault(site, {"site": site, "count": 0, "last_ts": ts})
        entry["count"] += 1
        entry["last_ts"] = ts
    return sorted(sites.values(), key=lambda d: (-d["count"], d["site"]))


def _render_contention(rows: list[dict], display) -> None:
    if not rows:
        display.info("No lock-contention events recorded.")
        return
    table_rows = [[r["site"], f"{r['count']:,}", _fmt_ts(r["last_ts"])] for r in rows]
    display.print_table(
        title="Lock contention by site",
        columns=["site", "count", "last seen"],
        rows=table_rows,
    )


def _gather_tasks(conn) -> dict:
    """Aggregate ``task_lifecycle`` events: totals by event and by task_kind.

    Makes the LLM task queue's history consumable (F5): claim/complete/revise/
    drop transitions were recorded as events but no surface read them back.
    ``by_event`` is the lifecycle funnel (claimed -> completed, with revised
    re-work and dropped losses visible); ``by_kind`` shows which queue kinds
    actually run. Reuses the defensive ``_attr_counts`` reader.
    """
    return {
        "total": _count_events(conn, "task_lifecycle"),
        "by_event": _attr_counts(conn, "task_lifecycle", "event"),
        "by_kind": _attr_counts(conn, "task_lifecycle", "task_kind"),
    }


def _render_tasks(data: dict, display) -> None:
    if data["total"] == 0:
        display.info("No task-lifecycle events recorded yet.")
        return
    display.console.print("[bold]Task queue lifecycle[/bold]")
    display.kv("events (total)", f"{data['total']:,}")
    for label, key in (("by event", "by_event"), ("by kind", "by_kind")):
        counts = data[key]
        if counts:
            ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            display.kv(label, ", ".join(f"{k}: {v}" for k, v in ordered))
        else:
            display.kv(label, "none recorded")


# --- small formatting / defensive-read helpers ----------------------------


def _count_events(conn, name: str) -> int:
    """Count ``events`` rows named ``name``; 0 on any read failure."""
    try:
        row = conn.execute("SELECT COUNT(*) FROM events WHERE name = ?", (name,)).fetchone()
        return row[0] if row else 0
    except Exception:
        _log.debug("metrics: events unreadable for %s", name, exc_info=True)
        return 0


def _attr_counts(conn, name: str, attr_key: str) -> dict:
    """Distinct-value counts of ``attr_key`` across events named ``name``.

    ``attrs`` is JSON; parsed in Python so the query does not depend on
    SQLite's JSON1 extension being compiled in. Malformed/missing attrs are
    skipped (never raise).
    """
    counts: dict[str, int] = {}
    try:
        rows = conn.execute("SELECT attrs FROM events WHERE name = ?", (name,)).fetchall()
    except Exception:
        _log.debug("metrics: events unreadable for %s", name, exc_info=True)
        return counts
    for r in rows:
        val = _attr_value(r[0] if r else None, attr_key)
        if val is None:
            continue
        key = str(val)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _attr_value(raw, key):
    """One attr value from a JSON ``attrs`` blob, or None (defensive)."""
    if not raw:
        return None
    try:
        attrs = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return attrs.get(key) if isinstance(attrs, dict) else None


def _fmt_ts(value) -> str:
    """Readable timestamp from an epoch float OR ISO string; '—' for None.

    cairn stores timestamps inconsistently: ``build_runs.started_at`` is ISO
    (``builder._iso_ts``) while ``events.ts`` is a raw ``time.time()`` epoch
    float. Both are handled so each metrics section needn't track the shape.
    Display-only; the JSON path keeps the raw value.
    """
    if value is None:
        return "—"
    dt = None
    if isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            dt = None
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            dt = None
    if dt is None:
        return str(value)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_dur(value) -> str:
    return f"{value:.1f}s" if value is not None else "—"


def _fmt_int(value) -> str:
    return f"{value:,}" if value is not None else "—"


def _fmt_resolution(r) -> str:
    """Resolution mix as 'exact/ambiguous/unresolved'; '—' when all NULL."""
    parts = [r["resolution_exact"], r["resolution_ambiguous"], r["resolution_unresolved"]]
    if all(p is None for p in parts):
        return "—"
    return "/".join(_fmt_int(p) for p in parts)
