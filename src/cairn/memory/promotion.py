"""Memory promotion, critic, decay, and search."""

import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..okf.bundle import OKFBundle
from ..okf.concept import OKFConcept
from ..graph import BASE_STOP_WORDS, simple_tokenize
from ..graph import note_contention
from ..refs import extract_file_refs, extract_symbol_refs
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
    stance: Optional[str] = None,
) -> Dict:
    """Create, score, redact, and store a memory, superseding a close latest match."""
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
            stance=stance,
        )
        signals = score_memory(concept, conn, bundle)
        apply_score(concept, signals)
        body_refs = extract_file_refs(concept.body or "") + extract_symbol_refs(
            concept.body or ""
        )
        if body_refs:
            concept.extensions["memory_refs_baseline"] = signals["graph_verification"]
        tier = store_mod.tier_for_score(signals["score"])
        if _is_session_bookkeeping(title, body):
            tier = "raw"
            concept.extensions["memory_triage"] = "session-bookkeeping"
        path = store_mod.store_memory(concept, bundle, tier=tier, conn=conn)

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


_TASK_ID_RE = re.compile(r"\bT\d{3}\b")
_BRANCH_RE = re.compile(
    r"(?<![\w/])(?:feature|feat|fix|bugfix|hotfix|chore|release|docs|refactor|spec)"
    r"/[A-Za-z0-9._-]+"
)
_DATED_COUNT_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b(?=.*\b\d+\s+(?:commits?|files?|prs?|tasks?|modules?|specs?|tests?)\b)"
    r"|\b\d+\s+(?:commits?|files?|prs?|tasks?|modules?|specs?|tests?)\b(?=.*\b\d{4}-\d{2}-\d{2}\b)"
)
_PROGRESS_COUNT_RE = re.compile(
    r"\b\d+\s+(?:[\w'-]+\s+){0,3}(?:done|left|remaining|pending|complete|completed|to go)\b"
)


def _is_session_bookkeeping(title: str, body: str) -> bool:
    """Return whether title or body is task, branch, dated-count, or title-only progress noise."""
    combined = f"{title}\n{body}"
    if (
        _TASK_ID_RE.search(combined)
        or _BRANCH_RE.search(combined)
        or _DATED_COUNT_RE.search(combined)
    ):
        return True
    return bool(_PROGRESS_COUNT_RE.search(title))


def _sanitize_ref_context(context: str) -> str:
    """Return a redacted reference context capped to 200 characters."""
    from .privacy import strip_private_data

    return strip_private_data(context or "")[:_MAX_REF_CONTEXT_CHARS]


# Hard cap on the memory_refs.context column (analytics; see _sanitize_ref_context).
_MAX_REF_CONTEXT_CHARS = 200


