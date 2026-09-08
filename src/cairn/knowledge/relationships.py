"""Author-declared doc relationship normalization (D1.1).

Source frontmatter may declare relationships through three keys:

* ``relates_to`` — entries (or ids) with an explicit relation
* ``supersedes`` — concept_ids this document replaces
* ``superseded-by`` — concept_ids that replace this document

All three normalize into one ``relates_to`` extension on the promoted OKF
concept: a list of ``{concept_id, relation, kind}`` dicts. Relation comes
from the closed D1 vocabulary; author-declared entries default to
``kind: extracted``. Concept ids are provenance identifiers and pass
through verbatim (never redacted, like ``affects_*``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import yaml

#: Closed relation vocabulary (D1).
RELATIONS = ("relates-to", "supersedes", "superseded-by", "references")

#: Relationship kinds (D1): extracted (author/frontmatter/ADR detection),
#: inferred (critic-approved LLM), derived (tag/module overlap).
KINDS = ("extracted", "inferred", "derived")

#: Kind for author-declared (frontmatter) relationships.
EXTRACTED = "extracted"

#: Relation used when an entry declares none.
DEFAULT_RELATION = "relates-to"

#: Source frontmatter keys read for relationships, in promotion order.
SOURCE_KEYS = ("relates_to", "supersedes", "superseded-by")

#: Relation forced by each source key (None = per-entry or default).
_RELATION_BY_SOURCE_KEY: Dict[str, Optional[str]] = {
    "relates_to": None,
    "supersedes": "supersedes",
    "superseded-by": "superseded-by",
}


def extract_relationships(extensions: dict) -> List[Dict[str, Any]]:
    """Normalized relationship entries from parsed source frontmatter.

    Reads the relationship keys of an unknown-frontmatter dict (as carried
    by ``ParsedDoc.extensions``) and returns ``{concept_id, relation,
    kind, ...}`` entries in declaration order, deduplicated on
    ``(concept_id, relation, kind)``. Entries without a resolvable
    concept_id are dropped.
    """
    entries: List[Dict[str, Any]] = []
    for key in SOURCE_KEYS:
        raw = extensions.get(key)
        if raw is None:
            continue
        forced_relation = _RELATION_BY_SOURCE_KEY[key]
        for item in _as_items(raw):
            for entry in normalize_relationships([item]):
                if forced_relation is not None:
                    entry["relation"] = forced_relation
                entries.append(entry)
    return _dedupe(entries)


def normalize_relationships(
    entries: Optional[List[Any]],
) -> List[Dict[str, Any]]:
    """Normalize relationship entries to ``{concept_id, relation, kind}``.

    Accepts dict entries (kept verbatim plus defaulted ``relation``/``kind``)
    or bare ids (shorthand for ``relation: relates-to``); a string holding a
    YAML flow value is parsed first so minimal-fallback frontmatter
    recovery round-trips. Idempotent on already-normalized entries.
    """
    out: List[Dict[str, Any]] = []
    for item in entries or []:
        out.extend(_normalize_entry(item))
    return _dedupe(out)


def _normalize_entry(item: Any) -> List[Dict[str, Any]]:
    """Normalized entries for one raw candidate; empty when the candidate
    yields no resolvable concept_id. Lists (including one recovered from a
    flow-value string) recurse."""
    if isinstance(item, str):
        item = _unwrap_flow_string(item)
    if isinstance(item, dict):
        entry = _finish_entry(dict(item))
        return [entry] if entry is not None else []
    if isinstance(item, (list, tuple)):
        out: List[Dict[str, Any]] = []
        for sub in item:
            out.extend(_normalize_entry(sub))
        return out
    concept_id = str(item).strip() if item is not None else ""
    entry = _finish_entry({"concept_id": concept_id})
    return [entry] if entry is not None else []


def _finish_entry(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Default relation/kind; None when no concept_id is resolvable."""
    if not entry.get("concept_id"):
        return None
    entry.setdefault("relation", DEFAULT_RELATION)
    if not entry.get("kind"):
        entry["kind"] = EXTRACTED
    return entry


def _as_items(raw: Any) -> list:
    """A relationship key's value as a list of entry candidates."""
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return [raw]


def _unwrap_flow_string(value: str) -> Any:
    """Parse a string that holds a YAML flow value (list/dict/quoted).

    Minimal-fallback frontmatter recovery keeps relationship values as raw
    strings; this recovers their structure. Anything that does not parse
    as a flow stays a string (a bare concept id).
    """
    text = value.strip()
    if text[:1] in ("[", "{", '"', "'"):
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            return value
    return value


def _dedupe(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """First occurrence wins, keyed on (concept_id, relation, kind)."""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for entry in entries:
        key = (entry.get("concept_id"), entry.get("relation"), entry.get("kind"))
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out
