"""L4 memory MCP tools: recall_memory, record_memory."""
from __future__ import annotations

import logging

from mcp.types import ToolAnnotations

from ._server_core import _append_embed_degradation_footnote, _bundle, _conn, _session_id, mcp
from .metric_buffering import instrument

logger = logging.getLogger(__name__)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def recall_memory(
    query: str,
    tier: str = "",
    include_superseded: bool = False,
    *,
    as_of: str | None = None,
    agent: str | None = None,
) -> str:
    """Search past decisions/patterns/mistakes/workarounds by symbol or title tokens, showing live refs-verified fractions; tier/include_superseded/as_of/agent filter."""
    from cairn.memory.promotion import search_memory
    from cairn.memory.scoring import _graph_verification

    if agent is not None:
        from cairn.memory.store import shared_recall_entries, validate_agent_id

        validate_agent_id(agent)
    bundle = _bundle()
    conn = _conn()
    try:
        results = search_memory(
            conn, bundle, query, tier=tier or None, session_id=_session_id(),
            include_superseded=include_superseded, as_of=as_of,
        )
    except Exception:
        conn.close()
        raise

    # Read-through merge, gated on agent: the default path runs no
    # sharing query.
    shared_by_id: dict = {}
    if agent is not None:
        extra, shared_by_id = shared_recall_entries(
            conn, bundle, agent, query, tier=tier or None,
            include_superseded=include_superseded, as_of=as_of,
            result_ids={c.concept_id for c in results},
        )
        results.extend(extra)

    if not results:
        conn.close()
        return (
            f"No memories matching '{query}'. Nothing was recorded under those "
            f"tokens. Try broader/fewer keywords, a symbol name instead of prose, "
            f"or `cairn memory digest` (via the CLI, no query) to see top tribal "
            f"memories for orientation. If you expected a memory here, it may not "
            f"have been captured -- see the Memory Capture Workflow in the skill."
        )
    out = [f"{len(results)} memories matching '{query}':"]
    # Surface backend quality when any hit used semantic/fused ranking: the
    # hash fallback carries token overlap, not semantic meaning.
    if any(c.extensions.get("provenance") for c in results):
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
            refs_verified: float | str
            try:
                refs_verified = round(_graph_verification(c, conn), 3)
            except Exception:
                refs_verified = "?"
            # Render: distinguish "n/a (0 refs)" from a real fraction.
            if n_refs == 0:
                refs_display = "n/a (0 refs)"
            else:
                refs_display = str(refs_verified)
            # Stale when any cited backtick ref no longer exists (fraction
            # < 1.0); zero-ref memories are n/a, never stale.
            is_stale = (
                n_refs > 0
                and isinstance(refs_verified, (int, float))
                and refs_verified < 1.0
            )
            tag = " [SUPERSEDED]" if superseded else ""
            stale_tag = " [STALE]" if is_stale else ""
            stance = c.extensions.get("memory_stance") or ""
            stance_tag = f", stance={stance}" if stance else ""
            out.append(f"  [{t} {score}, refs-verified={refs_display}{prov_tag}{stance_tag}] {c.title}{tag}{stale_tag}")
            if is_stale:
                out.append("    ^ a cited file/symbol no longer exists in the graph -- verify before relying on this memory")
            peer = c.extensions.get("memory_stance_peer") or ""
            if stance == "contested" and peer:
                out.append(f"    ^ contradicted by {peer} -- verify before relying on this memory")
            if c.description:
                out.append(f"    {c.description}")
            attribution = shared_by_id.get(getattr(c, "concept_id", None))
            if attribution:
                out.append(f"    {attribution}")
        # Footnote: surface the gap when some memories lack embeddings (e.g.
        # after an upgrade, before `cairn memory embed` has run) so the user
        # knows semantic recall is partial. Read-only; never writes.
        from cairn.graph.embeddings import unembedded_memory_hint
        hint = unembedded_memory_hint(conn, bundle)
        if hint:
            out.append(hint)
    finally:
        conn.close()
    return _append_embed_degradation_footnote("\n".join(out))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
@instrument
def record_memory(
    type: str, title: str, body: str, resource: str = "", confidence: float = 0.7,
    stance: str | None = None,
) -> str:
    """Capture a learning (type: decision|pattern|mistake|workaround); Why:/How to apply: body lines make it durable; stance optional."""
    from cairn.memory.promotion import capture_memory
    from . import embed_buffering

    bundle = _bundle()
    conn = _conn()
    try:
        result = capture_memory(
            conn, bundle, type_=type, title=title, body=body,
            resource=resource or None, confidence=confidence, stance=stance,
        )
    finally:
        conn.close()
    embed_buffering.enqueue(result["path"])
    signals = result["signals"]
    superseded = result.get("superseded")
    msg = f"Recorded {type} '{title}' -> {result['path']} (score={signals['score']}, tier={result['tier']})"
    if superseded:
        msg += f" [superseded {superseded}]"
    return msg
