"""Architectural wiki: deterministic, graph-derived per-repo summaries.

Public API:

    from cairn.wiki import generate_wiki
"""
from cairn.wiki.generator import generate_wiki

__all__ = ["generate_wiki"]
