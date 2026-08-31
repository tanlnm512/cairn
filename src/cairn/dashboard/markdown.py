"""Escape-first markdown renderer for the dashboard wiki view (D-002).

Pure stdlib (``html`` + ``re``): importing this module must never load the
server stack — the same guard the dashboard package is held to. Every line
is HTML-escaped before any construct is rendered, so inline HTML never
passes through; the whitelisted block constructs are headings, paragraphs,
unordered lists, and fenced code. Mermaid fences emit ``<pre class="mermaid">``
so the wiki detail view's client-side mermaid.js can render them live; with
JavaScript or the CDN unavailable the fence degrades to a visible code
block.
"""
from __future__ import annotations

import html
import re
from typing import List, Optional

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^```(\S*)\s*$")
_LIST_ITEM_RE = re.compile(r"^-\s+(.*)$")


def _emit_fence(out: List[str], lang: str, lines: List[str]) -> None:
    body = "\n".join(lines)
    if lang == "mermaid":
        out.append(f'<pre class="mermaid">{body}</pre>')
    else:
        out.append(f"<pre><code>{body}</code></pre>")


def render_markdown(text: str) -> str:
    """Render a markdown body to HTML: escape first, whitelist blocks."""
    out: List[str] = []
    paragraph: List[str] = []
    items: List[str] = []
    fence_lang: Optional[str] = None
    fence_lines: List[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + " ".join(paragraph) + "</p>")
            paragraph.clear()

    def flush_list() -> None:
        if items:
            out.append(
                "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"
            )
            items.clear()

    for raw in text.split("\n"):
        line = html.escape(raw, quote=False)
        fence = _FENCE_RE.match(line)
        if fence_lang is not None:
            if fence:
                _emit_fence(out, fence_lang, fence_lines)
                fence_lang = None
                fence_lines = []
            else:
                fence_lines.append(line)
            continue
        if fence:
            flush_paragraph()
            flush_list()
            fence_lang = fence.group(1)
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{heading.group(2)}</h{level}>")
            continue
        item = _LIST_ITEM_RE.match(line)
        if item:
            flush_paragraph()
            items.append(item.group(1))
            continue
        if not line.strip():
            flush_paragraph()
            flush_list()
            continue
        flush_list()
        paragraph.append(line.strip())

    if fence_lang is not None:
        _emit_fence(out, fence_lang, fence_lines)
    flush_paragraph()
    flush_list()
    return "\n".join(out)
