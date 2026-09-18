"""Resolved-review-comment event adapter: event payload to captured memory."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from ..memory.promotion import capture_memory
from ..okf.bundle import OKFBundle
from .spans import CommentSymbols, resolve_comment_symbols

__all__ = [
    "CAPTURE_CONFIDENCE",
    "PATTERN_MARKER",
    "ReviewEvent",
    "capture_review_event",
    "classify_event",
    "parse_event",
]

# Thread label marking a resolved comment as a reusable pattern (D-003);
# any other resolved comment records as a mistake.
PATTERN_MARKER = "cairn:pattern"

# Auto-captured comments are unvetted second-hand guidance; a low
# agent-confidence prior keeps the stored tier in the drafts band.
CAPTURE_CONFIDENCE = 0.3

# Cap on the comment summary carried in the memory title.
TITLE_SUMMARY_MAX_CHARS = 80

_PULL_RE = re.compile(r"/pull/(\d+)")

_EVENT_FIELDS = ("resolved", "file", "line", "body", "thread_url")


@dataclass(frozen=True)
class ReviewEvent:
    """One adapted resolved-comment event."""

    resolved: bool
    file_path: str
    line: int | None
    body: str
    thread_url: str


def parse_event(payload: object) -> ReviewEvent:
    """Validate one event payload against the pinned shape.

    Raises ``ValueError`` naming the offending field when the payload is
    not an object, a required field is missing, or a field has the wrong
    type. Unknown fields are ignored.
    """
    if not isinstance(payload, dict):
        raise ValueError("event payload must be a JSON object")
    for field in _EVENT_FIELDS:
        if field not in payload:
            raise ValueError(f"event payload field '{field}' is required")

    resolved = payload["resolved"]
    if not isinstance(resolved, bool):
        raise ValueError("event payload field 'resolved' must be a boolean")

    file_path = payload["file"]
    if not isinstance(file_path, str) or not file_path.strip():
        raise ValueError("event payload field 'file' must be a non-empty string")

    line = payload["line"]
    if line is not None and (
        not isinstance(line, int) or isinstance(line, bool) or line < 1
    ):
        raise ValueError("event payload field 'line' must be a positive integer or null")

    body = payload["body"]
    thread_url = payload["thread_url"]
    if not isinstance(body, str):
        raise ValueError("event payload field 'body' must be a string")
    if not isinstance(thread_url, str):
        raise ValueError("event payload field 'thread_url' must be a string")

    return ReviewEvent(resolved, file_path, line, body, thread_url)


def classify_event(event: ReviewEvent) -> str:
    """Deterministic capture type: marker -> pattern, anything else mistake."""
    return "pattern" if PATTERN_MARKER in event.body else "mistake"


def capture_review_event(
    conn: sqlite3.Connection, bundle: OKFBundle, payload: dict
) -> dict:
    """Record one adapted comment event; return what was recorded.

    An open (unresolved) comment records nothing: the result carries
    ``recorded: False``. A resolved comment is captured as a draft-tier
    memory of the classified type, keyed to the enclosing symbols (or the
    file when the location is file-level/unindexed), with the thread link
    in the body.
    """
    event = parse_event(payload)
    if not event.resolved:
        return _result(False)

    spans = resolve_comment_symbols(conn, event.file_path, event.line)
    memory_type = classify_event(event)
    title = _build_title(spans, event)
    stored = capture_memory(
        conn,
        bundle,
        type_=memory_type,
        title=title,
        body=_build_body(spans, event),
        resource=spans.file_path,
        confidence=CAPTURE_CONFIDENCE,
    )
    return _result(
        True,
        type_=memory_type,
        path=stored["path"],
        tier=stored["tier"],
        file_level=spans.file_level,
        symbols=[s.qualified_name or s.name for s in spans.symbols],
        title=title,
    )


def _result(
    recorded: bool,
    type_: str | None = None,
    path: str | None = None,
    tier: str | None = None,
    file_level: bool = True,
    symbols: list[str] | None = None,
    title: str | None = None,
) -> dict:
    return {
        "recorded": recorded,
        "type": type_,
        "path": path,
        "tier": tier,
        "file_level": file_level,
        "symbols": symbols or [],
        "title": title,
    }


def _build_title(spans: CommentSymbols, event: ReviewEvent) -> str:
    """Title carrying the keying and thread so the standard memory listing
    shows location, thread, and guidance: ``<file> [pull/N]: <summary>``."""
    summary = next(
        (ln.strip() for ln in event.body.splitlines() if ln.strip()), ""
    )
    if not summary:
        summary = "review comment"
    if len(summary) > TITLE_SUMMARY_MAX_CHARS:
        summary = summary[:TITLE_SUMMARY_MAX_CHARS].rstrip() + "..."
    pull = _PULL_RE.search(event.thread_url)
    prefix = f"{spans.file_path} pull/{pull.group(1)}: " if pull else f"{spans.file_path}: "
    return prefix + summary


def _build_body(spans: CommentSymbols, event: ReviewEvent) -> str:
    """Body carrying the comment text, the backtick-ref keying, and the
    thread link."""
    parts: list[str] = []
    if event.body.strip():
        parts.append(event.body.strip())
    parts.append(f"File: `{spans.file_path}`")
    if spans.symbols:
        names = ", ".join(f"`{s.qualified_name or s.name}`" for s in spans.symbols)
        parts.append(f"Symbols: {names}")
    if event.thread_url.strip():
        parts.append(f"Thread: {event.thread_url.strip()}")
    return "\n".join(parts)
