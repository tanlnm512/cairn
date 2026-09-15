"""Doctor command and system health checks."""
from __future__ import annotations

import os
import logging
import sqlite3
import time
import click
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Callable

from ..main import DEFAULT_DB_PATH, get_db, main

_log = logging.getLogger(__name__)

_PASS = "PASS"
_WARN = "WARN"
_FAIL = "FAIL"

# Thresholds (assertable; each documented at its check). Chosen so a healthy
# store stays PASS/WARN and only genuine breakage FAILs.
STALE_BUILD_DAYS = 7             # last build_runs row older than this -> WARN
MEMORY_REF_WINDOW_DAYS = 30      # tribal memories older than this, 0 refs in window -> WARN
CONTENTION_WINDOW_DAYS = 7       # lock_contention / stray_swept lookback
TOOL_HEALTH_WINDOW_DAYS = 7      # tool_metrics lookback window
TOOL_ERROR_RATE_WARN = 0.10      # a tool with >10% errors -> WARN
TOOL_P95_LATENCY_MS_WARN = 5000  # a tool with p95 latency over 5s -> WARN


@dataclass(frozen=True)
class HealthCheck:
    """A named doctor check returning one or more result rows."""

    name: str
    run: Callable[[str, sqlite3.Connection], list[dict]]


HEALTH_CHECKS: tuple[HealthCheck, ...] = (
    HealthCheck("schema", lambda _db, conn: [_check_schema(conn)]),
    HealthCheck("embeddings", lambda _db, conn: [_check_embeddings(conn)]),
    HealthCheck("ann", lambda _db, conn: [_check_ann(conn)]),
    HealthCheck("embed_server", lambda _db, conn: _check_embed_server(conn)),
    HealthCheck("freshness", lambda _db, conn: [_check_freshness(conn)]),
    HealthCheck("parse_errors", lambda _db, conn: [_check_parse_errors(conn)]),
    HealthCheck("concurrency", lambda _db, conn: [_check_concurrency(conn)]),
    HealthCheck("tool_health", lambda _db, conn: [_check_tool_health(conn)]),
    HealthCheck(
        "memory_staleness",
        lambda db, conn: [_check_memory_staleness(conn, db)],
    ),
    HealthCheck("config", lambda _db, _conn: [_check_config()]),
    HealthCheck("environment", lambda db, _conn: [_check_environment(db)]),
)


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


# --- the 9 checks ----------------------------------------------------------
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
    from ...graph.embeddings import _backend_name, is_hash_fallback

    # Report the effective backend, including config and defaults.
    configured = _backend_name()
    if is_hash_fallback():
        return _result(
            "embeddings",
            _WARN,
            "hash backend active -- token-overlap vectors, retrieval degraded",
            hint="install once: `cairn embed --install-deps`",
        )
    return _result("embeddings", _PASS, f"backend: {configured}")


