"""Escape-first markdown renderer for the dashboard wiki view (D-002).

Pure stdlib (``html`` + ``re``): importing this module must never load the
server stack — the same guard the dashboard package is held to. Every line
is HTML-escaped before any construct is rendered, so inline HTML never
passes through; the whitelisted block constructs are headings, paragraphs,
unordered lists, fenced code, and GFM pipe tables, and backtick spans wrap
in ``<code>`` after escaping. Mermaid fences emit ``<pre class="mermaid">``
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
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
_DELIM_ROW_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")


def _emit_fence(out: List[str], lang: str, lines: List[str]) -> None:
    body = "\n".join(lines)
    if lang == "mermaid":
        out.append(f'<pre class="mermaid">{body}</pre>')
    else:
        out.append(f"<pre><code>{body}</code></pre>")


def _inline_code(escaped_text: str) -> str:
    """Wrap backtick spans in <code>; the text stays escaped, never re-escaped."""
    return _CODE_SPAN_RE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped_text)


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


def _emit_table_row(escaped_row: str, tag: str, align: List[Optional[str]]) -> str:
    cells = []
    for i, cell in enumerate(_split_row(escaped_row)):
        text = _inline_code(cell.replace("\\|", "|"))
        style = f' style="{align[i]}"' if i < len(align) and align[i] else ""
        cells.append(f"<{tag}{style}>{text}</{tag}>")
    return "<tr>" + "".join(cells) + "</tr>"


def render_markdown(text: str) -> str:
    """Render a markdown body to HTML: escape first, whitelist blocks."""
    out: List[str] = []
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
            out.append("<p>" + _inline_code(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    def flush_list() -> None:
        if items:
            out.append(
                "<ul>"
                + "".join(f"<li>{_inline_code(item)}</li>" for item in items)
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
            out.append(_emit_table_row(header, "th", table_align))
            for row in body:
                out.append(_emit_table_row(row, "td", table_align))
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
            out.append(f"<h{level}>{_inline_code(heading.group(2))}</h{level}>")
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
            items.append(_inline_code(item.group(1)))
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
    return "\n".join(out)
