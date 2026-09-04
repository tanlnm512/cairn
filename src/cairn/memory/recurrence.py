"""Failure-signature recurrence tracking for the post_tool_failure hook.

The hook stays a pure "compute + spawn" path: it hashes the already
privacy-filtered error text into a stable signature and passes it to
``cairn memory record --recurrence-key``. All DB work happens in the child
CLI process via :func:`note_failure_signature`.
"""
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
    """Stable 16-hex-char key for a ``(tool_name, error)`` failure shape.

    Normalization collapses everything volatile out of an error string:
    case, whitespace runs, absolute paths, UUIDs, hex runs, and digit runs
    (pids, line numbers, timestamps). Two failures that differ only in such
    noise hash equal.
    """
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
    """Register one occurrence of ``sig`` and return the count BEFORE it.

    First occurrence inserts the row and returns 0; later occurrences
    bump ``occurrences``/``last_seen`` and return the prior count. The
    caller owns the commit.
    """
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT occurrences FROM memory_failure_signatures WHERE sig = ?",
        (sig,),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO memory_failure_signatures"
            " (sig, tool_name, occurrences, first_seen, last_seen)"
            " VALUES (?, ?, 1, ?, ?)",
            (sig, tool_name, now, now),
        )
        return 0
    conn.execute(
        "UPDATE memory_failure_signatures"
        " SET occurrences = occurrences + 1, last_seen = ?"
        " WHERE sig = ?",
        (now, sig),
    )
    return row[0]
