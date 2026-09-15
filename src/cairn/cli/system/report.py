"""Diagnostic report command and redaction helpers."""
from __future__ import annotations

import os
import platform
import re
import sqlite3
import click
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ... import __version__
from ...memory.privacy import strip_private_data
from .doctor import _knob_source, _run_doctor
from .metrics import _fmt_ts
from ..main import DEFAULT_DB_PATH, get_db, main

_log = logging.getLogger(__name__)

# Report bundles pass private strings and filesystem paths through redaction filters
# before printing or writing; the command never uploads data.

# The error-ish event names mirrored from the spec §6.4 degradation catalog:
# lock contention and the two silent backend fallbacks. (``stray_swept`` and
# the quality/lifecycle signals are normal operation, not errors.)
_ERROR_EVENTS: tuple[str, ...] = ("ann_fallback", "hash_fallback", "lock_contention")
_REPORT_LIMIT = 20  # bounded set of recent rows per source (events / tool errors)

# Absolute-path redaction. POSIX: a leading "/" followed by one or more
# directory segments ("~" shorthand included); Windows: a drive-letter root.
# The lookbehind keeps URL path portions ("https://host/x") and relative
# workspace paths ("src/main.py") intact -- only absolute local paths leak the
# user's directory structure. The basename survives so a report stays
# debuggable ("[PATH]/main.py:10" still names the failing file).
_POSIX_PATH_RE = re.compile(r"(?<![\w:/.-])/(?:[^\s\"']+/)+[^\s\"']*|(?<![\w])~/[^\s\"']+")
_WIN_PATH_RE = re.compile(r"(?<![\w])(?:[A-Za-z]:)?\\(?:[^\\\s\"']+\\)+[^\\\s\"']*")


def _redact_path_match(m: re.Match) -> str:
    """Replace one absolute-path hit with ``[PATH]/<basename>``."""
    leaf = re.split(r"[\\/]", m.group(0))[-1]
    return f"[PATH]/{leaf}" if leaf else "[PATH]"


def _redact_paths(text: str) -> str:
    """Collapse absolute filesystem paths to ``[PATH]/<basename>``."""
    text = _POSIX_PATH_RE.sub(_redact_path_match, text)
    return _WIN_PATH_RE.sub(_redact_path_match, text)


def _scrub(value):
    """Privacy gate for one bundle field.

    Strings pass through ``strip_private_data`` (secret shapes / tags / URI
    credentials, spec §7) and then ``_redact_paths`` (absolute local paths);
    anything else (ints/floats/None/timestamps) is returned unchanged. Applied
    to every field below so the redaction invariant holds regardless of source.
    """
    if isinstance(value, str):
        return _redact_paths(strip_private_data(value))
    return value


def _scrub_strings(d: dict) -> dict:
    """Apply the privacy gate to every value in ``d`` (non-strings pass through)."""
    return {k: _scrub(v) for k, v in d.items()}


def _scrub_doctor(results: list[dict]) -> list[dict]:
    """Redact the dynamic-content fields of doctor rows (``detail``/``hint``).

    ``name``/``status`` come from a closed enum and cannot carry user data, so
    they are left as-is; ``detail`` (e.g. parse_errors lists file paths) and
    ``hint`` are the fields that could carry paths/secrets and are scrubbed.
    """
    out: list[dict] = []
    for r in results:
        rr = dict(r)
        rr["detail"] = _scrub(rr.get("detail"))
        if rr.get("hint") is not None:
            rr["hint"] = _scrub(rr["hint"])
        out.append(rr)
    return out


def _open_report_conn(db: str):
    """Open the store for reads; return None (never raise) if it can't open.

    Mirrors doctor's graceful-degradation contract: a missing/read-only/corrupt
    store degrades the DB-dependent sections to empty/FAIL rather than crashing.
    A path that doesn't exist is NOT created -- the report is a read-only
    diagnostic and must not materialize a store (or mask a typo'd ``--db``).
    """
    if not Path(db).exists():
        _log.debug("report: store missing at %s", db)
        return None
    try:
        return get_db(db)
    except Exception as e:  # OperationalError / DatabaseError / ...
        _log.debug("report: get_db(%s) raised %r", db, e)
        return None


