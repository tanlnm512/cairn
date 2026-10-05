"""Deterministic memory stance verdicts for `cairn memory reflect`."""
from __future__ import annotations

import sqlite3
from typing import Dict, Optional, Tuple

from ..okf.bundle import OKFBundle
from ..okf.concept import OKFConcept
from ..refs import extract_file_refs, extract_symbol_refs, symbol_exists
from .promotion import _norm_cid
from .scoring import _graph_verification

STANCE_KEY = "memory_stance"
STANCE_PEER_KEY = "memory_stance_peer"
REFS_BASELINE_KEY = "memory_refs_baseline"

SUPERSEDED_BY_KEY = "memory_superseded_by"


def _has_refs(concept: OKFConcept) -> bool:
    body = concept.body or ""
    return bool(extract_file_refs(body) or extract_symbol_refs(body))


def _read_peer(bundle: OKFBundle, edge: str) -> Optional[OKFConcept]:
    try:
        return bundle.read_concept(edge[:-3] if edge.endswith(".md") else edge)
    except Exception:
        return None


def _shares_verified_symbol_ref(
    conn: sqlite3.Connection, a: OKFConcept, b: OKFConcept, memo: Dict[str, bool]
) -> bool:
    shared = set(extract_symbol_refs(a.body or "")) & set(extract_symbol_refs(b.body or ""))
    for ref in sorted(shared):
        verified = memo.get(ref)
        if verified is None:
            verified = symbol_exists(conn, ref)
            memo[ref] = verified
        if verified:
            return True
    return False


def _verdict(
    concept: OKFConcept,
    peer: Optional[OKFConcept],
    fraction: float,
    baseline: Optional[float],
    has_refs: bool,
    conn: sqlite3.Connection,
    memo: Dict[str, bool],
) -> Tuple[Optional[str], Optional[str]]:
    # Returns (stance, peer_id); (None, None) when no evidence verdict fires.
    edge = concept.extensions.get(SUPERSEDED_BY_KEY)
    if edge and peer is not None and _shares_verified_symbol_ref(conn, concept, peer, memo):
        return "contested", peer.concept_id
    if has_refs:
        if baseline is not None and fraction < baseline:
            return "tentative", None
        if fraction >= 1.0:
            return "preferred", None
    return None, None


def _reflect_one(
    bundle: OKFBundle,
    conn: sqlite3.Connection,
    concept: OKFConcept,
    memo: Dict[str, bool],
) -> Tuple[bool, Optional[str]]:
    # Returns (file_changed, final_stance); mutates stance keys only.
    peer = None
    edge = concept.extensions.get(SUPERSEDED_BY_KEY)
    if edge:
        peer = _read_peer(bundle, edge)

    has_refs = _has_refs(concept)
    # Same 3-decimal space as capture-time seeding (signals["graph_verification"]).
    fraction = round(_graph_verification(concept, conn), 3)
    recorded = concept.extensions.get(REFS_BASELINE_KEY)
    verdict, peer_id = _verdict(concept, peer, fraction, recorded, has_refs, conn, memo)

    changed = False
    if has_refs:
        # Seeding from the live fraction cannot fire the drop rule; a
        # present baseline only rises (high-water mark).
        updated = fraction if recorded is None else max(recorded, fraction)
        if updated != recorded:
            concept.extensions[REFS_BASELINE_KEY] = updated
            changed = True

    if verdict is not None:
        if concept.extensions.get(STANCE_KEY) != verdict:
            concept.extensions[STANCE_KEY] = verdict
            changed = True
        if verdict == "contested":
            peer_rel = _norm_cid(bundle, peer_id)
            if concept.extensions.get(STANCE_PEER_KEY) != peer_rel:
                concept.extensions[STANCE_PEER_KEY] = peer_rel
                changed = True
        elif STANCE_PEER_KEY in concept.extensions:
            del concept.extensions[STANCE_PEER_KEY]
            changed = True

    final = verdict if verdict is not None else concept.extensions.get(STANCE_KEY)
    return changed, final


def reflect_store(bundle: OKFBundle, conn: sqlite3.Connection) -> Dict[str, int]:
    """Recompute stance keys across every memory, in sorted concept-id order.

    Writes a memory file (atomically, via bundle.write_concept) only when one
    of the three stance keys changes; never touches lifecycle tier/score,
    body, title, or the supersession keys. Returns
    ``{"changed": n, "unchanged": n, "contested": n}`` where ``contested``
    counts memories whose stance is contested after the pass.
    """
    summary = {"changed": 0, "unchanged": 0, "contested": 0}
    memo: Dict[str, bool] = {}
    with bundle.lock():
        for cid in bundle.list_concepts(prefix="memory/"):
            try:
                concept = bundle.read_concept(cid)
            except Exception:
                continue
            changed, final = _reflect_one(bundle, conn, concept, memo)
            if changed:
                concept.concept_id = cid
                bundle.write_concept(concept)
                summary["changed"] += 1
            else:
                summary["unchanged"] += 1
            if final == "contested":
                summary["contested"] += 1
    return summary
