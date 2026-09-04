"""L1 graph MCP tools: find_definition, get_callers, get_callees,
impact_analysis, explore, semantic_search, search_symbols, cross_repo_deps,
plus visualize_graph (a graph renderer, filed under L4 but structurally belongs
with the graph-query tools).

Each tool is decorated with @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
on the shared FastMCP instance from _server_core, and wrapped with the
metric-instrumenting decorator from metric_buffering.
"""
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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def find_definition(name: str) -> str:
    """Find where a symbol is defined. Returns file:line, kind, qualified name.

    Has a built-in 3-step fallback (exact → qualified name → substring LIKE), so
    it works as a shortcut when you're fairly confident in the name. For an
    ambiguous or partial name, search_symbols ranks matches by relevance instead.

    Example:
        find_definition("PaymentProcessor")
        ->  src/payments/PaymentProcessor.kt:14  class com.example.PaymentProcessor  (app)
    """
    from cairn.graph import queries

    conn = _conn()
    try:
        rows = queries.find_definition(conn, name)
    finally:
        conn.close()
    if not rows:
        return (
            f"No definition found for '{name}'. The name may be misspelled, "
            f"ambiguous, or the symbol lives outside the indexed workspace. "
            f"Try search_symbols(\"{name}\") to find near matches."
        )
    out = []
    for r in rows:
        out.append(
            f"{r['file_path']}:{r['line_start']}  {r['kind']} "
            f"{r['qualified_name'] or r['name']}  ({r['repo']})"
        )
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True), structured_output=True)
@instrument
def get_callers(name: str, fuzzy: bool = False, limit: int = 200, structured: bool = False) -> str | GetCallersResult:
    """Who calls this function/symbol? Returns caller symbol + file:line + repo.

    Precise (default): only callers of the exact resolved symbol. Trustworthy.
    Fuzzy: also includes call sites matched only by name across the whole
    workspace -- can mix in unrelated symbols that merely share the name.

    When to use fuzzy: only when precise returns nothing AND you suspect the
    edge couldn't be resolved (common-name method like `get`/`invoke`/`create`,
    or cross-language calls). Treat fuzzy results as candidates to verify, not
    as ground truth. Empty precise results mean 'no resolvable callers', NOT
    'no callers exist' -- if you pass fuzzy=False and precise comes up empty,
    this tool automatically retries with fuzzy=True itself and labels those
    rows as unverified candidates, so you don't have to remember the retry.

    limit: max rows returned (default 200). Lower it for common names under
    fuzzy=True where the raw count can run into the thousands.

    structured: when True, returns a typed GetCallersResult model (FastMCP
    derives outputSchema from it, so the response carries native
    structuredContent -- agents read fields directly, no regex). Default False
    preserves the existing prose return for backward compatibility (skill docs,
    empty-result-hint invariant)."""
    data = get_callers_data(name, fuzzy=fuzzy, limit=limit)
    if structured:
        return GetCallersResult.model_validate(data)
    return _render_callers(data)


def get_callers_data(name: str, fuzzy: bool = False, limit: int = 200) -> dict:
    """Structured core of ``get_callers``: returns a dict, no prose.

    Shared by the structuredContent and prose returns. An agent passes
    ``structured=True`` to read these fields directly instead of regex-parsing
    the tool's string.
    """
    from cairn.graph import queries

    limit = _clamp(limit, 1, 1000)  # bound LLM-supplied value at the boundary
    conn = _conn()
    try:
        rows = queries.get_callers(conn, name, fuzzy=fuzzy, limit=limit)
        used_fallback = False
        if not rows and not fuzzy:
            rows = queries.get_callers(conn, name, fuzzy=True, limit=limit)
            used_fallback = True
        # Staleness banner: check while conn is open; only relevant when there
        # are results (an empty answer can't be "stale").
        banner = _staleness_banner(conn, [r["file_path"] for r in rows]) if rows else ""
    finally:
        conn.close()

    # hit_limit only makes sense on the precise (non-fallback) path: when we
    # fell back to fuzzy it's because the precise callers don't exist, so a
    # higher limit wouldn't surface more precise results.
    hit_limit = (not used_fallback) and len(rows) >= limit
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
    if data["stale_banner"]:
        out.insert(0, data["stale_banner"])
    for c in data["callers"]:
        out.append(
            f"  {c['kind']} {c['name']}  {c['file_path']}:{c['line']}  ({c['repo']})"
        )
    if data["hit_limit"]:
        out.append("  ... hit the limit cap; pass a higher limit for more.")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True), structured_output=True)
