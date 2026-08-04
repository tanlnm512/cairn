"""Memory scoring engine: 7-signal weighted score.

score = 0.25*graph_verification + 0.20*cross_session_refs
      + 0.15*agent_confidence + 0.20*critic_score + 0.05*freshness
      + 0.05*reinforcement + 0.10*authority

Freshness uses exponential decay (exp(-λ·age)) rather than the earlier linear
ramp; a new reinforcement signal rewards memories that are accessed
frequently (adapted from agentmemory's temporalDecay + reinforcementBoost
model, but folded into cairn's existing single-score system rather than
running as a parallel retention scorer).
"""
from __future__ import annotations

import math
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Optional

from ..okf.concept import OKFConcept
from ..okf.bundle import OKFBundle

WEIGHTS = {
    "graph_verification": 0.25,
    "cross_session_refs": 0.20,
    "agent_confidence": 0.15,
    "critic_score": 0.20,
    "freshness": 0.05,
    "reinforcement": 0.05,
    "authority": 0.10,
}


def score_memory(
    concept: OKFConcept,
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    critic_score: Optional[float] = None,
) -> Dict:
    """Compute the 6-signal score for a memory concept.

    Returns a dict with each signal (0.0-1.0) and the weighted `score`.
    Does not mutate the concept; caller decides whether to store the new score.
    """
    signals = concept.extensions.get("memory_signals", {})
    confidence = signals.get("agent_confidence", concept.extensions.get("memory_score", 0.5))

    graph_v = _graph_verification(concept, conn)
    refs = _cross_session_refs(concept, conn)
    # cross_session_refs signal: normalize refs count (saturates ~5).
    refs_signal = min(refs / 5.0, 1.0)
    critic = critic_score if critic_score is not None else signals.get("critic_score", 0.5)
    freshness = _freshness(concept)
    reinforcement = _reinforcement(concept, conn)
    authority = _authority(concept)

    signals = {
        "graph_verification": round(graph_v, 3),
        "cross_session_refs": refs,
        "cross_session_refs_signal": round(refs_signal, 3),
        "agent_confidence": round(confidence, 3),
        "critic_score": round(critic, 3),
        "freshness": round(freshness, 3),
        "reinforcement": round(reinforcement, 3),
        "authority": round(authority, 3),
    }
    signals["score"] = round(compute_score(signals), 3)
    return signals


def compute_score(signals: Dict) -> float:
    """Apply the weighted formula to an already-computed signals dict.

    Factored out so callers that only need to substitute one signal (e.g.
    `batch_critic` overriding `critic_score` after an LLM pass) don't have
    to duplicate the weighted-sum formula themselves.
    """
    return (
        WEIGHTS["graph_verification"] * signals["graph_verification"]
        + WEIGHTS["cross_session_refs"] * signals["cross_session_refs_signal"]
        + WEIGHTS["agent_confidence"] * signals["agent_confidence"]
        + WEIGHTS["critic_score"] * signals["critic_score"]
        + WEIGHTS["freshness"] * signals["freshness"]
        + WEIGHTS["reinforcement"] * signals.get("reinforcement", 0.0)
        + WEIGHTS["authority"] * signals.get("authority", 0.5)
    )


def apply_score(concept: OKFConcept, signals: Dict):
    """Write computed signals + score back into a concept's frontmatter."""
    concept.extensions["memory_signals"] = {
        "graph_verification": signals["graph_verification"],
        "cross_session_refs": signals["cross_session_refs"],
        "agent_confidence": signals["agent_confidence"],
        "critic_score": signals["critic_score"],
        "freshness": signals["freshness"],
        "reinforcement": signals["reinforcement"],
        "authority": signals["authority"],
    }
    concept.extensions["memory_score"] = signals["score"]


# --- signal computations -------------------------------------------------

# Reuse the same extraction logic as the critic to ensure scoring and fact-checking
# agree on what counts as a "verified" reference (scoring and the critic must not
# drift on what a "verified" reference is). Imported from the neutral refs module
# (not from compass/critic) to avoid an L4 → L2 layer dependency edge.
from ..refs import (
    extract_file_refs as _extract_file_refs,
    extract_symbol_refs as _extract_symbol_refs,
    file_exists as _file_exists,
    symbol_exists as _symbol_exists,
)


