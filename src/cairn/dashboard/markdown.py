"""Escape-first markdown renderer for the dashboard wiki view (D-002).

Pure stdlib (``html`` + ``re``): importing this module must never load the
server stack — the same guard the dashboard package is held to. Every line
is HTML-escaped before any construct is rendered, so inline HTML never
passes through; the whitelisted block constructs are headings, paragraphs,
unordered lists, fenced code, and GFM pipe tables. Mermaid fences emit
``<pre class="mermaid">`` so the wiki detail view's client-side mermaid.js
can render them live; with JavaScript or the CDN unavailable the fence
degrades to a visible code block.

Inline constructs (one left-to-right pass over already-escaped text, code
spans consumed atomically so link syntax inside backticks stays literal):

- backtick spans wrap in ``<code>``; a span whose exact text appears in the
  caller's ``link_map`` (wiki refs the page itself vouches for) wraps in an
  anchor to the mapped href instead;
- ``[label](target)`` renders as an anchor only for allowlisted targets
  (``http(s)://``, ``/``, ``#``) — the href is emitted exclusively from the
  allowlist match, so ``javascript:``/``data:``/relative mischief stays
  literal text, never an attribute.

Headings carry deterministic slug ids (repeats suffixed ``-1``, ``-2``, ...);
:func:`render_markdown_with_toc` returns the h2/h3 outline with anchors
that provably match the rendered ids (same slugger over the same text).
"""
from __future__ import annotations

import html
import re
from typing import List, Optional

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^```(\S*)\s*$")
_LIST_ITEM_RE = re.compile(r"^-\s+(.*)$")
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
# One combined pass: the code-span alternative first, so `` `[x](/y)` ``
# consumes the link syntax inside the span atomically.
_INLINE_TOKEN_RE = re.compile(
    "`([^`]+)`" "|" r"\[([^\]]+)\]\(([^)\s]+)\)"
)
# Schemes/roots an inline link may target — anything else renders literal.
_ALLOWED_LINK_RE = re.compile(r"^(?:https?://|/|#)")
_DELIM_ROW_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")


def _emit_fence(out: List[str], lang: str, lines: List[str]) -> None:
    body = "\n".join(lines)
    if lang == "mermaid":
        out.append(f'<pre class="mermaid">{body}</pre>')
    else:
        out.append(f"<pre><code>{body}</code></pre>")


def _inline(escaped_text: str, link_map: Optional[dict] = None) -> str:
    """Inline pass over escaped text: code spans (optionally link-mapped)
    and allowlisted inline links; everything else stays as-is."""

    def replace(match: "re.Match[str]") -> str:
        code = match.group(1)
        if code is not None:
            href = (link_map or {}).get(code)
            if href is not None:
                safe = html.escape(href, quote=True)
                return f'<a class="code-ref" href="{safe}"><code>{code}</code></a>'
            return f"<code>{code}</code>"
        label, target = match.group(2), match.group(3)
        if _ALLOWED_LINK_RE.match(target):
            # The working text was escaped with quote=False (backtick/code
            # spans must stay readable), so a ``"`` inside the target would
            # terminate the href attribute — neutralize it here. Re-escaping
            # wholesale would double-encode ``&amp;`` in legitimate URLs.
            safe = target.replace('"', "&quot;")
            return f'<a href="{safe}">{label}</a>'
        return match.group(0)  # refused target: the literal text, escaped

    return _INLINE_TOKEN_RE.sub(replace, escaped_text)


def _slugify(text: str) -> str:
    """Deterministic heading slug: lowercase, non-alphanumerics collapse to
    ``-``; an empty result (all-symbol heading) reads ``section``."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def _next_slug(counts: dict, text: str) -> str:
    """``text``'s slug, suffixed ``-1``/``-2``/... on repeat within a page."""
    base = _slugify(text)
    seen = counts.get(base, 0)
    counts[base] = seen + 1
    return base if not seen else f"{base}-{seen}"


def _split_row(escaped_row: str) -> List[str]:
    cells = _UNESCAPED_PIPE_RE.split(escaped_row)
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def _column_alignment(escaped_delim: str) -> List[Optional[str]]:
    aligns: List[Optional[str]] = []
    for cell in _split_row(escaped_delim):
        left, right = cell.startswith(":"), cell.endswith(":")
        if left and right:
            aligns.append("text-align:center")
        elif left:
            aligns.append("text-align:left")
        elif right:
            aligns.append("text-align:right")
        else:
            aligns.append(None)
    return aligns


