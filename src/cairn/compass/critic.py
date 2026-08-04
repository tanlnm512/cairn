"""Critic pass: fact-check OKF concepts (compass/wiki) against the L1 graph.

Scope (important): this is a *deterministic reference checker*, NOT a general
hallucination detector. It verifies only backtick-quoted references:
  1. File paths mentioned in the concept body actually exist in the graph
  2. Symbol references mentioned exist as symbols
Plain prose statements with no backticks are NOT checked -- an LLM can still
assert false claims in un-backticked prose and have them pass here. To narrow
that gap, prose-heavy bodies with few/no verifiable references raise a warning
and face a stricter pass threshold (see _prose_heavy_warning). For full
trust, treat promoted bodies as critic-gated, not critic-proven.
The LLM quality-judge is optional; without it we only run the deterministic checks.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import List, Optional

from ..okf.concept import OKFConcept
from ..refs import (
    BACKTICK_RE,
    extract_file_refs as _extract_file_refs,
    extract_symbol_refs as _extract_symbol_refs,
    file_exists as _file_exists,
    symbol_exists as _symbol_exists,
)


@dataclass
class CriticResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    quality_score: float = 0.0
    passed: bool = False

    def __bool__(self):
        return self.passed


def critic_concept(
    concept: OKFConcept, conn: sqlite3.Connection, llm_judge=None
) -> CriticResult:
    """Run critic checks on a concept against the graph."""
    errors = []
    warnings = []

    body = concept.body or ""

    # 1. Extract backtick-quoted file references and check existence.
    file_refs = _extract_file_refs(body)
    for ref in file_refs:
        if not _file_exists(conn, ref):
            errors.append(f"Hallucinated/unresolved file path: {ref}")

    # 2. Extract `Symbol(...)` or backtick Capitalized tokens and verify.
    symbol_refs = _extract_symbol_refs(body)
    for sym in symbol_refs:
        if not _symbol_exists(conn, sym):
            warnings.append(f"Unknown symbol reference: {sym}")

    # 3. Prose-heavy / low-ref guard: the extractors above only inspect
    # backtick-quoted references, so a body can be long on unverifiable prose
    # and short on anything the critic actually checks. Flag such drafts with
    # a WARNING (non-blocking on its own) and require a higher quality bar below.
    warning = _prose_heavy_warning(body, len(file_refs) + len(symbol_refs))
    if warning is not None:
        warnings.append(warning)

    # 4. Optional LLM quality score.
    quality = 0.0
    if llm_judge:
        quality = llm_judge(body)
    else:
        # Deterministic heuristic: reward presence of all 5 sections.
        # Module compasses and flow compasses use different section titles, so
        # we recognize either set (a 5-section flow compass is just as complete
        # as a 5-section module compass).
        sections = sum(
            1 for h in [
                # Module compass sections
                "# What Does This Module Do?",
                "# Common Modification Patterns",
                "# Build-Failure Patterns",
                "# Cross-Module Dependencies",
                # Flow compass sections
                "# What Does This Flow Do?",
                "# Call Sequence",
                "# Failure-Prone Steps",
                "# Modules Spanned",
                # Shared by both
                "# Tribal Knowledge",
            ]
            if h in body
        )
        quality = min(sections / 5.0, 1.0)

    # No factual errors is mandatory. When the critic has no warnings this
    # passes at quality >= 0.5; when there ARE warnings -- e.g. a prose-heavy
    # draft with few verifiable references -- demand quality >= 0.7 so a
    # low-effort, citation-light body can't slip through on section headers
    # alone. Errors always fail regardless.
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
    None. Used to raise the pass threshold (see critic_concept) because such a
    draft has little for the deterministic critic to actually anchor on -- a
    false prose claim would otherwise ship unchecked.

    Deliberately conservative: markdown section headings (#, ##) are stripped
    before measuring, so a well-structured 5-section compass draft that simply
    lacks backticks is not penalized purely for being structured prose. The
    intent is to catch un-cited walls of text, not normal documentation.
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
