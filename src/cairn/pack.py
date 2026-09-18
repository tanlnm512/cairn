"""One-shot, token-budgeted context pack over the local graph.

``build_pack(conn, bundle, task, budget)`` composes existing read-only
surfaces into one deterministic pipeline: seed candidate symbols (semantic
search unioned with lexical term-mode anchors, falling back to lexical
search when embeddings are absent, rerank pinned off), expand through
precise call edges, rank by structural centrality, enrich the pool with
trimmed source, blast-radius lines, compass excerpts, and memory picks,
fit the rendered content into the budget, and emit a single markdown
block. No LLM and no network access in any stage.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Tuple

from cairn.bench.agent_suite import CHARS_PER_TOKEN
from cairn.dashboard.tokenizer import (
    HEURISTIC_MODE,
    active_tokenizer_mode,
    estimate_tokens,
)
from cairn.graph import embeddings
from cairn.graph.dataflow import closure_available
from cairn.graph.lexical import search_symbols, search_symbols_terms
from cairn.graph.semantic import semantic_search
from cairn.graph.traversal import (
    STRUCTURAL_EDGE_KINDS,
    find_definition_by_id,
    get_callers,
    get_callees,
)
from cairn.pack_enrich import (
    blast_radius_line,
    compass_excerpt,
    memory_picks,
    render_source,
)

if TYPE_CHECKING:
    from cairn.okf.bundle import OKFBundle

# Fixed content-kind vocabulary for pack items; every rendered block is one
# of these, so the cost hook and fitter stay content-kind-agnostic.
KIND_SOURCE = "source"
KIND_BLAST_RADIUS = "blast-radius"
KIND_COMPASS = "compass"
KIND_MEMORY = "memory"
ITEM_KINDS = frozenset({KIND_SOURCE, KIND_BLAST_RADIUS, KIND_COMPASS, KIND_MEMORY})

# Seed-stage union cap; the pool widens only through expansion.
SEED_LIMIT = 10

# Head slots the semantic leg may fill within the union cap; lexical-only
# additions take the remaining slots, so a full semantic result list cannot
# crowd name anchors out of the seed list.
SEMANTIC_SEED_LIMIT = 5

# Per-seed 1-hop neighbor cap, applied per direction (callers, callees).
EXPAND_LIMIT = 8

# Call-edge spellings expansion follows; inheritance and service edges stay out.
CALL_EDGE_KINDS = ("calls", "call")

# Report classes for the header's kept/dropped counts; symbol content kinds
# aggregate as "symbols".
REPORT_CLASSES = ("symbols", "compass", "memories")
KIND_CLASS = {
    KIND_SOURCE: "symbols",
    KIND_BLAST_RADIUS: "symbols",
    KIND_COMPASS: "compass",
    KIND_MEMORY: "memories",
}

# Task-level blocks reserved ahead of the ranked symbol blocks.
RESERVED_KINDS = frozenset({KIND_COMPASS, KIND_MEMORY})
RANKED_KINDS = frozenset({KIND_SOURCE, KIND_BLAST_RADIUS})


# ---------------------------------------------------------------------------
# Item model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PackItem:
    """One rendered content block, costed for budget fitting."""

    kind: str
    rank: int
    text: str
    cost: int

    def __post_init__(self) -> None:
        if self.kind not in ITEM_KINDS:
            raise ValueError(f"unknown pack item kind: {self.kind!r}")

    @classmethod
    def create(cls, kind: str, rank: int, text: str) -> "PackItem":
        """Build an item with its cost computed by the cost hook."""
        return cls(kind=kind, rank=rank, text=text, cost=item_cost(text))


@dataclass(frozen=True)
class PackSymbol:
    """One candidate symbol in the pipeline's pool."""

    symbol_id: str
    name: str
    qualified_name: str
    kind: str
    file_path: str
    is_seed: bool


@dataclass(frozen=True)
class PackResult:
    """The pipeline's output: the ranked symbol pool, the fitted items, and
    the per-class dropped counts."""

    task: str
    budget: int
    symbols: tuple[PackSymbol, ...]
    items: tuple[PackItem, ...]
    dropped: Dict[str, int]


# ---------------------------------------------------------------------------
# Cost hook
# ---------------------------------------------------------------------------


def item_cost(text: str) -> int:
    """Conservative token cost for one rendered block.

    Delegates to the shared estimator; the heuristic chars/4 division is
    rounded up so budget checks never under-count a block. Exact-tokenizer
    counts pass through unchanged.
    """
    tokens = estimate_tokens(text)
    if active_tokenizer_mode() == HEURISTIC_MODE and len(text) % CHARS_PER_TOKEN:
        tokens += 1
    return tokens


