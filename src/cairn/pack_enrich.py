"""Enrichment renders for the context pack.

Plain-string contract: callers wrap each render with its own kind label and
token cost. Reads graph, OKF-bundle, and memory surfaces only — imports no
pack module and never ``cairn.mcp_server``.
"""
from __future__ import annotations

import sqlite3

from .graph.scanner import resolve_file_path
from .graph.traversal import find_definition_by_id, impact_analysis
from .memory.promotion import search_memory
from .okf.bundle import OKFBundle
from .paths import resolve_workspace


def render_source(
    conn: sqlite3.Connection, symbol_id: str, line_cap: int = 40
) -> str:
    """Verbatim source for one symbol, trimmed to signature plus key body.

    Slices the symbol's stored ``line_start``/``line_end`` span and keeps at
    most ``line_cap`` lines (clamped to a minimum of 1); a trimmed render
    appends a ``... (+N more lines trimmed)`` marker so a cut is never
    mistaken for the full body. Returns "" when the symbol row, its span, or
    its file is unreadable — callers skip empty renders.
    """
    rows = find_definition_by_id(conn, symbol_id)
    if not rows:
        return ""
    row = rows[0]
    ls = row["line_start"] or 0
    le = row["line_end"] or ls
    if ls < 1 or le < ls:
        return ""
    workspace = str(resolve_workspace())
    abs_path = resolve_file_path(workspace, row["repo"], row["file_path"])
    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
    except OSError:
        return ""
    span = [line.rstrip("\n") for line in all_lines[ls - 1 : le]]
    cap = max(line_cap, 1)
    if len(span) <= cap:
        return "\n".join(span)
    trimmed = span[:cap]
    trimmed.append(f"... (+{len(span) - cap} more lines trimmed)")
    return "\n".join(trimmed)


def blast_radius_line(
    conn: sqlite3.Connection,
    qualified_name: str,
    symbol_id: str,
    limit: int = 100,
) -> str:
    """One-line depth-2 precise blast-radius summary for one symbol row.

    ``symbol_id`` pins the entry symbol — a bare name would resolve
    ambiguously for common names. ``limit`` caps the underlying traversal so
    one hub symbol cannot dominate. Format: ``total=<n>``; per-depth (0/1/2)
    counts; ``top:`` up to 5 distinct caller names; ``truncated`` appended
    only when the traversal hit ``limit``.
    """
    blast = impact_analysis(
        conn, qualified_name, max_depth=2, seed_id=symbol_id, limit=limit
    )
    by_depth = {0: 0, 1: 0, 2: 0}
    top: list[str] = []
    seen: set[str] = set()
    for entry in blast["impacted"]:
        depth = entry["depth"]
        if depth in by_depth:
            by_depth[depth] += 1
        name = entry["symbol"]
        if name and name not in seen and len(top) < 5:
            seen.add(name)
            top.append(name)
    line = (
        f"blast radius (depth<=2, precise): total={blast['total']}; "
        f"depth 0: {by_depth[0]}, depth 1: {by_depth[1]}, depth 2: {by_depth[2]}"
    )
    if top:
        line += "; top: " + ", ".join(top)
    if blast["truncated"]:
        line += "; truncated"
    return line


def compass_excerpt(bundle: OKFBundle, file_path: str) -> str | None:
    """Compass navigation guide covering ``file_path``, or None.

    Module-in-resource match: a compass concept matches when its resource is
    contained in the file path or the path is contained in the resource;
    first match in the bundle's sorted concept order wins. None means no
    coverage — callers omit the section instead of rendering it empty.
    """
    for cid in bundle.list_concepts(prefix="compass/"):
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        resource = concept.resource or ""
        if resource and (resource in file_path or file_path in resource):
            return f"# {concept.title or cid}\n\n{concept.body}"
    return None


def memory_picks(
    conn: sqlite3.Connection, bundle: OKFBundle, task: str, k: int = 3
) -> list[str]:
    """Top-k task-relevant memories as title-plus-description strings.

    Ranked by ``search_memory`` (lexical scan fused with the semantic scan
    when memory embeddings exist). Returns [] when nothing matches — callers
    omit the section instead of rendering it empty.
    """
    picks: list[str] = []
    for concept in search_memory(conn, bundle, task)[: max(k, 0)]:
        pick = concept.title or concept.concept_id
        if concept.description:
            pick += f"\n{concept.description}"
        picks.append(pick)
    return picks