def _check_ann(conn) -> dict:
    """3. ANN: sqlite-vec loaded / indexed / fresh.

    PASS when sqlite-vec is explicitly disabled (``CAIRN_ANN_BACKEND=off`` --
    an informed choice, not a degradation) or loads cleanly with a fresh
    index. WARN when sqlite-vec is *expected* (env unset or ``=sqlite-vec``,
    the default) but unavailable (not installed / load failed): semantic_search
    then falls back to the slower brute-force cosine scan. Also WARNs on two
    index-level states the load probe can't see: embeddings exist for the
    current model but no vec0 table was ever built (the index-less state
    emitted as ``ann_fallback reason=no_index``), and a vec0 table whose row
    count no longer matches the embeddings table (index drift). Drift now
    has two reported directions: too FEW vec rows means recent embeddings
    were never indexed (recall loss), too MANY means stale entries survived
    a deletion (which can mis-pair a reused rowid with an unrelated vector).
    Recovery is instructed, not performed -- doctor is read-only by
    contract, so the heal is ``cairn embed``'s final ``rebuild_index``.
    Uses ``ann_backend_enabled()`` plus live ``try_load`` / ``index_exists``
    / ``index_row_count`` probes, and surfaces the latest ``ann_fallback``
    event reason when one was recorded.
    """
    from ...graph.ann_index import (
        ann_backend_enabled,
        index_exists,
        index_row_count,
        try_load,
    )
    from ...graph.embeddings import current_model, embed_count

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
    # Extension loads fine -- probe the index itself. Both probes are moot
    # when there are no embeddings for the current model (a fresh/unembedded
    # store legitimately has no vec0 table; that's the embeddings check's
    # territory, not a missing index).
    try:
        emb_n = embed_count(conn) if conn is not None else 0
    except Exception:
        emb_n = 0
    if emb_n <= 0:
        return _result("ann", _PASS, "sqlite-vec available (no embeddings to index yet)")
    model = current_model()
    if conn is not None and not index_exists(conn, model):
        return _result(
            "ann",
            _WARN,
            f"no vec0 index for model '{model}' ({emb_n} embedding(s) unindexed) -- "
            "brute-force scan in use",
            hint="run `cairn embed` to build the ANN index",
        )
    idx_n = index_row_count(conn, model) if conn is not None else None
    if idx_n is not None and idx_n != emb_n:
        # Direction matters. Fewer vec rows than embeddings is a recall loss
        # (recent symbols invisible to ANN). More is worse: the stale entries
        # were left by a delete path that didn't sync (or a crash between an
        # embeddings write and its vec sync), and because SQLite can REUSE a
        # freed embeddings rowid, a stale entry can pair a fresh embedding
        # with an unrelated vector -- wrong results, not just missing ones.
        if idx_n < emb_n:
            detail = (
                f"ANN index stale: {emb_n} embedding(s) vs {idx_n} indexed "
                f"({emb_n - idx_n} unindexed) -- recent symbols invisible to "
                "semantic queries"
            )
        else:
            detail = (
                f"ANN index stale: {idx_n} indexed vs {emb_n} embedding(s) "
                f"({idx_n - emb_n} stale vector(s)) -- deleted symbols can "
                "shadow new ones via reused rowids"
            )
        # The heal is instructed, not performed: doctor is read-only by
        # contract (see the command docstring), so recovery stays with
        # `cairn embed`, whose final rebuild_index realigns the whole table.
        # Unchanged chunks are skipped by embed_all, so the "re-embed" is in
        # practice just the rebuild.
        return _result(
            "ann",
            _WARN,
            detail,
            hint="run `cairn embed` to rebuild the ANN index",
        )
    return _result("ann", _PASS, f"sqlite-vec available ({emb_n} vector(s) indexed)")