# ---------------------------------------------------------------------------
# Stage 1: seed
# ---------------------------------------------------------------------------


def _embeddings_present(conn: sqlite3.Connection) -> bool:
    """Whether the embeddings table holds rows for the current model."""
    try:
        model = embeddings.current_model()
    except RuntimeError:
        return False
    row = conn.execute(
        "SELECT 1 FROM embeddings WHERE model = ? LIMIT 1", (model,)
    ).fetchone()
    return row is not None


def seed_symbols(
    conn: sqlite3.Connection, task: str, limit: int = SEED_LIMIT
) -> List[PackSymbol]:
    """Seed candidate symbols for the task.

    With embeddings present the seed list is a union capped at ``limit``:
    semantic hits head it (rerank pinned off so no env-gated cross-encoder
    ever runs in the pack path), then lexical term-mode matches add
    candidates the semantic leg missed — per-term prefix queries so a
    sentence task still matches symbol-name tokens. Dedup by symbol id
    keeps the first occurrence; without embeddings the single leg is the
    lexical FTS/LIKE search.
    """
    tail: List[Dict[str, Any]] = []
    if _embeddings_present(conn):
        head_limit = min(SEMANTIC_SEED_LIMIT, limit)
        hits: List[Dict[str, Any]] = list(
            semantic_search(conn, task, limit=head_limit, rerank=False)
        )[:head_limit]
        tail = [
            dict(row) for row in search_symbols_terms(conn, task.split(), limit=limit)
        ]
    else:
        hits = [dict(row) for row in search_symbols(conn, task, limit=limit)]
    seeds: List[PackSymbol] = []
    seen: set = set()
    for hit in [*hits, *tail]:
        if len(seeds) >= limit:
            break
        symbol_id = hit.get("id")
        if symbol_id is None or symbol_id in seen:
            continue
        seen.add(symbol_id)
        seeds.append(
            PackSymbol(
                symbol_id=symbol_id,
                name=hit.get("name") or "",
                qualified_name=hit.get("qualified_name") or "",
                kind=hit.get("kind") or "",
                file_path=hit.get("file_path") or "",
                is_seed=True,
            )
        )
    return seeds


# ---------------------------------------------------------------------------
# Stage 2: expand
# ---------------------------------------------------------------------------


def _hop_ids(conn: sqlite3.Connection, seed: PackSymbol) -> List[str]:
    """Precise 1-hop call-edge neighbor ids for one seed, deduped.

    The cap applies per direction; only resolved ids survive — caller rows
    are joined symbol rows and precise callees carry a non-null target id.
    """
    ids: List[str] = []
    directions: Tuple[Tuple[Callable[..., Any], str, Dict[str, Any]], ...] = (
        (get_callers, "caller_id", {"symbol_id": seed.symbol_id}),
        (get_callees, "resolved", {}),
    )
    for fetch, field, extra in directions:
        filled = 0
        for kind in CALL_EDGE_KINDS:
            if filled >= EXPAND_LIMIT:
                break
            rows = fetch(conn, seed.name, limit=EXPAND_LIMIT - filled, kind=kind, **extra)
            filled += len(rows)
            for row in rows:
                neighbor_id = row[field]
                if neighbor_id and neighbor_id not in ids:
                    ids.append(neighbor_id)
    return ids


def expand_symbols(
    conn: sqlite3.Connection, seeds: List[PackSymbol]
) -> List[PackSymbol]:
    """Union seeds with their precise 1-hop call-edge neighbors.

    Neighbors join back to symbol rows and dedup by symbol id keeping the
    first occurrence — a symbol that is both a seed hit and a neighbor stays
    ``is_seed``. Pool order: seed order, then per-seed discovery order.
    """
    pool: List[PackSymbol] = []
    seen: set = set()
    for seed in seeds:
        if seed.symbol_id in seen:
            continue
        seen.add(seed.symbol_id)
        pool.append(seed)
    for seed in seeds:
        for neighbor_id in _hop_ids(conn, seed):
            if neighbor_id in seen:
                continue
            rows = find_definition_by_id(conn, neighbor_id)
            if not rows:
                continue
            row = rows[0]
            seen.add(neighbor_id)
            pool.append(
                PackSymbol(
                    symbol_id=row["id"],
                    name=row["name"],
                    qualified_name=row["qualified_name"] or "",
                    kind=row["kind"],
                    file_path=row["file_path"],
                    is_seed=False,
                )
            )
    return pool


