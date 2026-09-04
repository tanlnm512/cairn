"""L4 memory MCP tools: recall_memory, record_memory.

Each builds an OKFBundle via the shared ``_bundle()`` helper. Memory lifecycle
operations (digest, evolve, promote, demote, forget, decay) are CLI-only:
``cairn memory <verb>``.
"""
from __future__ import annotations

import logging

from mcp.types import ToolAnnotations

from ._server_core import _append_embed_degradation_footnote, _bundle, _conn, _session_id, mcp
from .metric_buffering import instrument

logger = logging.getLogger(__name__)


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
            conn, bundle, query, tier=tier or None, session_id=_session_id(),
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
            f"or `cairn memory digest` (via the CLI, no query) to see top tribal "
            f"memories for orientation. If you expected a memory here, it may not "
            f"have been captured -- see the Memory Capture Workflow in the skill."
        )
    out = [f"{len(results)} memories matching '{query}':"]
    # If any hit involved the semantic/fused ranking, surface the backend
    # quality: the hash fallback carries token-overlap signal, not real
    # semantic meaning. One-time warning is enough -- provenance on each line
    # carries it too.
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
    from . import embed_buffering

    bundle = _bundle()
    conn = _conn()
    try:
        result = capture_memory(
            conn, bundle, type_=type, title=title, body=body,
            resource=resource or None, confidence=confidence,
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
