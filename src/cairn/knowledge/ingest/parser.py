"""Source-document parsing for knowledge ingestion.

Reads metadata from YAML frontmatter (falling back to a minimal line-based
parse when the YAML is malformed) and from inline status markers; the body
is returned with the frontmatter block stripped. Parsing only — doc-type
classification lives in the classifier module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

# Frontmatter keys read from source documents; other keys are ignored.
_SOURCE_KEYS = ("title", "status", "tags", "description")

_FENCE = "---"

_BOLD_STATUS_RE = re.compile(r"^\s{0,3}\*\*\s*Status\s*:\s*\*\*\s*(.*)$")
_HEADING_STATUS_RE = re.compile(r"^\s{0,3}#{1,6}\s*Status\s*:\s*(.+)$")
_HEADING_STATUS_BARE_RE = re.compile(r"^\s{0,3}#{1,6}\s*Status\s*:?$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_MINIMAL_PAIR_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 ._-]*):\s*(.*)$")


@dataclass
class ParsedDoc:
    """Metadata and frontmatter-stripped body of one source document."""

    title: str | None = None
    status: str | None = None
    tags: list[str] = field(default_factory=list)
    description: str | None = None
    body: str = ""


def parse_source_doc(text: str) -> ParsedDoc:
    """Parse one source document into metadata plus a stripped body.

    Frontmatter metadata wins over inline markers; a missing frontmatter
    title falls back to the body's first heading.
    """
    block, body = _split_source_frontmatter(text)
    meta = _frontmatter_metadata(block)
    return ParsedDoc(
        title=_as_text(meta.get("title")) or _first_heading(body),
        status=_as_text(meta.get("status")) or _inline_status(body),
        tags=_as_tags(meta.get("tags")),
        description=_as_text(meta.get("description")),
        body=body,
    )


def _split_source_frontmatter(text: str) -> tuple[str | None, str]:
    """Split a leading ``---``-fenced frontmatter block from the body.

    Returns ``(None, text)`` when the document does not open with a fence
    or the fence is never closed.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == _FENCE:
            block = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :]).lstrip("\n")
            return block, body
    return None, text


def _frontmatter_metadata(block: str | None) -> dict:
    """Map the source keys of a frontmatter block to raw values.

    Malformed YAML never raises: the minimal line-based fallback recovers
    the clean ``key: value`` pairs it can and drops the rest.
    """
    if block is None:
        return {}
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError:
        return _minimal_frontmatter(block)
    if not isinstance(loaded, dict):
        return {}
    return {key: loaded[key] for key in _SOURCE_KEYS if key in loaded}


def _minimal_frontmatter(block: str) -> dict:
    """Recover clean ``key: value`` pairs from malformed YAML."""
    meta: dict[str, str] = {}
    for raw_line in block.splitlines():
        match = _MINIMAL_PAIR_RE.match(raw_line.strip())
        if match is None:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key not in _SOURCE_KEYS or not value:
            continue
        if value[0] in "\"'":
            if len(value) < 2 or value[-1] != value[0]:
                continue
            value = value[1:-1].strip()
        elif value[0] in "[{":
            closer = "]" if value[0] == "[" else "}"
            if not value.endswith(closer):
                continue
        if not value:
            continue
        meta[key] = value
    return meta


def _as_text(value: object) -> str | None:
    """Normalize a frontmatter scalar to stripped text (None if empty)."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_tags(value: object) -> list[str]:
    """Normalize frontmatter tags: a YAML list or a comma/flow string."""
    if isinstance(value, str):
        value = value.strip()
        if len(value) >= 2 and value[0] == "[" and value[-1] == "]":
            value = value[1:-1]
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return []
    return [str(item).strip() for item in items if str(item).strip()]


def _inline_status(body: str) -> str | None:
    """Read a ``**Status:**`` value or ``## Status`` marker from the body."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        value = _marker_value(line)
        if value is None:
            continue
        if value:
            return value
        for follower in lines[i + 1 :]:
            follower = follower.strip()
            if follower:
                return follower
        return None
    return None


def _marker_value(line: str) -> str | None:
    """Same-line value of a status marker line; "" if the marker is bare."""
    match = _BOLD_STATUS_RE.match(line)
    if match is None:
        match = _HEADING_STATUS_RE.match(line)
    if match is not None:
        return match.group(1).strip()
    if _HEADING_STATUS_BARE_RE.match(line) is not None:
        return ""
    return None


def _first_heading(body: str) -> str | None:
    """Text of the body's first ATX heading, if any."""
    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match is not None:
            return match.group(1).strip()
    return None
