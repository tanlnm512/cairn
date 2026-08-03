"""Pydantic models for the structuredContent outputs.

When a tool is called with ``structured=True``, it returns one of these models
instead of a formatted string. Declaring the return type as a Pydantic model
plus ``structured_output=True`` on the ``@mcp.tool`` decorator lets FastMCP
auto-derive ``outputSchema`` and populate the native ``structuredContent`` field
of the MCP response -- so a client reads typed fields directly instead of
regex-parsing prose.

Each model mirrors the dict shape the corresponding ``*_data`` helper produces;
the helper builds the model via ``model_validate`` so the structured and prose
paths share one implementation.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class CallerEntry(BaseModel):
    """One caller row."""

    kind: str
    name: str
    file_path: str
    line: int
    repo: str


class GetCallersResult(BaseModel):
    """Structured output for ``get_callers(structured=True)``."""

    symbol: str
    count: int
    used_fallback: bool
    hit_limit: bool
    stale_banner: str = ""
    callers: List[CallerEntry]


class CalleeEntry(BaseModel):
    """One callee row."""

    name: str
    resolved: bool
    file_path: str
    line: int


class GetCalleesResult(BaseModel):
    """Structured output for ``get_callees(structured=True)``."""

    symbol: str
    count: int
    used_fallback: bool
    hit_limit: bool
    callees: List[CalleeEntry]


class SymbolEntry(BaseModel):
    """One search_symbols row."""

    kind: str
    name: str
    file_path: str
    line: int
    repo: str


class SearchSymbolsResult(BaseModel):
    """Structured output for ``search_symbols(structured=True)``."""

    pattern: str
    count: int
    truncated: bool
    symbols: List[SymbolEntry]


class SemanticMatch(BaseModel):
    """One semantic_search row."""

    kind: str
    name: str
    qualified_name: Optional[str] = None
    file_path: str = ""
    repo: str
    score: float
    provenance: str = "semantic"
    reranked: bool = False
    rerank_score: Optional[float] = None
    chunk: str = ""
    callers: List[dict] = []
    callees: List[dict] = []


class SemanticSearchResult(BaseModel):
    """Structured output for ``semantic_search(structured=True)``."""

    query: str
    count: int
    matches: List[SemanticMatch]


class ImpactTestEntry(BaseModel):
    """One affected-test row from impact_analysis."""

    symbol: str
    file: str
    repo: str
    detection_method: str = ""


class CrossRepoDependent(BaseModel):
    """One cross-repo dependent entry from impact_analysis."""

    repo: str
    count: int


class ImpactAnalysisResult(BaseModel):
    """Structured output for ``impact_analysis(structured=True)``."""

    symbol: str
    total: int
    truncated: bool
    fuzzy: bool
    by_depth: dict
    cycles: List[str]
    affected_tests: List[ImpactTestEntry]
    cross_repo_dependents: List[CrossRepoDependent]