# ---------------------------------------------------------------------------
# Stage 3: rank
# ---------------------------------------------------------------------------


def _closure_centrality(conn: sqlite3.Connection, symbol_id: str) -> int:
    """Reverse-reach count: distinct closure sources reaching one symbol."""
    row = conn.execute(
        "SELECT COUNT(DISTINCT source_id) FROM transitive_edges WHERE target_id = ?",
        (symbol_id,),
    ).fetchone()
    return row[0] or 0


def _structural_indegree(conn: sqlite3.Connection, symbol_id: str) -> int:
    """Fallback weight when the closure is absent: direct in-degree over the
    same structural edge kinds the closure materializes."""
    placeholders = ",".join("?" for _ in STRUCTURAL_EDGE_KINDS)
    row = conn.execute(
        f"SELECT COUNT(*) FROM edges "
        f"WHERE target_id = ? AND kind IN ({placeholders})",
        (symbol_id, *STRUCTURAL_EDGE_KINDS),
    ).fetchone()
    return row[0] or 0


def rank_symbols(
    conn: sqlite3.Connection, pool: List[PackSymbol]
) -> List[PackSymbol]:
    """Order the pool: seeds first (task relevance), then centrality
    descending, ties broken by qualified name — fully deterministic.

    Centrality reads the precomputed transitive closure when built, falling
    back to direct structural in-degree otherwise.
    """
    weight = _closure_centrality if closure_available(conn) else _structural_indegree
    weights = {symbol.symbol_id: weight(conn, symbol.symbol_id) for symbol in pool}
    return sorted(
        pool,
        key=lambda symbol: (
            not symbol.is_seed,
            -weights[symbol.symbol_id],
            symbol.qualified_name,
        ),
    )


# ---------------------------------------------------------------------------
# Stage 4: fit
# ---------------------------------------------------------------------------


def _class_counts(items: Iterable[PackItem]) -> Dict[str, int]:
    """Per-report-class item counts, every class present in fixed order."""
    counts = {cls: 0 for cls in REPORT_CLASSES}
    for item in items:
        counts[KIND_CLASS[item.kind]] += 1
    return counts


def _counts_fragment(counts: Dict[str, int]) -> str:
    return " ".join(f"{cls}={counts[cls]}" for cls in REPORT_CLASSES)


def _symbol_items(conn: sqlite3.Connection, pool: List[PackSymbol]) -> List[PackItem]:
    """One enriched block per pool symbol, in rank order, pre-costed.

    Each block carries the symbol's metadata lines, its trimmed verbatim
    source when the stored span is readable, and its depth-2 precise
    blast-radius line — the symbol block is the unit the fitter admits or
    drops, so source and blast radius never separate.
    """
    items: List[PackItem] = []
    for position, symbol in enumerate(pool, start=1):
        lines = _symbol_lines(symbol, position)
        source = render_source(conn, symbol.symbol_id)
        if source:
            lines.extend(["", source])
        lines.append("")
        lines.append(f"- {blast_radius_line(conn, symbol.qualified_name, symbol.symbol_id)}")
        items.append(PackItem.create(KIND_SOURCE, position, "\n".join(lines)))
    return items


def _reserved_items(
    conn: sqlite3.Connection,
    bundle: "OKFBundle | None",
    pool: List[PackSymbol],
    task: str,
) -> List[PackItem]:
    """Task-level compass and memory sections, pre-costed for reserved-first
    admission.

    Compass excerpts dedup across the pool's file paths, first pool
    occurrence winning; memories are the task's top picks. Sections without
    coverage — or a ``None`` bundle — are omitted, never rendered empty.
    """
    if bundle is None:
        return []
    items: List[PackItem] = []
    excerpts: List[str] = []
    seen: set = set()
    for symbol in pool:
        excerpt = compass_excerpt(bundle, symbol.file_path)
        if excerpt is None or excerpt in seen:
            continue
        seen.add(excerpt)
        excerpts.append(excerpt)
    if excerpts:
        items.append(
            PackItem.create(
                KIND_COMPASS, 0, "\n\n".join(["", "## Compass", *excerpts])
            )
        )
    picks = memory_picks(conn, bundle, task)
    if picks:
        items.append(
            PackItem.create(
                KIND_MEMORY, 0, "\n\n".join(["", "## Memories", *picks])
            )
        )
    return items


def _header_reserve(task: str, budget: int, totals: Dict[str, int]) -> int:
    """Conservative token reserve for the header alone.

    Rendered at budget-width token count and widest per-class counts, the
    reserve never under-counts the header the emitter finally renders.
    """
    lines = _header_lines(
        task, budget, active_tokenizer_mode(), budget, totals, totals
    )
    return item_cost("\n".join(lines) + "\n")