def _report_versions(conn) -> dict:
    """Runtime + store versions. cairn/Python/platform/sqlite are always cheap;
    ``db_schema_user_version`` is a best-effort ``PRAGMA user_version`` probe
    (cairn applies ``CREATE TABLE IF NOT EXISTS`` DDL and does not track a
    numeric schema version, so this is typically 0; None when unreadable).
    """
    user_version = None
    if conn is not None:
        try:
            row = conn.execute("PRAGMA user_version").fetchone()
            user_version = row[0] if row else None
        except Exception:
            _log.debug("report: user_version unreadable", exc_info=True)
            user_version = None
    return {
        "cairn": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "db_schema_user_version": user_version,
    }


def _gather_recent_errors(conn) -> dict:
    """Bounded set of recent error-ish rows from ``events`` + ``tool_metrics``.

    ``events``: the degradation-catalog names (``_ERROR_EVENTS``), newest first,
    capped at ``_REPORT_LIMIT``. ``tool_metrics``: rows with ``status='error'``,
    newest first, same cap. Both degrade to empty lists on any read failure or
    when the store is unavailable -- never raise. Every string value is scrubbed.
    """
    events: list[dict] = []
    tool_errors: list[dict] = []
    if conn is None:
        return {"events": events, "tool_errors": tool_errors}

    placeholders = ",".join("?" for _ in _ERROR_EVENTS)
    try:
        rows = conn.execute(
            f"SELECT ts, name, session_id, attrs FROM events "
            f"WHERE name IN ({placeholders}) ORDER BY ts DESC LIMIT ?",
            [*_ERROR_EVENTS, _REPORT_LIMIT],
        ).fetchall()
        for r in rows:
            events.append(_scrub_strings({
                "ts": r[0],
                "name": r[1],
                "session_id": r[2],
                "attrs": r[3],
            }))
    except Exception:
        _log.debug("report: events unreadable", exc_info=True)

    try:
        rows = conn.execute(
            "SELECT tool_name, invoked_at, duration_ms, status, error_message "
            "FROM tool_metrics WHERE status = 'error' "
            "ORDER BY invoked_at DESC LIMIT ?",
            (_REPORT_LIMIT,),
        ).fetchall()
        for r in rows:
            tool_errors.append(_scrub_strings({
                "tool_name": r[0],
                "invoked_at": r[1],
                "duration_ms": r[2],
                "status": r[3],
                "error_message": r[4],
            }))
    except Exception:
        _log.debug("report: tool_metrics unreadable", exc_info=True)

    return {"events": events, "tool_errors": tool_errors}


def _report_config() -> dict:
    """Effective CAIRN_* knobs (the same list ``_check_config`` echoes).

    A structured key->value form of the config-echo check so the report's
    ``config`` section is self-describing in JSON. Values are scrubbed by the
    caller before inclusion.
    """
    return {
        "workers": os.environ.get("CAIRN_WORKERS", "<unset>"),
        "read_only": os.environ.get("CAIRN_READ_ONLY", "<unset>"),
        "fusion": os.environ.get("CAIRN_FUSION", "<unset>"),
        "ann_backend": os.environ.get("CAIRN_ANN_BACKEND", "<unset (=sqlite-vec)>"),
        # Same resolution _check_config uses, so report and doctor
        # agree on the effective embed backend (env > file > default).
        "embed_backend": _knob_source("CAIRN_EMBED_BACKEND", "<unset (=local)>")[0],
        "telemetry": os.environ.get("CAIRN_TELEMETRY", "<unset (=on)>"),
        "log_level": os.environ.get("CAIRN_LOG_LEVEL", "<unset (=WARNING)>"),
    }


def _build_report(db: str) -> dict:
    """Assemble the redacted bundle. Never raises.

    Versions/recent-errors share one read connection (closed in ``finally``);
    doctor opens its own via ``_run_doctor`` (reused unchanged). All string
    fields are routed through the privacy gate before the bundle is returned.
    """
    conn = _open_report_conn(db)
    try:
        versions = _scrub_strings(_report_versions(conn))
        recent_errors = _gather_recent_errors(conn)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                _log.debug("report: conn.close failed", exc_info=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "versions": versions,
        "doctor": _scrub_doctor(_run_doctor(db)),
        "recent_errors": recent_errors,
        "config": _scrub_strings(_report_config()),
    }


