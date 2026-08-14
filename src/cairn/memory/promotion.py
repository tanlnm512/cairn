"""Memory promotion, critic, decay, and search."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..okf.bundle import OKFBundle
from ..okf.concept import OKFConcept
from ..graph import BASE_STOP_WORDS, simple_tokenize
from ..graph import note_contention
from . import store as store_mod
from .scoring import DEFAULT_CRITIC_SCORE, apply_score, score_memory

logger = logging.getLogger(__name__)


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
    supersedes_threshold: float = 0.85,
) -> Dict:
    """Create, score, and store a new memory in one step.

    Shared by the CLI (`cairn memory record`/`capture`) and the MCP
    `record_memory` tool. Before storing, the new memory is compared against
    existing ``memory_is_latest`` memories of the same type via semantic
    cosine similarity; if a match exceeds ``supersedes_threshold``, the new
    memory supersedes the old one (chains the version history and flips the
    old to ``memory_is_latest: false``). Returns ``superseded`` in the result.

    The body AND title are redacted via :func:`strip_private_data` before
    scoring and storage, so secrets (API keys, bearer tokens, connection
    strings, ``<private>`` tags) never reach disk regardless of which caller
    reached this function. The title matters as much as the body: it is
    persisted verbatim, duplicated into the concept description, and
    slugified into the concept_id/filename (audit F3). The hook path already
    redacts before calling here; this is the floor for every other caller
    (the MCP ``record_memory`` tool, the CLI).
    """
    from .privacy import strip_private_data

    body = strip_private_data(body)
    title = strip_private_data(title)
    with bundle.lock():
        superseded_id = _find_supersession_candidate(
            conn, bundle, type_, title, body, supersedes_threshold
        )

        supersedes_chain: list[str] = []
        if superseded_id:
            # Inherit the old version chain so memory_supersedes is the full history.
            old = store_mod.get_memory(bundle, superseded_id)
            if old is not None:
                norm_id = _norm_cid(bundle, superseded_id)
                old_chain = [_norm_cid(bundle, cid) for cid in (old.extensions.get("memory_supersedes") or [])]
                supersedes_chain = [norm_id] + old_chain

        concept = store_mod.create_memory(
            type_=type_,
            title=title,
            body=body,
            resource=resource,
            confidence=confidence,
            session_origin=session_origin,
            tags=tags,
            supersedes=supersedes_chain or None,
        )
        signals = score_memory(concept, conn, bundle)
        apply_score(concept, signals)
        tier = store_mod.tier_for_score(signals["score"])
        path = store_mod.store_memory(concept, bundle, tier=tier)

        # Flip the old memory to is_latest=false AFTER the new one is safely on disk.
        if superseded_id:
            _mark_superseded(bundle, superseded_id, path)
            _append_promotion(concept, "supersede", signals["score"])

    return {
        "path": path,
        "tier": tier,
        "signals": signals,
        "concept": concept,
        "superseded": superseded_id,
    }


def _sanitize_ref_context(context: str) -> str:
    """Redact + hard-truncate a ref context before it reaches memory_refs.

    The ``context`` column stores the raw query that surfaced a memory
    (search_memory passes its ``query`` verbatim, audit F10); queries can
    quote secrets (a pasted connection string, an API key being searched
    for). :func:`strip_private_data` handles the known secret shapes; the
    200-char cap bounds anything the regex floor misses. Refs are analytics,
    not correctness -- a truncated context loses nothing.
    """
    from .privacy import strip_private_data

    return strip_private_data(context or "")[:_MAX_REF_CONTEXT_CHARS]


# Hard cap on the memory_refs.context column (analytics; see _sanitize_ref_context).
_MAX_REF_CONTEXT_CHARS = 200


def record_reference(
    conn: sqlite3.Connection, memory_path: str, session_id: str, context: str = ""
):
    """Record that a session referenced a memory (increments cross_session_refs).

    The context is redacted + truncated via ``_sanitize_ref_context`` before
    it is persisted. Best-effort: ref-counts are analytics, not correctness,
    so lock errors are swallowed.
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
                _sanitize_ref_context(context),
            ),
        )
        conn.commit()
    except sqlite3.OperationalError:
        note_contention("promotion.record_reference")
        # Lock contention or read-only connection -- ref counting is analytics.
        pass


