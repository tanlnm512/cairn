"""Layer 1 code graph: build, query, and resolve over a SQLite symbol/edge store."""
from .cross_repo import cross_repo_deps
from .explore import explore
from .lexical import search_symbols
from .stats import get_stats, get_tree
from .traversal import find_definition, get_callers, get_callees, impact_analysis


def __getattr__(name):
    # Lazy: pull semantic_search only when asked for, so structural-only
    # consumers of this package don't drag in the embeddings stack.
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
    if name == "rrf_fuse":
        from .fusion import rrf_fuse as _r

        globals()["rrf_fuse"] = _r
        return _r
    if name == "note_contention":
        from .schema import note_contention as _nc

        globals()["note_contention"] = _nc
        return _nc
    if name == "embeddings":
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
    "rrf_fuse",
    "note_contention",
    "embeddings",
]
