"""Re-exports of the graph query engine's public API.

Read-side graph operations are implemented in split modules under ``src/graph/``:
``traversal``, ``lexical``, ``cross_repo``, ``stats``, ``explore``,
``semantic``, and ``vector_math``. This file re-exports the public names so
``from cairn.graph.queries import ...`` keeps working; new code may also import
directly from ``cairn.graph``.
"""
from __future__ import annotations

# Public API surface -- re-exported for backward compatibility.
from .cross_repo import REPO_NAMESPACES, cross_repo_deps

from .explore import explore
from .lexical import search_symbols
from .stats import get_stats, get_tree, group_by_top_level
from .traversal import find_definition, get_callers, get_callees, impact_analysis, trace_flow
from .vector_math import l2norm as _l2norm, dot as _dot

# semantic_search is imported lazily below to avoid pulling the heavy
# embeddings/reranker/ann stack at module-load time when only structural
# queries are needed. A module-level __getattr__ keeps
# `from cairn.graph.queries import semantic_search` working.
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
    "semantic_search",  # noqa: F822 -- lazy-loaded via __getattr__ below
    "_l2norm",
    "_dot",
]
