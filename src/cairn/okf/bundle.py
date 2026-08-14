"""OKF bundle manager: read/write/list/search concepts in a .knowledge/ tree."""
from __future__ import annotations

import contextlib
import errno
import fcntl
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


from .concept import OKFConcept

logger = logging.getLogger(__name__)

# flock() locks are per open-file-description: a second os.open() of the same
# file -- even in the same thread/process -- gets an independent lock that
# conflicts with the first (EAGAIN under LOCK_NB). So nested lock() calls must
# NOT re-open+flock; the inner flock would hit the outer's lock and, because the
# acquire path uses LOCK_NB, busy-wait to a spurious TimeoutError rather than
# block forever. Tracked via a thread-local depth counter instead.
#
# KNOWN CONSTRAINT (deferred -- audit F6, fix spans memory/store.py +
# memory/promotion.py, not this file): this flock is a cross-process mutex
# over the .knowledge/ tree, but some callers currently hold it across SQLite
# writes on caller-owned connections (e.g. promote_memory ->
# rename_memory_embedding under `with bundle.lock():`). A blocking DB write
# ("database is locked" busy-waits) inside the critical section serializes
# every OTHER process's memory mutations behind it and can cascade into the
# 5s TimeoutError above. The fix is to narrow each call site's critical
# section to the bundle's own file I/O (read-modify-write of the .md +
# log.md append) and move the DB writes outside the `with` block.
# Also note the re-entrancy guard below is thread-local ONLY: a different
# thread in the SAME process opens an independent fd whose LOCK_NB flock
# conflicts with the first thread's -- cross-thread nesting fails after
# `timeout` instead of re-entering.
_LOCK_DEPTH = threading.local()


