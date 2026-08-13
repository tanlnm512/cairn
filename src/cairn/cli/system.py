"""System CLI: import-scip, metrics, status, eval, sync, doctor."""
from __future__ import annotations

import logging
import os
import sqlite3
import time
import click
import json
from datetime import datetime, timezone
from pathlib import Path

from .main import DEFAULT_DB_PATH, DEFAULT_KNOWLEDGE_PATH, get_db, main, queries, scanner_mod
from ._helpers import _shorten

_log = logging.getLogger(__name__)

@main.command()
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--tool", "tool_name", default=None, help="Filter by tool name.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def metrics(db, tool_name, as_json):
    """Report MCP tool invocation metrics (calls, avg latency, error rate)."""
    from . import display

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


# --------------------------------------------------------------------------
# cairn status
# --------------------------------------------------------------------------
@main.command()
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--knowledge", default=DEFAULT_KNOWLEDGE_PATH, help="Knowledge directory path.")
def status(db, knowledge):
    """System status and health across all layers."""
    from ..memory.promotion import memory_stats as mstats
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    try:
        s = queries.get_stats(conn)
        bundle = OKFBundle(knowledge)
        compass_n = len(bundle.list_concepts(prefix="compass/"))
        wiki_n = len(bundle.list_concepts(prefix="wiki/"))
        mem = mstats(bundle)

        # Show pending sync files (unindexed edits in debounce window).
        try:
            pending_rows = conn.execute(
                "SELECT path, repo_id, changed_at FROM pending_sync ORDER BY changed_at DESC"
            ).fetchall()
        except Exception:
            pending_rows = []

        # Parse errors are written by the builder/incremental indexer but read
        # by zero commands -- surface the newest few so a degraded build isn't
        # invisible. Silent when the table is empty (clean DB output unchanged).
        try:
            parse_err_total = conn.execute(
                "SELECT COUNT(*) FROM parse_errors"
            ).fetchone()[0]
            parse_err_rows = conn.execute(
                "SELECT file_path, error_message FROM parse_errors "
                "ORDER BY timestamp DESC LIMIT 5"
            ).fetchall()
        except Exception:
            parse_err_total = 0
            parse_err_rows = []
    finally:
        conn.close()

    from . import display
    display.kv("graph", f"{s['repos']} repos · {s['symbols']:,} symbols · {s['edges']:,} edges")
    display.kv("compass", f"{compass_n} files")
    display.kv("wiki", f"{wiki_n} articles")
    display.kv("memory", "")
    for tier, info in mem.items():
        display.kv(f"  {tier}", f"{info['count']:>4} (avg {info['avg_score']:.2f})")

    if pending_rows:
        display.warning(f"Pending sync: {len(pending_rows)} files")
        for row in pending_rows[:20]:
            display.dim(f"  {_shorten(row['path'])}")
        if len(pending_rows) > 20:
            display.dim(f"  ... and {len(pending_rows) - 20} more")

    if parse_err_total:
        display.warning(f"Parse errors: {parse_err_total}")
        for row in parse_err_rows:
            msg = row["error_message"] or ""
            if len(msg) > 100:
                msg = msg[:100] + "..."
            display.dim(f"  {_shorten(row['file_path'])} — {msg}")
        if parse_err_total > len(parse_err_rows):
            display.dim(f"  ... and {parse_err_total - len(parse_err_rows)} more")


# --------------------------------------------------------------------------
# cairn eval
# --------------------------------------------------------------------------
@main.command(name="eval")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--knowledge", default=DEFAULT_KNOWLEDGE_PATH, help="Knowledge directory path.")
@click.option("--corpus", type=click.Choice(["L1", "L5", "all"]), default="all", help="Corpus filter.")
@click.option("--queries", "queries_path", default=None,
              help="Path to eval queries.yaml (default: bundled tests/eval/queries.yaml).")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def eval_cmd(db, knowledge, corpus, queries_path, as_json):
    """Run retrieval evaluation harness across L1/L5 corpora."""
    from pathlib import Path

    from ..eval import run_evaluation

    qpath = Path(queries_path) if queries_path else None
    conn = get_db(db)
    try:
        report = run_evaluation(conn, bundle_root=knowledge, queries_path=qpath, corpus_filter=corpus)
    finally:
        conn.close()

    if as_json:
        click.echo(json.dumps(report, indent=2))
        return

    from . import display
    rows = []
    for c_key in ["L1", "L5"]:
        if corpus != "all" and c_key != corpus:
            continue
        data = report.get(c_key, {})
        rows.append([
            c_key,
            f"{data.get('count', 0):,}",
            f"{data.get('recall_at_10', 0.0):.4f}",
            f"{data.get('mrr', 0.0):.4f}",
        ])
    display.print_table(None, ["corpus", "samples", "recall@10", "mrr"], rows)