def _check_embed_server(conn) -> list[dict]:
    """4. Embed server: probe / model-listing / parity sample / latency.

    One informational PASS line unless a server-family backend (server, omlx,
    ollama) is configured -- no network I/O happens and the default-configured
    output stays byte-stable. With a server backend, doctor
    re-evaluates by design: reset_backend_cache() drops the cached probe and
    stamp resolution (and any session adoptions) before probing. Verdicts: an
    unreachable server or a missing configured model FAILs with a remediation
    hint; a failed parity sample WARNs with the measured mean (advice --
    re-embed, not broken); otherwise PASS naming host, model, and the latency
    bucket of one tiny embed round-trip. Zero stored rows under the current
    stamp makes the parity arm vacuous (check_parity's contract), so a fresh
    install still PASSes. An active ladder degradation (recorded
    earlier in this process) surfaces as an appended WARN entry naming
    rung/reason/remediation -- sampled before the cache reset so
    doctor reports it instead of erasing it.
    """
    from urllib.parse import urlsplit

    from ...graph.embed_ladder import (
        _fetch_model_listing,
        check_parity,
        degradation_active,
        degradation_footnote,
    )
    from ...graph.embeddings import (
        _SERVER_FAMILY,
        _backend_name,
        _embed_server,
        _server_base_url,
        _server_model,
        current_model,
        embeddings_available,
        reset_backend_cache,
    )
    from ...graph.semantic import _ms_bucket

    # Resolve the backend from env, config file, and defaults.
    configured = _backend_name()
    if configured not in _SERVER_FAMILY:
        return [
            _result(
                "embed_server",
                _PASS,
                f"disabled by config (CAIRN_EMBED_BACKEND={configured})",
            )
        ]

    results: list[dict] = []
    if degradation_active():
        results.append(
            _result("embed_server_degraded", _WARN, degradation_footnote())
        )

    reset_backend_cache()

    try:
        base = _server_base_url().rstrip("/")
    except RuntimeError as e:
        results.append(
            _result(
                "embed_server",
                _FAIL,
                str(e),
                hint="set CAIRN_EMBED_BASE_URL to an OpenAI-compatible /v1 URL, "
                "or use the omlx/ollama presets",
            )
        )
        return results

    # Probe and model-listing share one path (GET {base}/models,
    # 200 AND the configured id listed); a failed probe fetches the listing
    # once more to separate server-down from model-missing.
    if not embeddings_available():
        ids = _fetch_model_listing()
        if ids is None:
            results.append(
                _result(
                    "embed_server",
                    _FAIL,
                    f"embedding server unreachable (GET {base}/models failed)",
                    hint="start the embedding server and verify GET "
                    f"{base}/models lists the model (presets: omlx "
                    "http://127.0.0.1:8000/v1, ollama http://127.0.0.1:11434/v1)",
                )
            )
            return results
        model = _server_model()
        results.append(
            _result(
                "embed_server",
                _FAIL,
                f"configured model '{model}' not served; "
                f"available: {', '.join(ids) if ids else '<none>'}",
                hint="serve the model or set CAIRN_EMBED_SERVER_MODEL "
                "to a served id",
            )
        )
        return results

    model = _server_model()
    parity_note = "parity vacuous (no stored rows under this stamp)"
    try:
        stamp = current_model()
    except RuntimeError:
        stamp = None
    if stamp is not None:
        try:
            parity = check_parity(conn, stamp)
        except Exception as e:
            results.append(
                _result(
                    "embed_server",
                    _WARN,
                    f"parity sample failed to run: {e}",
                    hint="re-run `cairn doctor` once the embedding server is stable",
                )
            )
            return results
        if parity.sampled:
            if not parity.passed:
                results.append(
                    _result(
                        "embed_server",
                        _WARN,
                        f"parity sample failed ({parity.sampled} chunk(s) sampled): "
                        f"{parity.reason}",
                        hint="re-embed with `cairn embed` to realign stored vectors",
                    )
                )
                return results
            parity_note = (
                f"parity {parity.mean_cosine:.4f} over "
                f"{parity.sampled} stored chunk(s)"
            )

    try:
        t0 = time.perf_counter()
        _embed_server(["ping"])
        latency = _ms_bucket((time.perf_counter() - t0) * 1000.0)
    except Exception as e:
        results.append(
            _result(
                "embed_server",
                _FAIL,
                f"embed request failed: {e}",
                hint=f"verify the server serves POST {base}/embeddings "
                f"with model '{model}'",
            )
        )
        return results

    results.append(
        _result(
            "embed_server",
            _PASS,
            f"server ok: {urlsplit(base).netloc} model '{model}', "
            f"embed latency {latency}, {parity_note}",
        )
    )
    return results