def record_references_batch(
    conn: sqlite3.Connection, refs: list, session_id: str
):
    """Insert N memory_refs in ONE transaction (best-effort).

    ``refs`` is a list of (memory_path, context) tuples. Contexts are
    redacted + truncated via ``_sanitize_ref_context`` before persisting
    (audit F10). Batching avoids acquiring the SQLite write lock N times
    under concurrent servers.
    """
    if not refs:
        return
    import uuid

    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (uuid.uuid4().hex, memory_path, session_id, now, _sanitize_ref_context(context))
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
        note_contention("promotion.record_references_batch")
        # Lock contention or read-only connection -- ref counting is analytics.
        pass


def _lexical_memory_match(concepts, query):
    """Score memory concepts by multi-token keyword overlap against the query.

    Tokenizes the query (stop-filtered) and counts how many tokens appear in each
    concept's title + description + body. Recovers matches the substring-based
    bundle.search misses when query tokens are spread across fields.
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
    include_superseded: bool = False,
) -> List[OKFConcept]:
    """Search tribal + canonical memories via fused lexical + semantic ranking.

    Runs a lexical scan (substring + multi-token broaden) and a semantic scan
    (cosine over persisted memory_embeddings) and fuses both ranked lists with
    RRF (same technique as the code-search hybrid), so a semantically related
    memory with no shared keywords can surface instead of only being tried
    once lexical comes up completely empty. Semantic degrades to a no-op
    (pure lexical results) until at least one memory has been embedded --
    embedding happens out-of-band at capture/evolve time, not here. Superseded
    memories (``memory_is_latest: false``) are filtered out by default; pass
    ``include_superseded=True`` to traverse the version chain.
    """
    def _visible(c: OKFConcept) -> bool:
        if tier and not c.extensions.get("memory_tier", "").startswith(tier):
            return False
        if not include_superseded and c.extensions.get("memory_is_latest", True) is False:
            return False
        return True

    lexical_hits = [
        c for c in bundle.search(query, limit=20)
        if (c.concept_id.startswith("memory/") or c.extensions.get("memory_tier")) and _visible(c)
    ]
    resolved: Dict[str, OKFConcept] = {c.concept_id: c for c in lexical_hits}

    # Lexical broaden: only pay the cost of reading every memory concept from
    # disk when substring search alone came up thin.
    if len(lexical_hits) <= 2:
        all_mem = [
            c for cid in bundle.list_concepts(prefix="memory/")
            if (c := bundle.read_concept(cid)) is not None and _visible(c)
        ]
        for c in all_mem:
            resolved.setdefault(c.concept_id, c)
        seen = {c.concept_id for c in lexical_hits}
        for c in _lexical_memory_match(all_mem, query):
            if c.concept_id not in seen:
                lexical_hits.append(c)
                seen.add(c.concept_id)

    semantic_hits = _semantic_memory_search(
        conn, bundle, query, tier=tier, include_superseded=include_superseded
    )
    # Overwrite (not setdefault): the semantic object already carries the
    # provenance stamp that needs to survive into the returned result, so it
    # must win over the provenance-less lexical object for any id in both.
    for c in semantic_hits:
        resolved[c.concept_id] = c

    lexical_ids = [c.concept_id for c in lexical_hits]
    semantic_ids = [c.concept_id for c in semantic_hits]

    if semantic_ids:
        from cairn.graph import rrf_fuse

        # _semantic_memory_search already stamped "semantic"/"semantic (hash
        # backend)" on its own hits; upgrade any hit found BOTH ways to
        # "fused"/"fused (hash backend)" -- mirrors the code-search hybrid's
        # bm25/semantic/fused labeling. Lexical-only hits stay unstamped.
        lexical_id_set = set(lexical_ids)
        for c in semantic_hits:
            if c.concept_id in lexical_id_set:
                c.extensions["provenance"] = c.extensions["provenance"].replace("semantic", "fused")

        fused = rrf_fuse([lexical_ids, semantic_ids], k=60)
        results = [resolved[cid] for cid, _score in fused if cid in resolved]
    else:
        results = lexical_hits

    # Record references for tribal/canonical (not raw/drafts) in ONE batched
    # transaction instead of one write per result, to avoid N write-lock
    # acquisitions under concurrent `cairn serve` processes.
    if session_id:
        refs = [
            (c.concept_id, query)
            for c in results
            if c.extensions.get("memory_tier") in ("tribal",)
        ]
        record_references_batch(conn, refs, session_id)
    return results


def _semantic_memory_search(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    query: str,
    tier: Optional[str] = None,
    limit: int = 20,
    include_superseded: bool = False,
) -> List[OKFConcept]:
    """Cosine scan over persisted memory_embeddings, ranked by best-matching chunk.

    Returns [] if nothing has been embedded yet (fresh install, or before a
    backfill has run) or on any error -- never raises, mirroring
    knowledge/search.py's _semantic_search. A memory can have multiple
    embedded chunks (see chunk_memory_body); this dedupes to one entry per
    concept_id, keeping its single best-scoring chunk's rank.
    """
    try:
        from cairn.graph import embeddings as emb
        from cairn.retrieval import cosine_scan

        if emb.embed_memory_count(conn) == 0:
            return []
        model = emb.current_model(corpus="memory")
        q_blob, q_dim = emb.embed_query(query)
        rows = conn.execute(
            "SELECT doc_id, vec, dim FROM memory_embeddings WHERE model = ?",
            (model,),
        ).fetchall()
        triples = [(r["vec"], r["dim"], r["doc_id"]) for r in rows]
        # Deliberately brute-force: the memory corpus is small and curated, so a
        # full-table cosine scan is sub-millisecond and not worth a vec0 index.
        # (graph/ann_index.py's ANN path covers only the code-corpus embeddings
        # table; see its module docstring for when to extend it here.)
        scored = cosine_scan(q_blob, q_dim, triples, threshold=0.1)

        # Stamp provenance so callers know these are semantic, not lexical;
        # search_memory upgrades this to "fused" for hits found both ways.
        prov = "semantic (hash backend)" if emb.is_hash_fallback() else "semantic"

        seen_ids: set = set()
        out = []
        for _score, doc_id in scored:
            if doc_id in seen_ids:
                continue  # keep only the best-scoring chunk per concept
            seen_ids.add(doc_id)
            try:
                concept = bundle.read_concept(doc_id)
            except Exception:
                continue  # row orphaned by a since-moved/deleted memory
            if tier and not concept.extensions.get("memory_tier", "").startswith(tier):
                continue
            if not include_superseded and concept.extensions.get("memory_is_latest", True) is False:
                continue
            concept.extensions["provenance"] = prov
            out.append(concept)
            if len(out) >= limit:
                break
        return out
    except Exception:
        # Never let semantic ranking break recall_memory, but leave a debug
        # breadcrumb (schema drift / malformed blob shouldn't vanish silently).
        logger.debug("semantic memory search failed; returning []", exc_info=True)
        return []  # never let semantic ranking break recall_memory


def promote_memory(bundle: OKFBundle, memory_path: str, conn=None) -> Optional[str]:
    """Force-promote a memory to canonical (compass or wiki).

    Moves the file from its tier dir into compass/ (for decisions/patterns) or
    wiki/ (for the architecture). Returns the new concept_id or None on failure.

    If ``conn`` is provided, the memory's persisted embedding row is renamed
    from the old concept_id to the new one in place (content is unchanged by a
    promote), avoiding a re-embed of identical text. The caller is responsible
    for committing/owning the transaction; pass ``conn=None`` to skip (in which
    case the caller should enqueue a fresh embed at the new id).
    """
    with bundle.lock():
        concept = store_mod.get_memory(bundle, memory_path)
        if concept is None:
            return None
        mtype = concept.extensions.get("memory_type", "decision")
        # UUID suffix avoids same-title collisions; safe since callers always
        # use the returned concept_id rather than reconstructing this slug.
        import uuid
        unique_suffix = uuid.uuid4().hex[:6]
        # Decisions/patterns/mistakes -> compass; architecture-ish -> wiki.
        if mtype in ("decision", "pattern", "mistake", "workaround"):
            new_type = "Compass"
            new_id = f"compass/promoted-{store_mod.slugify(concept.title or '')}-{unique_suffix}"
        else:
            new_type = "Wiki-Feature"
            new_id = f"wiki/features/promoted-{store_mod.slugify(concept.title or '')}-{unique_suffix}"
        concept.type = new_type
        concept.extensions["memory_status"] = "canonical"
        # Clear the tier label: search_memory()/router.py filter on truthy
        # memory_tier to decide whether a result is a memory hit, so a stale
        # label here would make a promoted, canonical concept keep showing up as
        # an unpromoted memory.
        concept.extensions.pop("memory_tier", None)
        old_id = concept.concept_id
        # from_file leaves concept_id as an ABSOLUTE path, but embedding doc_ids
        # are stored relative (they originate from store_memory's return value).
        # Normalize so the embedding rename below matches the persisted row.
        try:
            old_id = str(Path(old_id).relative_to(bundle.root))
        except ValueError:
            pass  # already relative, or escapes root -- keep as-is
        concept.concept_id = new_id
        # Append the promotion-history entry BEFORE writing so the new file is
        # written exactly once with history included (no crash window between
        # unlinking the old file and the rewrite).
        _append_promotion(concept, "force_promote", concept.extensions.get("memory_score", 0.0))
        # Write the new file (with history) first...
        bundle.write_concept(concept)
        # Carry the embedding forward in place instead of orphaning it (a
        # promote never changes content, so re-embedding would be wasted work).
        # Import via the cairn.graph public surface (not the internal submodule)
        # per the layering rule enforced by test_layer_direction.
        if conn is not None:
            from cairn.graph import embeddings as _emb
            _emb.rename_memory_embedding(conn, old_id, new_id)  # caller commits
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
        # Compute critic score: LLM if available, else a neutral default.
        # DEFAULT_CRITIC_SCORE is shared with score_memory() so both code paths
        # agree on the neutral value; a float is required (compute_score has no
        # None handling).
        signals = score_memory(concept, conn, bundle)
        critic = llm_critic(concept) if llm_critic else DEFAULT_CRITIC_SCORE
        signals["critic_score"] = critic
        signals["score"] = _rescore_with_critic(signals, critic)
        apply_score(concept, signals)
        # Map the threshold branches to an explicit Decision enum so
        # promotion_history records which decision was reached.
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


def decay(bundle: OKFBundle, raw_max_days: int = 7, tribal_max_stale: int = 90, conn=None) -> Dict:
    """Expire raw memories older than raw_max_days; archive tribal past staleness.

    If ``conn`` is provided, also reap embedding rows orphaned by the tier moves
    (a decay moves a memory to a new concept_id, leaving its embedding row
    stranded at the old address). Reap is best-effort and also cleans orphans
    left by other paths; it never fails decay.
    """
    expired = 0
    archived = 0
    with bundle.lock():
        for concept in store_mod.list_memories(bundle, tier="raw"):
            ts = concept.timestamp
            if ts and _age_days(ts) > raw_max_days:
                old_id = concept.concept_id
                store_mod.store_memory(concept, bundle, tier="archived", old_id=old_id)
                expired += 1
        for concept in store_mod.list_memories(bundle, tier="tribal"):
            ts = concept.timestamp
            age = _age_days(ts) if ts else 0
            if age > tribal_max_stale:
                old_id = concept.concept_id
                store_mod.store_memory(concept, bundle, tier="archived", old_id=old_id)
                archived += 1
    reaped = 0
    if conn is not None and (expired or archived):
        # The moves above orphan embedding rows at the old concept_ids; clean
        # them now rather than letting dead vectors accumulate in the table
        # (memory search is a brute-force cosine scan, so orphans tax every
        # recall). Reap also catches orphans from other paths.
        from cairn.graph import embeddings as _emb
        try:
            reaped = _emb.reap_orphaned_memory_embeddings(conn, bundle)
        except Exception:
            logger.debug("memory embed reap during decay failed", exc_info=True)
            reaped = 0
    return {"expired_raw": expired, "archived_tribal": archived, "reaped_embeddings": reaped}


def tribal_digest(bundle: OKFBundle, limit: int = 10) -> List[OKFConcept]:
    """Top tribal memories by score, for a quick session-orientation digest.

    Reads tribal memories directly via list_memories() rather than the
    query-based search paths -- this answers "what's worth knowing before I
    start", not "find X".
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


