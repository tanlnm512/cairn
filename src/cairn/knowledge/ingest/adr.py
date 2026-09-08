"""ADR supersede-chain detection at ingest (D1.2).

Decision records chain by convention: a ``decisions``/``adr``-family
directory, ``NNNN-`` numbered filenames, and supersede markers in the
body or status line ("Supersedes ADR-0001", "Superseded by ADR-0002").
Detection turns those markers into the same relationship entries
author-declared frontmatter produces -- ``{concept_id, relation, kind}``
with kind ``extracted`` -- without any explicit frontmatter.

Every detected edge is durable in both directions (the memory
``_mark_superseded`` shape): the newer document carries a ``supersedes``
entry, the older document a ``superseded-by`` entry; a marker on either
side produces both halves. Resolution stays inside one staging run: a
marker's number must map to an accepted, numbered document in the same
repository and directory (or at a fed directory root, whose numbered
stems are the ADR layout itself); markers that do not resolve are
dropped, never staged as dangling pointers.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable

from cairn.knowledge.ingest.classifier import _DECISION_DIR_TOKENS, _tokens
from cairn.knowledge.relationships import EXTRACTED
from cairn.okf.utils import slugify

if TYPE_CHECKING:
    from cairn.knowledge.ingest.parser import ParsedDoc
    from cairn.knowledge.ingest.staging import StagedEntry

#: Filename numbering convention: a 3-4 digit prefix before a hyphen.
_ADR_NUMBER_RE = re.compile(r"^(\d{3,4})-")

#: Supersede markers in bodies and status lines. ``supersedes ADR-0001``
#: points new -> old; ``superseded by ADR-0002`` points old -> new. The
#: ``ADR`` prefix, a ``#``, a colon, and the hyphenated ``superseded-by``
#: spelling are tolerated; matching is case-insensitive and each verb
#: occurrence carries the one number that follows it.
_SUPERSEDES_RE = re.compile(
    r"\bsupersedes?\b[\s:]+(?:adr[\s.-]*)?#?(\d{3,4})\b",
    re.IGNORECASE,
)
_SUPERSEDED_BY_RE = re.compile(
    r"\bsuperseded[\s-]+by\b[\s:]+(?:adr[\s.-]*)?#?(\d{3,4})\b",
    re.IGNORECASE,
)


def detect_supersede_relationships(
    entries: Iterable["StagedEntry"],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Detected supersede entries per ``(repo, relpath)``, accepted
    entries only.

    Each value holds ``{concept_id, relation, kind}`` dicts: ``supersedes``
    on the newer document, ``superseded-by`` on the older one, kind
    ``extracted``. Documents without detected edges are absent.
    """
    accepted = [entry for entry in entries if entry.classification.skip_reason is None]
    numbers = _number_index(accepted)
    ids = {(entry.repo, entry.relpath): _concept_id(entry) for entry in accepted}
    keys_by_id: dict[str, tuple[str, str]] = {}
    for key in sorted(ids):
        keys_by_id.setdefault(ids[key], key)
    # Canonical edges as (newer_id, older_id), whichever marker named them.
    edges: set[tuple[str, str]] = set()
    for entry in accepted:
        doc_id = ids[(entry.repo, entry.relpath)]
        for target, forward in _referenced_ids(entry, numbers):
            if target == doc_id:
                continue
            edges.add((doc_id, target) if forward else (target, doc_id))
    detected: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for new_id, old_id in sorted(edges):
        detected.setdefault(keys_by_id[new_id], []).append(
            {"concept_id": old_id, "relation": "supersedes", "kind": EXTRACTED}
        )
        detected.setdefault(keys_by_id[old_id], []).append(
            {"concept_id": new_id, "relation": "superseded-by", "kind": EXTRACTED}
        )
    return detected


def _referenced_ids(
    entry: "StagedEntry", numbers: dict[tuple[str, str, str], str]
) -> list[tuple[str, bool]]:
    """``(concept_id, forward)`` targets of one document's supersede
    markers.

    ``forward`` is True when the entry supersedes the target ("supersedes
    ADR-NNNN"), False when the target supersedes the entry ("superseded
    by ADR-NNNN"). Numbers resolve against the same repository and
    directory only; unresolved numbers yield nothing.
    """
    family = (entry.repo, _dir_of(entry.relpath))
    text = _marker_text(entry.parsed)
    out: list[tuple[str, bool]] = []
    for pattern, forward in ((_SUPERSEDES_RE, True), (_SUPERSEDED_BY_RE, False)):
        for match in pattern.finditer(text):
            target = numbers.get((family[0], family[1], match.group(1)))
            if target is not None:
                out.append((target, forward))
    return out


def _number_index(
    accepted: list["StagedEntry"],
) -> dict[tuple[str, str, str], str]:
    """ADR number -> concept_id, keyed ``(repo, dir, number)``.

    Membership: a ``NNNN-`` numbered filename stem inside a
    decisions/adr-family directory (the classifier's decision-directory
    tokens), or with no parent directory (a fed directory's root is the
    decisions directory itself). First document per number in sorted
    ``(repo, relpath)`` order wins, keeping re-runs deterministic.
    """
    index: dict[tuple[str, str, str], str] = {}
    for entry in sorted(accepted, key=lambda e: (e.repo, e.relpath)):
        number = _adr_number(entry)
        if number is None:
            continue
        key = (entry.repo, _dir_of(entry.relpath), number)
        index.setdefault(key, _concept_id(entry))
    return index


def _adr_number(entry: "StagedEntry") -> str | None:
    """The ``NNNN-`` number of an ADR-family document, else None."""
    segments = [s for s in entry.relpath.replace("\\", "/").split("/") if s]
    match = _ADR_NUMBER_RE.match(segments[-1].rsplit(".", 1)[0])
    if match is None:
        return None
    parent = segments[:-1]
    if parent:
        dir_tokens: set[str] = set()
        for segment in parent:
            dir_tokens.update(_tokens(segment))
        if not dir_tokens & _DECISION_DIR_TOKENS:
            return None
    return match.group(1)


def _marker_text(parsed: "ParsedDoc") -> str:
    """Body with fenced code blocks excluded, plus the status line."""
    lines: list[str] = []
    fence = ""
    for line in parsed.body.splitlines():
        stripped = line.strip()
        if fence:
            if stripped.startswith(fence):
                fence = ""
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            continue
        lines.append(line)
    if parsed.status:
        lines.append(parsed.status)
    return "\n".join(lines)


def _concept_id(entry: "StagedEntry") -> str:
    """The concept_id staging assigns this document:
    ``knowledge/{doc_type}/{slug}``.

    Mirrors the store's doc-type slugification (``slugify(doc_type) or
    "general"``) so detected pointers name ids ``execute_manifest``
    actually creates.
    """
    doc_type = slugify(entry.classification.doc_type) or "general"
    return f"knowledge/{doc_type}/{entry.identity.slug}"


def _dir_of(relpath: str) -> str:
    """Parent directory in posix form; "" for root-level documents."""
    segments = [s for s in relpath.replace("\\", "/").split("/") if s]
    return "/".join(segments[:-1])