def _check_freshness(conn) -> dict:
    """5. Freshness: pending_sync edits, interrupted rebuilds, last build age.

    WARN when ``pending_sync`` has rows (the debounce window holds unindexed
    edits), when ``repo_build_state`` holds a stale 'building' marker (a
    single-repo rebuild crashed mid-flight and left the repo partial --
    recovery is ``cairn build --repo <repo>``), or the last ``build_runs``
    row is older than ``STALE_BUILD_DAYS``. PASS otherwise, including a fresh
    install (no symbols, no builds). A graph with symbols but no
    ``build_runs`` row (a pre-instrumentation DB) also WARNs so the gap is
    visible.
    """
    now = datetime.now(timezone.utc)
    parts: list[str] = []
    status = _PASS
    hint: str | None = None

    try:
        row = conn.execute("SELECT COUNT(*), MIN(changed_at) FROM pending_sync").fetchone()
        pending_n = row[0] if row else 0
        oldest = row[1] if row else None
    except Exception:
        pending_n, oldest = 0, None
    if pending_n:
        status = _WARN
        parts.append(f"{pending_n} pending-sync file(s), oldest {_age_str(now, oldest)}")

    # Interrupted single-repo rebuild: a 'building' marker nobody cleared.
    # The marker is only written by the on-disk repo path, so rows here mean
    # that process died mid-rebuild (builder.repo_build_in_progress is the
    # programmatic reader).
    try:
        interrupted = [
            r[0]
            for r in conn.execute(
                "SELECT repo_id FROM repo_build_state WHERE state = 'building' "
                "ORDER BY started_at"
            ).fetchall()
        ]
    except Exception:
        interrupted = []
    if interrupted:
        status = _WARN
        parts.append(
            f"interrupted rebuild of {', '.join(interrupted)} "
            f"(marker from a crashed `cairn build --repo`)"
        )
        hint = (
            f"re-run `cairn build --repo {interrupted[0]}` "
            f"to rebuild the partial repo"
        )

    try:
        brow = conn.execute(
            "SELECT started_at FROM build_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        last_build = brow[0] if brow else None
    except Exception:
        last_build = None
    if last_build:
        last_dt = _parse_ts(last_build)
        stale = last_dt is not None and (now - last_dt).days > STALE_BUILD_DAYS
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

    return _result(
        "freshness", status, "; ".join(parts) if parts else "up to date", hint=hint
    )


def _check_parse_errors(conn) -> dict:
    """6. Parse errors: count from parse_errors (newest 5 in detail).

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
    """7. Concurrency: lock_contention events (last 7d) + stray-sweep total.

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
    """8. Tool health: per-tool error rate + p95 latency (last 7d).

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


def _knob_source(name: str, default: str) -> tuple[str, str]:
    """Effective value and supplying layer for a CAIRN_EMBED_* knob.

    Mirrors embeddings._config_or_env's precedence (env > config file
    > default, non-string file values ignored) but also reports which layer
    supplied the value, so doctor's echo cannot diverge from dashboard
    truth.
    """
    from ...paths import get_config_value

    env = (os.environ.get(name) or "").strip()
    if env:
        return env, "env"
    file_val = get_config_value(name)
    if isinstance(file_val, str) and file_val.strip():
        return file_val.strip(), "file"
    return default, "default"


def _check_memory_staleness(conn, db: str) -> dict:
    """9. Memory staleness: tribal memories nobody references.

    Counts tribal memory files older than ``MEMORY_REF_WINDOW_DAYS`` by file
    mtime over ``<bundle>/memory/tribal/*.md`` (a stat per file, no YAML
    parse), and ``memory_refs`` rows recorded inside the window. WARN when old
    memories exist and zero references were recorded in that window (the tier
    is write-only); PASS otherwise, reporting the reference count. The bundle
    resolves from ``db``'s parent so the check audits the store ``--db``
    names. Read-only and never raising: a missing tribal directory is a
    fresh-install PASS; an unreadable bundle degrades to WARN with the reason.
    """
    tribal_dir = Path(db).parent / ".knowledge" / "memory" / "tribal"
    cutoff = datetime.now(timezone.utc) - timedelta(days=MEMORY_REF_WINDOW_DAYS)
    try:
        tribal = sorted(tribal_dir.glob("*.md")) if tribal_dir.is_dir() else []
        old = [
            p
            for p in tribal
            if datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc) < cutoff
        ]
    except OSError as e:
        return _result("memory_staleness", _WARN, f"cannot read memory bundle: {e}")
    row = conn.execute(
        "SELECT COUNT(*) FROM memory_refs WHERE referenced_at >= ?",
        (cutoff.isoformat(),),
    ).fetchone()
    refs = row[0] if row is not None else 0
    if not old:
        return _result(
            "memory_staleness",
            _PASS,
            f"no tribal memories older than {MEMORY_REF_WINDOW_DAYS}d "
            f"({len(tribal)} total, {refs} reference(s) in that window)",
        )
    if refs == 0:
        return _result(
            "memory_staleness",
            _WARN,
            f"{len(old)} tribal memories older than {MEMORY_REF_WINDOW_DAYS}d, "
            "0 references recorded in that window — memory is write-only",
            hint="read memories through explore / recall_memory so lookups record "
            "memory_refs; check wiring with the environment check",
        )
    return _result(
        "memory_staleness",
        _PASS,
        f"{len(old)} tribal memories older than {MEMORY_REF_WINDOW_DAYS}d, "
        f"{refs} reference(s) recorded in that window",
    )


def _check_config() -> dict:
    """10. Config echo: the CAIRN_* knobs that alter behavior (informational).

    Always PASS -- a transparency echo, not a health verdict. Lists the
    effective runtime knobs so a doctor snapshot is self-describing. The
    embedding knobs resolve env > config file > default: each echoes its
    effective value plus the layer that supplied it. The API key reports
    presence only -- its value is never echoed.
    """
    knobs = [
        ("workers", os.environ.get("CAIRN_WORKERS", "<unset>")),
        ("read_only", os.environ.get("CAIRN_READ_ONLY", "<unset>")),
        ("fusion", os.environ.get("CAIRN_FUSION", "<unset>")),
        ("ann_backend", os.environ.get("CAIRN_ANN_BACKEND", "<unset (=sqlite-vec)>")),
        ("telemetry", os.environ.get("CAIRN_TELEMETRY", "<unset (=on)>")),
        ("log_level", os.environ.get("CAIRN_LOG_LEVEL", "<unset (=WARNING)>")),
    ]
    # Defaults mirror the resolver's own fallbacks (embeddings.py).
    embed_knobs = [
        ("embed_backend", "CAIRN_EMBED_BACKEND", "local"),
        ("embed_base_url", "CAIRN_EMBED_BASE_URL", "<preset>"),
        ("embed_server_model", "CAIRN_EMBED_SERVER_MODEL", "bge-m3"),
        ("embed_timeout", "CAIRN_EMBED_TIMEOUT", "30"),
        ("embed_server_batch", "CAIRN_EMBED_SERVER_BATCH", "32"),
        ("embed_model_stamp", "CAIRN_EMBED_MODEL_STAMP", "<derived>"),
    ]
    for label, env_name, default in embed_knobs:
        value, source = _knob_source(env_name, default)
        knobs.append((label, f"{value} ({source})"))
    # Presence only: the value is not even bound, so it cannot leak out.
    key_source = _knob_source("CAIRN_EMBED_API_KEY", "")[1]
    key_echo = "<unset>" if key_source == "default" else f"<set via {key_source}>"
    knobs.append(("api_key", key_echo))
    return _result("config", _PASS, "; ".join(f"{k}={v}" for k, v in knobs))


# Environment wiring is appended to both doctor return paths. It reports read-only
# registration, spawn-probe, and endpoint findings; only a wrong existing store or an
# unreachable endpoint FAILs.

# Per-client MCP registration files the doctor audits -- exactly the config
# paths ``check_installed`` consults (agent_install/detect.py): the files
# ``install-agents`` writes plus each client CLI's own registration file
# (claude via ``claude mcp add --scope user`` -> ~/.claude.json, droid via
# ``droid mcp add`` -> ~/.factory/mcp.json). Workspace files are
# cwd-relative; home files are relative to Path.home().
_REG_WS_FILES: dict[str, str] = {
    "claude": ".mcp.json",
    "cursor": ".cursor/mcp.json",
    "droid": ".factory/mcp.json",  # file fallback shape
    "zcode": ".zcode/config.json",
    "opencode": "opencode.json",
    "kilo": "kilo.json",
    "omp": ".omp/mcp.json",
}
_REG_HOME_FILES: dict[str, tuple[str, ...]] = {
    "claude": ("~/.claude.json",),
    "cursor": ("~/.cursor/mcp.json",),
    "droid": ("~/.factory/mcp.json",),
    "zcode": ("~/.zcode/config.json", "~/.zcode/cli/config.json"),
    "agy": ("~/.gemini/config/mcp_config.json",),
    "opencode": ("~/.config/opencode/opencode.json",),
    "kilo": ("~/.config/kilo/kilo.json",),
    "omp": ("~/.omp/agent/mcp.json",),
}


def _client_config_paths(client: str) -> list[tuple[Path, str]]:
    """Config files that may hold ``client``'s cairn MCP registration.

    Mirrors the per-client paths ``check_installed`` consults (detect.py):
    the workspace file plus the client's home-level config(s); claude-desktop
    is global-only via ``claude_desktop_config_path``. Returns (path, display)
    pairs -- the display form (workspace-relative or ``~/``-prefixed) keeps
    doctor details scrub-safe.
    """
    pairs: list[tuple[Path, str]] = []
    if client in _REG_WS_FILES:
        rel = _REG_WS_FILES[client]
        pairs.append((Path.cwd() / rel, rel))
    if client == "claude-desktop":
        from ...agent_install import claude_desktop_config_path

        path = claude_desktop_config_path()
        try:
            disp = "~/" + str(path.relative_to(Path.home()))
        except ValueError:
            disp = str(path)
        pairs.append((path, disp))
    for rel in _REG_HOME_FILES.get(client, ()):
        pairs.append((Path.home() / rel.removeprefix("~/"), rel))
    return pairs


def _enumerate_registrations() -> list[tuple[str, str, dict]]:
    """Every installed client's cairn MCP registration, read-only.

    Enumerates installed clients via ``check_installed`` (the same installed
    state ``install-agents`` reports), then reads each client's config files
    for the cairn entry (shape-aware: flat ``mcpServers``, zcode's nested
    ``mcp.servers``, opencode/kilo's ``mcp.cairn``). Returns (client, display
    path, entry) triples; absent or unparseable files are skipped, never
    raised.
    """
    from ...agent_install import _registration_entry, check_installed

    found: list[tuple[str, str, dict]] = []
    for client, installed in check_installed(str(Path.cwd())).items():
        if not installed:
            continue
        for path, disp in _client_config_paths(client):
            entry = _registration_entry(str(path))
            if entry is not None:
                found.append((client, disp, entry))
    return found


def _sse_endpoint(entry: dict) -> str | None:
    """The SSE URL of a URL-based registration (``url`` / agy's ``serverUrl``)."""
    for key in ("url", "serverUrl"):
        value = entry.get(key)
        if isinstance(value, str) and "://" in value:
            return value
    return None


def _sse_host_port(url: str) -> tuple[str, int] | None:
    """(host, port) out of an SSE URL (the minimal parse the reachability
    probes already use); None when the URL has no usable host:port."""
    try:
        host_part = url.split("://", 1)[1].split("/", 1)[0]
        host, port_s = host_part.rsplit(":", 1)
        return host, int(port_s)
    except (IndexError, ValueError):
        return None


# Different-store verdict shape from verify_registration: the fail
# detail that names the store the registration ACTUALLY resolves alongside
# the intended one. Everything else it returns starts with "probe ".
_RESOLVES_PREFIX = "registration resolves db="
_TARGET_SEP = "; install target db="


def _registration_findings(
    db: str,
) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Sub-audit (b): client-registration consistency (mixed severity).

    Per installed client's cairn registration:

    * stdio -- the WRITTEN env block is inspected first (the spawn
      probe pins the intended env over the written one, so it cannot see a
      merely-missing entry): an env-less registration, or one not carrying
      the effective home's env, WARNs advising ``cairn install-agents``.
      Then verify_registration spawns the registration's exact binary+env
      with the read-only probe args (cwd = this workspace) against the
      doctor's own store (``db``, the store every other check audits): a
      FAIL is recorded only when it provably resolves a different EXISTING
      store (both stores named); a probe that errors, times out, or resolves
      a store that does not exist on disk stays a WARN.
    * SSE -- ``lifecycle.sse_responds`` probes the endpoint (bounded socket
      read, no request beyond a root GET); unreachable => FAIL naming the
      client and the endpoint.

    Returns (findings, hints, sse display paths); the SSE list feeds the
    platform/transport sub-audit (c).
    """
    from ...agent_install import _registration_argv, verify_registration
    from ...mcp_server import lifecycle
    from ...paths import cairn_home_env

    findings: list[tuple[str, str]] = []
    hints: list[str] = []
    sse_disps: list[str] = []
    stale_hint = (
        "run `cairn install-agents` to rewrite registrations with "
        "environment propagation"
    )
    # The doctor's own resolved store: cwd-based, the same store the other
    # checks audit -- the probe's comparison target.
    expected = {"db": str(db), "workspace": str(Path.cwd())}
    required_env = cairn_home_env()

    for client, disp, entry in _enumerate_registrations():
        if "command" in entry:
            written = entry.get("env")
            written_env = dict(written) if isinstance(written, dict) else {}
            # Env completeness is judged on the written block, BEFORE
            # the probe (which pins the intended env over it).
            missing = sorted(
                k for k, v in required_env.items() if written_env.get(k) != v
            )
            if not written_env:
                findings.append((
                    _WARN,
                    f"{client}: stdio registration in {disp} has no env "
                    "block (pre-environment-propagation shape; it resolves "
                    "the store by cwd only)",
                ))
                hints.append(stale_hint)
            elif missing:
                findings.append((
                    _WARN,
                    f"{client}: stdio registration in {disp} does not pin "
                    f"{', '.join(missing)} to the effective home",
                ))
                hints.append(stale_hint)

            status, detail = verify_registration(
                _registration_argv(entry), written_env, Path.cwd(), expected,
            )
            if status == "pass":
                continue
            resolved_db = ""
            if detail.startswith(_RESOLVES_PREFIX) and _TARGET_SEP in detail:
                body = detail[len(_RESOLVES_PREFIX):].split(_TARGET_SEP, 1)[0]
                resolved_db = body.split(" workspace=", 1)[0]
            if resolved_db and Path(resolved_db).exists():
                findings.append((
                    _FAIL,
                    f"{client}: registration resolves a different existing "
                    f"store -- {detail}",
                ))
                hints.append(stale_hint)
            elif resolved_db:
                findings.append((
                    _WARN,
                    f"{client}: {detail} (the resolved store does not exist "
                    "on disk)",
                ))
                hints.append(stale_hint)
            else:
                findings.append((
                    _WARN,
                    f"{client}: registration probe failed -- {detail}",
                ))
                hints.append(stale_hint)
        else:
            url = _sse_endpoint(entry)
            if url is None:
                continue
            sse_disps.append(disp)
            host_port = _sse_host_port(url)
            responds = host_port is not None and lifecycle.sse_responds(
                host=host_port[0], port=host_port[1]
            )
            if responds:
                continue
            findings.append((
                _FAIL,
                f"{client}: SSE registration in {disp} points at {url} but "
                "the endpoint does not respond (no daemon is serving it)",
            ))
            hints.append(
                "start the shared daemon (`cairn serve start`) or "
                "re-register with `cairn install-agents --stdio`"
            )
    return findings, hints, sse_disps


def _check_environment(db: str) -> dict:
    """11. Environment wiring: store / registrations / platform / binary.

    Status is the worst sub-audit (FAIL over WARN over PASS):

    (a) resolved-store existence -- WARN with the ``cairn init`` + ``cairn
        build`` hint when missing, mirroring _run_doctor's own missing-store
        branch (the mixed ruling forbids repeating schema's FAIL);
    (b) registration consistency -- enumerates installed clients via
        ``check_installed``; stdio registrations are env-inspected (stale
        registrations WARN) and spawn-probed against this doctor's own store
        via ``verify_registration`` (FAIL only on a provably different
        EXISTING store, naming both), SSE registrations are probed with
        ``lifecycle.sse_responds`` (unreachable endpoint => FAIL). All probes
        are read-only and timeout-bounded;
    (c) platform/transport -- WARN when an SSE registration exists but the
        LaunchAgent daemon lifecycle is macOS-only (read through
        lifecycle.is_macos so tests can drive the platform);
    (d) binary coherence -- WARN when the binary registrations resolve
        (``resolve_cg_command``) differs from the one the daemon lifecycle
        launches (``lifecycle.cg_bin``), naming both.

    Details are scrub-safe through ``_scrub_doctor``: relative/``~`` config
    names, client names, the doctor's own ``--db`` (which the schema check
    already echoes), and static remediation strings; absolute store paths a
    probe verdict carries are redacted by the report path.
    """
    from ...agent_install._common import resolve_cg_command
    from ...mcp_server import lifecycle

    findings: list[tuple[str, str]] = []
    hints: list[str] = []

    # (a) resolved-store existence.
    if not Path(db).exists():
        findings.append((_WARN, f"store not found at {db}"))
        hints.append("run `cairn init` + `cairn build` first")
    else:
        findings.append((_PASS, "resolved store present"))

    # (b) registration consistency (env inspection + spawn-probe + SSE).
    reg_findings, reg_hints, sse_disps = _registration_findings(db)
    findings.extend(reg_findings)
    hints.extend(reg_hints)

    # (c) platform/transport: an SSE registration nothing can serve here.
    if sse_disps and not lifecycle.is_macos():
        findings.append(
            (
                _WARN,
                f"SSE registration in {', '.join(sse_disps)} but the LaunchAgent "
                "daemon lifecycle is macOS-only -- nothing can serve it on "
                "this platform",
            )
        )
        hints.append(
            "re-register with `cairn install-agents --stdio` (or run the "
            "daemon on macOS)"
        )

    # (d) binary coherence: what registrations pin vs what the daemon runs.
    reg_cmd = resolve_cg_command()
    daemon_bin = lifecycle.cg_bin()
    if len(reg_cmd) != 1 or reg_cmd[0] != daemon_bin:
        findings.append(
            (
                _WARN,
                f"binary incoherence: registrations resolve "
                f"{' '.join(reg_cmd)} but the daemon lifecycle launches "
                f"{daemon_bin}",
            )
        )
        hints.append(
            "re-run `cairn install-agents` so registrations and the daemon "
            "launch the same binary"
        )

    status = _PASS
    for worse in (_FAIL, _WARN):
        if any(st == worse for st, _ in findings):
            status = worse
            break
    detail = "; ".join(text for _, text in findings)
    # dict.fromkeys dedupes repeated advice while preserving order.
    hint = "; ".join(dict.fromkeys(hints)) if hints else None
    return _result("environment", status, detail, hint=hint)


def _db_unavailable_results(error: Exception | None) -> list[dict]:
    """Result set when the store can't be opened: schema FAILs, the rest WARN.

    Config echo still PASSes (env/file only, independent of the store). Embeddings/
    ANN/embed-server are reported unavailable too: when the store is broken
    the backend state is moot until the store is fixed. This is what makes
    doctor crash-proof against a missing / read-only / corrupt store (spec:
    degrade to WARN with the reason, never crash).
    """
    msg = f"cannot open database: {error}"
    return [
        _result("schema", _FAIL, msg),
        _result("embeddings", _WARN, "database unavailable (see schema)"),
        _result("ann", _WARN, "database unavailable (see schema)"),
        _result("embed_server", _WARN, "database unavailable (see schema)"),
        _result("freshness", _WARN, "database unavailable (see schema)"),
        _result("parse_errors", _WARN, "database unavailable (see schema)"),
        _result("concurrency", _WARN, "database unavailable (see schema)"),
        _result("tool_health", _WARN, "database unavailable (see schema)"),
        _result("memory_staleness", _WARN, "database unavailable (see schema)"),
        _check_config(),
    ]


def _run_doctor(db: str) -> list[dict]:
    """Execute the 11 checks against ``db``. Never raises.

    A store that can't be opened FAILs the schema check and degrades the
    remaining DB-dependent checks to WARN. A store whose path doesn't EXIST
    is reported the same way instead of being silently created: doctor is a
    read-only diagnostic, and creating a fresh store would mask a typo'd
    ``--db`` with an all-PASS "fresh install".
    """
    conn = None
    db_error: Exception | None = None
    if not Path(db).exists():
        db_error = FileNotFoundError(
            f"store not found at {db} -- run `cairn init` + `cairn build` first"
        )
        _log.debug("doctor: store missing at %s", db)
    else:
        try:
            conn = get_db(db)
        except Exception as e:  # OperationalError (can't open) / DatabaseError (corrupt) / ...
            db_error = e
            _log.debug("doctor: get_db(%s) raised %r", db, e)

    if conn is None:
        # The environment audit needs no db connection, so it is appended on
        # the degraded path too: a broken store is precisely when
        # wiring matters.
        return [*_db_unavailable_results(db_error), _check_environment(db)]
    try:
        return [
            result
            for health_check in HEALTH_CHECKS
            for result in health_check.run(db, conn)
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
    """Run 11 system health checks (PASS/WARN/FAIL each).

    Surfaces silent degradations: schema integrity, embedding/ANN backend
    fallbacks, embed-server health (probe/model/parity/latency when a server
    backend is configured), graph freshness, parse errors, lock contention,
    per-tool error/latency health, tribal-memory reference staleness, and
    environment wiring (store resolution,
    registrations, platform/transport, binary coherence). Read-only -- never
    writes to the
    store. Exit code is
    0 when every check is PASS or WARN, and 1 when any check FAILs, so agents
    can gate on it (spec observability-telemetry §6.5).
    """
    from .. import display

    results = _run_doctor(db)
    if as_json:
        click.echo(json.dumps(results, indent=2))
    else:
        _render_doctor(results, display)
    code = 1 if any(r["status"] == _FAIL for r in results) else 0
    click.get_current_context().exit(code)
