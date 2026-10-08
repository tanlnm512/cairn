"""Derived knowledge index: rebuild knowledge_edges / knowledge_doc_refs."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from cairn.knowledge.relationships import DEFAULT_RELATION, EXTRACTED
from cairn.knowledge.store import normalize_doc_id, resolve_knowledge_doc
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept

logger = logging.getLogger(__name__)

#: Relation used for materialized tag/module-overlap edges.
DERIVED_RELATION = "relates-to"

#: Kind for recomputed (non-declared) overlap edges.
DERIVED_KIND = "derived"

#: Provenance of edges scanned from frontmatter.
PROVENANCE_FRONTMATTER = "frontmatter:relates_to"

#: Provenance values for derived edges, by what overlapped.
PROVENANCE_TAG_OVERLAP = "tag-overlap"
PROVENANCE_MODULE_OVERLAP = "module-overlap"
PROVENANCE_TAG_MODULE_OVERLAP = "tag+module-overlap"

#: ref_kind assumed for verified-family entries that carry no kind.
DEFAULT_REF_KIND = "file"

#: Reason stamped on dangling entries whose basename matched several
#: resources, so no single target could be picked.
AMBIGUOUS_POINTER_REASON = "ambiguous pointer"


def rebuild_knowledge_index(conn, bundle: OKFBundle) -> Dict[str, Any]:
    """Rebuild both derived tables and return counts plus dangling pointers."""
    docs = _load_docs(bundle)
    doc_ids = {doc_id for doc_id, _ in docs}
    declared, dangling = _frontmatter_edges(bundle, docs, doc_ids)
    derived = _derived_edges(docs, {(e["doc_id"], e["related_id"]) for e in declared})
    edges = _write_edges(conn, declared + derived)
    refs = _write_refs(conn, _doc_ref_rows(docs))
    return {
        "docs": len(docs),
        "edges": edges,
        "derived": len(derived),
        "doc_refs": refs,
        "dangling": dangling,
    }


def related_docs(conn, bundle: OKFBundle, doc_id: str) -> List[Dict[str, Any]]:
    """Return deterministic normalized neighbors from both edge directions."""
    normalized = normalize_doc_id(bundle, doc_id)
    neighbors: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, str, str]] = set()
    for direction, column, order in (
        ("outgoing", "doc_id", "related_id"),
        ("incoming", "related_id", "doc_id"),
    ):
        for row in conn.execute(
            f"SELECT doc_id, related_id, relation, kind, provenance, created_at "
            f"FROM knowledge_edges WHERE {column} = ? ORDER BY {order}",
            (normalized,),
        ):
            neighbor = row[1] if column == "doc_id" else row[0]
            key = (row[0], row[1], row[2], row[3])
            if key in seen:
                continue
            try:
                concept = bundle.read_concept(neighbor)
            except Exception:
                continue  # deleted between rebuilds: never a phantom neighbor
            seen.add(key)
            neighbors.append({
                "doc_id": neighbor,
                "title": concept.title,
                "relation": row[2],
                "kind": row[3],
                "provenance": row[4],
                "created_at": row[5],
                "direction": direction,
            })
    return neighbors


def supersede_chain(conn, bundle: OKFBundle, doc_id: str) -> List[Dict[str, Any]]:
    """Return the oldest-to-newest supersede chain containing ``doc_id``."""
    concept = resolve_knowledge_doc(bundle, doc_id)
    start = normalize_doc_id(bundle, concept.concept_id)
    newer_of, older_of, concepts = _supersede_facts(conn, bundle)
    concepts.setdefault(start, concept)  # a chain of one is never an edge endpoint

    # Walk back to the oldest member (deterministic on branched chains).
    visited = {start}
    oldest = start
    while True:
        older = next(
            (c for c in sorted(older_of.get(oldest, ())) if c not in visited),
            None,
        )
        if older is None:
            break
        visited.add(older)
        oldest = older

    # Walk forward collecting members oldest -> newest.
    chain_ids: List[str] = []
    seen: Set[str] = set()
    current: Optional[str] = oldest
    while current is not None and current not in seen:
        seen.add(current)
        chain_ids.append(current)
        current = next(
            (n for n in sorted(newer_of.get(current, ())) if n not in seen),
            None,
        )
    # The queried doc must be in its own chain; on a branched DAG the
    # sorted-first successor pick can diverge from it, so guard loudly.
    assert start in chain_ids, (
        f"supersede walk from {start} dropped the start doc: branched "
        "supersede DAG (linear chains are the writer contract)"
    )

    members: List[Dict[str, Any]] = []
    for pos, cid in enumerate(chain_ids):
        member = concepts[cid]
        members.append({
            "concept_id": cid,
            "title": member.title,
            "doc_status": member.extensions.get("doc_status", "active"),
            "relation": "superseded-by" if pos + 1 < len(chain_ids) else None,
        })
    return members


def _supersede_facts(conn, bundle: OKFBundle):
    """Return resolvable supersede adjacency and concept cache maps."""
    newer_of: Dict[str, Set[str]] = {}
    older_of: Dict[str, Set[str]] = {}
    concepts: Dict[str, Optional[OKFConcept]] = {}

    def concept_for(cid: str) -> Optional[OKFConcept]:
        if cid not in concepts:
            try:
                concepts[cid] = bundle.read_concept(cid)
            except Exception:
                concepts[cid] = None
        return concepts[cid]

    for doc_id, related_id, relation in conn.execute(
        "SELECT doc_id, related_id, relation FROM knowledge_edges "
        "WHERE relation IN ('supersedes', 'superseded-by')"
    ).fetchall():
        newer, older = (
            (doc_id, related_id) if relation == "supersedes"
            else (related_id, doc_id)
        )
        if concept_for(newer) is None or concept_for(older) is None:
            continue
        newer_of.setdefault(older, set()).add(newer)
        older_of.setdefault(newer, set()).add(older)
    return newer_of, older_of, concepts


def _load_docs(bundle: OKFBundle) -> List[Tuple[str, OKFConcept]]:
    """(bare concept_id, concept) for every parsable knowledge doc."""
    docs: List[Tuple[str, OKFConcept]] = []
    for cid in bundle.list_concepts(prefix="knowledge/"):
        try:
            docs.append((cid, bundle.read_concept(cid)))
        except Exception as e:
            logger.warning("Skipping unparsable knowledge doc %s: %s", cid, e)
    return docs


def _frontmatter_edges(
    bundle: OKFBundle,
    docs: List[Tuple[str, OKFConcept]],
    doc_ids: Set[str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return resolved declared edges and dangling pointer warnings."""
    from cairn.knowledge.relationships import normalize_relationships

    resources = _resource_index(docs)
    edges: List[Dict[str, Any]] = []
    dangling: List[Dict[str, Any]] = []
    for doc_id, concept in docs:
        for entry in normalize_relationships(
            concept.extensions.get("relates_to")
        ):
            pointer = str(entry.get("concept_id") or "").strip()
            related, ambiguous = _resolve_pointer(
                bundle, pointer, doc_ids, resources, doc_id
            )
            if related is None:
                dangling.append(
                    _pointer_warning(doc_id, pointer, ambiguous)
                )
                continue
            if related == doc_id:
                continue
            edges.append({
                "doc_id": doc_id,
                "related_id": related,
                "relation": str(entry.get("relation") or DEFAULT_RELATION),
                "kind": str(entry.get("kind") or EXTRACTED),
                "provenance": PROVENANCE_FRONTMATTER,
            })
    return edges, dangling


