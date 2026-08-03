"""Architectural wiki: deterministic, graph-derived per-repo summaries.

Public API:

    from codegraph.wiki import generate_wiki
"""
from codegraph.wiki.generator import generate_wiki

__all__ = ["generate_wiki"]
