"""Service-topology edge detection (code -> external service / HTTP call).

Runs as a post-parse pass over an already-parsed ``ParsedFile``, alongside
:mod:`parsers.routes`. It emits *edges* (not symbols) of two kinds:

  - ``kind='http_call'``   — a call to a known HTTP client method
    (``fetch(...)``, ``axios.get(...)``, ``http.Get/Post`` in Go,
    ``OkHttp``/``Retrofit`` in Kotlin/Java). The target is usually a string
    literal URL or an external method, so the resolver will mark these
    ``unresolved`` — which is correct and informative, not a gap.

  - ``kind='service_call'`` — a call from a route handler (``kind='route'``
    symbol produced by :mod:`parsers.routes`) to another service. Composes
    with route extraction to give a service-topology story.

The builder merges these into the ``ParsedFile``'s ``edges`` list before the
normal insert + resolver passes run — service-call edges are just more edges
to the rest of the pipeline. No schema change is needed: ``edges.kind`` is
free-text, indexed.

By default ``impact_analysis`` and ``trace_flow`` follow only *structural*
edges (``calls``/``extends``/``implements``) and exclude these, because their
targets are often external and would inflate blast radius. Callers opt in via
``include_service_edges=True`` (see :mod:`graph.traversal`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .base import Edge, ParsedFile


@dataclass
class ServiceCallExtraction:
    edges: List[Edge] = field(default_factory=list)

    def extend(self, other: "ServiceCallExtraction") -> None:
        self.edges.extend(other.edges)


# ---------------------------------------------------------------------------
# Detection: per-language HTTP client call patterns.
#
# These are lightweight regex scans over the raw source, not a second AST parse.
# Like the React-Router route detector, this means they can miss reformatted or
# multi-line variants — but they catch the overwhelmingly common shapes and
# never fail the file's indexing (the builder wraps the call in try/except).
# ---------------------------------------------------------------------------

# JavaScript/TypeScript: fetch("url"), axios.get("url"), axios.post, etc.
_JS_HTTP_PATTERNS = [
    # fetch("https://...") / fetch(`https://...`)
    re.compile(r"\bfetch\s*\(\s*(['\"`])(https?://[^'\"`]+)\1"),
    # axios.get("..."), axios.post("..."), axios.put, axios.delete, axios.patch
    re.compile(r"\baxios\.(?:get|post|put|delete|patch|head|options|request)\s*\(\s*(['\"`])(https?://[^'\"`]+)\1"),
]

# Go: http.Get("url"), http.Post("url", ...), http.NewRequest("GET", "url", ...)
_GO_HTTP_PATTERNS = [
    re.compile(r"\bhttp\.(?:Get|Post|Head|PostForm|NewRequest)\s*\(\s*(?:\"([A-Z]+)\",\s*)?(['\"])(https?://[^'\"]+)\2"),
]

# Kotlin/Java: Retrofit-style service interfaces and OkHttp calls. These are
# harder to pin without a full type-resolve, so we match the URL literal
# extraction plus the common client idioms.
_JVM_HTTP_PATTERNS = [
    # OkHttp: Request.Builder().url("https://...")
    re.compile(r"\.url\s*\(\s*(['\"])(https?://[^'\"]+)\1"),
    # Retrofit builders: Retrofit.Builder().baseUrl("https://...")
    re.compile(r"\.baseUrl\s*\(\s*(['\"])(https?://[^'\"]+)\1"),
]

_LANGUAGE_PATTERNS: Dict[str, List[re.Pattern]] = {
    "typescript": _JS_HTTP_PATTERNS,
    "javascript": _JS_HTTP_PATTERNS,
    "go": _GO_HTTP_PATTERNS,
    "kotlin": _JVM_HTTP_PATTERNS,
    "java": _JVM_HTTP_PATTERNS,
}


def detect_service_calls(pf: ParsedFile, language: str) -> Optional[ServiceCallExtraction]:
    """Detect HTTP/service-call edges in a parsed file.

    Returns None if nothing was found (the common case). Emits ``http_call``
    edges for direct HTTP client invocations; ``service_call`` edges for calls
    whose owner is a route handler (composing with route detection).
    """
    patterns = _LANGUAGE_PATTERNS.get(language)
    if not patterns:
        return None

    try:
        source = Path(pf.path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    extraction = ServiceCallExtraction()

    # Determine the set of owner symbol names that are route handlers — calls
    # owned by them become `service_call` rather than `http_call`.
    route_owners = {s.name for s in pf.symbols if s.kind == "route"}
    # If routes produced symbols, the handler functions are referenced from
    # them; include any handler name that appears as a route symbol's metadata.
    for s in pf.symbols:
        if s.kind == "route" and s.metadata and s.metadata.get("handler"):
            route_owners.add(s.metadata["handler"])

    for pat in patterns:
        for m in pat.finditer(source):
            line = source.count("\n", 0, m.start()) + 1
            # The URL is the last captured group in each pattern.
            url = m.groups()[-1] if m.groups() else m.group(0)
            owner = _owner_for_line(pf, line)
            kind = "service_call" if owner in route_owners else "http_call"
            extraction.edges.append(
                Edge(
                    source_name=owner,
                    kind=kind,
                    target_name=url,
                    line=line,
                )
            )

    return extraction if extraction.edges else None


def _owner_for_line(pf: ParsedFile, line: int) -> str:
    """Best-effort: find the symbol whose span contains `line`.

    Falls back to the nearest preceding symbol, then to "" (top-level). This
    mirrors the base parser's `_current_edge_owner` but operates post-parse on
    the already-collected symbol list rather than a live scope stack.
    """
    candidate = ""
    best_start = -1
    for s in pf.symbols:
        if s.line_start <= line and s.line_start > best_start:
            best_start = s.line_start
            candidate = s.name
    return candidate
