"""Memory promotion, critic, decay, and search."""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..okf.bundle import OKFBundle
from ..okf.concept import OKFConcept
from ..graph import BASE_STOP_WORDS, simple_tokenize
from . import store as store_mod
from .scoring import apply_score, score_memory


def capture_memory(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    type_: str,
    title: str,
    body: str,
    resource: Optional[str] = None,
    confidence: float = 0.7,
    session_origin: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> Dict:
    """Create, score, and store a new memory in one step.

    Shared by the CLI (`cg memory record`/`capture`) and the MCP
    `record_memory` tool so the create -> score -> tier -> store sequence
    lives in exactly one place.
    """
    concept = store_mod.create_memory(
        type_=type_,
        title=title,
        body=body,
        resource=resource,
        confidence=confidence,
        session_origin=session_origin,
        tags=tags,
    )
    signals = score_memory(concept, conn, bundle)
    apply_score(concept, signals)
    tier = store_mod.tier_for_score(signals["score"])
    path = store_mod.store_memory(concept, bundle, tier=tier)
    return {"path": path, "tier": tier, "signals": signals, "concept": concept}


def record_reference(
    conn: sqlite3.Connection, memory_path: str, session_id: str, context: str = ""
):
    """Record that a session referenced a memory (increments cross_session_refs).

    Best-effort: ref-counts are analytics, not correctness. A "database is
    locked" error (common when `cg serve` processes hold the WAL) must never
    break a read -- we swallow it and move on.
    """
    import uuid

    try:
        conn.execute(
            "INSERT INTO memory_refs (id, memory_path, session_id, referenced_at, context) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                memory_path,
                session_id,
                datetime.now(timezone.utc).isoformat(),
                context,
            ),
        )
        conn.commit()
    except sqlite3.OperationalError:
        # Lock contention or read-only connection -- ref counting is analytics.
        pass