def _emit_table_row(
    escaped_row: str,
    tag: str,
    align: List[Optional[str]],
    link_map: Optional[dict],
) -> str:
    cells = []
    for i, cell in enumerate(_split_row(escaped_row)):
        text = _inline(cell.replace("\\|", "|"), link_map)
        style = f' style="{align[i]}"' if i < len(align) and align[i] else ""
        cells.append(f"<{tag}{style}>{text}</{tag}>")
    return "<tr>" + "".join(cells) + "</tr>"


def render_markdown(text: str, link_map: Optional[dict] = None) -> str:
    """Render a markdown body to HTML: escape first, whitelist blocks."""
    return render_markdown_with_toc(text, link_map=link_map)[0]


def render_markdown_with_toc(
    text: str, link_map: Optional[dict] = None
) -> tuple:
    """``(html, toc)``: the rendered body plus its h2/h3 outline as
    ``[{level, text, anchor}]`` — each ``anchor`` is the id the matching
    heading emitted (one slugger over one body, so they cannot drift)."""
    out: List[str] = []
    toc: List[dict] = []
    slug_counts: dict = {}
    paragraph: List[str] = []
    items: List[str] = []
    fence_lang: Optional[str] = None
    fence_lines: List[str] = []
    # table_rows accumulates [header, delimiter, *body] in source order.
    table_cols: Optional[int] = None
    table_align: List[Optional[str]] = []
    table_rows: List[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            out.append(
                "<p>" + _inline(" ".join(paragraph), link_map) + "</p>"
            )
            paragraph.clear()

    def flush_list() -> None:
        if items:
            out.append(
                "<ul>"
                + "".join(f"<li>{_inline(item, link_map)}</li>" for item in items)
                + "</ul>"
            )
            items.clear()

    def flush_table() -> None:
        nonlocal table_cols, table_align, table_rows
        if table_cols is None:
            return
        header = table_rows[0]
        body = table_rows[2:]
        if all(len(_split_row(row)) == table_cols for row in body):
            out.append("<table>")
            out.append(_emit_table_row(header, "th", table_align, link_map))
            for row in body:
                out.append(_emit_table_row(row, "td", table_align, link_map))
            out.append("</table>")
        else:
            paragraph.extend(table_rows)
        table_cols = None
        table_align = []
        table_rows = []

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
            flush_table()
            flush_paragraph()
            flush_list()
            fence_lang = fence.group(1)
            continue
        if table_cols is not None:
            if line.strip() and _UNESCAPED_PIPE_RE.search(line):
                table_rows.append(line.strip())
                continue
            flush_table()
        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            # The slug/TOC text read the RAW line (pre-escape): the anchor
            # is stable regardless of escaping, and the TOC carries plain
            # text the template escapes itself.
            raw_heading = _HEADING_RE.match(raw)
            raw_title = raw_heading.group(2) if raw_heading else heading.group(2)
            anchor = _next_slug(slug_counts, raw_title)
            out.append(
                f'<h{level} id="{anchor}">'
                f"{_inline(heading.group(2), link_map)}</h{level}>"
            )
            if level in (2, 3):
                toc.append(
                    {
                        "level": level,
                        "text": _CODE_SPAN_RE.sub(r"\1", raw_title),
                        "anchor": anchor,
                    }
                )
            continue
        if paragraph and _DELIM_ROW_RE.match(line):
            header_cells = _split_row(paragraph[-1])
            align = _column_alignment(line)
            if _UNESCAPED_PIPE_RE.search(paragraph[-1]) and len(header_cells) == len(
                align
            ):
                header = paragraph.pop()
                flush_paragraph()
                flush_list()
                table_cols = len(header_cells)
                table_align = align
                table_rows = [header, line.strip()]
                continue
        item = _LIST_ITEM_RE.match(line)
        if item:
            flush_paragraph()
            items.append(_inline(item.group(1), link_map))
            continue
        if not line.strip():
            flush_paragraph()
            flush_list()
            continue
        flush_list()
        paragraph.append(line.strip())

    if fence_lang is not None:
        _emit_fence(out, fence_lang, fence_lines)
    flush_table()
    flush_paragraph()
    flush_list()
    return "\n".join(out), toc