def _pointer_warning(
    doc_id: str,
    pointer: str,
    ambiguous: Optional[List[str]],
) -> Dict[str, Any]:
    """Return an unresolved-pointer warning with optional candidates."""
    warning: Dict[str, Any] = {"doc_id": doc_id, "concept_id": pointer}
    if ambiguous is not None:
        warning["reason"] = AMBIGUOUS_POINTER_REASON
        warning["candidates"] = ambiguous
    return warning


def _resolve_pointer(
    bundle: OKFBundle,
    pointer: str,
    doc_ids: Set[str],
    resources: Dict[str, str],
    self_id: str,
) -> Tuple[Optional[str], Optional[List[str]]]:
    """Return one pointer's resolved doc id and basename ambiguity."""
    related = normalize_doc_id(bundle, pointer)
    ambiguous: Optional[List[str]] = None
    if related not in doc_ids or related == self_id:
        candidate = resources.get(pointer.lstrip("/"))
        if candidate is None:
            bare = pointer.lstrip("/")
            matches = sorted(
                (resource for resource in resources if resource.endswith(f"/{bare}")),
                key=lambda resource: (len(resource), resource),
            )
            # A unique basename match extends the pointer; picking among
            # two or more would index a guess.
            if len(matches) == 1:
                candidate = resources[matches[0]]
            elif matches:
                ambiguous = matches
        if candidate is not None:
            related = candidate
    if related in doc_ids:
        return related, None
    return None, ambiguous