# --------------------------------------------------------------------------
# cairn sync (manual re-index escape hatch)
# --------------------------------------------------------------------------
@main.command()
@click.option("--workspace", default=scanner_mod.DEFAULT_WORKSPACE)
@click.option("--db", default=str(DEFAULT_DB_PATH))
def sync(workspace, db):
    """Manually re-index changed files (used when watcher is disabled or for scripting).

    Detects files changed since last index via size/mtime comparison and
    re-indexes them. Equivalent to what the watcher does automatically.
    """
    from ..graph import scanner as scanner_mod
    from ..graph.incremental import reindex_paths

    conn = get_db(db)
    try:
        changed: list[str] = []

        for repo_path in scanner_mod.discover_repos(workspace):
            repo_name = repo_path.name
            try:
                file_rows = conn.execute(
                    "SELECT path, size, mtime FROM files WHERE repo_id = ?",
                    (repo_name,),
                ).fetchall()
            except Exception:
                continue

            existing = set()
            for row in file_rows:
                existing.add(row["path"])
                # files.path is repo-relative; resolve to absolute via the
                # single chokepoint for stat.
                p = Path(scanner_mod.resolve_file_path(workspace, repo_name, row["path"]))
                try:
                    st = p.stat()
                except OSError:
                    changed.append(str(p))
                    continue
                if st.st_size != (row["size"] or 0):
                    changed.append(str(p))
                elif abs(st.st_mtime - (row["mtime"] or 0.0)) > 0.5:
                    changed.append(str(p))

            # Detect new source files. Storage is repo-relative; the scanner
            # yields absolute, so compare on the relative form.
            for src in scanner_mod.iter_source_files(repo_path):
                rel = str(src.relative_to(repo_path)) if str(src).startswith(str(repo_path)) else str(src)
                if rel not in existing and str(src) not in existing:
                    changed.append(str(src))

        if not changed:
            from . import display
            display.success("No changes detected. Graph is up to date.")
            return

        from . import display
        import time
        sync_started = time.time()
        with display.progress_bar(description=f"Syncing {len(changed)} files", total=len(changed), unit="files") as bar:
            # reindex_paths doesn't expose per-file progress; show an indeterminate
            # bar that completes when it returns. For small N this is instant.
            result = reindex_paths(conn, workspace, changed)
            bar.update(bar._cg_task_id, completed=len(changed))
        # Refresh the dataflow index if any files were reindexed.
        if result["reindexed"]:
            try:
                from ..graph.dataflow import build_dataflow_index
                df_count = build_dataflow_index(conn)
                display.dim(f"  dataflow index: {df_count:,} symbols")
            except Exception:
                pass
        display.success(f"Synced: {result['reindexed']} reindexed, {result['deleted']} deleted")
        if result["errors"]:
            display.warning(f"{len(result['errors'])} errors")
            for e in result["errors"][:5]:
                display.dim(f"  {e}")

        # Persist a 'sync' build_runs row (best-effort; record_build_run
        # swallows all errors). reindex_paths returns reindexed/deleted only;
        # resolution mix / parse-error breakdown / phase_timings stay NULL
        # (the sync path has no scan/parse/resolve phase contract). Recorded
        # here in the CLI command rather than inside the shared reindex_paths
        # so the `cairn update` path records its own 'incremental' row.
        from ..graph.builder import record_build_run
        record_build_run(
            db,
            "sync",
            started_at=sync_started,
            duration_s=time.time() - sync_started,
            files=result["reindexed"],
            skipped=result["deleted"],
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------
# cairn doctor (spec observability-telemetry §6.5)
# --------------------------------------------------------------------------
# 8 health checks, each PASS/WARN/FAIL. Read-only -- doctor never writes to
# the store. Exit code is 0 when every check is PASS or WARN, and 1 when any
# check is FAIL, so agents can gate on it (spec §6.5, success metric §8).
#
# Threshold policy: a clean, freshly-built store exits 0 even when optional
# backends (sentence-transformers, sqlite-vec) are absent -- absence degrades
# to WARN (functional-but-slower), never FAIL. FAIL is reserved for
# "wrong/broken": an integrity error or a store that can't be opened.

_PASS = "PASS"
_WARN = "WARN"
_FAIL = "FAIL"

# Thresholds (assertable; each documented at its check). Chosen so a healthy
# store stays PASS/WARN and only genuine breakage FAILs.
STALE_BUILD_DAYS = 7             # last build_runs row older than this -> WARN
CONTENTION_WINDOW_DAYS = 7       # lock_contention / stray_swept lookback
TOOL_HEALTH_WINDOW_DAYS = 7      # tool_metrics lookback window
TOOL_ERROR_RATE_WARN = 0.10      # a tool with >10% errors -> WARN
TOOL_P95_LATENCY_MS_WARN = 5000  # a tool with p95 latency over 5s -> WARN


def _result(name: str, status: str, detail: str, hint: str | None = None) -> dict:
    """One doctor result row. ``hint`` is an optional remediation string."""
    return {"name": name, "status": status, "detail": detail, "hint": hint}


def _parse_ts(value) -> datetime | None:
    """Parse an ISO-8601 string OR an epoch float into an aware UTC datetime.

    cairn stores timestamps inconsistently: ``build_runs.started_at`` is ISO
    (``builder._iso_ts``), while ``events.ts`` and ``tool_metrics.invoked_at``
    are raw ``time.time()`` epoch floats (the buffered sinks enqueue
    ``time.time()`` directly). This helper accepts both so each check needn't
    track which column shape it reads.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_str(now: datetime, ts) -> str:
    """Human-readable age of ``ts`` relative to ``now`` ('3d old', '2h old')."""
    dt = _parse_ts(ts)
    if dt is None:
        return "age unknown"
    secs = int((now - dt).total_seconds())
    if secs < 0:
        return "just now"  # clock skew / a future-dated row
    if secs >= 86400:
        return f"{secs // 86400}d old"
    if secs >= 3600:
        return f"{secs // 3600}h old"
    if secs >= 60:
        return f"{secs // 60}m old"
    return f"{secs}s old"


def _percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile (e.g. 95 for p95). None for empty input."""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _latest_event_reason(conn, name: str) -> str | None:
    """Reason attr of the most recent ``name`` event, defensively read.

    Returns None when the table is missing, empty, or the attrs JSON is
    unreadable -- callers use it only to enrich a detail string.
    """
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT attrs FROM events WHERE name = ? ORDER BY ts DESC LIMIT 1",
            (name,),
        ).fetchone()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    try:
        attrs = json.loads(row[0])
        return attrs.get("reason") if isinstance(attrs, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


# --- the 8 checks ----------------------------------------------------------
# Each takes the live connection (None only inside _db_unavailable_results,
# which short-circuits before these run for the DB-dependent checks) and
# returns a result dict. Every DB read is bounded + defensive: a missing table
# or read-only store degrades to WARN with the reason, never raises.


def _check_schema(conn) -> dict:
    """1. Schema: bounded integrity probe (PRAGMA quick_check).

    FAIL on an integrity error (corrupt DB / not-a-database); PASS otherwise.
    ``quick_check`` is the bounded variant of integrity_check -- it skips
    index B-tree verification, so it scales to large DBs without a full
    re-walk. When the store itself can't be opened, this check is never
    reached; the command-level handler FAILs it with the open error instead.
    """
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as e:
        return _result("schema", _FAIL, f"integrity check failed: {e}")
    verdict = row[0] if row else None
    if verdict == "ok":
        return _result("schema", _PASS, "integrity ok")
    return _result("schema", _FAIL, f"integrity check reported: {verdict}")


def _check_embeddings(conn) -> dict:
    """2. Embeddings backend: real vs hash (degraded retrieval).

    WARN when the dep-free hash backend is silently active -- configured
    ``local`` (the default) but sentence-transformers isn't installed, so
    vectors carry token-overlap signal, not real semantics. PASS when a real
    backend is active OR the user explicitly chose ``hash`` (an informed
    choice, never a degradation). Mirrors ``embeddings.is_hash_fallback()``.
    """
    from ..graph.embeddings import is_hash_fallback

    configured = os.environ.get("CAIRN_EMBED_BACKEND", "local").strip().lower() or "local"
    if is_hash_fallback():
        return _result(
            "embeddings",
            _WARN,
            "hash backend active -- token-overlap vectors, retrieval degraded",
            hint="install once: `cairn embed --install-deps`",
        )
    return _result("embeddings", _PASS, f"backend: {configured}")


def _check_ann(conn) -> dict:
    """3. ANN: sqlite-vec loaded / degraded.

    PASS when sqlite-vec is explicitly disabled (``CAIRN_ANN_BACKEND=off`` --
    an informed choice, not a degradation) or loads cleanly. WARN when
    sqlite-vec is *expected* (env unset or ``=sqlite-vec``, the default) but
    unavailable (not installed / load failed): semantic_search then falls back
    to the slower brute-force cosine scan. Uses ``ann_backend_enabled()`` plus
    a live ``try_load`` probe, and surfaces the latest ``ann_fallback`` event
    reason when one was recorded.
    """
    from ..graph.ann_index import ann_backend_enabled, try_load

    configured = (
        os.environ.get("CAIRN_ANN_BACKEND", "sqlite-vec").strip().lower() or "sqlite-vec"
    )
    if configured != "sqlite-vec":
        return _result(
            "ann",
            _PASS,
            f"disabled by config (CAIRN_ANN_BACKEND={configured}); brute-force scan in use",
        )
    if not ann_backend_enabled():
        reason = _latest_event_reason(conn, "ann_fallback") or "sqlite-vec not installed"
        return _result(
            "ann",
            _WARN,
            f"sqlite-vec unavailable ({reason}) -- brute-force scan in use",
            hint="install once: `cairn embed --install-deps`",
        )
    loaded = try_load(conn) if conn is not None else False
    if not loaded:
        reason = _latest_event_reason(conn, "ann_fallback") or "extension load failed"
        return _result(
            "ann",
            _WARN,
            f"sqlite-vec importable but load failed ({reason}) -- brute-force scan in use",
            hint="install once: `cairn embed --install-deps`",
        )
    return _result("ann", _PASS, "sqlite-vec available")


def _check_freshness(conn) -> dict:
    """4. Freshness: pending_sync edits + last build age.

    WARN when ``pending_sync`` has rows (the debounce window holds unindexed
    edits) or the last ``build_runs`` row is older than ``STALE_BUILD_DAYS``.
    PASS otherwise, including a fresh install (no symbols, no builds). A graph
    with symbols but no ``build_runs`` row (a pre-instrumentation DB) also
    WARNs so the gap is visible.
    """
    now = datetime.now(timezone.utc)
    parts: list[str] = []
    status = _PASS

    try:
        row = conn.execute("SELECT COUNT(*), MIN(changed_at) FROM pending_sync").fetchone()
        pending_n = row[0] if row else 0
        oldest = row[1] if row else None
    except Exception:
        pending_n, oldest = 0, None
    if pending_n:
        status = _WARN
        parts.append(f"{pending_n} pending-sync file(s), oldest {_age_str(now, oldest)}")

    try:
        brow = conn.execute(
            "SELECT started_at FROM build_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        last_build = brow[0] if brow else None
    except Exception:
        last_build = None
    if last_build:
        last_dt = _parse_ts(last_build)
        stale = bool(last_dt) and (now - last_dt).days > STALE_BUILD_DAYS
        tag = f" (>{STALE_BUILD_DAYS}d)" if stale else ""
        parts.append(f"last build {_age_str(now, last_build)}{tag}")
        if stale:
            status = _WARN
    else:
        try:
            sym_n = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        except Exception:
            sym_n = 0
        if sym_n:
            status = _WARN
            parts.append(f"no build_runs recorded (but {sym_n} symbols indexed)")
        else:
            parts.append("no builds yet (fresh install)")

    return _result("freshness", status, "; ".join(parts) if parts else "up to date")


def _check_parse_errors(conn) -> dict:
    """5. Parse errors: count from parse_errors (newest 5 in detail).

    WARN when >0 -- a parse error means a file was skipped during indexing, so
    the graph is incomplete for that file. Closes the gap that parse_errors was
    written by the builder/incremental but read by no command.
    """
    try:
        total = conn.execute("SELECT COUNT(*) FROM parse_errors").fetchone()[0]
    except Exception:
        return _result("parse_errors", _WARN, "parse_errors table unavailable")
    if not total:
        return _result("parse_errors", _PASS, "0 parse errors")
    try:
        rows = conn.execute(
            "SELECT file_path, error_message FROM parse_errors "
            "ORDER BY timestamp DESC LIMIT 5"
        ).fetchall()
    except Exception:
        rows = []
    samples = []
    for r in rows:
        msg = (r[1] or "")[:80]
        samples.append(f"{r[0]}: {msg}" if msg else str(r[0]))
    detail = f"{total} parse error(s)"
    if samples:
        detail += "; newest: " + " | ".join(samples)
    return _result(
        "parse_errors", _WARN, detail, hint="run `cairn status` for the full list"
    )


def _check_concurrency(conn) -> dict:
    """6. Concurrency: lock_contention events (last 7d) + stray-sweep total.

    WARN when any ``lock_contention`` event was recorded in the last
    ``CONTENTION_WINDOW_DAYS`` (cross-process lock waits absorbed by
    busy_timeout -- the v0.9.x bug class). ``stray_swept`` totals are reported
    in the detail but are NOT a WARN trigger: sweeping strays is the
    stdio-leak remediation *working*, not failing.
    """
    cutoff = time.time() - CONTENTION_WINDOW_DAYS * 86400
    try:
        contention = conn.execute(
            "SELECT COUNT(*) FROM events WHERE name = ? AND ts >= ?",
            ("lock_contention", cutoff),
        ).fetchone()[0]
    except Exception:
        contention = 0
    stray_total = 0
    try:
        for r in conn.execute(
            "SELECT attrs FROM events WHERE name = ? AND ts >= ?",
            ("stray_swept", cutoff),
        ).fetchall():
            try:
                a = json.loads(r[0]) if r[0] else {}
                if isinstance(a, dict):
                    stray_total += int(a.get("count", 0) or 0)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
    except Exception:
        pass
    parts = [f"{contention} lock-contention event(s) in {CONTENTION_WINDOW_DAYS}d"]
    if stray_total:
        parts.append(f"{stray_total} stray process(es) swept")
    return _result("concurrency", _WARN if contention else _PASS, "; ".join(parts))


def _check_tool_health(conn) -> dict:
    """7. Tool health: per-tool error rate + p95 latency (last 7d).

    WARN when ANY tool's error rate exceeds ``TOOL_ERROR_RATE_WARN`` or its p95
    latency exceeds ``TOOL_P95_LATENCY_MS_WARN``. PASS when no metrics are
    recorded (no MCP traffic yet) or every tool is within thresholds.
    """
    cutoff = time.time() - TOOL_HEALTH_WINDOW_DAYS * 86400
    try:
        tools = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT tool_name FROM tool_metrics WHERE invoked_at >= ?",
                (cutoff,),
            ).fetchall()
        ]
    except Exception:
        tools = []
    if not tools:
        return _result("tool_health", _PASS, "no tool metrics recorded yet")

    offenders: list[str] = []
    healthy = 0
    for tool in tools:
        try:
            row = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) "
                "FROM tool_metrics WHERE tool_name = ? AND invoked_at >= ?",
                (tool, cutoff),
            ).fetchone()
            calls = row[0] if row else 0
            errs = row[1] if row else 0
        except Exception:
            continue
        if not calls:
            continue
        err_rate = (errs or 0) / calls
        try:
            durations = [
                d[0]
                for d in conn.execute(
                    "SELECT duration_ms FROM tool_metrics WHERE tool_name = ? "
                    "AND invoked_at >= ? AND duration_ms IS NOT NULL",
                    (tool, cutoff),
                ).fetchall()
            ]
        except Exception:
            durations = []
        p95 = _percentile(durations, 95)
        bad_rate = err_rate > TOOL_ERROR_RATE_WARN
        bad_lat = p95 is not None and p95 > TOOL_P95_LATENCY_MS_WARN
        if bad_rate or bad_lat:
            lat = f", p95 {p95:.0f}ms" if p95 is not None else ""
            offenders.append(f"{tool}: {err_rate * 100:.0f}% err{lat}")
        else:
            healthy += 1
    if offenders:
        return _result(
            "tool_health",
            _WARN,
            f"{len(offenders)} tool(s) over threshold; " + "; ".join(offenders),
        )
    return _result("tool_health", _PASS, f"{healthy} tool(s) within thresholds")