def record_references_batch(
    conn: sqlite3.Connection, refs: list, session_id: str
):
    """Insert N memory_refs in ONE transaction (best-effort).

    Batching into one write transaction avoids acquiring the SQLite write lock
    N times, a primary cause of "database is locked" under concurrent servers.

    Args:
        refs: list of (memory_path, context) tuples.
    """
    if not refs:
        return
    import uuid

    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (uuid.uuid4().hex, memory_path, session_id, now, context)
        for memory_path, context in refs
    ]
    try:
        conn.executemany(
            "INSERT INTO memory_refs (id, memory_path, session_id, referenced_at, context) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    except sqlite3.OperationalError:
        # Lock contention or read-only connection -- ref counting is analytics.
        pass


def _lexical_memory_match(concepts, query):
    """Score memory concepts by multi-token keyword overlap against the query.

    Tokenizes the query (stop-filtered) and counts how many tokens appear in each
    concept's title + description + body. Returns the top-scoped concepts. This
    recovers matches that the substring-based bundle.search misses when the query
    tokens are spread across fields (e.g. "backoff retry policy").
    """
    tokens = [t for t in simple_tokenize(query) if t not in BASE_STOP_WORDS]
    if not tokens:
        return []
    scored = []
    for c in concepts:
        hay = f"{c.title} {c.description} {c.body}".lower()
        hits = sum(1 for t in tokens if t in hay)
        if hits:
            scored.append((hits, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored]


def search_memory(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    query: str,
    tier: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[OKFConcept]:
    """Search tribal + canonical memories. Records a reference for each result.

    When the lexical substring search (bundle.search) returns nothing, falls
    back to a semantic scan over the memory corpus -- this recovers conceptual
    matches the lexical layer cannot reach (e.g. querying "messaging" finds a
    memory titled "EventBus vs Flow"). The fallback is additive and silent:
    if the semantic extra isn't installed, behavior is unchanged.
    """
    results = bundle.search(query, limit=20)
    # Filter to memory concepts only.
    results = [
        c
        for c in results
        if c.concept_id.startswith("memory/") or c.extensions.get("memory_tier")
    ]
    if tier:
        results = [c for c in results if c.extensions.get("memory_tier", "").startswith(tier)]

    # Lexical broaden: if initial results are thin (empty or very few), run a
    # multi-token keyword scan over all memory concepts. This recovers matches
    # where query tokens are spread across fields (e.g. "backoff retry policy"
    # when the memory title starts with a different token).
    if len(results) <= 2:
        all_mem = [
            c
            for cid in bundle.list_concepts(prefix="memory/")
            if (c := bundle.read_concept(cid)) is not None
        ]
        lexical = _lexical_memory_match(all_mem, query)
        seen = {c.concept_id for c in results}
        for c in lexical:
            if c.concept_id not in seen:
                results.append(c)
                seen.add(c.concept_id)

    # Semantic fallback when lexical search comes up empty. Embeds the query
    # and cosine-ranks every memory concept by its title+description+body.
    if not results:
        results = _semantic_memory_fallback(conn, bundle, query, tier=tier)

    # Record references for tribal/canonical (not raw/drafts) in ONE batched
    # transaction instead of one write per result. Batching avoids N
    # write-lock acquisitions for N tribal hits, a primary cause of
    # "database is locked" under concurrent `cg serve` processes.
    if session_id:
        refs = [
            (c.concept_id, query)
            for c in results
            if c.extensions.get("memory_tier") in ("tribal",)
        ]
        record_references_batch(conn, refs, session_id)
    return results


def _semantic_memory_fallback(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    query: str,
    tier: Optional[str] = None,
    limit: int = 20,
) -> List[OKFConcept]:
    """Semantic recall over memory concepts when lexical search misses.

    Embeds the query with the configured backend and cosine-ranks every memory
    concept (by its title + description + body text). Returns the top matches.
    Silently returns [] if the semantic backend isn't available, so callers
    degrade to the empty-result path without crashing.
    """
    try:
        from codegraph.graph import embeddings as emb
        from codegraph.retrieval import cosine_scan

        if not emb.embeddings_available():
            return []
        concepts = [
            c
            for c in (bundle.read_concept(cid) for cid in bundle.list_concepts())
            if c is not None
            and (
                c.concept_id.startswith("memory/")
                or c.extensions.get("memory_tier")
            )
        ]
        if tier:
            concepts = [
                c for c in concepts
                if c.extensions.get("memory_tier", "").startswith(tier)
            ]
        if not concepts:
            return []
        texts = [
            " ".join(filter(None, [c.title, c.description, c.body]))
            for c in concepts
        ]
        # Embed the query + every memory text with the SAME backend so the
        # dimensions and embedding space line up. _embed returns float32 BLOBs.
        q_blob, dim = emb.embed_query(query)
        mem_blobs, _ = emb._embed(texts)

        # Unified cosine scan over the shared vector_math helpers.
        rows = [
            (blob if isinstance(blob, bytes) else bytes(blob), len(blob) // 4, concept)
            for concept, blob in zip(concepts, mem_blobs)
        ]
        # Mild threshold: this is a fallback, so we surface candidates even when
        # similarity is modest. Below 0.1 the match is essentially noise.
        scored = cosine_scan(q_blob, dim, rows, threshold=0.1)
        out = [c for s, c in scored[:limit]]
        # Stamp provenance so callers know these are semantic, not lexical.
        for c in out:
            c.extensions["provenance"] = "semantic"
        return out
    except Exception:
        return []  # never let the fallback break recall_memory


def promote_memory(bundle: OKFBundle, memory_path: str, conn=None) -> Optional[str]:
    """Force-promote a memory to canonical (compass or wiki).

    Moves the file from its tier dir into compass/ (for decisions/patterns) or
    wiki/ (for architecture). Returns the new concept_id or None on failure.
    """
    concept = store_mod.get_memory(bundle, memory_path)
    if concept is None:
        return None
    mtype = concept.extensions.get("memory_type", "decision")
    # Decisions/patterns/mistakes -> compass; architecture-ish -> wiki.
    if mtype in ("decision", "pattern", "mistake", "workaround"):
        new_type = "Compass"
        new_id = f"compass/promoted-{store_mod.slugify(concept.title)}"
    else:
        new_type = "Wiki-Feature"
        new_id = f"wiki/features/promoted-{store_mod.slugify(concept.title)}"
    concept.type = new_type
    concept.extensions["memory_status"] = "canonical"
    # Clear the tier label: this concept has left the memory tier system
    # entirely, and search_memory()/router.py filter on truthy memory_tier
    # to decide whether a result is a memory hit -- a stale "tribal"/"raw"
    # label here would make a promoted, canonical concept keep showing up
    # in recall_memory output as if it were still an unpromoted memory.
    concept.extensions.pop("memory_tier", None)
    old_id = concept.concept_id
    concept.concept_id = new_id
    # Append the promotion-history entry BEFORE writing so the new file is
    # written exactly once with history included (avoids a crash window
    # between unlinking the old file and the rewrite). _append_promotion only
    # mutates the in-memory concept's promotion_history extension, so calling
    # it before the write is safe and correct.
    _append_promotion(concept, "force_promote", concept.extensions.get("memory_score", 0.0))
    # Write the new file (with history) first...
    bundle.write_concept(concept)
    # ...and only once the new file is safely on disk, remove the old one.
    old_file = Path(bundle.root) / f"{old_id}.md"
    if old_file.exists():
        old_file.unlink()
    return new_id


def batch_critic(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    llm_critic=None,
) -> Dict:
    """Process all draft memories through the critic. Promote, drop, or leave."""
    from .store_protocol import Decision

    drafts = store_mod.list_memories(bundle, tier="drafts")
    promoted = 0
    dropped = 0
    tribal = 0
    for concept in drafts:
        # Compute critic score: LLM if available, else a neutral default (0.5).
        # compute_score has no None handling (it does WEIGHTS["critic_score"]
        # * signals["critic_score"]), so a float is required, and 0.5 matches
        # the neutral default score_memory uses when critic_score is absent.
        # This contributes a neutral 0.20*0.5 = 0.10, neither inflating nor
        # deflating.
        signals = score_memory(concept, conn, bundle)
        critic = llm_critic(concept) if llm_critic else 0.5
        signals["critic_score"] = critic
        signals["score"] = _rescore_with_critic(signals, critic)
        apply_score(concept, signals)
        # Named decision (Graphiti pattern): the threshold branches below map
        # to an explicit Decision enum so promotion_history records *which*
        # decision was reached, not just a freeform verb.
        new_tier = store_mod.tier_for_score(signals["score"])
        old_id = concept.concept_id  # capture before re-tier for cleanup
        if signals["score"] < 0.3:
            decision = Decision.ARCHIVE
            store_mod.store_memory(concept, bundle, tier="archived", old_id=old_id)
            dropped += 1
        elif new_tier == "tribal":
            decision = Decision.PROMOTE
            store_mod.store_memory(concept, bundle, tier="tribal", old_id=old_id)
            tribal += 1
        else:
            decision = Decision.KEEP_DRAFT
            store_mod.store_memory(concept, bundle, tier="drafts", old_id=old_id)
            promoted += 1  # remains a candidate
        _append_promotion(concept, decision, signals["score"])
    return {"processed": len(drafts), "tribal": tribal, "dropped": dropped, "remaining_drafts": promoted}


def decay(bundle: OKFBundle, raw_max_days: int = 7, tribal_max_stale: int = 90) -> Dict:
    """Expire raw memories older than raw_max_days; archive tribal past staleness."""
    now = datetime.now(timezone.utc)
    expired = 0
    archived = 0
    for concept in store_mod.list_memories(bundle, tier="raw"):
        ts = concept.timestamp
        if ts and _age_days(ts) > raw_max_days:
            old_id = concept.concept_id
            store_mod.store_memory(concept, bundle, tier="archived", old_id=old_id)
            expired += 1
    for concept in store_mod.list_memories(bundle, tier="tribal"):
        staleness = concept.extensions.get("memory_signals", {}).get("staleness_days", 0)
        ts = concept.timestamp
        age = _age_days(ts) if ts else 0
        if age > tribal_max_stale:
            old_id = concept.concept_id
            store_mod.store_memory(concept, bundle, tier="archived", old_id=old_id)
            archived += 1
    return {"expired_raw": expired, "archived_tribal": archived}


def tribal_digest(bundle: OKFBundle, limit: int = 10) -> List[OKFConcept]:
    """Top tribal memories by score, for a quick session-orientation digest.

    Reads tribal memories directly via list_memories() rather than through
    search_memory() (which requires a query) or the router.py/knowledge/
    search.py search mirrors -- this answers "what's worth knowing before I
    start", not "find X", so it deliberately doesn't go through any of the
    three divergent memory-search implementations.
    """
    mems = store_mod.list_memories(bundle, tier="tribal")
    mems.sort(key=lambda c: c.extensions.get("memory_score", 0), reverse=True)
    return mems[:limit]


def memory_stats(bundle: OKFBundle) -> Dict:
    """Count memories by tier and type, with average scores."""
    stats: Dict[str, Dict] = {}
    for tier in store_mod.TIERS:
        mems = store_mod.list_memories(bundle, tier=tier)
        scores = [m.extensions.get("memory_score", 0) for m in mems]
        avg = sum(scores) / len(scores) if scores else 0
        stats[tier] = {"count": len(mems), "avg_score": round(avg, 3)}
    return stats


# --- helpers -------------------------------------------------------------

def _age_days(ts: str) -> float:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0


def _rescore_with_critic(signals: Dict, critic: float) -> float:
    from .scoring import compute_score

    signals = {**signals, "critic_score": critic}
    return compute_score(signals)


def _append_promotion(concept: OKFConcept, action, score: float):
    """Append a record to ``promotion_history``.

    ``action`` may be a :class:`~codegraph.memory.store_protocol.Decision`
    (the named lifecycle enum) or a freeform string. The stable string value
    is persisted either way, so existing readers and serialized OKF files
    keep working.
    """
    # Accept Decision enums transparently; fall back to str for other callers.
    action_str = action.value if hasattr(action, "value") else str(action)
    hist = concept.extensions.setdefault("promotion_history", [])
    hist.append(
        {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "action": action_str,
            "score": round(score, 3),
        }
    )
