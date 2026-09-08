"""Derived knowledge index (D1): rebuild knowledge_edges / knowledge_doc_refs.

OKF frontmatter is the durable record; these two SQLite tables are a
rebuildable cache over it. Every :func:`rebuild_knowledge_index` call
recomputes both tables from the current bundle contents:

- declared edges: the ``relates_to`` extension, relation/kind as declared
- derived edges: tag / affects_modules overlap, materialized as ``kind:
  derived`` rows in both directions (the edges ``search_knowledge``
  expansion boosts, alongside declared extracted edges; inferred edges
  never boost)
- doc refs: entries carrying a ``ref`` in the ``sources``/``verified``
  families

Only declared edges whose target resolves to an existing knowledge concept
are indexed; dangling frontmatter pointers stay in the frontmatter (the
durable record) but never reach the index -- and are reported back by the
rebuild (``dangling`` in the return dict) and by the ingest dry-run
(:func:`dangling_manifest_pointers`) so a declared link that fails to
index is visible instead of silent. A pointer that is not a concept
id may still name its target by source path: ingest records that path as
the promoted doc's ``resource``, so such pointers resolve through the
bundle's resource map. A pair that already has a
declared edge in either direction gets no derived rows -- the explicit
record wins.

All ids are normalized to the bare bundle-relative concept_id shape via
:func:`cairn.knowledge.store.normalize_doc_id` before storage, matching the
``knowledge_embeddings.doc_id`` convention, so path-shaped bundle reads and
bare frontmatter pointers correlate.

Rebuild is idempotent: identical bundle contents produce identical table
contents, including ``created_at`` stamps (preserved for pre-existing edge
keys). The function writes on the caller's connection and never commits --
callers own the transaction boundary.
"""
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


def rebuild_knowledge_index(conn, bundle: OKFBundle) -> Dict[str, Any]:
    """Recompute knowledge_edges + knowledge_doc_refs from the bundle.

    Wipes and repopulates both tables in one transaction-less pass on
    ``conn`` (the caller commits). Idempotent on an unchanged bundle:
    identical rows, identical created_at stamps. Returns counts:
    ``{docs, edges, derived, doc_refs}`` plus ``dangling`` -- the declared
    ``relates_to`` pointers that matched neither a concept id nor a
    resource path (each ``{doc_id, concept_id}``; they stay
    frontmatter-only and never index).
    """
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
    """Stored neighbors of one doc, bare-id in / normalized-dict out.

    Reads both edge directions (doc_id and related_id sides) and normalizes
    the neighbor id with :func:`normalize_doc_id` for callers. Rows the
    bundle no longer resolves are skipped, so a deleted doc never surfaces
    as a phantom neighbor between rebuilds. Output is deterministic:
    outgoing rows first, each direction ordered by neighbor id.
    """
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
    """The supersede chain containing ``doc_id``, ordered oldest -> newest.

    Follows the stored ``supersedes`` / ``superseded-by`` edges (both
    directions declare the same newer/older fact, so either half alone
    walks the full chain). Any chain member may be the entry point: every
    member appears exactly once, positioned causally. Walks are
    visited-set-guarded, so a cyclic edge set terminates instead of
    looping, and edges naming docs the bundle no longer resolves are
    skipped (stale index between rebuilds). A doc with no supersede edges
    is a chain of one.

    Each member dict carries ``concept_id``, ``title``, ``doc_status`` and
    ``relation`` (this member's relation to the NEXT member; ``None`` on
    the last). Raises ``ValueError`` when ``doc_id`` resolves to no
    knowledge concept (see ``resolve_knowledge_doc``).
    """
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
    """Supersede adjacency from the edge index, endpoints resolvable.

    Returns ``(newer_of, older_of, concepts)``: ``newer_of[older]`` is the
    set of docs superseding it, ``older_of[newer]`` the set it supersedes,
    and ``concepts`` caches the resolved concept per id (``None`` for
    unresolvable ids, whose rows are dropped).
    """
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
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """Declared edges for the relates_to entries whose target resolves,
    plus the dangling pointers that matched neither a concept id nor a
    resource path (returned so callers can surface the failed index
    instead of leaving it silent).

    A pointer resolves as a bare/path-shaped concept_id; failing that, as
    the source document named by a promoted doc's ``resource`` path
    (ingest stores the source path there), so author-declared links written
    before concept ids existed still index as declared edges. A pointer
    that resolves to the declaring doc itself produces no edge and is not
    dangling.
    """
    from cairn.knowledge.relationships import normalize_relationships

    resources = _resource_index(docs)
    edges: List[Dict[str, Any]] = []
    dangling: List[Dict[str, str]] = []
    for doc_id, concept in docs:
        for entry in normalize_relationships(
            concept.extensions.get("relates_to")
        ):
            pointer = str(entry.get("concept_id") or "").strip()
            related = normalize_doc_id(bundle, pointer)
            if related not in doc_ids or related == doc_id:
                # Fallback: the pointer may name the source document by path
                # (ingest records that path as the promoted doc's resource).
                candidate = resources.get(pointer.lstrip("/"))
                if candidate is not None:
                    related = candidate
            if related not in doc_ids:
                dangling.append({"doc_id": doc_id, "concept_id": pointer})
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


