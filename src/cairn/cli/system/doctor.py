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
from .metrics import _parse_ts

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
    """Reason attr of the most recent ``name`` event; None when missing, empty, or unreadable."""
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


# --- the 11 checks ---------------------------------------------------------
# Each takes the live connection and returns a result dict; DB reads are
# bounded and defensive — degrade to WARN with the reason, never raise.


def _check_schema(conn) -> dict:
    """1. Schema: bounded integrity probe (PRAGMA quick_check); FAIL on integrity error, PASS otherwise."""
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as e:
        return _result("schema", _FAIL, f"integrity check failed: {e}")
    verdict = row[0] if row else None
    if verdict == "ok":
        return _result("schema", _PASS, "integrity ok")
    return _result("schema", _FAIL, f"integrity check reported: {verdict}")


def _check_embeddings(conn) -> dict:
    """2. Embeddings: WARN only on the silent hash fallback; explicit hash or a real backend PASSes."""
    from ...graph.embeddings import backend_name, is_hash_fallback

    # Report the effective backend, including config and defaults.
    configured = backend_name()
    if is_hash_fallback():
        return _result(
            "embeddings",
            _WARN,
            "hash backend active -- token-overlap vectors, retrieval degraded",
            hint="install once: `cairn embed --install-deps`",
        )
    return _result("embeddings", _PASS, f"backend: {configured}")


def _check_ann(conn) -> dict:
    """3. ANN: sqlite-vec expected/loaded/indexed/fresh; WARN on unavailable, missing, or drifted indexes; never heals."""
    from ...graph.ann_index import (
        ann_backend_enabled,
        configured_backend,
        index_exists,
        index_row_count,
        try_load,
    )
    from ...graph.embeddings import current_model, embed_count

    configured = configured_backend()
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
    # Extension loads fine -- probe the index itself; with no embeddings for
    # the current model a fresh store legitimately has no vec0 table.
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
        # Fewer vec rows than embeddings is recall loss; more is worse — stale
        # entries can pair a reused rowid with an unrelated vector (wrong
        # results, not just missing ones).
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
        # Doctor is read-only by contract: instruct the heal (`cairn embed`,
        # whose final rebuild_index realigns the table) rather than perform it.
        return _result(
            "ann",
            _WARN,
            detail,
            hint="run `cairn embed` to rebuild the ANN index",
        )
    return _result("ann", _PASS, f"sqlite-vec available ({emb_n} vector(s) indexed)")


def _check_embed_server(conn) -> list[dict]:
    """4. Embed server: probe/model/parity/latency; unreachable or missing model FAILs, parity failure WARNs, else PASS."""
    from urllib.parse import urlsplit

    from ...graph.embed_ladder import (
        _fetch_model_listing,
        check_parity,
        degradation_active,
        degradation_footnote,
    )
    from ...graph.embeddings import (
        SERVER_FAMILY,
        backend_name,
        _embed_server,
        _server_base_url,
        _server_model,
        current_model,
        embeddings_available,
        reset_backend_cache,
    )
    from ...graph.semantic import _ms_bucket

    # Resolve the backend from env, config file, and defaults.
    configured = backend_name()
    if configured not in SERVER_FAMILY:
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
    """5. Freshness: WARN on pending_sync rows, interrupted 'building' markers, or stale/missing build history."""
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

    # Interrupted single-repo rebuild: a 'building' marker nobody cleared
    # means the on-disk repo path died mid-rebuild.
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
        stale = last_dt is not None and (now - last_dt) > timedelta(days=STALE_BUILD_DAYS)
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
    """6. Parse errors: WARN when >0 (each skipped file leaves the graph incomplete); newest 5 in detail."""
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
    """7. Concurrency: WARN on lock_contention in the window; stray_swept totals report without warning."""
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
    """8. Tool health: WARN on any tool's error rate or p95 latency over threshold; PASS with no metrics."""
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
    """Effective value and supplying layer for a CAIRN_EMBED_* knob (env > config > default)."""
    from ...paths import get_config_value

    env = (os.environ.get(name) or "").strip()
    if env:
        return env, "env"
    file_val = get_config_value(name)
    if isinstance(file_val, str) and file_val.strip():
        return file_val.strip(), "file"
    return default, "default"


def _check_memory_staleness(conn, db: str) -> dict:
    """9. Memory staleness: WARN when old tribal memories exist with zero in-window references; never raises."""
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
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM memory_refs WHERE referenced_at >= ?",
            (cutoff.isoformat(),),
        ).fetchone()
        refs = row[0] if row is not None else 0
    except Exception as e:
        return _result("memory_staleness", _WARN, f"cannot read memory_refs: {e}")
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
    """10. Config echo (always PASS): effective CAIRN_* knobs with source layer; API key presence only."""
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


# Environment wiring is appended to both doctor return paths. It reports
# store-presence and registration transport-class findings; only a
# non-daemon endpoint FAILs.