def _check_config() -> dict:
    """8. Config echo: the CAIRN_* knobs that alter behavior (informational).

    Always PASS -- a transparency echo, not a health verdict. Lists the
    effective runtime knobs so a doctor snapshot is self-describing.
    """
    knobs = [
        ("workers", os.environ.get("CAIRN_WORKERS", "<unset>")),
        ("read_only", os.environ.get("CAIRN_READ_ONLY", "<unset>")),
        ("fusion", os.environ.get("CAIRN_FUSION", "<unset>")),
        ("ann_backend", os.environ.get("CAIRN_ANN_BACKEND", "<unset (=sqlite-vec)>")),
        ("embed_backend", os.environ.get("CAIRN_EMBED_BACKEND", "<unset (=local)>")),
        ("telemetry", os.environ.get("CAIRN_TELEMETRY", "<unset (=on)>")),
        ("log_level", os.environ.get("CAIRN_LOG_LEVEL", "<unset (=WARNING)>")),
    ]
    return _result("config", _PASS, "; ".join(f"{k}={v}" for k, v in knobs))


def _db_unavailable_results(error: Exception | None) -> list[dict]:
    """Result set when the store can't be opened: schema FAILs, the rest WARN.

    Config echo still PASSes (env-only, independent of the store). Embeddings/
    ANN are reported unavailable too: when the store is broken the backend
    state is moot until the store is fixed. This is what makes doctor
    crash-proof against a missing / read-only / corrupt store (spec: degrade
    to WARN with the reason, never crash).
    """
    msg = f"cannot open database: {error}"
    return [
        _result("schema", _FAIL, msg),
        _result("embeddings", _WARN, "database unavailable (see schema)"),
        _result("ann", _WARN, "database unavailable (see schema)"),
        _result("freshness", _WARN, "database unavailable (see schema)"),
        _result("parse_errors", _WARN, "database unavailable (see schema)"),
        _result("concurrency", _WARN, "database unavailable (see schema)"),
        _result("tool_health", _WARN, "database unavailable (see schema)"),
        _check_config(),
    ]


