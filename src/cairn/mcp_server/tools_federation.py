"""L1 federation MCP tool: federated_search.

Thin adapter over ``cairn.graph.federation``: every registered workspace
store is queried live through its own read-only connection and the merged
ranking carries per-repo attribution.
"""
from __future__ import annotations

from mcp.types import ToolAnnotations

from ._server_core import mcp
from .metric_buffering import instrument
from .tools_graph import _clamp


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def federated_search(
    query: str, limit: int = 20, *, shared_embed: bool = False
) -> str:
    """Search every registered workspace store by meaning and merge the
    rankings into one list where every hit is attributed to the workspace
    it came from ('@ <workspace>' per line). A store without embeddings for
    the current model contributes its lexical BM25 ranking (provenance
    'bm25'); cross-store scores are rank-fused (RRF), so 'score' is a small
    rank-fusion number -- trust rank order, not magnitude. Stores that are
    missing, locked, or unindexed are named under 'unavailable' and never
    abort the query. For the active workspace alone, prefer semantic_search.

    Pass shared_embed=true to serve the query embed from one shared backend
    when every reachable store's embedding rows carry the current model
    stamp; any other stamp mix keeps per-store embedding. Sharing never
    changes which hits are returned, only how many times the query is
    embedded. Default is off.
    """
    from cairn.graph.federation import federated_search as _federated

    limit = _clamp(limit, 1, 1000)  # bound LLM-supplied value at the boundary
    if not query or not query.strip():
        return (
            "Query must not be empty. Pass a natural-language phrase, e.g. "
            'federated_search("where do we handle retries").'
        )

    result = _federated(query, limit=limit, shared_embed=shared_embed)

    ok = [ws for ws, state in result.states.items() if state == "ok"]
    out = [
        f'=== federated_search: "{query}" '
        f"({len(result.hits)} hit(s) from {len(ok)}/{len(result.states)} store(s)) ==="
    ]
    for hit in result.hits:
        short = (hit.get("file_path") or "").rsplit("/", 1)[-1]
        prov = hit.get("provenance", "semantic")
        out.append(
            f"  [{prov} {hit['score']:.4f}] {hit['kind']} "
            f"{hit.get('qualified_name') or hit['name']}  ({short})  "
            f"[{hit.get('repo', '')}]  @ {hit['workspace']}"
        )
    if not result.states:
        out.append("")
        out.append(
            "No workspace stores are registered. Run `cairn init` once inside "
            "each workspace to register its store, then retry."
        )
    else:
        if result.dropped:
            out.append("")
            out.append(
                f"Unavailable stores ({len(result.dropped)}) -- named, not searched:"
            )
            for ws in result.dropped:
                out.append(f"  {ws} ({result.states.get(ws, 'unknown')})")
        if not result.hits:
            out.append("")
            out.append(
                "No matches across the searched stores. Try search_symbols on "
                "the specific store for a lexical match, or rephrase with "
                "more specific terms."
            )
    return "\n".join(out)