def dangling_manifest_pointers(
    bundle: OKFBundle, rows: List[Dict[str, Any]]
) -> List[Dict[str, str]]:
    """Dangling relates_to pointers across staged manifest rows (dry run).

    Applies the rebuild's resolution to a not-yet-written manifest: a
    pointer resolves when its normalized id names a knowledge concept --
    promoted in the store, or staged by this same run -- or when it names
    a source document by path (a promoted doc's recorded ``resource``, or
    a staged row's ``source_path``, which becomes the promoted doc's
    resource). Anything else is dangling: it stays frontmatter-only and
    never indexes. Each row carries ``{doc_id, concept_id}`` with
    ``doc_id`` the row's source_path. Deterministic: manifest row order,
    declaration order within a row.
    """
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
    dangling: List[Dict[str, str]] = []
    for row in rows:
        self_id = str(row.get("concept_id") or "")
        if not self_id:
            continue  # a skipped row stages no doc and carries no pointers
        source = str(row.get("source_path") or self_id)
        for entry in normalize_relationships(list(row.get("relationships") or [])):
            pointer = str(entry.get("concept_id") or "").strip()
            related = normalize_doc_id(bundle, pointer)
            if related not in doc_ids or related == self_id:
                # Fallback: the pointer may name a source document by path.
                candidate = resources.get(pointer.lstrip("/"))
                if candidate is not None:
                    related = candidate
            if related not in doc_ids:
                dangling.append({"doc_id": source, "concept_id": pointer})
    return dangling


def _resource_index(docs: List[Tuple[str, OKFConcept]]) -> Dict[str, str]:
    """``{resource path: doc_id}`` for promoted docs carrying one.

    Keys are leading-slash-stripped so workspace-relative and absolute
    spellings of the same source path match. First doc wins per resource
    (sorted iteration order keeps it deterministic).
    """
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
    """Symmetric kind=derived edges for docs sharing tags or modules.

    Pairs with a declared edge in either direction are skipped (the
    explicit record already connects them).
    """
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
    """Replace the doc-refs table contents (no timestamp to preserve)."""
    conn.execute("DELETE FROM knowledge_doc_refs")
    unique = sorted({
        (r["doc_id"], r["ref"], r["ref_kind"], r["verified"]) for r in rows
    })
    conn.executemany(
        "INSERT INTO knowledge_doc_refs (doc_id, ref, ref_kind, verified) "
        "VALUES (?, ?, ?, ?)",
        unique,
    )
    return len(unique)
