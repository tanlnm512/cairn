"""Compass Router: classify natural-language intent and route across all 5 layers.

Hybrid query strategy. When the graph layer fires, queries are expanded via
multi-token BM25 search + 1-hop graph neighborhood traversal instead of a
single CamelCase `find_definition` lookup. This gives meaningful results for
natural-language queries like "where do we handle retries" that contain no
CamelCase symbol name.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Dict, List

from ..graph.queries import (
    find_definition,
    get_callers,
    get_callees,
    search_symbols,
)
from ..graph.tokenize import BASE_STOP_WORDS, simple_tokenize
from ..graph.schema import note_contention
from ..okf.bundle import OKFBundle

# Keyword -> (intent, layer). Order matters: first match wins.
# L5 patterns MUST precede L1: "impact of changing" also matches L1's
# \b(impact|...) and "epic" could match L1 patterns. More specific multi-word
# phrases are placed first.
INTENT_PATTERNS = [
    # L5: knowledge (specific phrases first — must precede L1's broad "impact")
    (r"\b(business rule|business policy)\b", "knowledge_lookup", "L5"),
    (r"\b(impact of changing|affects which|what repos)\b", "knowledge_impact", "L5"),
    (r"\b(requirement|epic)\b", "knowledge_lookup", "L5"),
    (r"\bpolicy\b", "knowledge_lookup", "L5"),
    # L1: graph
    (r"\b(where is|defined|find definition|definition of)\b", "definition_lookup", "L1"),
    (r"\b(calls|callers|callees|who calls|invokes)\b", "call_graph", "L1"),
    (r"\b(impact|breaks|what if i change|blast radius)\b", "impact_analysis", "L1"),
    # L2: wiki
    (r"\b(how does|how do|flow|process|works?)\b", "feature_understanding", "L2"),
    (r"\b(pattern|architecture|design)\b", "architecture", "L2"),
    # L3: compass
    (r"\b(navigate|where do i start|guide|onboard)\b", "module_navigation", "L3"),
    (r"\b(gotcha|trap|non-obvious|watch out|pitfall)\b", "non_obvious_gotchas", "L3"),
    # L4: memory
    (r"\b(why did|decision|chose|rationale)\b", "past_decisions", "L4"),
    (r"\b(mistake|error|wrong|forgot|bug we hit)\b", "common_mistakes", "L4"),
]

# Common English words to filter from queries before FTS5/graph search.
# Extends the shared BASE_STOP_WORDS (see graph/tokenize.py) with a few extra
# words that only show up in code-navigation queries ("where do we *handle*
# retries") and carry no technical signal for symbol/doc matching.
_STOP_WORDS = BASE_STOP_WORDS | frozenset({
    "handle", "handles", "handled", "handling",
})

# CamelCase stop words — capitalized common words that look like symbols.
_CAMELCASE_STOP = frozenset({
    "The", "How", "Why", "What", "When", "Where", "Layer", "Does", "Did",
    "Are", "Is",
})


def classify_intent(query: str) -> Dict:
    """Return {intent, layer, query}."""
    q_lower = query.lower()
    for pattern, intent, layer in INTENT_PATTERNS:
        if re.search(pattern, q_lower):
            return {"intent": intent, "layer": layer, "query": query}
    return {"intent": "complex", "layer": "ALL", "query": query}


def _nonempty(v) -> bool:
    """Check whether a query result value is non-empty.

    For dicts, check if any value is non-empty. This handles graph results
    which may have keys but all empty values.
    """
    if not v or v in ([], {}, ""):
        return False
    if isinstance(v, dict):
        # For dict results (like graph), check if any value is non-empty
        # Special case: graph result has seed_count, use that as primary indicator
        if "seed_count" in v:
            return v["seed_count"] > 0
        return any(_nonempty(val) for val in v.values())
    return True


def route_query(
    query: str, conn: sqlite3.Connection, bundle: OKFBundle
) -> Dict:
    """Route a natural-language query to the appropriate layer(s).

    Returns {intent, layer, layers_queried, results: {graph, wiki, compass, memory}}.
    """
    intent = classify_intent(query)
    layer = intent["layer"]
    results: Dict = {}

    # Extract a likely symbol/module token from the query for L1/L3.
    token = _extract_symbol_token(query)

    if layer in ("L1", "ALL"):
        results["graph"] = _query_graph(query, token, conn)
    if layer in ("L2", "ALL"):
        results["wiki"] = _search_wiki(query, bundle)
    if layer in ("L3", "ALL"):
        results["compass"] = _get_compass(token, bundle)
    if layer in ("L4", "ALL"):
        results["memory"] = _search_memory(query, bundle, conn)
    if layer in ("L5", "ALL"):
        results["knowledge"] = _search_knowledge(query, bundle)

    route = {
        "intent": intent["intent"],
        "layer": layer,
        "layers_queried": list(results.keys()),
        "results": results,
    }

    # Degrade: if the targeted layer(s) produced nothing, fall back across the
    # always-true derived tier (graph) plus the asserted tier (memory + knowledge)
    # so the router never returns an empty body.
    if not any(_nonempty(v) for v in results.values()):
        fb = {
            "graph": _query_graph(query, token, conn),
            "memory": _search_memory(query, bundle, conn),
            "knowledge": _search_knowledge(query, bundle),
        }
        route["results"] = {k: v for k, v in fb.items() if _nonempty(v)}
        route["degraded"] = True

    # Add explicit empty flag to indicate when all layers returned nothing
    route["empty"] = not any(_nonempty(v) for v in route["results"].values())

    return route


def _query_graph(query: str, token: str, conn: sqlite3.Connection) -> Dict:
    """Hybrid graph query: multi-token BM25 + 1-hop graph expansion.

    1. Extract tokens from the query (CamelCase + lowercase).
    2. Search symbols via FTS5 OR query (BM25-ranked).
    3. For top seeds, pull 1-hop callers + callees (graph expansion).
    4. Fallback to single-token find_definition when no tokens extracted.
    """
    tokens = _extract_query_tokens(query)
    if tokens:
        return _query_graph_hybrid(conn, tokens)

    # Fallback: original single-CamelCase-token lookup (backward compat).
    out = {}
    if token:
        defs = find_definition(conn, token)
        callers = get_callers(conn, token)
        out["definition"] = [
            {"name": d["name"], "file": f"{d['file_path']}:{d['line_start']}", "kind": d["kind"]}
            for d in defs[:5]
        ]
        out["callers"] = len(callers)
    return out


def _query_graph_hybrid(conn: sqlite3.Connection, tokens: List[str]) -> Dict:
    """BM25 search over multiple tokens + graph neighborhood expansion.

    Runs search_symbols per-token (FTS5 prefix match) and merges results by
    symbol ID. Also tries stemmed variants (strip trailing s/es/ing/ed) to
    match plural/inflected forms. Top seeds get 1-hop caller/callee expansion.
    """
    seeds = []
    seen_ids = set()
    # Build search variants: original + stemmed for each token.
    search_terms = []
    for tok in tokens:
        search_terms.append(tok)
        stemmed = _stem_token(tok)
        if stemmed and stemmed != tok:
            search_terms.append(stemmed)
    per_token_limit = max(3, 10 // len(search_terms))

    for term in search_terms:
        try:
            rows = search_symbols(conn, term, limit=per_token_limit)
        except sqlite3.OperationalError:
            note_contention("router.query_graph_hybrid")
            continue
        for r in rows:
            d = dict(r)
            rid = d.get("id", "")
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            seeds.append({
                "name": d.get("name", ""),
                "kind": d.get("kind", ""),
                "file": f"{d.get('file_path', '')}:{d.get('line_start', '')}",
                "repo": d.get("repo", ""),
            })

    # 1-hop graph expansion for top seeds.
    callers_out = []
    callees_out = []
    seen_caller_keys = set()
    seen_callee_keys = set()
    for seed in seeds[:5]:
        name = seed["name"]
        if not name:
            continue
        for c in get_callers(conn, name, limit=5):
            cd = dict(c)
            key = (cd.get("caller_name", ""), name, cd.get("file_path", ""), cd.get("edge_line", ""))
            if key in seen_caller_keys:
                continue
            seen_caller_keys.add(key)
            callers_out.append(f"{cd['caller_name']} ({cd.get('file_path', '')}:{cd.get('edge_line', '')})")
        for c in get_callees(conn, name, limit=5):
            cd = dict(c)
            key = (name, cd.get("callee_name", ""), cd.get("file_path", ""), cd.get("edge_line", ""))
            if key in seen_callee_keys:
                continue
            seen_callee_keys.add(key)
            callees_out.append(f"{cd['callee_name']} ({cd.get('file_path', '')}:{cd.get('edge_line', '')})")

    return {
        "seeds": [f"{s['kind']} {s['name']}  {s['file']}  [{s['repo']}]" for s in seeds],
        "seed_count": len(seeds),
        "callers": callers_out,
        "callees": callees_out,
        "neighbor_count": len(callers_out) + len(callees_out),
    }


def _search_wiki(query: str, bundle: OKFBundle) -> List[str]:
    return [c.title for c in bundle.search(query, limit=3) if c.type.startswith("Wiki")]


def _get_compass(token: str, bundle: OKFBundle) -> List[str]:
    out = []
    for cid in bundle.list_concepts(prefix="compass/"):
        try:
            c = bundle.read_concept(cid)
            if token and (token in (c.resource or "") or token in cid):
                out.append(c.title)
        except Exception:
            continue
    return out


def _search_memory(query: str, bundle: OKFBundle, conn: sqlite3.Connection) -> List[str]:
    """Routes through the shared search_memory() (lexical + semantic fallback).

    session_id is intentionally omitted so routing/degraded fallback doesn't
    inflate cross_session_refs for queries that weren't an explicit memory recall.
    """
    from ..memory.promotion import search_memory

    return [c.title for c in search_memory(conn, bundle, query)[:3]]


def _search_knowledge(query: str, bundle: OKFBundle) -> List[str]:
    """Search knowledge documents."""
    return [
        c.title for c in bundle.search(query, limit=5)
        if c.concept_id.startswith("knowledge/")
    ]


def _extract_symbol_token(query: str) -> str:
    """Extract a likely CamelCase symbol name from the query."""
    # Find CamelCase tokens of length >=3.
    matches = re.findall(r"\b([A-Z][a-zA-Z0-9]{2,})\b", query)
    for m in matches:
        if m not in _CAMELCASE_STOP:
            return m
    return ""


def _extract_query_tokens(query: str) -> List[str]:
    """Extract meaningful search tokens from a natural-language query.

    Returns a deduplicated list suitable for FTS5 search: CamelCase symbols
    (>=3 chars, stop-filtered, kept in original case) and lowercase technical
    terms (>=3 chars, stop-filtered), skipping lowercase forms that duplicate a
    CamelCase token.
    """
    tokens = []
    seen = set()
    seen_lower = set()  # lowercase forms of captured tokens
    q = query.strip()

    # 1. CamelCase tokens (carry structural signal).
    for m in re.findall(r"\b([A-Z][a-zA-Z0-9]{2,})\b", q):
        if m not in _CAMELCASE_STOP and m not in seen:
            tokens.append(m)
            seen.add(m)
            seen_lower.add(m.lower())

    # 2. Lowercase tokens (>=3 chars, skip if already captured as CamelCase).
    for tok in simple_tokenize(q, stop_words=_STOP_WORDS):
        if tok not in seen and tok not in seen_lower:
            tokens.append(tok)
            seen.add(tok)
            seen_lower.add(tok)

    return tokens


def _stem_token(token: str) -> str:
    """Rudimentary stemming for FTS5 prefix search.

    Strips common English suffixes so 'retries' -> 'retri', 'handling' -> 'handl'
    (the trailing wildcard in _pattern_to_fts handles the rest). Returns empty
    if stripping would leave < 3 chars.
    """
    transformations = {
        "ies": "y",     # retries -> retry
        "ied": "y",     # carried -> carry
        "ing": "",      # handling -> handl
        "tion": "",     # creation -> crea
        "ment": "",     # deployment -> deploym
        "ness": "",     # readiness -> readi
        "able": "",     # configurable -> configur
        "ible": "",     # accessible -> access
        "ful": "",      # helpful -> help
        "ous": "",      # verbose -> verb
    }
    for suffix, replacement in transformations.items():
        if token.endswith(suffix):
            stem = token[: -len(suffix)] + replacement
            if len(stem) >= 3:
                return stem
    # Simple trailing 's', 'es', 'ed' — must not shorten below 3 chars.
    if token.endswith("es") and len(token) > 5:
        return token[:-2]
    if token.endswith("s") and len(token) > 4:
        return token[:-1]
    if token.endswith("ed") and len(token) > 5:
        return token[:-2]
    return ""