# Per-client MCP registration files the doctor audits -- exactly the paths
# check_installed consults (agent_install/detect.py); workspace files are
# cwd-relative, home files are relative to Path.home().
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
    "opencode": ("~/.config/opencode/opencode.json",),
    "kilo": ("~/.config/kilo/kilo.json",),
    "omp": ("~/.omp/agent/mcp.json",),
}


def _scrub_safe_display(path: Path) -> str:
    """Display path with a ``~/`` prefix when it sits under home, else absolute."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _client_config_paths(client: str) -> list[tuple[Path, str, bool]]:
    """Config files that may hold ``client``'s registration: (path, scrub-safe display, workspace_owned) triples."""
    pairs: list[tuple[Path, str, bool]] = []
    if client in _REG_WS_FILES:
        rel = _REG_WS_FILES[client]
        pairs.append((Path.cwd() / rel, rel, True))
    if client == "claude-desktop":
        from ...agent_install import claude_desktop_config_path

        path = claude_desktop_config_path()
        pairs.append((path, _scrub_safe_display(path), False))
    if client == "agy":
        from ...agent_install.clients.agy import agy_config_path

        path = agy_config_path()
        pairs.append((path, _scrub_safe_display(path), False))
    for rel in _REG_HOME_FILES.get(client, ()):
        pairs.append((Path.home() / rel.removeprefix("~/"), rel, False))
    return pairs


def _enumerate_registrations() -> list[tuple[str, Path, str, dict, bool]]:
    """Yield (client, config path, display path, entry, workspace_owned) per installed cairn registration; unreadable files are skipped."""
    from ...agent_install import _registration_entry, check_installed

    found: list[tuple[str, Path, str, dict, bool]] = []
    for client, installed in check_installed(str(Path.cwd())).items():
        if not installed:
            continue
        for path, disp, workspace_owned in _client_config_paths(client):
            entry = _registration_entry(str(path))
            if entry is not None:
                found.append((client, path, disp, entry, workspace_owned))
    return found


def _registration_url(entry: dict) -> str | None:
    """The endpoint URL of a URL-based registration (``url`` / agy's ``serverUrl``)."""
    for key in ("url", "serverUrl"):
        value = entry.get(key)
        if isinstance(value, str) and "://" in value:
            return value
    return None


def _stale_registration(entry: dict, daemon_url: str) -> bool:
    """True when the registration predates the HTTP daemon (stdio spawn-shaped, or a non-daemon endpoint)."""
    if "command" in entry:
        return True
    url = _registration_url(entry)
    return url is not None and url != daemon_url


def _registration_findings() -> tuple[list[tuple[str, str]], list[str]]:
    """Sub-audit (b): transport-class findings — stdio spawn-shaped, SSE endpoint, unsupported desktop."""
    from ...agent_install._common import daemon_mcp_url

    findings: list[tuple[str, str]] = []
    hints: list[str] = []
    rewrite_hint = (
        "run `cairn install-agents` to rewrite registrations to the daemon "
        "URL (`cairn doctor --fix` repoints user-owned configs)"
    )
    daemon_url = daemon_mcp_url()

    for client, _path, disp, entry, workspace_owned in (
            _enumerate_registrations()):
        if client == "claude-desktop":
            findings.append((
                _WARN,
                f"claude-desktop: registration in {disp} is unsupported "
                "(Claude Desktop has no URL-based MCP surface; the daemon "
                "serves HTTP only)",
            ))
            hints.append(
                "remove the cairn entry from the Claude Desktop config "
                "(it is never repointed)"
            )
            continue
        if "command" in entry:
            if workspace_owned:
                findings.append((
                    _WARN,
                    f"{client}: stdio registration in {disp} is "
                    "workspace-owned (untrusted repo content; doctor does "
                    "not execute or rewrite it)",
                ))
            else:
                findings.append((
                    _WARN,
                    f"{client}: stdio registration in {disp} predates the "
                    "HTTP daemon (`cairn serve` serves HTTP only)",
                ))
            hints.append(rewrite_hint)
            continue
        url = _registration_url(entry)
        if url is None or url == daemon_url:
            continue
        findings.append((
            _FAIL,
            f"{client}: SSE registration in {disp} points at {url} but the "
            f"daemon serves {daemon_url} (pre-HTTP-era endpoint)",
        ))
        hints.append(rewrite_hint)
    return findings, hints