def _run_doctor(db: str) -> list[dict]:
    """Execute the 8 checks against ``db``. Never raises.

    A store that can't be opened FAILs the schema check and degrades the
    remaining DB-dependent checks to WARN.
    """
    conn = None
    db_error: Exception | None = None
    try:
        conn = get_db(db)
    except Exception as e:  # OperationalError (can't open) / DatabaseError (corrupt) / ...
        db_error = e
        _log.debug("doctor: get_db(%s) raised %r", db, e)

    if conn is None:
        return _db_unavailable_results(db_error)
    try:
        return [
            _check_schema(conn),
            _check_embeddings(conn),
            _check_ann(conn),
            _check_freshness(conn),
            _check_parse_errors(conn),
            _check_concurrency(conn),
            _check_tool_health(conn),
            _check_config(),
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


_STATUS_STYLE = {_PASS: "success", _WARN: "warning", _FAIL: "error"}
_STATUS_GLYPH = {_PASS: "✓", _WARN: "!", _FAIL: "✗"}


def _render_doctor(results: list[dict], display) -> None:
    """Render one block per check, status-prefixed and color-coded.

    Dynamic detail/name text is markup-escaped so a file path containing ``[``
    can't corrupt rich's markup (same rationale as ``display._value``).
    """
    from rich.markup import escape

    for r in results:
        st = r["status"]
        display.console.print(
            f"[{_STATUS_STYLE[st]}]{_STATUS_GLYPH[st]} {st}[/] "
            f"[bold]{escape(r['name'])}[/bold]: {escape(r['detail'])}"
        )
        if r.get("hint"):
            display.dim(f"      hint: {r['hint']}")


@main.command()
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def doctor(db, as_json):
    """Run 8 system health checks (PASS/WARN/FAIL each).

    Surfaces silent degradations: schema integrity, embedding/ANN backend
    fallbacks, graph freshness, parse errors, lock contention, and per-tool
    error/latency health. Read-only -- never writes to the store. Exit code is
    0 when every check is PASS or WARN, and 1 when any check FAILs, so agents
    can gate on it (spec observability-telemetry §6.5).
    """
    from . import display

    results = _run_doctor(db)
    if as_json:
        click.echo(json.dumps(results, indent=2))
    else:
        _render_doctor(results, display)
    code = 1 if any(r["status"] == _FAIL for r in results) else 0
    click.get_current_context().exit(code)


