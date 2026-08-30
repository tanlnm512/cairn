"""Memory storage: tiered OKF files (raw/drafts/tribal/archived).

Memories are OKF concepts with memory lifecycle extensions in frontmatter:
  memory_status, memory_score, memory_signals, memory_tier, memory_type,
  session_origin, promotion_history.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..okf.bundle import OKFBundle
from ..okf.concept import OKFConcept
from ..okf.provenance import Tier
from ..okf.utils import slugify

TIERS = ("raw", "drafts", "tribal", "archived")
TIER_DIRS = {
    "raw": "memory/raw",
    "drafts": "memory/drafts",
    "tribal": "memory/tribal",
    "archived": "memory/archived",
}
TIER_TYPE_PREFIX = {
    "raw": "Raw",
    "drafts": "Draft",
    "tribal": "Tribal",
    "archived": "Tribal",  # archived keeps its tribal type
}


def tier_for_score(score: float) -> str:
    """Map a memory score to a tier."""
    if score < 0.3:
        return "raw"
    if score < 0.5:
        return "drafts"
    return "tribal"


def create_memory(
    type_: str,
    title: str,
    body: str,
    resource: Optional[str] = None,
    confidence: float = 0.5,
    session_origin: Optional[str] = None,
    tags: Optional[List[str]] = None,
    score: Optional[float] = None,
    supersedes: Optional[List[str]] = None,
) -> OKFConcept:
    """Build a memory OKF concept with lifecycle frontmatter.

    ``supersedes`` is the concept_id(s) of the prior version(s) this memory
    replaces. When set, the new memory is marked ``memory_is_latest: true``
    and the superseded chain is inherited + extended. Callers must flip
    ``memory_is_latest`` to false on the old memory (see ``evolve_memory`` in
    promotion.py).
    """
    tier = tier_for_score(score if score is not None else confidence)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    okf_type = f"{TIER_TYPE_PREFIX[tier]}-{type_}"
    extensions = {
        "memory_status": tier if tier != "drafts" else "draft",
        "memory_score": round(score if score is not None else confidence, 3),
        "memory_signals": {
            "agent_confidence": confidence,
            "freshness": 1.0,
            "staleness_days": 0,
        },
        "memory_tier": tier,
        "tier": Tier.ASSERTED.value,
        "memory_type": type_,
        "session_origin": session_origin or "",
        "promotion_history": [
            {"date": ts, "action": "captured", "score": round(confidence, 3), "tier": tier}
        ],
        # Supersession: a memory is "latest" by default. When it supersedes an
        # older memory, memory_supersedes chains the version history. The old
        # memory gets memory_superseded_by set and memory_is_latest flipped to
        # false by the caller.
        "memory_is_latest": True,
        "memory_supersedes": list(supersedes) if supersedes else [],
        "memory_superseded_by": None,
    }
    return OKFConcept(
        type=okf_type,
        title=title,
        description=title,
        resource=resource,
        tags=tags or [type_],
        timestamp=ts,
        body=body,
        extensions=extensions,
    )


def store_memory(concept: OKFConcept, bundle: OKFBundle, tier: Optional[str] = None, old_id: Optional[str] = None):
    """Write a memory concept to its tier directory.

    The tier is read from concept.extensions['memory_tier'] unless overridden.
    When old_id is provided and differs from the new location, the old file
    is unlinked to prevent orphan files on re-tiering.
    """
    t = tier or concept.extensions.get("memory_tier", "drafts")
    slug = slugify(concept.title or "") or "memory"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if t == "raw":
        # Raw tier keeps the date prefix (so decay can purge by age) but now
        # also gets a uuid suffix like every other tier — without it, two
        # same-day captures with identically-slugified titles (common from
        # the generic titles the auto-capture hooks produce) silently
        # overwrite each other via bundle.write_concept.
        import uuid
        unique_suffix = uuid.uuid4().hex[:6]
        concept.concept_id = f"{TIER_DIRS['raw']}/{ts}-{slug}-{unique_suffix}"
    else:
        # Add UUID suffix for non-raw tiers to prevent same-title collisions
        import uuid
        unique_suffix = uuid.uuid4().hex[:6]
        concept.concept_id = f"{TIER_DIRS[t]}/{slug}-{unique_suffix}"
    # Keep tier metadata in sync with file location.
    concept.extensions["memory_tier"] = t
    bundle.write_concept(concept)
    # Clean up old tier file only when old_id is explicitly provided.
    if old_id and old_id != concept.concept_id:
        old_file = Path(bundle.root) / f"{old_id}.md"
        if old_file.exists():
            old_file.unlink()
    return concept.concept_id


def list_memories(
    bundle: OKFBundle, tier: Optional[str] = None, tag: Optional[str] = None
) -> List[OKFConcept]:
    """List memory concepts, optionally filtered by tier or tag."""
    prefix = TIER_DIRS[tier] + "/" if tier else "memory/"
    out = []
    for cid in bundle.list_concepts(prefix=prefix):
        try:
            c = bundle.read_concept(cid)
            if tag and tag not in c.tags:
                continue
            out.append(c)
        except Exception:
            continue
    return out


def get_memory(bundle: OKFBundle, path: str) -> Optional[OKFConcept]:
    """Read a memory by its full concept id or path."""
    cid = path
    if cid.endswith(".md"):
        cid = cid[:-3]
    if not cid.startswith("memory/") and "/memory/" not in cid:
        cid = f"memory/{cid}"
    try:
        return bundle.read_concept(cid)
    except FileNotFoundError:
        # Try as a relative path from the bundle root.
        try:
            return bundle.read_concept(path.replace(".md", ""))
        except FileNotFoundError:
            return None


def delete_memory(bundle: OKFBundle, memory_path: str, conn=None) -> bool:
    """Permanently delete a memory and clean up its cross-session refs.

    Refuses (returns False) when ``memory_path`` resolves to a concept
    outside the memory/ namespace: ``get_memory``'s FileNotFoundError
    fallback retries the raw path, which can resolve compass/wiki/knowledge
    concepts (audit F8) -- without this guard ``cairn memory forget
    compass/foo`` would unlink a compass doc. Mirrors the scope check the
    MCP ``memory_delete`` tool enforces, at the store chokepoint so the CLI
    and every other caller inherit it.
    """
    cid = memory_path
    if cid.endswith(".md"):
        cid = cid[:-3]
    if not cid.startswith("memory/"):
        cid = f"memory/{cid}"
    # get_memory -> read_concept -> _validate_concept_path raises ValueError
    # when the concept_id escapes the bundle root. Catch it here so a
    # traversal attempt is a controlled refusal (returns False), not an
    # unhandled exception.
    with bundle.lock():
        try:
            concept = get_memory(bundle, memory_path)
        except ValueError:
            return False
        if concept is not None:
            # Namespace guard: only the memory/ fallback resolution can land
            # here outside memory/ (the memory/-prefixed cid above is always
            # in-namespace). Resolve both sides so symlinked roots don't
            # false-positive the relative_to check.
            resolved = concept.concept_id
            try:
                resolved = str(
                    Path(resolved).resolve().relative_to(Path(bundle.root).resolve())
                )
            except ValueError:
                pass
            if not (resolved == "memory/" or resolved.startswith("memory/")):
                return False
            cid = concept.concept_id
            # Normalize to relative for DB lookup.
            try:
                cid = str(Path(cid).relative_to(bundle.root))
            except ValueError:
                pass
        # Route the file path through the write-path validator so a malformed
        # concept_id can't escape the bundle root via the delete path. Raises
        # ValueError if cid escapes root; treat that as "nothing to delete".
        try:
            file_path = bundle._validate_concept_path(cid)
        except ValueError:
            return False
        if not file_path.exists():
            return False
        file_path.unlink()
    # Clean up memory_refs in DB. Do NOT commit here -- the caller owns the
    # transaction boundary; committing a connection we don't own can either
    # commit an in-flight caller transaction or hit "database is locked".
    if conn is not None:
        conn.execute("DELETE FROM memory_refs WHERE memory_path = ?", (cid,))
    return True


TIER_ORDER = ["tribal", "drafts", "raw", "archived"]


def demote_memory(bundle: OKFBundle, memory_path: str, target_tier: str = "raw", conn=None) -> Optional[str]:
    """Demote a memory to a lower tier. Returns new path or None.

    Rejects promotions — target_tier must be strictly lower than current.

    If ``conn`` is provided, the memory's persisted embedding row is renamed
    from the old concept_id to the new one in place (content is unchanged by a
    demote), avoiding a re-embed of identical text. The caller owns the commit.
    """
    with bundle.lock():
        concept = get_memory(bundle, memory_path)
        if concept is None:
            return None
        current_tier = concept.extensions.get("memory_tier", "drafts")
        try:
            # TIER_ORDER is highest-first: tribal=0, drafts=1, raw=2, archived=3.
            # Demotion means target index > current index. Reject promotions (target < current)
            # and same-tier moves.
            if TIER_ORDER.index(target_tier) <= TIER_ORDER.index(current_tier):
                return None  # target is same or higher — not a demotion
        except ValueError:
            return None  # invalid tier name
        # Normalize concept_id to relative (from_file sets absolute paths).
        old_id = concept.concept_id
        try:
            old_id = str(Path(old_id).relative_to(bundle.root))
        except ValueError:
            pass  # keep as-is if not under bundle root
        concept.extensions["memory_tier"] = target_tier
        new_id = store_memory(concept, bundle, tier=target_tier, old_id=old_id)
        # Carry the embedding forward in place (a demote never changes content,
        # so re-embedding would be wasted work). old_id is already relative here.
        # Import via the cairn.graph public surface per the layering rule.
        if conn is not None:
            from ..graph import embeddings as _emb
            _emb.rename_memory_embedding(conn, old_id, new_id)  # caller commits
        return new_id


def purge_archived(bundle: OKFBundle, max_days: int = 90) -> int:
    """Delete archived memories older than max_days. Returns count purged."""
    now = datetime.now(timezone.utc)
    purged = 0
    with bundle.lock():
        for concept in list_memories(bundle, tier="archived"):
            ts = concept.timestamp
            if ts:
                try:
                    age = (now - datetime.fromisoformat(ts)).days
                except (ValueError, TypeError):
                    age = 0
                if age > max_days:
                    file_path = Path(bundle.root) / f"{concept.concept_id}.md"
                    if file_path.exists():
                        file_path.unlink()
                    purged += 1
    return purged


def _slugify(text: str) -> str:
    # Local (not okf.utils.slugify): the consolidation paths want a richer
    # slugifier that preserves underscores and other \w word characters and
    # doesn't ASCII-truncate (okf.utils.slugify is ASCII-only and truncates to
    # 60 chars, which would change consolidated concept_ids).
    import re
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[-\s]+", "-", text).strip("-")


def consolidate_memories(bundle: OKFBundle) -> int:
    """Consolidate redundant raw memories into unified tribal knowledge.

    Groups raw memories by title tokens / key concepts, merges content,
    promotes the consolidated memory to 'tribal', and archives raw duplicates.
    Returns the number of memories consolidated.
    """
    with bundle.lock():
        raw_ids = bundle.list_concepts(prefix="memory/raw")
        if len(raw_ids) < 2:
            return 0

        concepts = []
        for cid in raw_ids:
            try:
                concepts.append(bundle.read_concept(cid))
            except Exception:
                pass

        groups: dict[str, list[OKFConcept]] = {}
        for c in concepts:
            key = (c.title or "").strip().lower()
            if not key:
                continue
            groups.setdefault(key, []).append(c)

        consolidated_count = 0
        for title_key, group in groups.items():
            if len(group) < 2:
                continue

            primary = group[0]
            merged_body_lines = [primary.body or ""]
            for c in group[1:]:
                if c.body and c.body not in merged_body_lines:
                    merged_body_lines.append(c.body)

            new_title = primary.title or title_key.title()
            # UUID suffix so distinct consolidations that share a title don't
            # clobber each other via write_concept's atomic os.replace.
            import uuid
            unique_suffix = uuid.uuid4().hex[:6]
            unified_concept = OKFConcept(
                type="TribalMemory",
                title=new_title,
                description=primary.description or f"Consolidated memory for {new_title}",
                body="\n\n---\n\n".join(merged_body_lines),
                tags=list(set(sum([c.tags for c in group if c.tags], []))),
                concept_id=f"memory/tribal/{_slugify(new_title)}-{unique_suffix}",
                extensions={
                    "memory_tier": "tribal",
                    "consolidated_from": [c.concept_id for c in group],
                },
            )
            bundle.write_concept(unified_concept)

            for c in group:
                if c.concept_id and c.concept_id != unified_concept.concept_id:
                    try:
                        c.extensions["memory_tier"] = "archived"
                        # UUID suffix (same format as store_memory's non-raw tier)
                        # so distinct memories that share a title don't clobber
                        # each other via write_concept's atomic os.replace.
                        archived_suffix = uuid.uuid4().hex[:6]
                        c.concept_id = f"memory/archived/{_slugify(c.title or 'memory')}-{archived_suffix}"
                        bundle.write_concept(c)
                    except Exception:
                        pass

            consolidated_count += len(group)

    return consolidated_count