@contextlib.contextmanager
def _okf_bundle_lock(root: Path, timeout: float = 5.0):
    """Advisory cross-process lock over mutations to the bundle at ``root``."""
    key = str(root.resolve())
    depth = getattr(_LOCK_DEPTH, "depth", None)
    if depth is None:
        depth = {}
        _LOCK_DEPTH.depth = depth

    if depth.get(key, 0) > 0:
        # Already held by this thread (a nested call) -- no-op.
        depth[key] += 1
        try:
            yield
        finally:
            depth[key] -= 1
        return

    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".okf.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out after {timeout}s waiting for the OKF bundle "
                        f"lock at {lock_path} -- another process is mutating "
                        f"{root}."
                    )
                time.sleep(0.05)
        depth[key] = 1
        try:
            yield
        finally:
            depth[key] = 0
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class OKFBundle:
    """Manages the .knowledge/ directory tree of OKF concepts."""

    def __init__(self, root_path: str):
        self.root = Path(root_path)
        # Lazily-built search index: concept_id -> {title, description, tags,
        # body_lower}. ``None`` means "not built yet"; built on first search()
        # call and invalidated by any mutation (write/delete) so it can never
        # go stale.
        self._index: Optional[Dict[str, Dict[str, Any]]] = None

    def lock(self, timeout: float = 5.0):
        """Advisory cross-process lock guarding a read-modify-write sequence."""
        return _okf_bundle_lock(self.root, timeout=timeout)

    def _validate_concept_path(self, concept_id: str) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        root_abs = self.root.resolve()
        path = (self.root / f"{concept_id}.md").resolve()
        try:
            if not path.is_relative_to(root_abs):
                raise ValueError(f"concept_id escapes bundle root: {concept_id!r}")
        except AttributeError:
            # Python < 3.9 fallback if needed
            if not str(path).startswith(str(root_abs)):
                raise ValueError(f"concept_id escapes bundle root: {concept_id!r}")
        return path

    def read_concept(self, concept_id: str) -> OKFConcept:
        """Read a concept by its ID (path without .md, relative to root)."""
        path = self._validate_concept_path(concept_id)
        return OKFConcept.from_file(str(path))

    def write_concept(self, concept: OKFConcept):
        """Write a concept to the bundle at <concept_id>.md."""
        if not concept.concept_id:
            raise ValueError("concept_id is required to write")
        path = self._validate_concept_path(concept.concept_id)
        concept.to_file(str(path))
        # Invalidate the search index: the written concept may be new or have
        # changed title/description/tags/body, so any cached entry is stale.
        self._index = None
        self.update_log("write", concept.concept_id)

    def invalidate_search_index(self) -> None:
        """Drop the in-memory search index so it is rebuilt on the next search.

        Callers that mutate the bundle outside ``write_concept`` (e.g. a direct
        ``unlink`` of a concept file) should invoke this so the index can never
        return stale or phantom entries.
        """
        self._index = None

    def list_concepts(self, prefix: Optional[str] = None) -> List[str]:
        """List all concept IDs (relative paths without .md), optional prefix filter."""
        ids = []
        if not self.root.is_dir():
            return ids
        for md in self.root.rglob("*.md"):
            if md.name in ("index.md", "log.md"):
                continue
            rel = md.relative_to(self.root).with_suffix("").as_posix()
            if prefix is None or rel.startswith(prefix):
                ids.append(rel)
        return sorted(ids)

    def _build_search_index(self) -> Dict[str, Dict[str, Any]]:
        """Parse every concept once and cache the searchable fields.

        Returns ``{concept_id: {title, description, tags, body_lower, tags_str}}``.
        Built lazily on the first ``search`` call and reused across subsequent
        queries. Invalidated by ``write_concept`` and
        ``invalidate_search_index``.
        """
        index: Dict[str, Dict[str, Any]] = {}
        for cid in self.list_concepts():
            try:
                concept = self.read_concept(cid)
            except Exception as e:
                logger.warning("Failed to read concept %s: %s", cid, e)
                continue
            tags = concept.tags or []
            index[cid] = {
                "title": (concept.title or "").lower(),
                "description": (concept.description or "").lower(),
                "tags": tags,
                "tags_str": " ".join(tags).lower(),
                "body_lower": (concept.body or "").lower(),
            }
        return index

    def search(
        self, query: str, tags: Optional[List[str]] = None, limit: int = 20
    ) -> List[OKFConcept]:
        """Simple text search across concept title, description, tags, body.

        Uses a lazily-built in-memory index; the index is invalidated whenever
        a concept is written.
        """
        if self._index is None:
            self._index = self._build_search_index()
        query_lower = query.lower()
        results: List[tuple] = []
        for cid, entry in self._index.items():
            concept_tags = entry["tags"]
            if tags and not any(t in concept_tags for t in tags):
                continue
            haystacks = [
                entry["title"],
                entry["description"],
                entry["body_lower"],
                entry["tags_str"],
            ]
            score = sum(h.count(query_lower) for h in haystacks)
            if score > 0:
                # Parse the matching concept from disk only for the hits we
                # actually return (keeps the index small).
                try:
                    concept = self.read_concept(cid)
                except Exception as e:
                    logger.warning("Failed to read concept %s: %s", cid, e)
                    continue
                results.append((score, concept))
        results.sort(key=lambda x: -x[0])
        return [c for _, c in results[:limit]]

    def validate_bundle(self) -> List[str]:
        from .conformance import check_bundle

        return check_bundle(str(self.root))

    def generate_index(self, dir_path: str):
        """Auto-generate an index.md for a directory listing its concepts."""
        target = self.root / dir_path
        if not target.is_dir():
            return
        concepts = []
        for md in sorted(target.rglob("*.md")):
            if md.name in ("index.md", "log.md"):
                continue
            try:
                c = OKFConcept.from_file(str(md))
                concepts.append(
                    (
                        md.relative_to(target).as_posix(),
                        c.title or md.stem,
                        c.description or "",
                        c.type,
                    )
                )
            except Exception as e:
                logger.warning("Failed to read concept %s: %s", md, e)
                continue
        lines = ["# Index\n"]
        lines.append("| File | Type | Title | Description |")
        lines.append("|------|------|-------|-------------|")
        for rel, title, desc, ctype in concepts:
            desc_short = desc[:60].replace("|", "\\|")
            lines.append(f"| {rel} | {ctype} | {title} | {desc_short} |")
        index_path = target / "index.md"
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def update_log(self, action: str, concept_id: str):
        """Append an entry to log.md."""
        log_path = self.root / "log.md"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = f"- {ts} | {action} | {concept_id}\n"
        if log_path.exists():
            with log_path.open("a", encoding="utf-8") as f:
                f.write(entry)
        else:
            log_path.write_text(f"# OKF Bundle Log\n\n{entry}", encoding="utf-8")
