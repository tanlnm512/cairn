"""doc-link task results: parse, critic-check, apply inferred edges (D1).

The deterministic gate for doc-link completions verifies that every
proposed edge names concept_ids resolving to EXISTING knowledge docs,
confines endpoints to the completing task's island members when the
caller supplies them, and rejects self-referential edges
(un-backticked prose is not checked). A rejected result
performs no writes: the caller leaves the task in-progress and
re-completable, with each error naming the offending reference.

An accepted completion is written back as the durable record --
``kind: inferred`` ``relates_to`` entries on BOTH docs' frontmatter
(``supersedes`` mirrors to ``superseded-by`` and vice versa) -- and the
derived index is rebuilt on the caller's connection so the inferred edge
is queryable immediately. The caller owns the task-status transition.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from cairn.okf.bundle import OKFBundle

#: Proposed-edge line format accepted in a doc-link result.
DOC_LINK_LINE_SPEC = "<concept_id> <relation> <related_id>"

#: Relation vocabulary as it appears in the output spec.
_RELATION_CHOICES = "relates-to | supersedes | superseded-by | references"

#: The entry written on the opposite doc, by proposed relation.
MIRROR_RELATION = {
    "relates-to": "relates-to",
    "supersedes": "superseded-by",
    "superseded-by": "supersedes",
    "references": "references",
}

#: Output spec for doc-link tasks (rendered into the task body).
DOC_LINK_OUTPUT_SPEC = (
    "Propose relationships between the isolated documents in facts.members. "
    "Read each member document first. Output ONLY proposed-edge lines, one "
    "per line, in this exact format:\n"
    f"{DOC_LINK_LINE_SPEC}\n"
    f"where <relation> is one of: {_RELATION_CHOICES}. "
    "<concept_id> and <related_id> must be existing knowledge document ids "
    "(facts.members lists them). No prose, no code fences, no extra "
    "formatting."
)


def parse_doc_link_result(
    result: Optional[str],
) -> Tuple[List[Tuple[str, str, str]], List[str]]:
    """Parse proposed edges out of a doc-link result body.

    One edge per line: ``<concept_id> <relation> <related_id>``. Heading
    lines, bullet markers, code-fence markers, and backticks around ids
    are tolerated; anything else that is not a well-formed edge line is a
    parse error naming the line. Returns ``(edges, errors)``.
    """
    edges: List[Tuple[str, str, str]] = []
    errors: List[str] = []
    for raw in (result or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        if line.startswith(("- ", "* ")):
            line = line[2:].strip()
        parts = [part.strip("`") for part in line.split()]
        if len(parts) != 3:
            errors.append(
                f"malformed edge line: '{raw.strip()}' "
                f"(expected: {DOC_LINK_LINE_SPEC})"
            )
            continue
        src, relation, dst = parts
        if relation not in MIRROR_RELATION:
            errors.append(
                f"invalid relation '{relation}' in line '{raw.strip()}' "
                f"(expected one of: {_RELATION_CHOICES})"
            )
            continue
        edges.append((src, relation, dst))
    return edges, errors


def validate_doc_link_edges(
    bundle: OKFBundle,
    proposed: List[Tuple[str, str, str]],
    members: Optional[List[str]] = None,
) -> List[str]:
    """Critic check: every referenced concept_id must exist and edges
    must not be self-referential. When ``members`` is provided, each
    edge endpoint must additionally be one of those ids (the completing
    task's island). Errors name the invalid reference."""
    from cairn.knowledge.islands import knowledge_doc_ids
    from cairn.knowledge.store import normalize_doc_id

    known = set(knowledge_doc_ids(bundle))
    member_ids = (
        {normalize_doc_id(bundle, m) for m in members}
        if members is not None
        else None
    )
    errors: List[str] = []
    for src, relation, dst in proposed:
        norm_src = normalize_doc_id(bundle, src)
        norm_dst = normalize_doc_id(bundle, dst)
        if norm_src not in known:
            errors.append(f"unknown concept_id: '{src}' (edge source)")
        if norm_dst not in known:
            errors.append(f"unknown concept_id: '{dst}' (edge target)")
        if member_ids is not None:
            for raw, norm in ((src, norm_src), (dst, norm_dst)):
                if norm not in member_ids:
                    errors.append(
                        f"edge endpoint outside task members: '{raw}'"
                    )
        if norm_src == norm_dst:
            errors.append(f"self-referential edge: '{src} {relation} {dst}'")
    return errors


def apply_doc_link_edges(
    bundle: OKFBundle,
    conn,
    proposed: List[Tuple[str, str, str]],
) -> Dict[str, Any]:
    """Write accepted proposals into both docs' frontmatter as
    ``kind: inferred`` entries, then rebuild the derived index (committed
    on ``conn``). An entry for the same pair+relation already present in
    either kind wins -- no duplicates are written. Returns
    ``{docs_updated, edges_applied}``."""
    from cairn.knowledge.index import rebuild_knowledge_index
    from cairn.knowledge.store import normalize_doc_id

    touched: set = set()
    for src_raw, relation, dst_raw in proposed:
        src = normalize_doc_id(bundle, src_raw)
        dst = normalize_doc_id(bundle, dst_raw)
        _append_inferred(bundle, src, dst, relation)
        _append_inferred(
            bundle, dst, src, MIRROR_RELATION.get(relation, relation)
        )
        touched.update((src, dst))
    rebuild_knowledge_index(conn, bundle)
    conn.commit()
    return {"docs_updated": sorted(touched), "edges_applied": len(proposed)}


def _append_inferred(
    bundle: OKFBundle, doc_id: str, other_id: str, relation: str
) -> None:
    """Add one inferred relates_to entry unless the pair+relation is
    already recorded (an explicit extracted/derived/inferred record wins)."""
    from cairn.knowledge.relationships import INFERRED, normalize_relationships

    concept = bundle.read_concept(doc_id)
    entries = normalize_relationships(concept.extensions.get("relates_to"))
    if any(
        entry.get("concept_id") == other_id
        and entry.get("relation") == relation
        for entry in entries
    ):
        return
    entries.append(
        {"concept_id": other_id, "relation": relation, "kind": INFERRED}
    )
    concept.extensions["relates_to"] = entries
    bundle.write_concept(concept)