def fit_items(
    items: List[PackItem], budget: int, header_reserve: int
) -> Tuple[List[PackItem], Dict[str, int]]:
    """Fit rendered blocks into the budget past the header reserve.

    Reserved task-level blocks (compass, memory) are admitted first in input
    order, each only if it fits; symbol blocks then admit as a rank-order
    prefix, so the tail — lowest centrality — is what drops. Returns the kept
    items in emit order (ranked, then reserved) and the per-class dropped
    counts.
    """
    remaining = budget - header_reserve
    ranked: List[PackItem] = []
    reserved: List[PackItem] = []
    for item in items:
        if item.kind in RESERVED_KINDS and item.cost <= remaining:
            reserved.append(item)
            remaining -= item.cost
    for item in items:
        if item.kind in RANKED_KINDS:
            if item.cost > remaining:
                break
            ranked.append(item)
            remaining -= item.cost
    kept = ranked + reserved
    total_counts = _class_counts(items)
    kept_counts = _class_counts(kept)
    dropped = {cls: total_counts[cls] - kept_counts[cls] for cls in REPORT_CLASSES}
    return kept, dropped


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _require_task(task: str) -> str:
    """The validated task text; blank input is an input error."""
    if task is None or not task.strip():
        raise ValueError("--task is required: the task description must not be empty")
    return task.strip()


def _require_budget(budget: int) -> int:
    """The validated budget; zero or negative is an input error."""
    if budget is None or budget <= 0:
        raise ValueError(f"--budget must be a positive token count, got {budget}")
    return budget


def build_pack(
    conn: sqlite3.Connection,
    bundle: "OKFBundle | None",
    task: str,
    budget: int,
) -> PackResult:
    """Run the pack pipeline for one task within a token budget.

    Stages run in pipeline order: seed, expand, rank, enrich, fit, emit. A
    ``None`` bundle omits compass and memory enrichment. Blank or
    whitespace-only task text raises ``ValueError`` naming ``--task``; zero
    or negative budget raises ``ValueError`` naming ``--budget``.
    """
    task = _require_task(task)
    budget = _require_budget(budget)
    pool = rank_symbols(conn, expand_symbols(conn, seed_symbols(conn, task)))
    items = _symbol_items(conn, pool) + _reserved_items(conn, bundle, pool, task)
    reserve = _header_reserve(task, budget, _class_counts(items))
    kept, dropped = fit_items(items, budget, reserve)
    return PackResult(
        task=task,
        budget=budget,
        symbols=tuple(pool),
        items=tuple(kept),
        dropped=dropped,
    )


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------


def _header_lines(
    task: str,
    budget: int,
    mode: str,
    tokens: int,
    kept: Dict[str, int],
    dropped: Dict[str, int],
) -> List[str]:
    """The pack header; ``tokens`` is filled in by the accounting loop, and
    ``kept``/``dropped`` carry the per-class item counts."""
    return [
        "# cairn context pack",
        "",
        f"- task: {task}",
        f"- budget: {budget}",
        f"- tokenizer: {mode}",
        f"- tokens: {tokens}",
        f"- kept: {_counts_fragment(kept)}",
        f"- dropped: {_counts_fragment(dropped)}",
    ]


def _symbol_lines(symbol: PackSymbol, position: int) -> List[str]:
    """One symbol section; ``position`` is its 1-based slot in pool order."""
    heading = symbol.qualified_name or symbol.name
    return [
        "",
        f"## {position}. {heading}",
        "",
        f"- kind: {symbol.kind}",
        f"- file: {symbol.file_path}",
        f"- seed: {str(symbol.is_seed).lower()}",
    ]


def render_pack(result: PackResult) -> str:
    """Render the pack as one stdout-safe markdown block: header, then the
    fitted items' sections in emit order.

    The header reports the block's total conservative token cost, header
    included — the count line is part of the text it counts, so the render
    iterates to the fixed point. Plain text throughout.
    """
    mode = active_tokenizer_mode()
    task = " ".join(result.task.split())
    kept = _class_counts(result.items)
    tokens = 0
    while True:
        lines = _header_lines(
            task, result.budget, mode, tokens, kept, result.dropped
        )
        for item in result.items:
            lines.extend(item.text.split("\n"))
        block = "\n".join(lines) + "\n"
        counted = item_cost(block)
        if counted == tokens:
            return block
        tokens = counted
