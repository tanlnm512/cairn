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
    # One read-only conn for all verification lookups.
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
def recall_memory(query: str, tier: str = "", include_superseded: bool = False) -> str:
    """Search past decisions, patterns, mistakes, workarounds. Increments refs.

    Each result shows a live-recomputed refs-verified fraction (backtick-quoted
    file/symbol refs in the body that still exist in the graph right now) --
    not just the score cached at write time, which may have gone stale since.
    A low value means the memory may cite a file/symbol that was since
    renamed or removed; verify before relying on it.

    Query by symbol name or title keywords (e.g. "ApiFactory", "backoff"), not
    natural-language prose -- matching is token-based with a semantic fallback.

    By default superseded (revised) memories are hidden -- only the latest
    version of a decision is returned. Set include_superseded=true to audit
    the full revision history (each superseded memory points to its successor).

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
        results = search_memory(
            conn, bundle, query, tier=tier or None, session_id="mcp",
            include_superseded=include_superseded,
        )
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
    # If any hit came via the semantic fallback, surface the backend quality:
    # the hash fallback carries token-overlap signal, not real semantic meaning.
    # One-time warning is enough -- provenance on each line carries it too.
    if any(c.extensions.get("provenance", "").startswith("semantic") for c in results):
        from cairn.graph import embeddings as _emb
        _emb.warn_hash_fallback_once(logger, context="recall_memory")
    # Reuse the already-open conn for verification.
    try:
        for c in results:
            score = c.extensions.get("memory_score", "?")
            t = c.extensions.get("memory_tier", "?")
            # provenance is "" for lexical hits, "semantic" / "semantic (hash
            # backend)" for the semantic fallback. Shown compactly so an agent
            # can see when results are degraded (hash backend = token-overlap).
            prov = c.extensions.get("provenance", "")
            prov_tag = f", {prov}" if prov else ""
            superseded = c.extensions.get("memory_is_latest", True) is False
            # Detect zero-refs separately so we surface "nothing was checked"
            # distinctly from "all refs passed" -- otherwise a prose-only memory
            # shows refs-verified=1.0 and looks fully verified when nothing was.
            from cairn.refs import extract_file_refs, extract_symbol_refs
            body = c.body or ""
            n_refs = len(extract_file_refs(body)) + len(extract_symbol_refs(body))
            try:
                refs_verified = round(_graph_verification(c, conn), 3)
            except Exception:
                refs_verified = "?"
            # Render: distinguish "n/a (0 refs)" from a real fraction.
            if n_refs == 0:
                refs_display = "n/a (0 refs)"
            else:
                refs_display = str(refs_verified)
            # Stale flag: a discrete verdict derived from the fraction. A memory
            # is stale when at least one cited backtick ref no longer exists in
            # the graph (fraction < 1.0). Memories with no backtick refs have
            # nothing to verify (n/a) and are never flagged stale. This is the
            # recall-side analog of the critic gate -- surfacing silent drift
            # loudly. Threshold chosen deliberately: < 1.0 = "any ref stale".
            is_stale = (
                n_refs > 0
                and isinstance(refs_verified, (int, float))
                and refs_verified < 1.0
            )
            tag = " [SUPERSEDED]" if superseded else ""
            stale_tag = " [STALE]" if is_stale else ""
            out.append(f"  [{t} {score}, refs-verified={refs_display}{prov_tag}] {c.title}{tag}{stale_tag}")
            if is_stale:
                out.append("    ^ a cited file/symbol no longer exists in the graph -- verify before relying on this memory")
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
    superseded = result.get("superseded")
    msg = f"Recorded {type} '{title}' -> {result['path']} (score={signals['score']}, tier={result['tier']})"
    if superseded:
        msg += f" [superseded {superseded}]"
    return msg


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
@instrument
def memory_evolve(memory_path: str, title: str = "", body: str = "") -> str:
    """Revise an existing memory by creating a new version that supersedes it.

    The old memory is marked superseded (hidden from recall_memory unless
    include_superseded=true) and its version chain is inherited, so the full
    decision history is preserved. Use this when you know a decision/pattern
    has changed and want to record the revision explicitly rather than letting
    record_memory's automatic near-dup detection handle it.

    At least one of title or body must be provided (and differ from the old).
    """
    from cairn.memory.promotion import evolve_memory

    bundle = _bundle()
    conn = _rw_conn()
    try:
        result = evolve_memory(
            conn, bundle, memory_path,
            new_title=title or None,
            new_body=body or None,
        )
    finally:
        conn.close()
    if result is None:
        return f"Error: could not find memory at '{memory_path}'."
    signals = result["signals"]
    return (
        f"Evolved '{memory_path}' -> {result['path']} "
        f"(score={signals['score']}, tier={result['tier']}, superseded {result['superseded']})"
    )


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
        conn.commit()
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
