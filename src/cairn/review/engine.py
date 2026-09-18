"""Review pack engine: the diff blast radius plus review-context sections.

Read-only consumer of the blast engine, the memory store, and the
compass/wiki readers; ``seeds`` and ``changed_files`` from the blast result
key the pack's enrichment sections.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from textwrap import indent

from ..graph.blast import compute_blast, render_blast

TEXT_FORMAT = "text"
MARKDOWN_FORMAT = "markdown"
PACK_FORMATS = (TEXT_FORMAT, MARKDOWN_FORMAT)

_MEMORY_TYPE_DEFAULT = "decision"
_COMPASS_MISS_PREFIX = "No compass file found for "
_WIKI_MISS_MARKER = " results matching '"

# Memory types the pre-submit guard warns about.
PRE_SUBMIT_MEMORY_TYPES = ("mistake", "pattern")
# Ref the pre-submit check diffs against when the caller gives none.
PRE_SUBMIT_BASE = "main"


def build_pack(
    conn: sqlite3.Connection,
    workspace: str,
    *,
    base: str,
    fuzzy: bool = False,
    refresh: bool | None = None,
) -> dict:
    """Build the review-context pack for the diff against ``base``.

    The pack carries the blast result's radius facts verbatim plus the
    memory, compass, and wiki sections keyed to ``seeds``. An empty radius
    renders as a no-dependents statement.
    """
    blast = compute_blast(conn, workspace, base=base, fuzzy=fuzzy, refresh=refresh)
    pack = {
        "basis": blast["basis"],
        "seeds": blast["seeds"],
        "changed_files": blast["changed_files"],
        "areas": blast["areas"],
        "radius": blast["radius"],
        "unindexed_files": blast["unindexed_files"],
        "deleted_files": blast["deleted_files"],
        "truncated": blast["truncated"],
    }
    return enrich_pack(conn, workspace, pack)


def enrich_pack(conn: sqlite3.Connection, workspace: str, pack: dict) -> dict:
    """Attach the ``memories``, ``compass``, and ``wiki`` sections to ``pack``.

    Memories are matched per seed symbol, compass guides and wiki pages per
    module derived from the seeds' file paths. Readers are imported at call
    time, never at module import. Each section is
    ``{"entries": [...], "error": str | None}``; a reader failure records
    ``error`` and renders as a degraded section instead of raising.
    """
    seeds = pack.get("seeds") or []
    modules = sorted({_module_of(seed["file_path"]) for seed in seeds})
    pack["memories"] = _memory_section(conn, workspace, seeds)
    pack["compass"] = _compass_section(modules)
    pack["wiki"] = _wiki_section(modules)
    return pack


def build_pre_submit(
    conn: sqlite3.Connection,
    workspace: str,
    *,
    base: str = PRE_SUBMIT_BASE,
    fuzzy: bool = False,
    refresh: bool | None = None,
) -> dict:
    """Match mistake and pattern memories against the diff's seed symbols.

    Seeds come from the same diff-to-seed path as the pack, diffed against
    ``base``. The result carries the ``memories`` section with only
    mistake/pattern matches; entries without such a match are dropped, so
    an empty section means no warnings.
    """
    blast = compute_blast(conn, workspace, base=base, fuzzy=fuzzy, refresh=refresh)
    section = _memory_section(conn, workspace, blast["seeds"])
    if section["error"] is None:
        entries = [
            {**entry, "matches": _warning_matches(entry)}
            for entry in section["entries"]
        ]
        section["entries"] = [entry for entry in entries if entry["matches"]]
    return {"memories": section}


def render_pack(pack: dict, output_format: str) -> str:
    """Render the pack in ``text`` or ``markdown`` format.

    The radius layer mirrors ``render_blast`` semantics; the enrichment
    sections render on top of it. Packs without enrichment sections render
    the radius layer alone.
    """
    if output_format not in PACK_FORMATS:
        raise ValueError(f"unsupported review pack format: {output_format}")
    body = render_blast(pack, output_format)
    sections = _render_enrichment(pack, output_format)
    if not sections:
        return body
    return body.rstrip("\n") + "\n\n" + sections.rstrip("\n") + "\n"


def render_pre_submit(result: dict, output_format: str) -> str:
    """Render pre-submit warnings through the pack section emitters.

    Zero warnings without a reader failure render as an empty string.
    """
    if output_format not in PACK_FORMATS:
        raise ValueError(f"unsupported review pack format: {output_format}")
    memories = result.get("memories") or {"entries": [], "error": None}
    if not memories["entries"] and not memories["error"]:
        return ""
    renderer = _render_text_sections if output_format == TEXT_FORMAT else _render_markdown_sections
    return renderer(result).lstrip("\n")


def has_blocking_matches(result: dict) -> bool:
    """True when a pre-submit result carries at least one mistake/pattern match.

    A memory-reader error leaves the match set unverified, so it gates nothing.
    """
    memories = result.get("memories") or {"entries": [], "error": None}
    if memories["error"]:
        return False
    return any(_warning_matches(entry) for entry in memories["entries"])


def _module_of(file_path: str) -> str:
    return Path(file_path).stem


def _warning_matches(entry: dict) -> list[dict]:
    return [
        match
        for match in entry["matches"]
        if match["type"] in PRE_SUBMIT_MEMORY_TYPES
    ]


def _seed_query(seed: dict) -> str:
    parts = dict.fromkeys([seed["name"], _module_of(seed["file_path"])])
    return " ".join(parts)


def _memory_section(conn: sqlite3.Connection, workspace: str, seeds: list[dict]) -> dict:
    section: dict = {"entries": [], "error": None}
    queries: list[tuple[dict, str]] = []
    seen: set[str] = set()
    for seed in seeds:
        query = _seed_query(seed)
        if query not in seen:
            seen.add(query)
            queries.append((seed, query))
    if not queries:
        return section
    try:
        from ..memory.promotion import search_memory
        from ..okf.bundle import OKFBundle
        from ..paths import resolve_store

        bundle = OKFBundle(str(resolve_store(workspace).knowledge))
        for seed, query in queries:
            concepts = search_memory(conn, bundle, query) or []
            section["entries"].append(
                {
                    "seed": seed["name"],
                    "file_path": seed["file_path"],
                    "matches": [
                        {
                            "type": c.extensions.get(
                                "memory_type", _MEMORY_TYPE_DEFAULT
                            ),
                            "title": c.title or "",
                            "body": (c.body or "").strip(),
                        }
                        for c in concepts
                    ],
                }
            )
    except Exception as exc:
        section["error"] = str(exc)
    return section


def _compass_section(modules: list[str]) -> dict:
    section: dict = {"entries": [], "error": None}
    if not modules:
        return section
    try:
        from ..mcp_server.tools_compass import get_compass

        for module in modules:
            guide = get_compass(module)
            found = not guide.startswith(_COMPASS_MISS_PREFIX)
            section["entries"].append(
                {"module": module, "guide": guide if found else "", "found": found}
            )
    except Exception as exc:
        section["error"] = str(exc)
    return section


def _wiki_section(modules: list[str]) -> dict:
    section: dict = {"entries": [], "error": None}
    if not modules:
        return section
    try:
        from ..mcp_server.tools_compass import search_knowledge

        for module in modules:
            pages = search_knowledge(module, type_filter="Wiki", full_body=True)
            found = _WIKI_MISS_MARKER not in pages
            section["entries"].append(
                {"module": module, "pages": pages if found else "", "found": found}
            )
    except Exception as exc:
        section["error"] = str(exc)
    return section


def _render_enrichment(pack: dict, output_format: str) -> str:
    renderer = _render_text_sections if output_format == TEXT_FORMAT else _render_markdown_sections
    return renderer(pack)


def _render_text_sections(pack: dict) -> str:
    lines: list[str] = []

    memories = pack.get("memories")
    if memories is not None:
        lines += ["", "Memories:"]
        if memories["error"]:
            lines.append(f"  (memory enrichment unavailable: {memories['error']})")
        elif not memories["entries"]:
            lines.append("  No changed symbols to key memories on.")
        for entry in memories["entries"]:
            header = f"{entry['seed']} ({entry['file_path']})"
            if not entry["matches"]:
                lines.append(f"  {header}: no matching memories.")
                continue
            lines.append(f"  {header}:")
            for match in entry["matches"]:
                lines.append(f"    - [{match['type']}] {match['title']}".rstrip())
                if match["body"]:
                    lines.append(indent(match["body"], "      "))

    compass = pack.get("compass")
    if compass is not None:
        lines += ["", "Module guides:"]
        if compass["error"]:
            lines.append(f"  (compass enrichment unavailable: {compass['error']})")
        elif not compass["entries"]:
            lines.append("  No changed modules to look up.")
        for entry in compass["entries"]:
            if not entry["found"]:
                lines.append(f"  {entry['module']}: no compass guide.")
                continue
            lines.append(f"  {entry['module']}:")
            lines.append(indent(entry["guide"], "    "))

    wiki = pack.get("wiki")
    if wiki is not None:
        lines += ["", "Wiki pages:"]
        if wiki["error"]:
            lines.append(f"  (wiki enrichment unavailable: {wiki['error']})")
        elif not wiki["entries"]:
            lines.append("  No changed modules to look up.")
        for entry in wiki["entries"]:
            if not entry["found"]:
                lines.append(f"  {entry['module']}: no wiki pages.")
                continue
            lines.append(f"  {entry['module']}:")
            lines.append(indent(entry["pages"], "    "))

    return "\n".join(lines)


def _render_markdown_sections(pack: dict) -> str:
    lines: list[str] = []

    memories = pack.get("memories")
    if memories is not None:
        lines += ["", "## Memories", ""]
        if memories["error"]:
            lines.append(f"_Memory enrichment unavailable: {memories['error']}_")
        elif not memories["entries"]:
            lines.append("_No changed symbols to key memories on._")
        for entry in memories["entries"]:
            lines.append(f"### `{entry['seed']}` (`{entry['file_path']}`)")
            if not entry["matches"]:
                lines.append("- no matching memories")
            for match in entry["matches"]:
                lines.append(f"- **[{match['type']}]** {match['title']}".rstrip())
                if match["body"]:
                    lines.append(indent(match["body"], "  "))
            lines.append("")

    compass = pack.get("compass")
    if compass is not None:
        lines += ["", "## Module guides", ""]
        if compass["error"]:
            lines.append(f"_Compass enrichment unavailable: {compass['error']}_")
        elif not compass["entries"]:
            lines.append("_No changed modules to look up._")
        for entry in compass["entries"]:
            if not entry["found"]:
                lines.append(f"- {entry['module']}: no compass guide")
                continue
            lines += [f"### {entry['module']}", "", entry["guide"], ""]

    wiki = pack.get("wiki")
    if wiki is not None:
        lines += ["", "## Wiki", ""]
        if wiki["error"]:
            lines.append(f"_Wiki enrichment unavailable: {wiki['error']}_")
        elif not wiki["entries"]:
            lines.append("_No changed modules to look up._")
        for entry in wiki["entries"]:
            if not entry["found"]:
                lines.append(f"- {entry['module']}: no wiki pages")
                continue
            lines += [f"### {entry['module']}", "", entry["pages"], ""]

    return "\n".join(lines)
