"""Per-language comment-node map and marker-comment extraction."""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

from tree_sitter import Node

from .base import RationaleRecord

# Comment node types per registry language; unmapped languages yield no records.
COMMENT_NODE_TYPES: Dict[str, Tuple[str, ...]] = {
    "python": ("comment",),
    "go": ("comment",),
    "rust": ("line_comment", "block_comment"),
    "java": ("line_comment", "block_comment"),
    "kotlin": ("line_comment", "multiline_comment"),
    "c": ("comment",),
    "cpp": ("comment",),
    "csharp": ("comment",),
    "ruby": ("comment",),
    "swift": ("comment", "multiline_comment"),
    "dart": ("comment",),
    "objc": ("comment",),
    "typescript": ("comment",),
    "tsx": ("comment",),
    "javascript": ("comment",),
    "php": ("comment",),
}

_MARKER_RE = re.compile(r"^(NOTE|WHY|HACK):", re.IGNORECASE)


def _comment_body(text: str) -> str:
    """Strip the comment opener and fold continuation lines to single spaces."""
    stripped = text.strip()
    if stripped.startswith("/*"):
        body = stripped[2:].rstrip()
        if body.endswith("*/"):
            body = body[:-2]
        lines = []
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("*"):
                line = line.lstrip("*").strip()
            if line:
                lines.append(line)
        return " ".join(lines)
    return stripped.lstrip("#/").strip()


def extract_rationale(node: Node, source: bytes) -> Optional[RationaleRecord]:
    """Return a RationaleRecord for a NOTE/WHY/HACK comment node, else None."""
    body = _comment_body(
        source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    )
    match = _MARKER_RE.match(body)
    if match is None:
        return None
    return RationaleRecord(
        line=node.start_point[0] + 1,
        kind=match.group(1).lower(),
        text=body[match.end() :].strip(),
    )
