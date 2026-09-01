"""Sources-footer parsing and graph resolution for wiki pages.

A wiki page ends in a `## Sources` footer naming the files it cited. This
module extracts the footer entries (tolerating backtick list items and
inline-link forms) and resolves them against the L1 graph so promotion
carries only verified sources.
"""
from __future__ import annotations

import re
import sqlite3
from typing import List, Tuple

from ..refs import BACKTICK_RE
from ..refs import symbol_exists as _symbol_exists
from ..refs import unresolved_file_refs as _unresolved_file_refs

SOURCES_HEADING = "## Sources"

# `[text](target)` inline link; the target may carry a `#fragment`.
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def parse_sources_footer(body: str) -> List[str]:
    """Return the entries under the `## Sources` heading, in order.

    Tolerates ``- `path` `` list items and inline-link ``[text](path#L1)``
    forms (fragment stripped); for a line carrying links, the link targets
    are the entries. Prose before the footer is excluded; an absent or empty
    footer yields [].
    """
    lines = (body or "").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == SOURCES_HEADING:
            start = i + 1
            break
    if start is None:
        return []

    entries: List[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            break  # the next heading ends the footer
        links = _LINK_RE.findall(stripped)
        if links:
            entries.extend(target.split("#", 1)[0] for target in links)
            continue
        entries.extend(BACKTICK_RE.findall(stripped))
    return entries


def resolve_sources(
    entries: List[str], conn: sqlite3.Connection
) -> Tuple[List[str], List[str]]:
    """Resolve footer entries against the graph.

    Returns (resolved, errors): an entry resolves as a file (``file_exists``)
    or as a symbol (``symbol_exists``); unresolved entries are reported as
    errors and excluded from resolved.
    """
    unresolved_paths = set(_unresolved_file_refs(conn, entries))
    resolved: List[str] = []
    errors: List[str] = []
    for entry in entries:
        if entry not in unresolved_paths or _symbol_exists(conn, entry):
            resolved.append(entry)
        else:
            errors.append(f"Unresolved Sources footer entry: {entry}")
    return resolved, errors
