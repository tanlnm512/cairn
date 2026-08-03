"""Layer 1 code graph: build, query, and resolve over a SQLite symbol/edge store.

Public API for read-side graph operations. New code should import from here:

    from codegraph.graph import find_definition, get_callers, semantic_search

Internal split modules (traversal, lexical, cross_repo, stats, explore,
semantic, vector_math) hold the actual implementations; the ``queries``
shim re-exports the same names for backward compatibility.

A small set of shared text/vector primitives (``simple_tokenize``,
``BASE_STOP_WORDS``, ``l2norm``, ``dot``) and the ``embeddings`` module are
also exposed here as the public surface for higher layers (knowledge L5,
memory L4) to consume without reaching into submodule internals. They load
lazily via ``__getattr__`` so a structural-only import stays embeddings-free.
"""
from .cross_repo import cross_repo_deps
from .explore import explore
from .lexical import search_symbols
from .stats import get_stats, get_tree
from .traversal import find_definition, get_callers, get_callees, impact_analysis


def __getattr__(name):
    # Lazy: pull semantic_search only when asked for, so importing
    # src.graph for structural queries doesn't drag in the embeddings stack.
    if name == "semantic_search":
        from .semantic import semantic_search

        return semantic_search
    # Text/vector primitives shared with higher layers (knowledge, memory).
    # Imported lazily so structural-only consumers don't pay the tokenization
    # import cost either, and so the embeddings stack stays opt-in.
    if name in ("simple_tokenize", "BASE_STOP_WORDS"):
        from .tokenize import simple_tokenize as _t, BASE_STOP_WORDS as _b

        globals()["simple_tokenize"] = _t
        globals()["BASE_STOP_WORDS"] = _b
        return _t if name == "simple_tokenize" else _b
    if name in ("l2norm", "dot"):
        from .vector_math import l2norm as _l, dot as _d

        globals()["l2norm"] = _l
        globals()["dot"] = _d
        return _l if name == "l2norm" else _d
    if name == "embeddings":
        # Use import_module (not `from . import embeddings`) to avoid
        # re-triggering this __getattr__ recursively: `from . import embeddings`
        # looks up the `embeddings` attribute on the package, which re-enters
        # __getattr__ before the submodule is cached. import_module resolves the
        # submodule directly and caches it on sys.modules.
        import importlib

        _emb = importlib.import_module(".embeddings", __package__)
        globals()["embeddings"] = _emb
        return _emb
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "find_definition",
    "get_callers",
    "get_callees",
    "impact_analysis",
    "search_symbols",
    "cross_repo_deps",
    "get_stats",
    "get_tree",
    "explore",
    "semantic_search",
    # Shared text/vector primitives exposed for higher layers (L4/L5).
    "simple_tokenize",
    "BASE_STOP_WORDS",
    "l2norm",
    "dot",
    "embeddings",
]
