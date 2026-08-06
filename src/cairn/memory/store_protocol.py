"""MemoryStore protocol: the swappable storage seam for the memory layer.

Defines a 5-method storage protocol with no lifecycle opinions, plus a concrete
``OKFMemoryStore`` adapter that wraps ``store.py`` functions against a fixed
bundle+conn pair. The lifecycle logic (``promotion``/``scoring``/``critic``)
is a *consumer* of a ``MemoryStore``: pass a mock store in tests and the
lifecycle runs without materializing a filesystem bundle. Also defines the
named ``Decision`` enum so ``batch_critic`` and ``promotion_history`` use
auditable constants instead of bare thresholds and freeform action strings.
"""
from __future__ import annotations

import sqlite3
from enum import Enum
from typing import Dict, List, Optional, Protocol, runtime_checkable

from ..okf.bundle import OKFBundle
from ..okf.concept import OKFConcept


class Decision(str, Enum):
    """Named lifecycle decision for a memory under critic/promotion review.

    The ``value`` is the stable string persisted to ``promotion_history`` --
    readers that treat ``action`` as a freeform string keep working
    (``Decision.ARCHIVE.value == "archive"``).
    """

    NEW = "new"              # first capture / newly created
    PROMOTE = "promote"      # raised to a higher tier (drafts -> tribal)
    KEEP_DRAFT = "keep_draft"  # stays a draft candidate (needs more evidence)
    ARCHIVE = "archive"      # demoted out (low score / decayed / suppressed)
    DUPLICATE = "duplicate"  # consolidated into another memory
    AMBIGUOUS = "ambiguous"  # critic couldn't decide; left in place


@runtime_checkable
class MemoryStore(Protocol):
    """Storage protocol for memory concepts. No lifecycle opinions.

    The five methods are the full surface a memory lifecycle (capture, critic,
    promote/demote, decay, search) needs. Higher layers call these instead of
    reaching into ``OKFBundle`` directly; a test injects a mock implementation
    to run the lifecycle without a filesystem bundle.
    """

    def add(self, concept: OKFConcept, tier: Optional[str] = None,
            old_id: Optional[str] = None) -> str:
        """Write ``concept`` to ``tier`` (or its current tier). Return its id."""
        ...

    def get(self, path: str) -> Optional[OKFConcept]:
        """Read a memory by concept id / path. None if missing."""
        ...

    def search(self, tier: Optional[str] = None,
               tag: Optional[str] = None) -> List[OKFConcept]:
        """List memories, optionally filtered by tier or tag."""
        ...

    def update(self, concept: OKFConcept, tier: Optional[str] = None,
               old_id: Optional[str] = None) -> str:
        """Re-write an existing memory (re-tier if ``tier`` given). Return id."""
        ...

    def delete(self, memory_path: str) -> bool:
        """Permanently delete a memory. Return False if it didn't exist."""
        ...


class OKFMemoryStore:
    """Concrete ``MemoryStore`` backed by ``store.py`` + an ``OKFBundle``.

    Binds the bundle+conn once at construction so the protocol methods need no
    further arguments. This is the production implementation; tests use a mock
    or ``InMemoryMemoryStore``.
    """

    def __init__(self, bundle: OKFBundle, conn: Optional[sqlite3.Connection] = None):
        self.bundle = bundle
        self.conn = conn

    def add(self, concept: OKFConcept, tier: Optional[str] = None,
            old_id: Optional[str] = None) -> str:
        from .store import store_memory
        return store_memory(concept, self.bundle, tier=tier, old_id=old_id)

    def get(self, path: str) -> Optional[OKFConcept]:
        from .store import get_memory
        return get_memory(self.bundle, path)

    def search(self, tier: Optional[str] = None,
               tag: Optional[str] = None) -> List[OKFConcept]:
        from .store import list_memories
        return list_memories(self.bundle, tier=tier, tag=tag)

    def update(self, concept: OKFConcept, tier: Optional[str] = None,
               old_id: Optional[str] = None) -> str:
        # store_memory is already an upsert (write_concept uses os.replace),
        # so add and update share one implementation.
        from .store import store_memory
        return store_memory(concept, self.bundle, tier=tier, old_id=old_id)

    def delete(self, memory_path: str) -> bool:
        from .store import delete_memory
        return delete_memory(self.bundle, memory_path, conn=self.conn)


class InMemoryMemoryStore:
    """A filesystem-free ``MemoryStore`` for tests.

    Holds concepts in a dict keyed by concept_id. Tier is tracked via
    ``concept.extensions['memory_tier']``.
    """

    def __init__(self):
        self._concepts: Dict[str, OKFConcept] = {}

    def _assign_id(self, concept: OKFConcept, tier: Optional[str]) -> str:
        import uuid
        from .store import TIER_DIRS, _slugify
        t = tier or concept.extensions.get("memory_tier", "drafts")
        concept.extensions["memory_tier"] = t
        if t == "raw":
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return f"{TIER_DIRS['raw']}/{ts}-{_slugify(concept.title)}"
        suffix = uuid.uuid4().hex[:6]
        return f"{TIER_DIRS.get(t, 'memory/drafts')}/{_slugify(concept.title)}-{suffix}"

    def add(self, concept: OKFConcept, tier: Optional[str] = None,
            old_id: Optional[str] = None) -> str:
        cid = self._assign_id(concept, tier)
        concept.concept_id = cid
        self._concepts[cid] = concept
        if old_id and old_id != cid:
            self._concepts.pop(old_id, None)
        return cid

    def get(self, path: str) -> Optional[OKFConcept]:
        cid = path[:-3] if path.endswith(".md") else path
        if not cid.startswith("memory/"):
            cid = f"memory/{cid}"
        return self._concepts.get(cid)

    def search(self, tier: Optional[str] = None,
               tag: Optional[str] = None) -> List[OKFConcept]:
        out = []
        for c in self._concepts.values():
            if tier and c.extensions.get("memory_tier") != tier:
                continue
            if tag and tag not in (c.tags or []):
                continue
            out.append(c)
        return out

    def update(self, concept: OKFConcept, tier: Optional[str] = None,
               old_id: Optional[str] = None) -> str:
        return self.add(concept, tier=tier, old_id=old_id)

    def delete(self, memory_path: str) -> bool:
        cid = memory_path[:-3] if memory_path.endswith(".md") else memory_path
        if not cid.startswith("memory/"):
            cid = f"memory/{cid}"
        existed = cid in self._concepts
        self._concepts.pop(cid, None)
        return existed
