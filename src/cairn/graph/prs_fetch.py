"""PR view/diff fetchers over the gh CLI boundary, plus the store-age read."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cairn.graph.prs import GhError, _gh

_VIEW_FIELDS = "number,headRefName,baseRefName"


def normalize_impact_ref(value: str) -> str:
    """Return the ``gh pr view`` ref for a PR number (optional ``#``) or branch name; ValueError otherwise."""
    ref = value.strip().removeprefix("#")
    if not ref:
        raise ValueError("--impact needs a PR number or branch name")
    if ref.isdigit():
        return ref
    if ref.startswith("-"):
        raise ValueError(f"--impact ref {value!r} must not start with '-'")
    return ref


def fetch_pr_view(repo: Path, ref: str) -> tuple[int, str, str]:
    """Fetch (number, head_ref, base_ref) for one PR or branch via ``gh pr view``."""
    argv = ["pr", "view", normalize_impact_ref(ref), "--json", _VIEW_FIELDS]
    try:
        data = json.loads(_gh(Path(repo), argv))
    except json.JSONDecodeError as exc:
        raise GhError(f"gh pr view printed unparseable JSON: {exc}") from exc
    try:
        return int(data["number"]), data["headRefName"], data["baseRefName"]
    except (KeyError, TypeError, ValueError) as exc:
        raise GhError(
            f"gh pr view JSON lacks usable number/headRefName/baseRefName: {exc}"
        ) from exc


def fetch_pr_diff(repo: Path, number: int) -> str:
    """Fetch a PR's patch text via ``gh pr diff`` (never local git)."""
    return _gh(Path(repo), ["pr", "diff", str(int(number))])


def store_build_age(conn) -> str | None:
    """Human-readable age of the newest build_runs row, or None when absent/unreadable."""
    try:
        row = conn.execute(
            "SELECT started_at FROM build_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return None
    return _age_str(row[0]) if row else None


def _age_str(started_at) -> str | None:
    """Age of an ISO-8601 (aware) timestamp as ``<n>d/h/m/s old``, or None when unparseable."""
    if not started_at:
        return None
    try:
        dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 0:
        return "just now"
    if secs >= 86400:
        return f"{secs // 86400}d old"
    if secs >= 3600:
        return f"{secs // 3600}h old"
    if secs >= 60:
        return f"{secs // 60}m old"
    return f"{secs}s old"
