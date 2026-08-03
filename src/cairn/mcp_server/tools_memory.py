"""L4 memory MCP tools: recall_memory, record_memory, memory_promote,
memory_demote, memory_delete, memory_decay.

Each builds an OKFBundle via the shared ``_bundle()`` helper.
"""
from __future__ import annotations

import logging
from pathlib import Path

from mcp.types import ToolAnnotations

from ._server_core import _bundle, _conn, _rw_conn, mcp
from .metric_buffering import instrument
from .tools_graph import _clamp

logger = logging.getLogger(__name__)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def memory_digest(limit: int = 10) -> str:
    """Top tribal memories by score -- call this once for session orientation
    before reaching for recall_memory(query), which needs a specific query.

    Each result shows a live-recomputed refs-verified fraction (backtick-quoted
    file/symbol refs in the body that still exist in the graph right now). A low
    value flags a memory citing a file/symbol that was since renamed or removed;
    verify before relying on it.
    """
    from cairn.memory.promotion import tribal_digest
    from cairn.memory.scoring import _graph_verification

    limit = _clamp(limit, 1, 1000)  # bound LLM-supplied value at the boundary
    bundle = _bundle()
    mems = tribal_digest(bundle, limit=limit)
    if not mems:
        return "No tribal memories yet."
    out = [f"Top {len(mems)} tribal memories:"]
    # One read-only conn for all verification lookups (was per-result in recall).
    conn = _conn()
    try:
        for c in mems:
            score = c.extensions.get("memory_score", "?")
            try:
                refs_verified = round(_graph_verification(c, conn), 3)
            except Exception:
                refs_verified = "?"
            out.append(f"  [{score}, refs-verified={refs_verified}] {c.title}")
            if c.description:
                out.append(f"    {c.description}")
    finally:
        conn.close()
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def recall_memory(query: str, tier: str = "") -> str:
    """Search past decisions, patterns, mistakes, workarounds. Increments refs.

    Each result shows a live-recomputed refs-verified fraction (backtick-quoted
    file/symbol refs in the body that still exist in the graph right now) --
    not just the score cached at write time, which may have gone stale since.
    A low value means the memory may cite a file/symbol that was since
    renamed or removed; verify before relying on it.

    Query by symbol name or title keywords (e.g. "ApiFactory", "backoff"), not
    natural-language prose -- matching is token-based with a semantic fallback.

    Example:
        recall_memory("ApiFactory backoff")
        ->  2 memories matching 'ApiFactory backoff':
              [0.78 | refs 3/3] ApiFactory uses per-flavor base URLs
                decision · confidence 0.9
              ...
    """
    from cairn.memory.promotion import search_memory
    from cairn.memory.scoring import _graph_verification

    bundle = _bundle()
    conn = _conn()
    try:
        results = search_memory(conn, bundle, query, tier=tier or None, session_id="mcp")
    except Exception:
        conn.close()
        raise

    if not results:
        conn.close()
        return (
            f"No memories matching '{query}'. Nothing was recorded under those "
            f"tokens. Try broader/fewer keywords, a symbol name instead of prose, "
            f"or memory_digest() (no query) to see top tribal memories for "
            f"orientation. If you expected a memory here, it may not have been "
            f"captured -- see the Memory Capture Workflow in the skill."
        )
    out = [f"{len(results)} memories matching '{query}':"]
    # Reuse the already-open conn for verification (refactored from the old
    # per-result _conn() churn).
    try:
        for c in results:
            score = c.extensions.get("memory_score", "?")
            t = c.extensions.get("memory_tier", "?")
            try:
                refs_verified = round(_graph_verification(c, conn), 3)
            except Exception:
                refs_verified = "?"
            out.append(f"  [{t} {score}, refs-verified={refs_verified}] {c.title}")
            if c.description:
                out.append(f"    {c.description}")
    finally:
        conn.close()
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
@instrument
def record_memory(
    type: str, title: str, body: str, resource: str = "", confidence: float = 0.7
) -> str:
    """Capture a learning. type: decision|pattern|mistake|workaround.

    For decision/mistake/workaround, structure body as: the fact/rule itself,
    then a `Why:` line (the reasoning -- a constraint, incident, or tradeoff
    that led here) and a `How to apply:` line (when this should change future
    behavior). The why is what makes a memory worth surfacing months later;
    a bare fact without it is easy to misapply once the original context is
    forgotten.

    Don't record what's cheaper to re-derive than to recall: facts already
    answerable by explore/find_definition/get_callers, plain git history
    (who changed what -- `git log`/`git blame` are authoritative), or
    ephemeral in-progress state that's only relevant to the current session.
    Every raw capture still costs review/decay cycles even if never promoted.
    """
    from cairn.memory.promotion import capture_memory

    bundle = _bundle()
    conn = _rw_conn()
    try:
        result = capture_memory(
            conn, bundle, type_=type, title=title, body=body,
            resource=resource or None, confidence=confidence,
        )
    finally:
        conn.close()
    signals = result["signals"]
    return f"Recorded {type} '{title}' -> {result['path']} (score={signals['score']}, tier={result['tier']})"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
