"""Critic pass: fact-check OKF concepts (compass/wiki) against the L1 graph."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from ..okf.concept import OKFConcept
from ..refs import (
    BACKTICK_RE,
    extract_file_refs as _extract_file_refs,
    extract_symbol_refs as _extract_symbol_refs,
    file_exists as _file_exists,  # noqa: F401 -- re-exported for consumers
    symbol_exists as _symbol_exists,
    unresolved_file_refs as _unresolved_file_refs,
)


NOTES_HEADING = "## Notes"
_NOTES_HEADING_RE = re.compile(rf"(?m)^{NOTES_HEADING}[ \t\r]*$")
_SAME_OR_HIGHER_HEADING_RE = re.compile(
    r"(?m)^#{1,2}(?:[ \t]+.*)?[ \t\r]*$"
)


def split_notes(body: str) -> tuple[str, str]:
    """Return ``(Notes section, body without Notes)`` with bytes intact."""
    heading = _NOTES_HEADING_RE.search(body)
    if heading is None:
        return "", body
    boundary = _SAME_OR_HIGHER_HEADING_RE.search(body, heading.end())
    end = boundary.start() if boundary is not None else len(body)
    return body[heading.start() : end], body[: heading.start()] + body[end:]


def without_notes(body: str) -> str:
    """Return a body with its exact Notes section removed."""
    return split_notes(body)[1]


def splice_notes(existing_body: str, generated_body: str) -> str:
    """Splice the existing Notes section verbatim into generated content."""
    notes, _ = split_notes(existing_body)
    if not notes:
        return generated_body
    content = without_notes(generated_body)
    if content and not content.endswith(("\n", "\r")):
        content += "\n"
    return content + notes


def preserved_concept_notes(bundle, concept_id: str, generated_body: str) -> str:
    """Read an existing concept and splice its Notes into generated content."""
    try:
        existing = bundle.read_concept(concept_id)
    except OSError:
        return generated_body
    return splice_notes(existing.body or "", generated_body)


@dataclass
class CriticResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    quality_score: float = 0.0
    passed: bool = False

    def __bool__(self):
        return self.passed


# Section headings the default quality heuristic recognizes (module compass,
# flow compass, and the heading both share).
_DEFAULT_SECTION_VOCAB = (
    "# What Does This Module Do?",
    "# Common Modification Patterns",
    "# Build-Failure Patterns",
    "# Cross-Module Dependencies",
    "# What Does This Flow Do?",
    "# Call Sequence",
    "# Failure-Prone Steps",
    "# Modules Spanned",
    "# Tribal Knowledge",
)


def critic_concept(
    concept: OKFConcept,
    conn: sqlite3.Connection,
    llm_judge=None,
    section_vocab: Optional[Sequence[str]] = None,
) -> CriticResult:
    """Run critic checks on a concept against the graph.

    ``section_vocab`` replaces the recognized section headings the quality
    heuristic scores (default: the compass headings above).
    """
    errors = []
    warnings = []

    body = without_notes(concept.body or "")

    # 1. Extract backtick-quoted file references and check existence.
    file_refs = _extract_file_refs(body)
    for ref in _unresolved_file_refs(conn, file_refs):
        errors.append(f"Hallucinated/unresolved file path: {ref}")

    # 2. Extract `Symbol(...)` or backtick Capitalized tokens and verify.
    symbol_refs = _extract_symbol_refs(body)
    for sym in symbol_refs:
        if not _symbol_exists(conn, sym):
            warnings.append(f"Unknown symbol reference: {sym}")

    # 3. Prose-heavy / low-ref guard: the extractors only inspect backtick
    # references, so flag long unverifiable prose with a WARNING (non-blocking)
    # and apply a higher quality bar below.
    warning = _prose_heavy_warning(body, len(file_refs) + len(symbol_refs))
    if warning is not None:
        warnings.append(warning)

    # 4. Optional LLM quality score.
    quality = 0.0
    if llm_judge:
        quality = llm_judge(body)
    else:
        # Deterministic heuristic: fraction of the recognized section
        # headings present. A compass is complete at 5 sections even though
        # the default pool spans both compass shapes; an explicit vocabulary
        # is complete when fully present.
        vocab = _DEFAULT_SECTION_VOCAB if section_vocab is None else section_vocab
        sections = sum(1 for h in vocab if h in body)
        total = 5.0 if section_vocab is None else float(max(len(vocab), 1))
        quality = min(sections / total, 1.0)

    # No factual errors is mandatory. With no warnings this passes at quality
    # >= 0.5; with warnings (e.g. a prose-heavy draft) demand quality >= 0.7.
    threshold = 0.7 if warnings else 0.5
    passed = len(errors) == 0 and quality >= threshold
    return CriticResult(errors=errors, warnings=warnings, quality_score=quality, passed=passed)


# --- extractors ----------------------------------------------------------
#
# Extraction catches qualified names (`ApiClient.safeApiCall`),
# lowerCamelCase methods, snake_case Python functions, and the full set of
# file extensions (.kt/.java/.swift/.py/.ts/.tsx/.js/.jsx/.dart/.m/.mm).
# Matching compares against the referenced path, not just a basename
# substring, so `queries.py` in the body only passes if that exact file is
# the one being referenced.

# Heuristic thresholds for the prose-heavy / low-ref guard. A draft with
# more than this many characters of non-backtick prose but fewer than this
# many verified references is flagged as unverifiable. Tuned conservatively
# so the 5-section compass drafts (each section heading counts as structure)
# never trip it, while a wall of un-cited prose does.
PROSE_HEAVY_MIN_CHARS = 400
PROSE_HEAVY_MIN_REFS = 2


def _prose_char_count(body: str) -> int:
    """Total length of body with all backtick-quoted spans removed.

    This is the portion the critic does NOT check (backtick refs are checked;
    prose is not), so it's the right denominator for the prose-heavy heuristic.
    """
    return len(BACKTICK_RE.sub("", body))


def _prose_heavy_warning(body: str, verified_ref_count: int) -> Optional[str]:
    """Detect a body that is long on prose but short of verifiable references.

    Returns a warning string when the draft is "prose-heavy / low-ref", else
    None. Markdown section headings are stripped before measuring so a
    well-structured draft isn't penalized purely for being structured prose.
    """
    # Drop heading lines so structure alone doesn't trigger the heuristic.
    prose_only = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )
    prose_chars = _prose_char_count(prose_only)
    if prose_chars > PROSE_HEAVY_MIN_CHARS and verified_ref_count < PROSE_HEAVY_MIN_REFS:
        return (
            f"Prose-heavy draft with few verifiable references "
            f"({verified_ref_count} backtick ref(s), {prose_chars} chars of prose): "
            "deterministic critic cannot verify un-cited claims"
        )
    return None


# --- path validation ---------------------------------------------------

def validate_paths(conn: sqlite3.Connection, bundle) -> List[dict]:
    """Scan all concepts and check backtick-quoted file/symbol refs against the graph.

    Returns a list of stale entries: [{concept_id, verified}] where verified < 1.0.
    Does NOT delete concepts — only marks them for review.
    """
    from ..memory.scoring import _graph_verification

    stale = []
    for cid in bundle.list_concepts():
        try:
            c = bundle.read_concept(cid)
        except Exception:
            continue
        score = _graph_verification(c, conn)
        if score < 1.0:
            stale.append({"concept_id": cid, "verified": round(score, 3)})
    return stale
