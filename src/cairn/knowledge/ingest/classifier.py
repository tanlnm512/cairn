"""Classification and draft-status gating for knowledge ingestion."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from cairn.knowledge.ingest.parser import ParsedDoc

DEFAULT_DOC_TYPE = "spec"

# FR-005: statuses that block ingestion; --include-drafts re-admits the
# whole family (TC-009), tagging each readmitted doc `draft`. Orthogonal
# to the store's DOC_STATUSES lifecycle.
_SKIP_STATUSES = frozenset(
    {"draft", "proposed", "review", "superseded", "deprecated"}
)
_DRAFT_STATUS = "draft"

# FR-004 doc-kind -> doc_type map, checked in order: the first matching
# kind wins, so decision must precede reference ("Architecture Decision
# Record" is a decision, not a reference doc). Tokens match whole words
# only; phrases match hyphen/slash-normalized text (prior-art, code-standard).
_KIND_RULES: tuple[tuple[str, tuple[str, ...], frozenset[str], frozenset[str]], ...] = (
    (
        "decision",
        (),
        frozenset({"adr", "decision", "decisions", "finding", "findings"}),
        frozenset(),
    ),
    (
        "spec",
        (),
        frozenset(
            {
                "feat",
                "feats",
                "spec",
                "specs",
                "specification",
                "specifications",
                "proposal",
                "proposals",
                "design",
                "designs",
                "uc",
                "ucs",
            }
        ),
        frozenset({"component spec", "use case"}),
    ),
    (
        "workflow",
        (),
        frozenset({"guide", "guides", "runbook", "runbooks", "setup", "setups"}),
        frozenset(),
    ),
    (
        "business-rule",
        (),
        frozenset({"convention", "conventions", "standard", "standards"}),
        frozenset({"code standard", "agent instruction"}),
    ),
    (
        "spec",
        ("reference",),
        frozenset({"vision", "architecture"}),
        frozenset({"prior art"}),
    ),
)

# D-007 layer 2: filename/directory conventions, checked in this order.
_DECISION_DIR_TOKENS = frozenset({"decisions", "decision", "adr", "adrs"})
_NUMBERED_FILENAME_RE = re.compile(r"^\d{3,4}-")
_FILENAME_KIND_TOKENS: tuple[tuple[str, frozenset[str]], ...] = (
    ("decision", frozenset({"adr"})),
    ("spec", frozenset({"feat"})),
)


@dataclass
class Classification:
    """doc_type, extra tags, and skip reason for one source document."""

    doc_type: str = DEFAULT_DOC_TYPE
    extra_tags: list[str] = field(default_factory=list)
    skip_reason: str | None = None


def classify_doc(
    parsed: ParsedDoc,
    relpath: str,
    include_drafts: bool,
    rules: Mapping[str, str] | None = None,
) -> Classification:
    """Classify one parsed document and apply the draft-status gate (FR-004/005).

    Layers per D-007: workspace title-keyword rules (FR-010, checked
    first so they refine the built-ins), then title keywords, then
    filename/directory conventions, then the `spec` default.
    """
    doc_type = DEFAULT_DOC_TYPE
    extra_tags: list[str] = []
    matched = _classify_title(parsed.title, rules)
    if matched is not None:
        doc_type, extra_tags = matched[0], list(matched[1])
    else:
        relpath_type = _classify_relpath(relpath)
        if relpath_type is not None:
            doc_type = relpath_type
    skip_reason = None
    status = (parsed.status or "").strip().lower()
    if status in _SKIP_STATUSES:
        if include_drafts:
            extra_tags.append(_DRAFT_STATUS)
        else:
            skip_reason = f"status: {status}"
    return Classification(
        doc_type=doc_type, extra_tags=extra_tags, skip_reason=skip_reason
    )


def _classify_title(
    title: str | None, rules: Mapping[str, str] | None = None
) -> tuple[str, tuple[str, ...]] | None:
    """Match workspace rules then the doc-kind rules against the title."""
    if not title:
        return None
    override = _workspace_rule(title, rules)
    if override is not None:
        return override, ()
    tokens = set(_tokens(title))
    text = _normalized(title)
    for doc_type, extra_tags, kind_tokens, phrases in _KIND_RULES:
        if tokens & kind_tokens or any(phrase in text for phrase in phrases):
            return doc_type, extra_tags
    return None


def _workspace_rule(title: str | None, rules: Mapping[str, str] | None) -> str | None:
    """First workspace title-keyword rule that matches, if any (FR-010).

    Mirrors the built-in rules' discipline: a single keyword matches whole
    tokens only, and a multi-word keyword matches the whole phrase in the
    hyphen/slash-normalized title -- never a substring inside a word
    ("arch" must not match "search").
    """
    if not title or not rules:
        return None
    tokens = set(_tokens(title))
    text = f" {_normalized(title)} "
    for keyword, doc_type in rules.items():
        key = _normalized(keyword)
        if key and (key in tokens or f" {key} " in text):
            return doc_type
    return None


def _classify_relpath(relpath: str) -> str | None:
    """Match filename/directory conventions against the source path."""
    segments = [s for s in relpath.replace("\\", "/").split("/") if s]
    if not segments:
        return None
    dir_tokens: set[str] = set()
    for segment in segments[:-1]:
        dir_tokens.update(_tokens(segment))
    if dir_tokens & _DECISION_DIR_TOKENS:
        return "decision"
    filename = segments[-1]
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    if _NUMBERED_FILENAME_RE.match(stem):
        return "decision"
    name_tokens = set(_tokens(stem))
    for doc_type, kind_tokens in _FILENAME_KIND_TOKENS:
        if name_tokens & kind_tokens:
            return doc_type
    return None


def _tokens(text: str) -> list[str]:
    """Lowercased word tokens of a title, filename, or path segment."""
    return [token for token in re.split(r"[^a-z0-9]+", text.lower()) if token]


def _normalized(text: str) -> str:
    """Hyphen/slash-normalized lowercase text for phrase matching."""
    return " ".join(_tokens(text))