def _graph_verification(concept: OKFConcept, conn: sqlite3.Connection) -> float:
    """Fraction of backtick-quoted file/symbol refs that exist in the graph.

    Uses the same extraction and verification logic as the critic
    (_extract_file_refs, _extract_symbol_refs, _file_exists, _symbol_exists)
    to ensure consistency across the codebase.
    Supports all file extensions (.kt/.java/.swift/.py/.ts/.tsx/.js/.jsx/.dart/.m/.mm)
    and symbol patterns (CapitalizedWord, lowerCamelCase, snake_case, qualified names).
    """
    body = concept.body or ""

    # Use the critic's extractors to get file and symbol references
    file_refs = _extract_file_refs(body)
    symbol_refs = _extract_symbol_refs(body)

    all_refs = file_refs + symbol_refs
    if not all_refs:
        return 1.0  # nothing to verify; neutral-positive

    verified = 0

    # Check file references using the critic's logic
    for ref in file_refs:
        if _file_exists(conn, ref):
            verified += 1

    # Check symbol references using the critic's logic
    for ref in symbol_refs:
        if _symbol_exists(conn, ref):
            verified += 1

    return verified / len(all_refs)


def _cross_session_refs(concept: OKFConcept, conn: sqlite3.Connection) -> int:
    """Count references to this memory in the memory_refs table."""
    if not concept.concept_id:
        return 0
    cur = conn.cursor()
    row = cur.execute(
        "SELECT COUNT(DISTINCT session_id) AS c FROM memory_refs WHERE memory_path = ?",
        (concept.concept_id,),
    ).fetchone()
    return row["c"] if row else 0


# Decay window per memory_type, in days. `decision` memories are context-bound
# (an architecture choice that can be superseded by later changes) and keep
# the original 90-day window. `pattern`/`mistake`/`workaround` memories tend
# to stay valid longer -- a workaround remains correct until the underlying
# dependency changes, not just because time passed -- so they decay over a
# 3x longer window instead of aging out purely on the calendar.
FRESHNESS_WINDOW_DAYS = {
    "decision": 90,
    "pattern": 270,
    "mistake": 270,
    "workaround": 270,
}
DEFAULT_FRESHNESS_WINDOW_DAYS = 90


def _freshness(concept: OKFConcept) -> float:
    """Exponential decay: exp(-λ·age) where λ = ln(2)/half_life.

    The half-life is the type-dependent freshness window, so a memory reaches
    0.5 at the window boundary (e.g. 90 days for a decision) rather than
    hitting 0 as the old linear ramp did. This is adapted from agentmemory's
    temporalDecay = exp(-lambda * daysSinceCreation), but keeps cairn's
    type-dependent windows so a `workaround` decays 3x slower than a
    `decision`.

    Human-authored documents (doc_source == "manual") never age out.
    """
    ts = concept.timestamp or concept.extensions.get("timestamp")
    if not ts:
        return 0.5
    if concept.extensions.get("doc_source") == "manual":
        return 1.0
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return 0.5
    now = datetime.now(timezone.utc)
    days = (now - dt).total_seconds() / 86400.0
    mtype = concept.extensions.get("memory_type", "decision")
    half_life = FRESHNESS_WINDOW_DAYS.get(mtype, DEFAULT_FRESHNESS_WINDOW_DAYS)
    if half_life <= 0:
        return 1.0
    lam = math.log(2) / half_life
    return math.exp(-lam * days)


def _reinforcement(concept: OKFConcept, conn: sqlite3.Connection) -> float:
    """Reward signal from how recently and often this memory was recalled.

    Adapted from agentmemory's reinforcementBoost = sigma * Σ(1/daysSince(ts)),
    but driven by cairn's memory_refs table (which records a row on every
    recall hit). The boost saturates at 1.0, so a frequently-recalled memory
    stays warm even as its freshness decays -- pure exponential decay would
    forget everything eventually; this rewards reuse.

    Returns 0.0 for a never-recalled memory (or when memory_refs is empty).
    """
    if not concept.concept_id:
        return 0.0
    cur = conn.cursor()
    # memory_refs.memory_path may be stored as absolute (from_file sets
    # absolute concept_id) or relative. Query both forms to be robust.
    cid = concept.concept_id
    cids = [cid]
    if os.path.isabs(cid) and "memory/" in cid:
        # Strip everything before the last "memory/" to get the relative form.
        rel = "memory/" + cid.split("memory/")[-1]
        if rel != cid:
            cids.append(rel)
    placeholders = ",".join("?" * len(cids))
    rows = cur.execute(
        f"SELECT referenced_at FROM memory_refs WHERE memory_path IN ({placeholders})",
        cids,
    ).fetchall()
    if not rows:
        return 0.0
    now = datetime.now(timezone.utc)
    sigma = 0.3  # scaling constant (same default as agentmemory)
    boost = 0.0
    for row in rows:
        ts = row["referenced_at"] if isinstance(row, sqlite3.Row) else row[0]
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            days_since = (now - dt).total_seconds() / 86400.0
            if days_since > 0:
                boost += 1.0 / days_since
        except (ValueError, TypeError):
            continue
    return min(1.0, boost * sigma)


def _authority(concept: OKFConcept) -> float:
    """Authority signal: human-authored documents get full authority; agent memories get half."""
    return 1.0 if concept.extensions.get("doc_source") == "manual" else 0.5
