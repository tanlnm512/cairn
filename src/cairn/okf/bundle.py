"""OKF bundle manager: read/write/list/search concepts in a .knowledge/ tree."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


from .concept import OKFConcept

logger = logging.getLogger(__name__)


class OKFBundle:
    """Manages the .knowledge/ directory tree of OKF concepts."""

    def __init__(self, root_path: str):
        self.root = Path(root_path)
        # Lazily-built search index: concept_id -> {title, description, tags,
        # body_lower}. ``None`` means "not built yet"; built on first search()
        # call and invalidated by any mutation (write/delete) so it can never
        # go stale.
        self._index: Optional[Dict[str, Dict[str, Any]]] = None

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