def _check_environment(db: str) -> dict:
    """11. Environment wiring: store presence and registration transport classes; scrub-safe details."""
    findings: list[tuple[str, str]] = []
    hints: list[str] = []

    # (a) resolved-store existence.
    if not Path(db).exists():
        findings.append((_WARN, f"store not found at {db}"))
        hints.append("run `cairn init` + `cairn build` first")
    else:
        findings.append((_PASS, "resolved store present"))

    # (b) registration transport classes.
    reg_findings, reg_hints = _registration_findings()
    findings.extend(reg_findings)
    hints.extend(reg_hints)

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
    """Results when the store can't open: schema FAILs, DB-dependent checks WARN, config stays PASS."""
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
    """Execute the 11 checks against ``db``; never raises and never creates a missing store."""
    conn = None
    db_error: Exception | None = None
    if not Path(db).exists():
        db_error = FileNotFoundError(
            f"store not found at {db} -- run `cairn init` + `cairn build` first"
        )
        _log.debug("doctor: store missing at %s", db)
    else:
        try:
            # Read-only open: no migrations, no WAL flip; the sqlite_master
            # probe keeps corrupt-file detection on the degraded path.
            conn = get_db(db, read_only=True)
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        except Exception as e:  # OperationalError (can't open) / DatabaseError (corrupt) / ...
            db_error = e
            _log.debug("doctor: get_db(%s) raised %r", db, e)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None

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
_FIX_TRADEOFF = (
    "tradeoff: every client shares the single HTTP daemon instead of "
    "spawning per-client stdio processes"
)


def _render_doctor(results: list[dict], display) -> None:
    """Render one block per check; dynamic text is markup-escaped so paths cannot corrupt rich markup."""
    from rich.markup import escape

    for r in results:
        st = r["status"]
        display.console.print(
            f"[{_STATUS_STYLE[st]}]{_STATUS_GLYPH[st]} {st}[/] "
            f"[bold]{escape(r['name'])}[/bold]: {escape(r['detail'])}"
        )
        if r.get("hint"):
            display.dim(f"      hint: {escape(r['hint'])}")


def _fix_summary_lines(actions: list[dict]) -> list[str]:
    """One guidance line per fix action, plus the transport tradeoff."""
    from .doctor_fix import (
        _MALFORMED_CONFIG,
        _REFUSED_RECENT,
        _REPOINTED,
        _SKIPPED_WORKSPACE,
        _UNREADABLE_CONFIG,
        _UNREPLACEABLE,
        _UNSUPPORTED_DESKTOP,
        _WRITE_FAILED,
    )

    lines: list[str] = []
    for action in actions:
        client = action.get("client", "unknown")
        config = action.get("config", "unknown")
        url = action.get("url", "")
        name = action.get("action")
        if name == _REPOINTED:
            lines.append(
                f"repointed {client} in {config} to the daemon URL ({url})"
            )
        elif name == _UNSUPPORTED_DESKTOP:
            lines.append(
                f"{client} in {config} is unsupported: remove the cairn "
                "entry (Claude Desktop cannot speak the HTTP transport)"
            )
        elif name == _REFUSED_RECENT:
            lines.append(
                f"refused {config}: changed recently; retry "
                "`cairn doctor --fix` after it settles"
            )
        elif name == _SKIPPED_WORKSPACE:
            lines.append(
                f"skipped {config}: workspace-owned; edit the repo "
                "registration deliberately"
            )
        elif name == _UNREADABLE_CONFIG:
            lines.append(
                f"could not inspect {config} (stat failed); retry "
                "`cairn doctor --fix` once the file is readable"
            )
        elif name == _MALFORMED_CONFIG:
            lines.append(
                f"skipped {config}: not a JSON object; fix or remove the "
                "stale cairn entry manually"
            )
        elif name == _UNREPLACEABLE:
            lines.append(
                f"could not repoint {config}: unrecognized registration "
                "shape; edit it manually"
            )
        elif name == _WRITE_FAILED:
            lines.append(
                f"failed to repoint {config}: backup or write error; fix the "
                "file and re-run `cairn doctor --fix`"
            )
        else:
            lines.append(f"{client} {config}: {name} ({url})")
    if actions:
        lines.append(_FIX_TRADEOFF)
    return lines


def _render_fix_summary(actions: list[dict], display) -> None:
    """Render the fix-action count, one line per action, and the tradeoff."""
    from rich.markup import escape

    display.console.print(f"[bold]fix actions: {len(actions)}[/bold]")
    for line in _fix_summary_lines(actions):
        display.dim(f"      {escape(line)}")


@main.command()
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
@click.option(
    "--fix",
    is_flag=True,
    help="Repoint stale cairn-owned stdio/SSE registrations to the daemon URL.",
)
def doctor(db, as_json, fix):
    """Run system health checks; exits 0 on PASS/WARN-only, 1 on any FAIL; read-only unless --fix repoints cairn-owned registrations to the daemon URL."""
    from .. import display

    results = _run_doctor(db)
    actions: list[dict] | None = None
    if fix:
        from .doctor_fix import apply_doctor_fixes

        actions = apply_doctor_fixes(db)
        results = [
            _check_environment(db) if r["name"] == "environment" else r
            for r in results
        ]
    if as_json:
        if actions is None:
            click.echo(json.dumps(results, indent=2))
        else:
            click.echo(json.dumps(
                {
                    "checks": results,
                    "actions": actions,
                    "summary": _fix_summary_lines(actions),
                },
                indent=2,
            ))
    else:
        _render_doctor(results, display)
        if actions is not None:
            _render_fix_summary(actions, display)
    code = 1 if any(r["status"] == _FAIL for r in results) else 0
    click.get_current_context().exit(code)