# --- supersession helpers ------------------------------------------------


def _norm_cid(bundle: OKFBundle, concept_id: str) -> str:
    """Normalize an (possibly absolute) concept_id to bundle-relative.

    OKFConcept.from_file sets concept_id to an absolute path; the supersession
    chain should store relative ids so it survives a workspace move.
    """
    try:
        return str(Path(concept_id).resolve().relative_to(Path(bundle.root).resolve()))
    except (ValueError, TypeError):
        return concept_id


def _find_supersession_candidate(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    type_: str,
    title: str,
    body: str,
    threshold: float = 0.85,
) -> Optional[str]:
    """Find the best existing memory that the new one supersedes.

    Two-tier check: (1) cheap blocking by same ``type`` + ``memory_is_latest``;
    (2) quick exact title match -> immediate supersession; otherwise (3) embed
    the new text + each candidate and take the top cosine, superseding if >=
    threshold. Returns the candidate concept_id, or None.
    """
    candidates: list[OKFConcept] = []
    for cid in bundle.list_concepts(prefix="memory/"):
        c = bundle.read_concept(cid)
        if c is None:
            continue
        if c.extensions.get("memory_type") != type_:
            continue
        if c.extensions.get("memory_is_latest", True) is False:
            continue
        candidates.append(c)
    if not candidates:
        return None

    # Tier 1: exact title match (case-insensitive) — strongest lexical signal.
    title_lower = (title or "").strip().lower()
    for c in candidates:
        if (c.title or "").strip().lower() == title_lower and title_lower:
            return c.concept_id

    # Tier 2: semantic cosine. Reuses the same backend as search_memory's
    # semantic fallback so dimensions line up. Silently returns None if the
    # embedding backend isn't available -- supersession is an enhancement,
    # not a correctness requirement.
    try:
        from cairn.graph import embeddings as emb
        from cairn.retrieval import cosine_scan

        if not emb.embeddings_available():
            return None
        new_text = " ".join(filter(None, [title, body]))
        q_blob, dim = emb.embed_query(new_text)
        cand_texts = [
            " ".join(filter(None, [c.title, c.description, c.body]))
            for c in candidates
        ]
        blobs, _ = emb._embed(cand_texts)
        rows = [
            (blob if isinstance(blob, bytes) else bytes(blob), len(blob) // 4, c)
            for c, blob in zip(candidates, blobs)
        ]
        scored = cosine_scan(q_blob, dim, rows, threshold=threshold)
        if scored:
            return scored[0][1].concept_id
    except Exception:
        pass
    return None


def _mark_superseded(bundle: OKFBundle, old_id: str, new_id: str) -> None:
    """Flip memory_is_latest=false on the old memory and link it to the new."""
    old = store_mod.get_memory(bundle, old_id)
    if old is None:
        return
    old.extensions["memory_is_latest"] = False
    old.extensions["memory_superseded_by"] = new_id
    old_id_norm = old.concept_id
    try:
        old_id_norm = str(Path(old_id_norm).relative_to(bundle.root))
    except ValueError:
        pass
    # Re-write in place (same path) so the version chain is durable on disk.
    bundle.write_concept(old)


def evolve_memory(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    memory_path: str,
    new_title: Optional[str] = None,
    new_body: Optional[str] = None,
) -> Optional[Dict]:
    """Explicit revision: create a new version that supersedes ``memory_path``.

    The agent-initiated path (vs. insert-time supersession): chains
    ``memory_supersedes``, flips the old to ``memory_is_latest: false``, and
    stores the new version. At least one of new_title / new_body must differ
    from the old memory.

    The new body AND title are redacted via :func:`strip_private_data`
    before storage, mirroring ``capture_memory``'s floor -- the MCP
    ``memory_evolve`` tool and the CLI both reach this function, so without
    redaction here a secret in an evolved body or the new title would
    persist verbatim (the same two-codepath divergence that once left
    ``record_memory`` unredacted; titles additionally leak into the
    description field and the slugified filename, audit F3). ``new_body``
    is None when only the title changes; the old body was already redacted
    at capture time.
    """
    from .privacy import strip_private_data

    if new_body is not None:
        new_body = strip_private_data(new_body)
    if new_title is not None:
        new_title = strip_private_data(new_title)
    with bundle.lock():
        old = store_mod.get_memory(bundle, memory_path)
        if old is None:
            return None
        mtype = old.extensions.get("memory_type", "decision")
        norm_old = _norm_cid(bundle, old.concept_id)
        old_chain = [_norm_cid(bundle, cid) for cid in (old.extensions.get("memory_supersedes") or [])]
        chain = [norm_old] + old_chain
        confidence = old.extensions.get("memory_signals", {}).get("agent_confidence", 0.7)
        concept = store_mod.create_memory(
            type_=mtype,
            title=new_title or old.title or "memory",
            body=new_body or old.body or "",
            resource=old.resource,
            confidence=confidence,
            tags=old.tags,
            supersedes=chain,
        )
        signals = score_memory(concept, conn, bundle)
        apply_score(concept, signals)
        tier = store_mod.tier_for_score(signals["score"])
        new_path = store_mod.store_memory(concept, bundle, tier=tier)
        _mark_superseded(bundle, old.concept_id, new_path)
        _append_promotion(concept, "evolve", signals["score"])
        return {"path": new_path, "tier": tier, "signals": signals, "superseded": old.concept_id}


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

    ``action`` may be a :class:`~cairn.memory.store_protocol.Decision` (the
    named lifecycle enum) or a freeform string; the stable string value is
    persisted either way.
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
