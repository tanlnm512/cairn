"""OKF (Open Knowledge Format) v0.2 concept model.

An OKF concept is a markdown file with YAML frontmatter. The only required
frontmatter field is `type`. This module provides read/write/validate for
concepts used across Layers 2-4 (compass, wiki, memory).

v0.2 wire format (spec §13.1): a concept's last content change is recorded as
`generated: { by, at }`. Internally the "when" is still carried on the
`timestamp` field; the translation happens only at this serialization
boundary. Files with a bare v0.1 `timestamp:` are still read (spec §13.1
"MAY fall back").
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

OKF_VERSION = "0.2"


def _default_generated_by() -> str:
    """Actor for `generated.by` (spec §7 `<producer>/<version>` form).

    Imported lazily to avoid a circular import back into the top-level package.
    """
    try:
        from cairn import __version__  # local import avoids cycle
        return f"cairn/{__version__}"
    except Exception:
        return "cairn"


def _coerce_timestamp(value: Any) -> Optional[str]:
    """Normalize a parsed timestamp to the ISO-8601 string readers expect.

    YAML auto-converts an unquoted ISO datetime into a `datetime` object, but
    the recency readers call `.replace("Z", "+00:00")` on it, which only works
    on a string. Coerce here so unquoted timestamps survive intact.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        # Round-trip through the same format to_markdown() emits, so the
        # internal repr is stable regardless of how the file was authored.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


@dataclass
class OKFConcept:
    """An OKF concept file."""

    # Required by OKF
    type: str

    # Recommended / common fields (stored in frontmatter)
    title: Optional[str] = None
    description: Optional[str] = None
    resource: Optional[str] = None  # canonical URI to the underlying asset
    tags: List[str] = field(default_factory=list)
    # Internally carries the "when" from v0.2 `generated.at`; serialized back
    # out as `generated.at` by `to_markdown`.
    timestamp: Optional[str] = None

    # v0.2 first-class optional families (spec §5). Parsed out of frontmatter
    # so they don't silently land in `extensions`; emitted only when set.
    generated_by: Optional[str] = None     # actor for `generated.by` (§7)
    sources: Optional[List[Dict[str, Any]]] = None
    verified: Optional[List[Dict[str, Any]]] = None
    status: Optional[str] = None           # draft | stable | deprecated (§5.4)
    stale_after: Optional[str] = None      # YYYY-MM-DD absolute date (§5.5)

    # Concept identity (file path without .md, relative to bundle root)
    concept_id: str = ""

    # Body (markdown)
    body: str = ""

    # Producer-defined extension keys (any other frontmatter)
    extensions: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str) -> "OKFConcept":
        """Parse a markdown file into an OKFConcept."""
        text = Path(path).read_text(encoding="utf-8")
        return cls._from_text(text, concept_id=str(Path(path).with_suffix("")))

    @classmethod
    def _from_text(cls, text: str, concept_id: str = "") -> "OKFConcept":
        frontmatter, body = cls._split_frontmatter(text)
        if frontmatter is None:
            # No frontmatter; treat whole text as body, type unknown.
            return cls(type="", concept_id=concept_id, body=text)
        type_ = frontmatter.pop("type", "") or ""
        title = frontmatter.pop("title", None)
        description = frontmatter.pop("description", None)
        resource = frontmatter.pop("resource", None)
        tags = frontmatter.pop("tags", []) or []
        # v0.2 `generated: {by, at}` first; fall back to a bare `timestamp`
        # (spec §13.1 "MAY fall back") so on-disk v0.1 files still parse.
        generated = frontmatter.pop("generated", None) or {}
        generated_by = generated.get("by") if isinstance(generated, dict) else None
        timestamp = generated.get("at") if isinstance(generated, dict) else None
        if timestamp is None:
            timestamp = frontmatter.pop("timestamp", None)
        # Normalize at parse time so hand-edited (or unquoted) timestamps
        # survive intact.
        timestamp = _coerce_timestamp(timestamp)
        frontmatter.pop("okf_version", None)
        # v0.2 optional families (spec §5).
        sources = frontmatter.pop("sources", None)
        verified = frontmatter.pop("verified", None)
        # Spec §11: a bare `verified` mapping MUST be treated as a one-element
        # list. Normalize at parse time so consumers always see a list.
        if isinstance(verified, dict):
            verified = [verified]
        status = frontmatter.pop("status", None)
        stale_after = frontmatter.pop("stale_after", None)
        return cls(
            type=type_,
            title=title,
            description=description,
            resource=resource,
            tags=list(tags),
            timestamp=timestamp,
            generated_by=generated_by,
            sources=sources,
            verified=verified,
            status=status,
            stale_after=stale_after,
            concept_id=concept_id,
            body=body.lstrip("\n"),
            extensions=frontmatter,  # remaining keys
        )

    def to_file(self, path: str):
        """Write the concept to a markdown file atomically.

        Uses temp-file-then-os.replace so a crash mid-write won't leave a
        truncated target, no temp residue remains after success, and the
        target is either fully written or unchanged.
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Create temp file in the same directory as target for os.replace atomicity
        tmp_path = f"{path}.{os.getpid()}.tmp"
        try:
            # Write to temp file first
            Path(tmp_path).write_text(self.to_markdown(), encoding="utf-8")
            # Atomically replace target with temp file
            os.replace(tmp_path, path)
        except Exception:
            # Clean up temp file on any error
            if Path(tmp_path).exists():
                Path(tmp_path).unlink()
            raise

    def to_markdown(self) -> str:
        """Render the concept as markdown with YAML frontmatter."""
        fm: Dict[str, Any] = {"type": self.type}
        if self.title is not None:
            fm["title"] = self.title
        if self.description is not None:
            fm["description"] = self.description
        if self.resource is not None:
            fm["resource"] = self.resource
        if self.tags:
            fm["tags"] = self.tags
        # Compute the serialization timestamp locally without mutating self --
        # to_markdown is a read-only serializer; callers wanting the timestamp
        # persisted should set it explicitly.
        ts = self.timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # v0.2 wire format (spec §5.2/§13.1): emit `generated: {by, at}`.
        fm["generated"] = {
            "by": self.generated_by or _default_generated_by(),
            "at": ts,
        }
        if self.status is not None:
            fm["status"] = self.status
        if self.stale_after is not None:
            fm["stale_after"] = self.stale_after
        if self.sources:
            fm["sources"] = self.sources
        if self.verified:
            fm["verified"] = self.verified
        fm["okf_version"] = OKF_VERSION
        fm.update(self.extensions)
        yaml_block = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
        return f"---\n{yaml_block}\n---\n\n{self.body}"

    @property
    def file_path(self) -> str:
        return f"{self.concept_id}.md"

    def validate(self) -> List[str]:
        """Return a list of validation errors (empty = valid)."""
        errors = []
        if not self.type:
            errors.append("Missing required field: type")
        if not isinstance(self.tags, list):
            errors.append("tags must be a list")
        return errors

    @staticmethod
    def _split_frontmatter(text: str) -> tuple:
        """Split markdown into (frontmatter_dict, body). Returns (None, text) if none."""
        if not text.startswith("---"):
            return None, text
        parts = text[3:].split("---", 1)
        if len(parts) != 2:
            return None, text
        yaml_text, body = parts
        try:
            fm = yaml.safe_load(yaml_text) or {}
        except yaml.YAMLError:
            return None, text
        if not isinstance(fm, dict):
            return None, text
        return fm, body.lstrip("\n")
