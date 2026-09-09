"""Doc-graph island detection and doc-link task queueing (D1).

Connected components over the knowledge doc graph: nodes are ingested
knowledge docs, edges are every ``knowledge_edges`` row (any relation,
any kind). Islands are the components detached from the corpus, as
pair-units worth one LLM linking pass each:

- a component of exactly two docs (connected to each other, to nothing
  else), or
- a pair of singleton docs (no edges at all), paired in id order; an odd
  singleton stays unpaired.

Each pair-unit queues one ``doc-link`` task whose facts carry the member
concept_ids. Completing such a task is gated by
:mod:`cairn.knowledge.doc_link`. Queueing is idempotent: a member set
that already has a doc-link task (any status) is skipped, so re-ingests
never duplicate tasks.
"""
from __future__ import annotations

from typing import Any, Dict, List, Set

from cairn.okf.bundle import OKFBundle

#: Task kind queued for island pairs.
DOC_LINK_KIND = "doc-link"

#: Resource label for doc-link tasks (members live in the facts).
DOC_LINK_RESOURCE = "knowledge-island"


def knowledge_doc_ids(bundle: OKFBundle) -> List[str]:
    """Bare concept_ids of every parsable knowledge doc, sorted."""
    ids: List[str] = []
    for cid in bundle.list_concepts(prefix="knowledge/"):
        try:
            bundle.read_concept(cid)
        except Exception:
            continue
        ids.append(cid)
    return sorted(ids)


def doc_components(conn, bundle: OKFBundle) -> List[List[str]]:
    """Connected components over the doc graph, each sorted, ordered by
    smallest member id. Docs the bundle can no longer parse are not
    nodes; edge rows naming them are ignored."""
    ids = knowledge_doc_ids(bundle)
    node = set(ids)
    adjacency: Dict[str, Set[str]] = {doc_id: set() for doc_id in ids}
    for doc_id, related_id in conn.execute(
        "SELECT doc_id, related_id FROM knowledge_edges"
    ).fetchall():
        if doc_id in node and related_id in node:
            adjacency[doc_id].add(related_id)
            adjacency[related_id].add(doc_id)

    components: List[List[str]] = []
    seen: Set[str] = set()
    for start in ids:
        if start in seen:
            continue
        stack, component = [start], set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current] - component)
        seen |= component
        components.append(sorted(component))
    return components


def doc_islands(conn, bundle: OKFBundle) -> List[List[str]]:
    """Pair-units worth a doc-link task, in deterministic order.

    Two-doc components queue as themselves; singletons pair in id order
    (first with second, third with fourth, ...). Larger components are
    internally connected already and never queue.
    """
    units: List[List[str]] = []
    singletons: List[str] = []
    for component in doc_components(conn, bundle):
        if len(component) == 2:
            units.append(component)
        elif len(component) == 1:
            singletons.append(component[0])
    for i in range(0, len(singletons) - 1, 2):
        units.append(sorted(singletons[i : i + 2]))
    return units


def queue_doc_link_tasks(conn, bundle: OKFBundle) -> int:
    """Queue one doc-link task per island pair without one yet.

    Dedup key: the island's member set vs the facts of every existing
    doc-link task regardless of status (a completed or dropped task still
    covers its island). Returns the number of tasks created.
    """
    from cairn.llm.tasks import create_task

    covered = _queued_member_sets(bundle)
    queued = 0
    for members in doc_islands(conn, bundle):
        key = frozenset(members)
        if key in covered:
            continue
        create_task(
            bundle,
            task_kind=DOC_LINK_KIND,
            resource=DOC_LINK_RESOURCE,
            facts=_island_facts(bundle, members),
        )
        covered.add(key)
        queued += 1
    return queued


def _queued_member_sets(bundle: OKFBundle) -> Set[frozenset]:
    """Member sets of every existing doc-link task (any status)."""
    from cairn.llm.tasks import list_tasks

    covered: Set[frozenset] = set()
    for task in list_tasks(bundle, kind_prefix=DOC_LINK_KIND):
        members = (task.facts or {}).get("members")
        if isinstance(members, list) and members:
            covered.add(frozenset(str(m) for m in members))
    return covered


def _island_facts(bundle: OKFBundle, members: List[str]) -> Dict[str, Any]:
    """Facts for a doc-link task: member ids plus their titles."""
    titles: Dict[str, str] = {}
    for member in members:
        try:
            titles[member] = bundle.read_concept(member).title or member
        except Exception:
            continue
    return {"members": list(members), "member_titles": titles}
