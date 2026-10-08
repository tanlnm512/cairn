"""L1 graph MCP tools: find_definition, get_callers, get_callees, impact_analysis, path, explore, semantic_search, search_symbols, repo_map, file_api, cross_repo_deps, plus visualize_graph (a graph renderer, filed under L4 but structurally belongs with the graph-query tools)."""
from __future__ import annotations

import logging
import os
import re
import sqlite3

from mcp.types import ToolAnnotations

from ._server_core import (
    _append_embed_degradation_footnote,
    _bundle,
    _conn,
    _fresh_graph,
    _read_only_mode,
    _repo_of,
    _session_id,
    _staleness_banner,
    mcp,
)
from .metric_buffering import instrument
from .structured import (
    GetCallersResult,
    GetCalleesResult,
    ImpactAnalysisResult,
    SearchSymbolsResult,
    SemanticSearchResult,
)

logger = logging.getLogger(__name__)


def _clamp(value, lo, hi):
    """Clamp an int to [lo, hi], used to bound LLM-supplied depth/limit values at the tool boundary."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = lo
    return max(lo, min(v, hi))


def _with_freshness(text: str, freshness) -> str:
    banner = freshness.banner()
    return f"{banner}\n{text}" if banner else text


def _prepend_banner(text: str, banner: str) -> str:
    return f"{banner}\n{text}" if banner else text


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def find_definition(name: str) -> str:
    """Find where a symbol is defined (file:line, kind, qualified name) with exact→qualified→substring fallback."""
    from cairn.graph import queries

    conn = _conn()
    try:
        freshness = _fresh_graph(conn)
        rows = queries.find_definition(conn, name)
    finally:
        conn.close()
    if not rows:
        return _with_freshness(
            f"No definition found for '{name}'. The name may be misspelled, "
            f"ambiguous, or the symbol lives outside the indexed workspace. "
            f"Try search_symbols(\"{name}\") to find near matches.",
            freshness,
        )
    out = []
    for r in rows:
        out.append(
            f"{r['file_path']}:{r['line_start']}  {r['kind']} "
            f"{r['qualified_name'] or r['name']}  ({r['repo']})"
        )
    return _with_freshness("\n".join(out), freshness)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True), structured_output=True)
@instrument
def get_callers(name: str, fuzzy: bool = False, limit: int = 200, structured: bool = False) -> str | GetCallersResult:
    """Who calls this symbol? Precise by default, auto-retrying fuzzy when precise is empty; structured=True returns typed rows."""
    data = get_callers_data(name, fuzzy=fuzzy, limit=limit)
    if structured:
        return GetCallersResult.model_validate(data)
    return _render_callers(data)


def _neighbor_rows(
    name: str, fuzzy: bool, limit: int, query
) -> tuple[list, bool, str, bool]:
    """Shared precise-then-fuzzy edge query -> (rows, used_fallback, stale_banner, hit_limit)."""
    limit = _clamp(limit, 1, 1000)  # bound LLM-supplied value at the boundary
    conn = _conn()
    try:
        freshness = _fresh_graph(conn)
        rows = query(conn, name, fuzzy, limit)
        used_fallback = False
        if not rows and not fuzzy:
            rows = query(conn, name, True, limit)
            used_fallback = True
        banner = freshness.banner() or (
            _staleness_banner(conn, [r["file_path"] for r in rows])
            if rows
            else ""
        )
    finally:
        conn.close()
    # hit_limit only makes sense on the precise (non-fallback) path: when we
    # fell back to fuzzy it's because the precise rows don't exist, so a
    # higher limit wouldn't surface more precise results.
    hit_limit = (not used_fallback) and len(rows) >= limit
    return rows, used_fallback, banner, hit_limit


def get_callers_data(name: str, fuzzy: bool = False, limit: int = 200) -> dict:
    """Structured core of ``get_callers``: dict result, no prose."""
    from cairn.graph import queries

    rows, used_fallback, banner, hit_limit = _neighbor_rows(
        name,
        fuzzy,
        limit,
        lambda conn, n, fuzzy, limit: queries.get_callers(conn, n, fuzzy=fuzzy, limit=limit),
    )
    return {
        "symbol": name,
        "count": len(rows),
        "used_fallback": used_fallback,
        "hit_limit": hit_limit,
        "stale_banner": banner,
        "callers": [
            {
                "kind": r["caller_kind"],
                "name": r["caller_name"],
                "file_path": r["file_path"],
                "line": r["edge_line"],
                "repo": r["repo"],
            }
            for r in rows
        ],
    }


def _render_callers(data: dict) -> str:
    """Render the structured ``get_callers_data`` result as the prose return."""
    if data["count"] == 0:
        return f"No callers found for '{data['symbol']}' (checked precise and fuzzy)."
    if data["used_fallback"]:
        out = [
            f"0 precise callers for '{data['symbol']}'; {data['count']} fuzzy "
            "candidates (name-match only -- verify each against actual code "
            "before treating it as a real caller):"
        ]
    else:
        out = [f"{data['count']} callers of '{data['symbol']}':"]
    for c in data["callers"]:
        out.append(
            f"  {c['kind']} {c['name']}  {c['file_path']}:{c['line']}  ({c['repo']})"
        )
    if data["hit_limit"]:
        out.append("  ... hit the limit cap; pass a higher limit for more.")
    return _prepend_banner("\n".join(out), data.get("stale_banner", ""))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True), structured_output=True)
@instrument
def get_callees(name: str, fuzzy: bool = False, limit: int = 200, structured: bool = False) -> str | GetCalleesResult:
    """What does this symbol call? Precise omits unresolved library calls; fuzzy includes them; auto-retries fuzzy when precise is empty."""
    data = get_callees_data(name, fuzzy=fuzzy, limit=limit)
    if structured:
        return GetCalleesResult.model_validate(data)
    return _render_callees(data)


def get_callees_data(name: str, fuzzy: bool = False, limit: int = 200) -> dict:
    """Structured core of ``get_callees``."""
    from cairn.graph import queries

    rows, used_fallback, banner, hit_limit = _neighbor_rows(
        name,
        fuzzy,
        limit,
        lambda conn, n, fuzzy, limit: queries.get_callees(conn, n, fuzzy=fuzzy, limit=limit),
    )
    return {
        "symbol": name,
        "count": len(rows),
        "used_fallback": used_fallback,
        "hit_limit": hit_limit,
        "stale_banner": banner,
        "callees": [
            {
                "name": r["callee_name"],
                "resolved": bool(r["resolved"]),
                "file_path": r["file_path"],
                "line": r["edge_line"],
            }
            for r in rows
        ],
    }


def _render_callees(data: dict) -> str:
    """Render the structured ``get_callees_data`` result as the prose return."""
    if data["count"] == 0:
        return _prepend_banner(
            f"No callees found for '{data['symbol']}' "
            "(checked precise and fuzzy).",
            data.get("stale_banner", ""),
        )
    if data["used_fallback"]:
        out = [
            f"0 precise callees for '{data['symbol']}'; {data['count']} fuzzy "
            "candidates (includes unresolved/external calls -- verify each "
            "before treating it as a real callee):"
        ]
    else:
        out = [f"{data['count']} callees of '{data['symbol']}':"]
    for c in data["callees"]:
        tag = "" if c["resolved"] else " (unresolved)"
        out.append(f"  {c['name']}{tag}  {c['file_path']}:{c['line']}")
    if data["hit_limit"]:
        out.append("  ... hit the limit cap; pass a higher limit for more.")
    return _prepend_banner("\n".join(out), data.get("stale_banner", ""))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True), structured_output=True)
@instrument
def impact_analysis(
    name: str,
    depth: int = 5,
    fuzzy: bool = False,
    cached: bool = False,
    limit: int = 500,
    structured: bool = False,
) -> str | ImpactAnalysisResult:
    """Recursive caller impact up to depth (precise by default; fuzzy adds unresolved edges; cached reads precomputed dataflow)."""
    from cairn.graph import queries

    depth = _clamp(depth, 1, 10)     # bound LLM-supplied value at the boundary
    limit = _clamp(limit, 1, 1000)   # bound LLM-supplied value at the boundary
    conn = _conn()
    try:
        freshness = _fresh_graph(conn)
        # Cached path: read precomputed dataflow table (O(1)).
        if cached:
            from cairn.graph.dataflow import get_dataflow as _get_dataflow
            df = _get_dataflow(conn, name)
            if df is not None:
                out = [f"Impact of '{name}' (cached, repo: {df['repo']}):"]
                if df["within_repo"]:
                    out.append(f"  Within-repo impact ({len(df['within_repo'])} symbols): {', '.join(df['within_repo'][:20])}")
                    if len(df["within_repo"]) > 20:
                        out.append(f"    ... and {len(df['within_repo']) - 20} more")
                else:
                    out.append("  Within-repo impact: (none)")
                if df["cross_repo"]:
                    out.append(f"  Cross-repo consumers: {', '.join(df['cross_repo'])}")
                else:
                    out.append("  Cross-repo consumers: (none)")
                out.append("(from precomputed cache — run `cairn dataflow build` to refresh)")
                return _with_freshness("\n".join(out), freshness)
            # No cache entry — fall through to live analysis below.

        # Live path: recursive caller traversal.
        result = queries.impact_analysis(conn, name, max_depth=depth, fuzzy=fuzzy, limit=limit)
        xref = queries.cross_repo_deps(conn, _repo_of(conn, name) or "")
    finally:
        conn.close()

    data = impact_analysis_data(result, xref, name=name, fuzzy=fuzzy, limit=limit)
    data["stale_banner"] = freshness.banner()
    if structured:
        return ImpactAnalysisResult.model_validate(data)
    return _render_impact_analysis(data, limit=limit)


def impact_analysis_data(result: dict, xref: dict, *, name: str, fuzzy: bool, limit: int) -> dict:
    """Structured core of ``impact_analysis`` live path."""
    from cairn.graph.tests import filter_tests as _filter_tests

    by_depth: dict[int, list] = {}
    for r in result["impacted"]:
        by_depth.setdefault(r["depth"], []).append(r)
    affected_tests = _filter_tests(result["impacted"])
    dependents = xref.get("dependents", [])
    return {
        "symbol": name,
        "total": result["total"],
        "truncated": bool(result.get("truncated")),
        "fuzzy": fuzzy,
        "by_depth": {str(d): len(by_depth[d]) for d in sorted(by_depth)},
        "cycles": [c["symbol"] for c in result.get("cycles", [])],
        "affected_tests": [
            {
                "symbol": t["symbol"],
                "file": t["file"],
                "repo": t["repo"],
                "detection_method": t.get("detection_method", ""),
            }
            for t in affected_tests
        ],
        "cross_repo_dependents": [
            {"repo": d["repo"], "count": d["count"]} for d in dependents
        ],
    }


def _render_impact_analysis(data: dict, *, limit: int) -> str:
    """Render the structured impact-analysis result as the prose return."""
    name = data["symbol"]
    out = [f"Impact of '{name}': {data['total']} total impacted symbols."]
    if data["truncated"]:
        out.append(f"  (traversal stopped early at limit={limit}; pass a higher limit for the full count.)")
    if data["cycles"]:
        out.append(f"Cycles: {data['cycles']}")
    for d_str, count in data["by_depth"].items():
        out.append(f"  Depth {d_str}: {count} callers")
    affected_tests = data["affected_tests"]
    if affected_tests:
        out.append(f"Affected tests ({len(affected_tests)} — run these to verify the change):")
        for t in affected_tests[:15]:
            out.append(
                f"  {t['symbol']}  {t['file']}  ({t['repo']}, {t['detection_method']})"
            )
        if len(affected_tests) > 15:
            out.append(f"  ... and {len(affected_tests) - 15} more")
    if not data["total"] and not data["fuzzy"]:
        out.append("(0 precise within-repo callers — retry fuzzy=True before concluding unused.)")
    dependents = data["cross_repo_dependents"]
    if dependents:
        consumer_list = ", ".join(f"{d['repo']} (x{d['count']})" for d in dependents[:5])
        out.append(f"Cross-repo: {len(dependents)} repo(s) depend — {consumer_list}")
    return _prepend_banner("\n".join(out), data.get("stale_banner", ""))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def path(
    from_pattern: str, to_pattern: str, fuzzy: bool = False, max_depth: int = 4, limit: int = 50
) -> str:
    """Trace the shortest structural path between two symbol patterns; fuzzy follows ambiguous hops."""
    from cairn.graph.taint import find_symbol_paths, resolve_pattern_symbols

    max_depth = _clamp(max_depth, 1, 10)  # bound LLM-supplied value at the boundary
    limit = _clamp(limit, 1, 1000)        # bound LLM-supplied value at the boundary
    conn = _conn()
    try:
        freshness = _fresh_graph(conn)
        from_ids = {row["id"] for row in resolve_pattern_symbols(conn, from_pattern)}
        to_ids = {row["id"] for row in resolve_pattern_symbols(conn, to_pattern)}
        paths = find_symbol_paths(conn, from_ids, to_ids, fuzzy=fuzzy, max_depth=max_depth)
    finally:
        conn.close()

    if not from_ids or not to_ids:
        missing = [
            f"no symbols match {side} pattern '{pattern}'"
            for side, pattern, ids in (
                ("from", from_pattern, from_ids),
                ("to", to_pattern, to_ids),
            )
            if not ids
        ]
        return _with_freshness("\n".join(missing), freshness)
    if not paths:
        return _with_freshness(
            f"no path within {max_depth} hops from '{from_pattern}' to '{to_pattern}'",
            freshness,
        )
    out = []
    for number, symbol_path in enumerate(paths[:limit], start=1):
        out.append(
            f"path {number} ({from_pattern} -> {to_pattern}, "
            f"{len(symbol_path.hops)} hops):"
        )
        for hop in symbol_path.hops:
            line = hop.line if hop.line is not None else "?"
            out.append(f"  {hop.file}:{line} {hop.symbol} [{hop.resolution}]")
    return _with_freshness("\n".join(out), freshness)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def explore(query: str) -> str:
    """Answer 'how does X work' in one call: matching source, call paths, blast radius, memory, rationale, and taint warnings."""
    from cairn.graph import queries
    from cairn.graph.config import load_config
    from cairn.graph.taint import (
        build_registry,
        format_taint_warning,
        intersect_seeds,
    )
    from cairn.paths import resolve_workspace

    conn = _conn()
    tribal: list = []
    rationale: list = []
    taint_paths: list = []
    try:
        freshness = _fresh_graph(conn)
        result = queries.explore(conn, query)
        if result["seeds"]:
            seed_names = [s["name"] for s in result["seeds"] if s.get("name")]

            from cairn.graph.rationale import records_for_symbol_ids
            rationale = records_for_symbol_ids(
                conn, [s["id"] for s in result["seeds"] if s.get("id")]
            )

            config = load_config(resolve_workspace())
            registry = build_registry(config.taint_sources, config.taint_sinks)
            taint_paths = intersect_seeds(conn, registry, set(seed_names))

            from cairn.graph import note_contention
            from cairn.memory.promotion import record_references_batch, search_memory

            mems = search_memory(
                conn, _bundle(), " ".join(seed_names[:5]),
                tier="tribal", session_id=None,
            )
            tribal = mems[:3]
            if not _read_only_mode():
                # Refs are recorded here (not via search_memory's session_id)
                # so only the memories actually rendered above are credited.
                try:
                    record_references_batch(
                        conn, [(c.concept_id, query) for c in tribal], _session_id()
                    )
                except sqlite3.OperationalError:
                    # Ref counting is analytics -- a lock collision degrades to
                    # an uncredited surface, never a failed tool call.
                    note_contention("tools_graph.explore memory refs")
    finally:
        conn.close()

    seeds = result["seeds"]
    files = result["files"]
    callers = result["call_paths"]["callers"]
    callees = result["call_paths"]["callees"]
    blast = result["blast_radius"]
    hops = result["dispatch_hops"]

    if not seeds:
        return _with_freshness(
            _append_embed_degradation_footnote(
                f"No symbols matching '{query}'. "
                "Try a broader query or use search_symbols."
            ),
            freshness,
        )

    out = [f'=== explore: "{query}" ===']
    out.append(f"{len(seeds)} symbol(s) matched.")
    out.append("")

    # --- Source section ---
    total_lines = sum(
        e["line_end"] - e["line_start"] + 1
        for entries in files.values()
        for e in entries
    )
    out.append(f"=== Source ({len(files)} file(s), {total_lines} line(s)) ===")
    if files:
        for file_path, entries in files.items():
            out.append(f"{file_path}")
            for e in entries:
                out.append(
                    f"  [{e['kind']} {e['symbol']}  lines {e['line_start']}-{e['line_end']}]"
                )
                width = len(str(e["line_end"]))
                for i, line in enumerate(e["lines"]):
                    lineno = e["line_start"] + i
                    out.append(f"  {lineno:>{width}}  {line}")
            out.append("")
    else:
        out.append("  (source unavailable — files moved since index)")
        out.append("")

    # --- Call paths section ---
    out.append("=== Call paths ===")
    if callers or callees:
        for c in callers[:20]:
            res = f"  [{c['resolution'] or 'unknown'}]" if c.get("resolution") else ""
            short = c["file_path"].rsplit("/", 1)[-1]
            out.append(
                f"  {c['to']}  <- called by  {c['from']}  "
                f"({short}:{c['line']}){res}"
            )
        for c in callees[:20]:
            res = f"  [{c['resolution'] or 'unknown'}]" if c.get("resolution") else ""
            short = c["file_path"].rsplit("/", 1)[-1]
            out.append(
                f"  {c['from']}  -> calls  {c['to']}  "
                f"({short}:{c['line']}){res}"
            )
        if len(callers) > 20:
            out.append(f"  ... and {len(callers) - 20} more caller edges")
        if len(callees) > 20:
            out.append(f"  ... and {len(callees) - 20} more callee edges")
    else:
        out.append("  (no resolved call edges among the matched symbols)")
    out.append("")

    # --- Blast radius section ---
    out.append("=== Blast radius (depth 2) ===")
    if blast:
        for name, info in list(blast.items())[:5]:
            repos_str = (
                f", {len(info['repos'])} repo(s)" if info.get("repos") else ""
            )
            out.append(f"  {name}: {info['total']} caller(s){repos_str}")
            if info.get("top_callers"):
                top_str = ", ".join(info["top_callers"][:5])
                out.append(f"    top: {top_str}")
    else:
        out.append("  (no callers within depth 2)")
    out.append("")

    # --- Ambiguous dispatch section (the differentiator) ---
    out.append("=== Ambiguous dispatch ===")
    if hops:
        for h in hops:
            cands = ", ".join(h["candidates"])
            tgt = h["dispatches_to"]
            out.append(f'  "{tgt}" could dispatch to: {cands}')
    else:
        out.append("  (none — all call edges were precisely resolved)")

    # --- Tribal memory section ---
    out.append(f"=== Tribal memory ({len(tribal)}) ===")
    if tribal:
        for c in tribal:
            title = c.title or c.concept_id
            stance = c.extensions.get("memory_stance") or ""
            if stance:
                title += f", stance={stance}"
                peer = c.extensions.get("memory_stance_peer") or ""
                if stance == "contested" and peer:
                    title += f" (peer: {peer})"
            out.append(f"  {title}")
            m = re.search(r"^How to apply:\s*(.+)$", c.body, re.M)
            apply_line = m.group(1).strip() if m else (c.description or "").strip()
            if apply_line:
                out.append(f"    How to apply: {apply_line}")
    else:
        out.append("  (none)")

    # --- Rationale section ---
    if rationale:
        seed_files = {s["id"]: s["file_path"] or "" for s in seeds if s.get("id")}
        out.append(f"=== Rationale ({len(rationale)}) ===")
        for r in rationale:
            short = seed_files.get(r["symbol_id"], "").rsplit("/", 1)[-1]
            out.append(f"  {short}:{r['line']} [{r['kind']}] {r['text']}")

    # --- Taint paths section ---
    if taint_paths:
        out.append("=== Taint paths ===")
        out.append(format_taint_warning(taint_paths))
        out.append("")
    return _with_freshness(
        _append_embed_degradation_footnote("\n".join(out)), freshness
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True), structured_output=True)
@instrument
def semantic_search(query: str, limit: int = 20, include_callers: bool = False, structured: bool = False, rerank: bool | None = None) -> str | SemanticSearchResult:
    """Search symbols by meaning (BM25+vector fusion by default, CAIRN_FUSION=0 for cosine; optional rerank and include_callers subgraphs)."""
    from cairn.graph import embeddings as emb

    limit = _clamp(limit, 1, 1000)  # bound LLM-supplied value at the boundary
    conn = _conn()
    try:
        freshness = _fresh_graph(conn)
        if not emb.embeddings_available():
            return _with_freshness(emb.install_hint(), freshness)

        emb.warn_hash_fallback_once(logger, context="semantic_search")

        # Do NOT lazily embed during a search query -- embed_all() writes contend
        # with the daemon's WAL lock and fails with "database is locked". Embedding
        # is a build-time operation (`cairn embed`).
        if emb.embed_count(conn) == 0:
            return _with_freshness(
                "Semantic index is empty. Run `cairn embed` once to index the "
                "corpus (build-time, ~1-2 min for 50k symbols), then retry "
                "this query. Embedding is not done lazily during search to "
                "avoid write-lock contention with the running server.",
                freshness,
            )
        from cairn.graph import queries
        rows = queries.semantic_search(conn, query, limit=limit, include_callers=include_callers, rerank=rerank)
    finally:
        conn.close()

    data = {
        "query": query,
        "count": len(rows),
        "stale_banner": freshness.banner(),
        "matches": [
            {
                "kind": r["kind"],
                "name": r["name"],
                "qualified_name": r.get("qualified_name"),
                "file_path": r.get("file_path") or "",
                "repo": r["repo"],
                "score": r["score"],
                "provenance": r.get("provenance", "semantic"),
                "reranked": bool(r.get("reranked")),
                "rerank_score": r.get("rerank_score"),
                "chunk": r.get("chunk") or "",
                "callers": r.get("callers") or [],
                "callees": r.get("callees") or [],
            }
            for r in rows
        ],
    }
    if structured:
        return SemanticSearchResult.model_validate(data)
    return _append_embed_degradation_footnote(
        _prepend_banner(
            _render_semantic_search(data, include_callers=include_callers),
            data["stale_banner"],
        )
    )


def _render_semantic_search(data: dict, include_callers: bool = False) -> str:
    """Render the structured semantic-search result as the prose return."""
    query = data["query"]
    rows = data["matches"]
    if not rows:
        fusion_on = os.environ.get("CAIRN_FUSION", "1") != "0"
        return _prepend_banner(
            f"No matches for '{query}' -- neither the vector scan nor the "
            f"BM25 fallback found anything{'' if fusion_on else ' above the cosine threshold'}. "
            "The corpus may not be embedded yet (run `cairn embed` to index), or the "
            "wording may not match any token. Try search_symbols(\"...\") for a "
            "lexical match, or rephrase with more specific terms.",
            data.get("stale_banner", ""),
        )
    out = [f"=== semantic_search: \"{query}\" ({len(rows)} match(es)) ==="]
    for r in rows:
        short = (r["file_path"] or "").rsplit("/", 1)[-1]
        if r.get("reranked"):
            label = f"rerank {r['rerank_score']:.2f}"
        else:
            # Under the default RRF fusion, "score" is a rank-fusion number,
            # not a cosine similarity -- label by actual provenance so a
            # bm25-only or fused hit isn't mistaken for a pure semantic one.
            provenance = r.get("provenance", "semantic")
            label = f"{provenance} {r['score']:.2f}"
        out.append(
            f"  [{label}] {r['kind']} "
            f"{r['qualified_name'] or r['name']}  ({short})  [{r['repo']}]"
        )
        if r.get("chunk"):
            # Show the embedded chunk (first line) for context.
            first_line = r["chunk"].split("\n", 1)[0]
            if first_line:
                out.append(f"    {first_line}")
        if include_callers:
            callers = r.get("callers") or []
            callees = r.get("callees") or []
            if callers:
                names = ", ".join(c["name"] for c in callers)
                out.append(f"    called by: {names}")
            if callees:
                names = ", ".join(c["name"] for c in callees)
                out.append(f"    calls: {names}")
    out.append("")
    out.append(
        "Note: these are similarity-scored fuzzy matches, not structural edges. "
        "Use get_callers/impact_analysis to verify precise relationships."
    )
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True), structured_output=True)
@instrument
def search_symbols(pattern: str, kind: str = "", structured: bool = False) -> str | SearchSymbolsResult:
    """Lexical symbol search (FTS5/BM25, wildcards, optional kind filter) — the default discovery entry point."""
    data = search_symbols_data(pattern, kind=kind)
    if structured:
        return SearchSymbolsResult.model_validate(data)
    return _render_search_symbols(data)


def search_symbols_data(pattern: str, kind: str = "") -> dict:
    """Structured core of ``search_symbols``."""
    from cairn.graph import queries

    conn = _conn()
    try:
        freshness = _fresh_graph(conn)
        rows = queries.search_symbols(conn, pattern, kind=kind or None)
    finally:
        conn.close()

    if not rows:
        # Emit empty_result at the tool boundary only: the search primitive
        # is shared by explore/semantic and would double-count.
        try:
            from cairn.telemetry import EMPTY_RESULT, emit as _emit

            _emit(EMPTY_RESULT, query_kind="search_symbols")
        except Exception:
            logger.debug("search_symbols empty_result emit failed", exc_info=True)

    SHOWN = 50
    returned = rows[:SHOWN]
    # total_count is the match count returned by the search primitive, capped
    # at the primitive's own limit — not the unbounded DB match count; it
    # drives the "and N more" message.
    return {
        "pattern": pattern,
        "count": len(returned),
        "total_count": len(rows),
        "truncated": len(rows) > SHOWN,
        "stale_banner": freshness.banner(),
        "symbols": [
            {
                "kind": r["kind"],
                "name": r["name"],
                "file_path": r["file_path"],
                "line": r["line_start"],
                "repo": r["repo"],
            }
            for r in returned
        ],
    }


def _render_search_symbols(data: dict) -> str:
    """Render the structured ``search_symbols_data`` result as the prose return."""
    # total_count is the primitive's capped match count; count is how many were shipped.
    total_count = data.get("total_count", data["count"])
    if total_count == 0:
        return _prepend_banner(
            f"No symbols matching '{data['pattern']}'. The token may not be indexed or "
            f"may use different casing/wording. Try a broader pattern (fewer "
            f"characters, a leading wildcard), or semantic_search(\"{data['pattern']}\") "
            f"to match by meaning.",
            data.get("stale_banner", ""),
        )
    out = [f"{total_count} symbols matching '{data['pattern']}':"]
    for s in data["symbols"]:
        out.append(
            f"  {s['kind']} {s['name']}  {s['file_path']}:{s['line']}  ({s['repo']})"
        )
    if data["truncated"]:
        out.append(f"  ... and {total_count - len(data['symbols'])} more")
    return _prepend_banner("\n".join(out), data.get("stale_banner", ""))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True), structured_output=True)
@instrument
def repo_map(structured: bool = False) -> str | dict:
    """Return a deterministic repository orientation map (clusters, hubs, hotspots, dropped counts)."""
    from cairn.graph.repo_map import build_repo_map

    conn = _conn()
    try:
        freshness = _fresh_graph(conn)
        result = build_repo_map(conn)
    finally:
        conn.close()
    if structured:
        return result
    return _with_freshness(_render_repo_map(result), freshness)


def _render_repo_map(result: dict) -> str:
    """Render the canonical repository map as bounded prose."""
    lines = []
    for scope in result["repos"]:
        lines.append(f"Repository {scope['repo']}")
        for cluster in scope["clusters"]:
            lines.append(
                f"  {cluster['path']}: {cluster['files']} files, "
                f"{cluster['symbols']} symbols, {cluster['edges']} edges"
            )
            for hub in cluster["hubs"]:
                lines.append(
                    f"    {hub['qualified_name']} "
                    f"({hub['path']}, in-degree {hub['incoming']})"
                )
            lines.append(f"    dropped hubs: {cluster['dropped_hubs']}")
        lines.append(f"  dropped clusters: {scope['dropped_clusters']}")
    lines.append("Workspace hotspots")
    for hotspot in result["hotspots"]:
        lines.append(
            f"  {hotspot['repo']}:{hotspot['path']} "
            f"{hotspot['qualified_name']} (in-degree {hotspot['incoming']})"
        )
    lines.append(f"  dropped hotspots: {result['dropped_hotspots']}")
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True), structured_output=True)
@instrument
def file_api(
    path: str, repo: str | None = None, structured: bool = False
) -> str | list[dict]:
    """Return every stored symbol in one indexed file (no bodies); repo disambiguates duplicate relative paths."""
    from cairn.graph.file_api import file_api as graph_file_api

    conn = _conn()
    try:
        freshness = _fresh_graph(conn)
        symbols = graph_file_api(conn, path, repo=repo)
    finally:
        conn.close()
    if structured:
        return symbols
    return _with_freshness(_render_file_api(path, symbols), freshness)


def _render_file_api(path: str, symbols: list[dict]) -> str:
    """Render file symbols with signatures and spans, never bodies."""
    if not symbols:
        return f"No indexed symbols in '{path}'."
    lines = [f"{len(symbols)} symbols in '{path}':"]
    for symbol in symbols:
        signature = symbol["signature"] or "signature unavailable"
        lines.append(
            f"  {symbol['kind']} {signature} "
            f"[{symbol['line_start']}, {symbol['line_end']}]"
        )
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def cross_repo_deps(repo: str, limit: int = 50) -> str:
    """Cross-repo dependency map: what the repo depends on and what depends on it."""
    from cairn.graph import queries

    limit = _clamp(limit, 1, 1000)  # bound LLM-supplied value at the boundary
    conn = _conn()
    try:
        freshness = _fresh_graph(conn)
        result = queries.cross_repo_deps(conn, repo)
    finally:
        conn.close()
    out = [f"=== {repo} depends on ==="]
    deps = result["dependencies"]
    if deps:
        for d in deps[:limit]:
            out.append(f"  {d['repo']} ({d['evidence']}) x{d['count']}")
        if len(deps) > limit:
            out.append(f"  ... and {len(deps) - limit} more")
    else:
        out.append("  (none)")
    out.append(f"=== Dependents of {repo} ===")
    dependents = result["dependents"]
    if dependents:
        for d in dependents[:limit]:
            out.append(f"  {d['repo']} x{d['count']}")
        if len(dependents) > limit:
            out.append(f"  ... and {len(dependents) - limit} more")
    else:
        out.append("  (none)")
    return _with_freshness("\n".join(out), freshness)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def visualize_graph(
    scope: str = "symbol",
    symbol: str = "",
    module: str = "",
    repo: str = "",
    depth: int = 3,
    format: str = "mermaid",
) -> str:
    """Generate a Mermaid/DOT/JSON diagram of a graph scope (symbol|module|impact|repo|deps)."""
    from cairn.viz import query as vq
    from cairn.viz import renderers as vr

    depth = _clamp(depth, 1, 10)  # bound LLM-supplied value at the boundary
    conn = _conn()
    try:
        if scope == "symbol":
            graph = vq.get_symbol_graph(conn, symbol)
        elif scope == "impact":
            graph = vq.get_impact_graph(conn, symbol, max_depth=depth)
        elif scope == "module":
            graph = vq.get_module_graph(conn, module)
        elif scope == "repo":
            graph = vq.get_repo_graph(conn, repo)
        elif scope == "deps":
            graph = vq.get_deps_graph(conn)
        else:
            available_scopes = ["symbol", "impact", "module", "repo", "deps"]
            return (
                f"Unknown scope '{scope}'. Available: "
                f"{', '.join(available_scopes)}"
            )
    finally:
        conn.close()

    if format == "dot":
        return vr.to_dot(graph)
    if format == "json":
        return vr.to_json(graph)
    return vr.to_mermaid(graph)