@instrument
def get_callees(name: str, fuzzy: bool = False, limit: int = 200, structured: bool = False) -> str | GetCalleesResult:
    """What does this function/symbol call? Returns callee names + file:line.

    Precise (default): only calls resolved to a workspace symbol (drops stdlib
    and external calls like listOf, println, Retrofit.Builder).
    Fuzzy: also includes unresolved outgoing calls, so you see the FULL set of
    what a function invokes, including library calls shown as '(unresolved)'.

    When to use fuzzy: exploring a function's behavior broadly, or when you
    need to find calls into libraries/stdlib that precise intentionally omits.
    Empty precise results don't mean the function calls nothing -- if you pass
    fuzzy=False and precise comes up empty, this tool automatically retries
    with fuzzy=True itself and labels those rows as unverified candidates.

    limit: max rows returned (default 200).

    structured: when True, returns a dict (``{symbol, count, used_fallback,
    hit_limit, callees: [...]}``) instead of a formatted string, so agents
    don't have to regex the prose. Default False preserves the prose return."""
    data = get_callees_data(name, fuzzy=fuzzy, limit=limit)
    if structured:
        return GetCalleesResult.model_validate(data)
    return _render_callees(data)


def get_callees_data(name: str, fuzzy: bool = False, limit: int = 200) -> dict:
    """Structured core of ``get_callees``."""
    from cairn.graph import queries

    limit = _clamp(limit, 1, 1000)  # bound LLM-supplied value at the boundary
    conn = _conn()
    try:
        rows = queries.get_callees(conn, name, fuzzy=fuzzy, limit=limit)
        used_fallback = False
        if not rows and not fuzzy:
            rows = queries.get_callees(conn, name, fuzzy=True, limit=limit)
            used_fallback = True
    finally:
        conn.close()

    # hit_limit only makes sense on the precise (non-fallback) path: when we
    # fell back to fuzzy it's because the precise callees don't exist.
    hit_limit = (not used_fallback) and len(rows) >= limit
    return {
        "symbol": name,
        "count": len(rows),
        "used_fallback": used_fallback,
        "hit_limit": hit_limit,
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
        return f"No callees found for '{data['symbol']}' (checked precise and fuzzy)."
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
    return "\n".join(out)


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
    """Recursive impact analysis: what breaks if this symbol changes?
    Traverses callers up to `depth` (default 5). Reports total + by depth.

    Precise (default): walks only resolved edges, so blast radius is NOT
    inflated by name collisions. This is the trustworthy estimate for
    'what breaks if I change this signature'.
    Fuzzy: also traverses unresolved name-only edges. Much broader but noisy --
    a common name like `create` can cascade into hundreds of unrelated symbols.

    cached: when True, returns O(1) precomputed dataflow (populated during
    `cairn build`/`cairn sync`). Falls back to live analysis if no cache entry.
    Live analysis is the default for freshness.

    limit: caps total accumulated impacted rows (default 500) so a common
    name under fuzzy=True can't blow up the response. Traversal stops early
    once hit, rather than just truncating the display.

    structured: when True, returns a dict (``{symbol, total, truncated, fuzzy,
    by_depth, cycles, affected_tests, cross_repo}``) instead of a formatted
    string. The cached-path early return stays prose under both modes (it's a
    different shape). Default False preserves the prose return.

    When to use fuzzy: when precise impact seems suspiciously small for a
    widely-used symbol.

    Example:
        impact_analysis("PaymentProcessor.create", depth=3)
        ->  Impact of 'PaymentProcessor.create' (within-repo, precise):
              Total: 12 impacted across depth 3
                depth 1: 4   depth 2: 6   depth 3: 2
            Cross-repo consumers: checkout-svc, reporting-svc
            (within-repo only — pair with cross_repo_deps for the full picture)
    """
    from cairn.graph import queries

    depth = _clamp(depth, 1, 10)     # bound LLM-supplied value at the boundary
    limit = _clamp(limit, 1, 1000)   # bound LLM-supplied value at the boundary
    conn = _conn()
    try:
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
                return "\n".join(out)
            # No cache entry — fall through to live analysis below.

        # Live path: recursive caller traversal.
        result = queries.impact_analysis(conn, name, max_depth=depth, fuzzy=fuzzy, limit=limit)
        xref = queries.cross_repo_deps(conn, _repo_of(conn, name) or "")
    finally:
        conn.close()

    data = impact_analysis_data(result, xref, name=name, fuzzy=fuzzy, limit=limit)
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
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def explore(query: str) -> str:
    """Answer 'how does X work' in one call. Returns matching symbols' verbatim
    source grouped by file, the call paths between them (including ambiguous
    dispatch hops), a blast-radius summary, and any matching tribal memory
    (past decisions/mistakes from this workspace's memory store). Recommended
    first move for any structural question; reach for
    get_callers/impact_analysis/search_knowledge to drill down when this is
    thin.

    Example:
        explore("how does ApiFactory create clients")
        ->  === explore: "how does ApiFactory create clients" ===
            2 symbol(s) matched.

            === Source (1 file(s), 18 line(s)) ===
            src/net/ApiFactory.kt
              [class com.example.ApiFactory  lines 14-32]
                14  class ApiFactory { ... }

            === Call paths ===
              create -> calls  buildClient  (ApiFactory.kt:22)  [exact]

            === Blast radius (depth 2) ===
              com.example.ApiFactory: 8 caller(s)

            === Ambiguous dispatch ===
              (none — all call edges were precisely resolved)

            === Tribal memory (1) ===
              Never evict numpy from sys.modules mid-process
                How to apply: keep numpy loaded until the interpreter exits
    """
    from cairn.graph import queries

    conn = _conn()
    tribal: list = []
    try:
        result = queries.explore(conn, query)
        if result["seeds"]:
            from cairn.graph import note_contention
            from cairn.memory.promotion import record_references_batch, search_memory

            seed_names = [s["name"] for s in result["seeds"] if s.get("name")][:5]
            mems = search_memory(
                conn, _bundle(), " ".join(seed_names),
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
        return _append_embed_degradation_footnote(
            f"No symbols matching '{query}'. Try a broader query or use search_symbols."
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
            short = file_path.rsplit("/", 1)[-1]
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
            out.append(f"  {c.title or c.concept_id}")
            m = re.search(r"^How to apply:\s*(.+)$", c.body, re.M)
            apply_line = m.group(1).strip() if m else (c.description or "").strip()
            if apply_line:
                out.append(f"    How to apply: {apply_line}")
    else:
        out.append("  (none)")
    return _append_embed_degradation_footnote("\n".join(out))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True), structured_output=True)
@instrument
def semantic_search(query: str, limit: int = 20, include_callers: bool = False, structured: bool = False, rerank: bool | None = None) -> str | SemanticSearchResult:
    """Semantic (concept) search over symbols. Finds code by meaning, not just
    exact words — 'where do we handle retries' finds backoff/recovery code even
    when no symbol literally says 'retry'. Results are fuzzy; combine with
    get_callers/impact_analysis for precise follow-up. Requires the 'semantic'
    extra (pip install 'cairn-intel[semantic]'), or set CAIRN_EMBED_BACKEND=hash
    for a dep-free smoke test.

    By default (CAIRN_FUSION unset or not "0"), results blend BM25 +
    vector via Reciprocal Rank Fusion, and the displayed score is that RRF
    rank score (small, e.g. 0.01-0.02, tightly clustered by rank) -- NOT a
    cosine similarity, regardless of how strong the real semantic match is.
    Each result's provenance ('semantic', 'bm25', or 'fused(bm25+semantic)')
    is shown alongside its score so you can tell which source it came from.
    Trust rank order under fusion; if you need the score itself to reflect
    match strength, set CAIRN_FUSION=0 to get true 0..1 cosine scores.

    Set CAIRN_RERANK=1 to add a cross-encoder rerank stage on top of the
    cosine/fusion scan (retrieves a wider candidate pool, re-scores with a
    joint query/candidate model, resorts). Results show '[rerank X.XX]'
    instead of the cosine/fusion label when the rerank stage actually ran for
    that call -- if it's disabled or the model failed to load, results
    silently fall back to plain ordering, so don't assume rerank ran just
    because the env var is set. In auto mode (default) the rerank stage is
    skipped when the fused ranking is already decisive (margin over the RRF
    scores >= CAIRN_RERANK_MIN_MARGIN and the top hit is an exact name match),
    since the cross-encoder cannot change such an answer and costs most of
    the latency. The `rerank` parameter overrides that per call: None = auto
    (gate decides), True = force the rerank stage when enabled (bypasses the
    confidence gate; CAIRN_RERANK=0 still wins), False = never rerank.

    include_callers=True attaches each hit's immediate (1-hop, precise-only)
    callers/callees, so you get a small subgraph instead of a flat list --
    skips the separate get_callers/get_callees follow-up call. Off by
    default since it costs extra graph queries per result.

    structured: when True, returns a dict (``{query, count, matches: [...]}``)
    instead of a formatted string. Default False preserves the prose return.
    The "semantic not installed" and "empty index" early-return cases stay
    prose strings under both modes (they're error states, not result sets).

    Example:
        semantic_search("where do we handle retries")
        ->  === semantic_search: "where do we handle retries" (3 match(es)) ===
              [fused(bm25+semantic) 0.02] function retryWithBackoff  (Backoff.kt)
              ...
    """
    from cairn.graph import embeddings as emb

    if not emb.embeddings_available():
        return emb.install_hint()

    # Surface the dep-free hash fallback once per process: under it the cosine
    # signal is token-overlap only, not real semantic meaning. Provenance on
    # each result (semantic (hash backend) / fused(bm25+semantic, hash)) carries
    # the signal on every call; this warning catches a caller reading just the
    # score/label.
    emb.warn_hash_fallback_once(logger, context="semantic_search")

    limit = _clamp(limit, 1, 1000)  # bound LLM-supplied value at the boundary
    conn = _conn()
    try:
        # Do NOT lazily embed during a search query -- embed_all() writes contend
        # with the daemon's WAL lock and fails with "database is locked". Embedding
        # is a build-time operation (`cairn embed`).
        if emb.embed_count(conn) == 0:
            return (
                "Semantic index is empty. Run `cairn embed` once to index the "
                "corpus (build-time, ~1-2 min for 50k symbols), then retry "
                "this query. Embedding is not done lazily during search to "
                "avoid write-lock contention with the running server."
            )
        from cairn.graph import queries
        rows = queries.semantic_search(conn, query, limit=limit, include_callers=include_callers, rerank=rerank)
    finally:
        conn.close()

    data = {
        "query": query,
        "count": len(rows),
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
        _render_semantic_search(data, include_callers=include_callers)
    )


def _render_semantic_search(data: dict, include_callers: bool = False) -> str:
    """Render the structured semantic-search result as the prose return."""
    query = data["query"]
    rows = data["matches"]
    if not rows:
        fusion_on = os.environ.get("CAIRN_FUSION", "1") != "0"
        return (
            f"No matches for '{query}' -- neither the vector scan nor the "
            f"BM25 fallback found anything{'' if fusion_on else ' above the cosine threshold'}. "
            "The corpus may not be embedded yet (run `cairn embed` to index), or the "
            "wording may not match any token. Try search_symbols(\"...\") for a "
            "lexical match, or rephrase with more specific terms."
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
    """Search symbols by lexical pattern (supports * wildcards). Optional kind filter.

    The default discovery entry point: FTS5 + BM25 ranking, fast, handles
    wildcards and underscore-split names. Returns up to 50 ranked matches;
    use a qualified name from here as input to the nav tools (get_callers,
    get_callees, impact_analysis), which take a bare name and return [] silently
    on an ambiguous match.

    structured: when True, returns a dict (``{pattern, count, truncated,
    symbols: [...]}``) instead of a formatted string. Default False preserves
    the prose return.

    Example:
        search_symbols("Payment*", kind="class")
        ->  3 symbols matching 'Payment*':
              class com.example.PaymentProcessor  src/payments/...  (app)
              ...
    """
    data = search_symbols_data(pattern, kind=kind)
    if structured:
        return SearchSymbolsResult.model_validate(data)
    return _render_search_symbols(data)


def search_symbols_data(pattern: str, kind: str = "") -> dict:
    """Structured core of ``search_symbols``."""
    from cairn.graph import queries

    conn = _conn()
    try:
        rows = queries.search_symbols(conn, pattern, kind=kind or None)
    finally:
        conn.close()

    if not rows:
        # Zero matches -> emit a durable empty_result (spec §6.4) so the
        # empty-result rate is measurable for the lexical search tool too.
        # Emitted here at the MCP tool boundary, NOT in the search_symbols
        # primitive (lexical.py): that primitive is shared by explore/semantic
        # and would double-count; this wrapper has a single caller. Best-effort.
        try:
            from cairn.telemetry import EMPTY_RESULT, emit as _emit

            _emit(EMPTY_RESULT, query_kind="search_symbols")
        except Exception:
            logger.debug("search_symbols empty_result emit failed", exc_info=True)

    SHOWN = 50
    returned = rows[:SHOWN]
    # Distinguish the FULL DB match count (total_count, could be thousands)
    # from how many symbols are actually shipped (count == len(symbols)).
    # total_count drives the "and N more" message.
    return {
        "pattern": pattern,
        "count": len(returned),
        "total_count": len(rows),
        "truncated": len(rows) > SHOWN,
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
    # total_count is the full DB match count; count is how many were shipped.
    total_count = data.get("total_count", data["count"])
    if total_count == 0:
        return (
            f"No symbols matching '{data['pattern']}'. The token may not be indexed or "
            f"may use different casing/wording. Try a broader pattern (fewer "
            f"characters, a leading wildcard), or semantic_search(\"{data['pattern']}\") "
            f"to match by meaning."
        )
    out = [f"{total_count} symbols matching '{data['pattern']}':"]
    for s in data["symbols"]:
        out.append(
            f"  {s['kind']} {s['name']}  {s['file_path']}:{s['line']}  ({s['repo']})"
        )
    if data["truncated"]:
        out.append(f"  ... and {total_count - len(data['symbols'])} more")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def cross_repo_deps(repo: str, limit: int = 50) -> str:
    """Cross-repo dependency map for a repo. What it depends on, what depends on it.

    limit: max rows shown per section (default 50); results are already
    grouped/sorted by repo so this only bites in very large workspaces."""
    from cairn.graph import queries

    limit = _clamp(limit, 1, 1000)  # bound LLM-supplied value at the boundary
    conn = _conn()
    try:
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
    return "\n".join(out)


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
    """Generate a visual diagram (Mermaid/DOT/JSON) of a graph scope.

    scope: symbol | module | impact | repo | deps
    """
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
