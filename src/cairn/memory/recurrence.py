"""Failure-signature recurrence tracking for the post_tool_failure hook."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_PATH_RE = re.compile(r"(?:/[A-Za-z0-9._-]+)+")
_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b")
_DIGITS_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")


def failure_signature(tool_name: str, error: str) -> str:
    """Return a stable 16-hex key for the normalized failure shape."""
    text = _WS_RE.sub(" ", str(error)).strip().lower()
    text = _UUID_RE.sub(" ", text)
    text = _PATH_RE.sub(" ", text)
    text = _HEX_RE.sub(" ", text)
    text = _DIGITS_RE.sub("0", text)
    text = text[:200]
    return hashlib.sha256(
        f"{tool_name}\n{text}".encode("utf-8")).hexdigest()[:16]


def note_failure_signature(conn: sqlite3.Connection, sig: str,
                           tool_name: str) -> int:
    """Record one failure occurrence and return the prior occurrence count."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO memory_failure_signatures"
        " (sig, tool_name, occurrences, first_seen, last_seen)"
        " VALUES (?, ?, 1, ?, ?)"
        " ON CONFLICT(sig) DO UPDATE SET"
        " occurrences = occurrences + 1, last_seen = excluded.last_seen",
        (sig, tool_name, now, now),
    )
    row = conn.execute(
        "SELECT occurrences FROM memory_failure_signatures WHERE sig = ?",
        (sig,),
    ).fetchone()
    return (row[0] - 1) if row else 0