def dangling_manifest_pointers(
    bundle: OKFBundle, rows: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Return staged relates_to pointers that would not index."""
    from cairn.knowledge.relationships import normalize_relationships

    docs = _load_docs(bundle) if bundle.root.exists() else []
    doc_ids = {doc_id for doc_id, _ in docs}
    doc_ids.update(
        str(row.get("concept_id"))
        for row in rows
        if row.get("concept_id")
    )
    resources = _resource_index(docs)
    for row in rows:
        if row.get("source_path") and row.get("concept_id"):
            resources.setdefault(
                str(row["source_path"]).lstrip("/"),
                str(row["concept_id"]),
            )
    dangling: List[Dict[str, Any]] = []
    for row in rows:
        self_id = str(row.get("concept_id") or "")
        if not self_id:
            continue  # a skipped row stages no doc and carries no pointers
        source = str(row.get("source_path") or self_id)
        for entry in normalize_relationships(list(row.get("relationships") or [])):
            pointer = str(entry.get("concept_id") or "").strip()
            related, ambiguous = _resolve_pointer(
                bundle, pointer, doc_ids, resources, self_id
            )
            if related is None:
                dangling.append(
                    _pointer_warning(source, pointer, ambiguous)
                )
    return dangling


def _resource_index(docs: List[Tuple[str, OKFConcept]]) -> Dict[str, str]:
    """Return resource paths mapped to first promoted doc ids."""
    index: Dict[str, str] = {}
    for doc_id, concept in docs:
        resource = (concept.resource or "").strip()
        if not resource:
            continue
        index.setdefault(resource.lstrip("/"), doc_id)
    return index


def _derived_edges(
    docs: List[Tuple[str, OKFConcept]],
    declared_pairs: Set[Tuple[str, str]],
) -> List[Dict[str, Any]]:
    """Return overlap edges not already declared in either direction."""
    meta = [
        (
            doc_id,
            set(concept.tags or []),
            set(concept.extensions.get("affects_modules") or []),
        )
        for doc_id, concept in docs
    ]
    edges: List[Dict[str, Any]] = []
    for i in range(len(meta)):
        doc_a, tags_a, mods_a = meta[i]
        for j in range(i + 1, len(meta)):
            doc_b, tags_b, mods_b = meta[j]
            if (doc_a, doc_b) in declared_pairs or (doc_b, doc_a) in declared_pairs:
                continue
            tag_hit = bool(tags_a & tags_b)
            mod_hit = bool(mods_a & mods_b)
            if not (tag_hit or mod_hit):
                continue
            provenance = (
                PROVENANCE_TAG_MODULE_OVERLAP if tag_hit and mod_hit
                else PROVENANCE_TAG_OVERLAP if tag_hit
                else PROVENANCE_MODULE_OVERLAP
            )
            for src, dst in ((doc_a, doc_b), (doc_b, doc_a)):
                edges.append({
                    "doc_id": src,
                    "related_id": dst,
                    "relation": DERIVED_RELATION,
                    "kind": DERIVED_KIND,
                    "provenance": provenance,
                })
    return edges


def _doc_ref_rows(docs: List[Tuple[str, OKFConcept]]) -> List[Dict[str, Any]]:
    """knowledge_doc_refs rows from sources/verified family entries."""
    rows: List[Dict[str, Any]] = []
    for doc_id, concept in docs:
        for family in (concept.verified, concept.sources):
            for entry in family or []:
                if not isinstance(entry, dict):
                    continue
                ref = str(entry.get("ref") or "").strip()
                if not ref:
                    continue
                rows.append({
                    "doc_id": doc_id,
                    "ref": ref,
                    "ref_kind": str(entry.get("kind") or DEFAULT_REF_KIND),
                    "verified": 1 if entry.get("verified") else 0,
                })
    return rows


def _write_edges(conn, edges: List[Dict[str, Any]]) -> int:
    """Replace the edge table contents, preserving created_at for keys
    that already exist (rebuild idempotency includes the timestamps)."""
    existing = {
        (r[0], r[1], r[2], r[3]): r[4]
        for r in conn.execute(
            "SELECT doc_id, related_id, relation, kind, created_at "
            "FROM knowledge_edges"
        ).fetchall()
    }
    conn.execute("DELETE FROM knowledge_edges")
    now = time.time()
    rows = []
    seen: Set[Tuple[str, str, str, str]] = set()
    for e in edges:
        key = (e["doc_id"], e["related_id"], e["relation"], e["kind"])
        if key in seen:
            continue
        seen.add(key)
        rows.append((*key, e["provenance"], existing.get(key, now)))
    conn.executemany(
        "INSERT INTO knowledge_edges "
        "(doc_id, related_id, relation, kind, provenance, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def _write_refs(conn, rows: List[Dict[str, Any]]) -> int:
    """Replace doc refs, deduping on PK with max verified flags."""
    conn.execute("DELETE FROM knowledge_doc_refs")
    verified_by_key: Dict[Tuple[str, str, str], int] = {}
    for r in rows:
        key = (r["doc_id"], r["ref"], r["ref_kind"])
        verified_by_key[key] = max(verified_by_key.get(key, 0), r["verified"])
    unique = sorted(
        (doc_id, ref, ref_kind, verified)
        for (doc_id, ref, ref_kind), verified in verified_by_key.items()
    )
    conn.executemany(
        "INSERT INTO knowledge_doc_refs (doc_id, ref, ref_kind, verified) "
        "VALUES (?, ?, ?, ?)",
        unique,
    )
    return len(unique)