def record_references_batch(
    conn: sqlite3.Connection, refs: list, session_id: str
):
    """Insert sanitized memory references in one best-effort transaction."""
    if not refs:
        return
    import uuid

    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (uuid.uuid4().hex, memory_path, session_id, now, _sanitize_ref_context(context))
        for memory_path, context in refs
    ]
    try:
        for attempt in range(3):
            try:
                conn.executemany(
                    "INSERT INTO memory_refs (id, memory_path, session_id, referenced_at, context) "
                    "VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
                break
            except sqlite3.OperationalError:
                if attempt == 2:
                    raise
                time.sleep(0.05)
    except sqlite3.OperationalError as e:
        note_contention("promotion.record_references_batch", error=e)
        # Lock contention or read-only connection -- ref counting is analytics.
        pass


def _lexical_memory_match(concepts, query):
    """Return concepts whose stop-filtered query tokens span title, description, or body."""
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


def _parse_instant(value) -> Optional[datetime]:
    """Return an aware UTC datetime, midnight UTC for dates, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _instant_key(instant: datetime) -> str:
    """Fixed-width UTC sort key for an instant; equal-width keys of this
    form order chronologically under plain string comparison.
    """
    u = instant.astimezone(timezone.utc)
    return (
        f"{u.year:04d}-{u.month:02d}-{u.day:02d}"
        f"T{u.hour:02d}:{u.minute:02d}:{u.second:02d}.{u.microsecond:06d}"
    )


_BOUND_KEYS: Dict[Any, Optional[str]] = {}
_BOUND_KEYS_MAX = 4096


def _bound_key(value) -> Optional[str]:
    """Return a memoized chronological validity key, or None for an open bound."""
    if not value:
        return None
    try:
        return _BOUND_KEYS[value]
    except KeyError:
        pass
    except TypeError:  # unhashable extension value: parse without caching
        dt = _parse_instant(value)
        return _instant_key(dt) if dt is not None else None
    dt = _parse_instant(value)
    key = _instant_key(dt) if dt is not None else None
    if len(_BOUND_KEYS) < _BOUND_KEYS_MAX:
        _BOUND_KEYS[value] = key
    return key


def _valid_at(c: OKFConcept, query_key: str) -> bool:
    """Return whether a concept validity interval contains query_key."""
    valid_from = _bound_key(c.extensions.get("valid_from"))
    if valid_from is not None and valid_from > query_key:
        return False
    valid_until = _bound_key(c.extensions.get("valid_until"))
    if valid_until is not None and valid_until <= query_key:
        return False
    return True


def search_memory(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    query: str,
    tier: Optional[str] = None,
    session_id: Optional[str] = None,
    include_superseded: bool = False,
    *,
    as_of: Optional[str] = None,
) -> List[OKFConcept]:
    """Return visible memories ranked by fused lexical and semantic matches."""
    if as_of is not None:
        instant = _parse_instant(as_of)
        if instant is None:
            raise ValueError(f"as_of must be an ISO-8601 date, got {as_of!r}")
    else:
        instant = datetime.now(timezone.utc)
    query_key = _instant_key(instant)

    def _visible(c: OKFConcept) -> bool:
        if tier and not c.extensions.get("memory_tier", "").startswith(tier):
            return False
        if not include_superseded and c.extensions.get("memory_is_latest", True) is False:
            return False
        return _valid_at(c, query_key)

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
        conn, bundle, query, tier=tier, include_superseded=include_superseded,
        query_key=query_key,
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
    query_key: Optional[str] = None,
) -> List[OKFConcept]:
    """Return each embedded memory once at its best-matching chunk without raising."""
    if query_key is None:
        query_key = _instant_key(datetime.now(timezone.utc))
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
            if not _valid_at(concept, query_key):
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
    """Promote a memory to compass or wiki and return its new concept id."""
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
        if conn is not None:
            from cairn.graph import embeddings as _emb
            _emb.rename_memory_embedding(conn, old_id, new_id)  # caller commits
        # ...and only once the new file is safely on disk, remove the old one.
        old_file = Path(bundle.root) / f"{old_id}.md"
        if old_file.exists():
            old_file.unlink()
            bundle.invalidate_search_index()
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
            store_mod.store_memory(concept, bundle, tier="archived", old_id=old_id, conn=conn)
            dropped += 1
        elif new_tier == "tribal":
            decision = Decision.PROMOTE
            store_mod.store_memory(concept, bundle, tier="tribal", old_id=old_id, conn=conn)
            tribal += 1
        else:
            decision = Decision.KEEP_DRAFT
            store_mod.store_memory(concept, bundle, tier="drafts", old_id=old_id, conn=conn)
            promoted += 1  # remains a candidate
        _append_promotion(concept, decision, signals["score"])
    return {"processed": len(drafts), "tribal": tribal, "dropped": dropped, "remaining_drafts": promoted}


def decay(bundle: OKFBundle, raw_max_days: int = 7, tribal_max_stale: int = 90, conn=None) -> Dict:
    """Expire raw and stale tribal memories, then reap orphaned embeddings."""
    expired = 0
    archived = 0
    with bundle.lock():
        for concept in store_mod.list_memories(bundle, tier="raw"):
            ts = concept.timestamp
            if ts and _age_days(ts) > raw_max_days:
                old_id = concept.concept_id
                store_mod.store_memory(concept, bundle, tier="archived", old_id=old_id, conn=conn)
                expired += 1
        for concept in store_mod.list_memories(bundle, tier="tribal"):
            ts = concept.timestamp
            age = _age_days(ts) if ts else 0
            if age > tribal_max_stale:
                old_id = concept.concept_id
                store_mod.store_memory(concept, bundle, tier="archived", old_id=old_id, conn=conn)
                archived += 1
    reaped = 0
    if conn is not None and (expired or archived):
        from cairn.graph import embeddings as _emb
        try:
            reaped = _emb.reap_orphaned_memory_embeddings(conn, bundle)
        except Exception:
            logger.debug("memory embed reap during decay failed", exc_info=True)
            reaped = 0
    return {"expired_raw": expired, "archived_tribal": archived, "reaped_embeddings": reaped}


def tribal_digest(bundle: OKFBundle, limit: int = 10) -> List[OKFConcept]:
    """Return top-scoring tribal memories for session orientation."""
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
    """Return a concept id relative to the bundle root."""
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
    """Return the closest latest same-type memory to supersede, or None."""
    candidates: list[OKFConcept] = []
    for cid in bundle.list_concepts(prefix="memory/"):
        try:
            c = bundle.read_concept(cid)
        except Exception:
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
    # Re-write in place (same path) so the version chain is durable on disk.
    bundle.write_concept(old)


def evolve_memory(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    memory_path: str,
    new_title: Optional[str] = None,
    new_body: Optional[str] = None,
) -> Optional[Dict]:
    """Create and store a redacted revision that supersedes memory_path."""
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
        new_path = store_mod.store_memory(concept, bundle, tier=tier, conn=conn)
        _mark_superseded(bundle, old.concept_id, new_path)
        _append_promotion(concept, "evolve", signals["score"])
        return {"path": new_path, "tier": tier, "signals": signals, "superseded": old.concept_id}


# --- helpers -------------------------------------------------------------

def _age_days(ts: str) -> float:
    """Age in days of an ISO-8601 timestamp; 0 when malformed or naive."""
    dt = store_mod.parse_memory_timestamp(ts)
    if dt is None:
        return 0
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0


def _rescore_with_critic(signals: Dict, critic: float) -> float:
    from .scoring import compute_score

    signals = {**signals, "critic_score": critic}
    return compute_score(signals)


def _append_promotion(concept: OKFConcept, action, score: float):
    """Append action's stable string value to promotion_history."""
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
