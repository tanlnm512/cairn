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
from ...graph.stats import get_stats
from .doctor import _knob_source, _run_doctor
from .metrics import _fmt_ts
from ..main import DEFAULT_DB_PATH, get_db, main

_log = logging.getLogger(__name__)

# Report bundles pass private strings and filesystem paths through redaction filters
# before printing or writing; the command never uploads data.

# The error-ish event names mirrored from the degradation event catalog:
# lock contention and the two silent backend fallbacks. (``stray_swept`` and
# the quality/lifecycle signals are normal operation, not errors.)
_ERROR_EVENTS: tuple[str, ...] = ("ann_fallback", "hash_fallback", "lock_contention")
_REPORT_LIMIT = 20  # bounded set of recent rows per source (events / tool errors)

# Absolute-path redaction: POSIX leading "/" or "~" roots, Windows
# drive-letter roots; URLs and relative paths stay intact, basenames survive
# so reports stay debuggable.
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
    """Privacy gate for one field: strings get strip_private_data then path redaction; other types pass through."""
    if isinstance(value, str):
        return _redact_paths(strip_private_data(value))
    return value


def _scrub_strings(d: dict) -> dict:
    """Apply the privacy gate to every value in ``d`` (non-strings pass through)."""
    return {k: _scrub(v) for k, v in d.items()}


def _scrub_doctor(results: list[dict]) -> list[dict]:
    """Redact the dynamic doctor fields (``detail``/``hint``); name/status come from a closed enum."""
    out: list[dict] = []
    for r in results:
        rr = dict(r)
        rr["detail"] = _scrub(rr.get("detail"))
        if rr.get("hint") is not None:
            rr["hint"] = _scrub(rr["hint"])
        out.append(rr)
    return out


def _open_report_conn(db: str):
    """Open the store for reads; None (never raise) when it cannot open, and never create a missing store."""
    if not Path(db).exists():
        _log.debug("report: store missing at %s", db)
        return None
    try:
        return get_db(db)
    except Exception as e:  # OperationalError / DatabaseError / ...
        _log.debug("report: get_db(%s) raised %r", db, e)
        return None


def _report_versions(conn) -> dict:
    """Runtime + store versions; db_schema_user_version is a best-effort PRAGMA probe (None when unreadable)."""
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
    """Bounded recent error rows from events + tool_metrics (newest first, scrubbed, never raise)."""
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
    """Effective CAIRN_* knobs as a structured key->value echo, mirroring _check_config."""
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


def _source_root() -> Path | None:
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "src").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def _audit_quality_gate(root: Path) -> dict:
    from .audit_status import collect_audit_status

    try:
        status = collect_audit_status(root)
        return {
            "total": status["total"],
            "remaining": status["remaining"],
            "by_priority": {
                priority: counts["remaining"]
                for priority, counts in status["totals"].items()
            },
        }
    except Exception:
        return {"status": "error"}


def _comment_quality_gate(root: Path) -> dict:
    from .comment_style import DEFAULT_BASELINE, check_comment_baseline

    try:
        status = check_comment_baseline(root, root.joinpath(*DEFAULT_BASELINE))
        if not status["ok"]:
            return {"status": "error"}
        return {"remaining": status["remaining"]}
    except Exception:
        return {"status": "error"}


def _quality_gates() -> dict:
    try:
        root = _source_root()
    except Exception:
        root = None
    if root is None:
        return {
            "audit_status": {"status": "unavailable"},
            "comment_style": {"status": "unavailable"},
        }
    return {
        "audit_status": _audit_quality_gate(root),
        "comment_style": _comment_quality_gate(root),
    }


def _report_resolution(conn) -> dict:
    if conn is None:
        return {}
    try:
        return get_stats(conn)["resolution_by_language"]
    except Exception:
        _log.debug("report: resolution stats unreadable", exc_info=True)
        return {}


def _build_report(db: str) -> dict:
    """Assemble the redacted bundle; never raises, every string routed through the privacy gate."""
    conn = _open_report_conn(db)
    try:
        versions = _scrub_strings(_report_versions(conn))
        recent_errors = _gather_recent_errors(conn)
        resolution = _report_resolution(conn)
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
        "resolution": resolution,
        "quality_gates": _quality_gates(),
        "config": _scrub_strings(_report_config()),
    }


def _render_report(bundle: dict) -> str:
    """Plain-text rendering (no ANSI) so the bundle pastes cleanly and --out captures stdout verbatim."""
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

    lines.append("## Resolution")
    if bundle["resolution"]:
        for language, counts in bundle["resolution"].items():
            lines.append(
                f"{language}: {counts['exact']} exact ({counts['exact_pct']:.1%}), "
                f"{counts['ambiguous']} ambiguous ({counts['ambiguous_pct']:.1%}), "
                f"{counts['unresolved']} unresolved ({counts['unresolved_pct']:.1%})"
            )
    else:
        lines.append("  none")
    lines.append("")

    quality = bundle["quality_gates"]
    lines.append("## Quality gates")
    audit = quality["audit_status"]
    if "status" in audit:
        lines.append(f"audit_status: {audit['status']}")
    else:
        lines.append(f"audit_status total: {audit['remaining']}/{audit['total']} remaining")
        for priority, remaining in audit["by_priority"].items():
            lines.append(f"audit_status {priority}: {remaining} remaining")
    comment = quality["comment_style"]
    if "status" in comment:
        lines.append(f"comment_style: {comment['status']}")
    else:
        lines.append(f"comment_style remaining: {comment['remaining']}")
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
    """Print a redacted diagnostic bundle (versions, doctor checks, recent errors, config); never uploads.
    ``--out`` writes the same text to a file and confirms on stderr so JSON output stays parseable."""
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