@instrument
def memory_promote(memory_path: str) -> str:
    """Force-promote a memory to canonical (compass/wiki). Moves it into
    compass/ (decisions/patterns/mistakes/workarounds) or wiki/features/
    (architecture), bypassing the raw→drafts→tribal tiers entirely."""
    from cairn.memory.promotion import promote_memory

    bundle = _bundle()
    conn = _rw_conn()
    try:
        new_id = promote_memory(bundle, memory_path, conn=conn)
    finally:
        conn.close()
    if new_id is None:
        return f"Error: could not find memory at '{memory_path}'."
    return f"Promoted '{memory_path}' -> {new_id}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
@instrument
def memory_demote(memory_path: str, tier: str = "raw") -> str:
    """Demote a memory to a lower tier (tribal→drafts→raw→archived).
    Validates downward-only; rejects promotions via this tool — use
    memory_promote instead."""
    from cairn.memory.store import demote_memory

    bundle = _bundle()
    new_path = demote_memory(bundle, memory_path, target_tier=tier)
    if new_path is None:
        return (
            f"Error: cannot demote '{memory_path}' to '{tier}'. "
            "Target tier must be strictly lower than current tier, or "
            "memory not found."
        )
    return f"Demoted '{memory_path}' -> {new_path} (tier → {tier})"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
@instrument
def memory_delete(memory_path: str) -> str:
    """Permanently delete a memory and its cross-session refs. Irreversible."""
    from cairn.memory.store import delete_memory as dm
    from cairn.memory.store import get_memory

    bundle = _bundle()
    # Scope check: confirm the resolved concept_id stays inside the memory/
    # namespace before deleting. get_memory() has a fallback that can resolve
    # paths outside memory/, so a bare concept_id could otherwise let an LLM
    # client point this tool at a compass/wiki/knowledge doc. Refuse here so
    # the destructive op can't escape its namespace.
    concept = get_memory(bundle, memory_path)
    if concept is not None:
        # read_concept -> _validate_concept_path -> OKFConcept.from_file sets a
        # *resolved* absolute concept_id; normalize to bundle-relative before the
        # namespace prefix check (resolve both sides so symlinked /var vs
        # /private/var roots don't trip relative_to).
        resolved = concept.concept_id
        try:
            resolved = str(Path(resolved).resolve().relative_to(Path(bundle.root).resolve()))
        except ValueError:
            pass
        if not (resolved == "memory/" or resolved.startswith("memory/")):
            logger.warning(
                "memory_delete refused out-of-namespace target: requested=%r resolved=%r",
                memory_path, resolved,
            )
            return (
                f"Refused: '{memory_path}' resolves outside the memory/ namespace "
                f"(resolved to '{resolved}'). memory_delete only removes memories."
            )
    conn = _rw_conn()
    try:
        ok = dm(bundle, memory_path, conn=conn)
    finally:
        conn.close()
    if not ok:
        return f"Memory not found: '{memory_path}'."
    logger.warning("memory_delete: deleted memory concept_id=%r", concept.concept_id if concept else memory_path)
    return f"Deleted memory: '{memory_path}'."


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
@instrument
def memory_decay(raw_max_days: int = 7, tribal_max_stale: int = 90) -> str:
    """Run time-based memory archival. Expires raw memories older than
    raw_max_days; archives tribal memories older than tribal_max_stale days.
    Returns counts of expired/archived memories."""
    from cairn.memory.promotion import decay

    # Bound LLM-supplied values at the boundary (cap at ~10 years).
    raw_max_days = _clamp(raw_max_days, 1, 3650)
    tribal_max_stale = _clamp(tribal_max_stale, 1, 3650)
    bundle = _bundle()
    result = decay(bundle, raw_max_days=raw_max_days, tribal_max_stale=tribal_max_stale)
    return (
        f"Decay complete: {result['expired_raw']} raw expired, "
        f"{result['archived_tribal']} tribal archived."
    )