def _render_report(bundle: dict) -> str:
    """Plain-text rendering of the bundle (clean copy-paste, no ANSI codes).

    Kept as plain text (rather than ``display``/rich) so the bundle pastes
    cleanly into a GitHub issue and so ``--out`` can faithfully capture the
    same text that goes to stdout. Doctor rows render as PASS/WARN/FAIL lines.
    """
    lines: list[str] = [
        "cairn report — redacted diagnostic bundle",
        "# Secrets scrubbed via memory.privacy.strip_private_data; absolute "
        "paths collapsed to [PATH]/<basename>. Safe to paste into a GitHub issue.",
        f"generated: {bundle['generated_at']}",
        "",
    ]

    v = bundle["versions"]
    lines.append("## Versions")
    lines.append(f"cairn: {v['cairn']}")
    lines.append(f"python: {v['python']}")
    lines.append(f"platform: {v['platform']}")
    lines.append(f"sqlite: {v['sqlite']}")
    lines.append(f"db_schema_user_version: {v['db_schema_user_version']}")
    lines.append("")

    lines.append("## Doctor")
    for r in bundle["doctor"]:
        lines.append(f"{r['status']:<4} {r['name']}: {r['detail']}")
        if r.get("hint"):
            lines.append(f"      hint: {r['hint']}")
    lines.append("")

    re_ = bundle["recent_errors"]
    lines.append(f"## Recent error events ({len(re_['events'])})")
    if re_["events"]:
        for e in re_["events"]:
            lines.append(f"  {_fmt_ts(e['ts'])} {e['name']} {e.get('attrs') or ''}")
    else:
        lines.append("  none")
    lines.append("")

    lines.append(f"## Recent tool errors ({len(re_['tool_errors'])})")
    if re_["tool_errors"]:
        for t in re_["tool_errors"]:
            lines.append(f"  {_fmt_ts(t['invoked_at'])} {t['tool_name']} {t.get('error_message') or ''}")
    else:
        lines.append("  none")
    lines.append("")

    lines.append("## Config")
    for k, val in bundle["config"].items():
        lines.append(f"{k}: {val}")

    return "\n".join(lines)


@main.command()
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit the bundle as JSON.")
@click.option("--out", "out_path", default=None,
              help="Write the bundle to this file as well as printing it.")
def report(db, as_json, out_path):
    """Print a redacted diagnostic bundle for bug reports / GitHub issues.

    Assembles four sections into one bundle: versions (cairn/Python/platform/
    sqlite/store), the 10 doctor checks, recent error-ish events and
    ``tool_metrics`` errors, and the effective ``CAIRN_*`` config.

    PRIVACY GATE (spec observability-telemetry §7): every string field is
    passed through ``memory.privacy.strip_private_data`` (known secret shapes
    -- API keys, bearer tokens, JWTs, ... -- and ``<private>`` tags are
    redacted to ``[REDACTED_SECRET]`` / ``[REDACTED]``) and then through path
    redaction (absolute local filesystem paths collapse to
    ``[PATH]/<basename>``, since ``str(exc)`` from file I/O embeds them).

    The bundle is printed to stdout and NEVER auto-uploaded. ``--out PATH``
    additionally writes it to a file (JSON with ``--json``, otherwise the same
    human-readable text); the file-write confirmation goes to stderr so it
    can't corrupt JSON output. Best-effort throughout: a missing, read-only, or
    corrupt store degrades to empty sections and a schema FAIL (mirroring
    ``cairn doctor``) and never raises.
    """
    bundle = _build_report(db)
    if as_json:
        text = json.dumps(bundle, indent=2, default=str)
    else:
        text = _render_report(bundle)
    click.echo(text)

    if out_path:
        try:
            Path(out_path).write_text(text + "\n", encoding="utf-8")
        except OSError as e:
            click.echo(f"warning: could not write --out {out_path}: {e}", err=True)
        else:
            click.echo(f"wrote report to {out_path}", err=True)
