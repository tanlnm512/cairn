"""Backward-compat re-exports for the graph query engine.

Historical entry point for all read-side graph operations. The actual
implementations now live in split modules under ``src/graph/``:

  - ``traversal``  -- find_definition, get_callers, get_callees, impact_analysis, trace_flow
  - ``lexical``    -- search_symbols (+ FTS5 pattern helpers)
  - ``cross_repo`` -- cross_repo_deps, REPO_NAMESPACES
  - ``stats``      -- get_stats, get_tree, group_by_top_level
  - ``explore``    -- explore (+ source-span / ambiguous-dispatch helpers)
  - ``semantic``   -- semantic_search (+ ANN-hit / caller-attach helpers)
  - ``vector_math``-- l2norm, dot, cosine

This file re-exports the public names so existing imports keep working:

    from codegraph.graph.queries import find_definition, search_symbols, ...

New code should prefer the package-level public API instead:

    from codegraph.graph import find_definition, search_symbols, ...
"""
from __future__ import annotations

# Public API surface -- re-exported for backward compatibility.
from .cross_repo import REPO_NAMESPACES, cross_repo_deps
from .explore import explore
from .lexical import search_symbols
from .stats import get_stats, get_tree, group_by_top_level
from .traversal import find_definition, find_definition_by_id, get_callers, get_callees, impact_analysis, trace_flow
from .vector_math import l2norm as _l2norm, dot as _dot

# semantic_search is imported lazily below to avoid pulling the heavy
# embeddings/reranker/ann stack at module-load time when only structural
# queries are needed. A module-level __getattr__ keeps
# `from codegraph.graph.queries import semantic_search` working.
def __getattr__(name):
    if name == "semantic_search":
        from .semantic import semantic_search

        return semantic_search
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "find_definition",
    "get_callers",
    "get_callees",
    "impact_analysis",
    "trace_flow",
    "search_symbols",
    "cross_repo_deps",
    "REPO_NAMESPACES",
    "get_stats",
    "get_tree",
    "group_by_top_level",
    "explore",
    "semantic_search",
    "_l2norm",
    "_dot",
]
