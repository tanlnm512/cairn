"""Wiki lifecycle: the single owner of page identity and derived truth.

The wiki stores two kinds with disjoint jobs, and this module is the only
code allowed to answer questions that cross them:

- **Plan** (``_wiki/manifest.json``, see :mod:`cairn.wiki.manifest`) is
  pipeline *intent*: which pages should exist (identity, title/description,
  module, seeds, input hash) and where their queue work stands (task
  linkage, queue attempts). It never describes content.
- **Content** (promoted ``Wiki-Article`` concepts at
  ``wiki/pages/{repo}/{page_id}``) is the only record of what *exists*:
  body, verified sources, provenance (``commit_sha``, ``task_id``).

Every reader (CLI, dashboard, pipeline skip logic) derives lifecycle state,
promotion, and staleness through this module at read time; a stored
lifecycle verdict does not exist and must never be introduced. The wiki is
the agent-facing knowledge surface for the whole workspace — code or
documents — searchable via ``search_knowledge``/``ask_compass`` and
explorable in the dashboard.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# The lifecycle vocabulary every read model exposes. Derived at read time;
# never stored anywhere.
DERIVED_STATES = (
    "planned",
    "queued",
    "in-progress",
    "promoted",
    "failed",
    "dropped",
)

# Task kinds that WRITE page content (generation or revision). Enrichment
# is content maintenance, never generation: it neither blocks nor feeds
# the plan's skip decisions. The revise-kind derivation appends "-revise"
# generically, so chains can reach "wiki-page-revise-revise" at the cap —
# match by prefix, never by exact membership.
WRITE_KIND_PREFIX = "wiki-page"


def _is_write_kind(task_kind: str) -> bool:
    return task_kind.startswith(WRITE_KIND_PREFIX) and not task_kind.startswith(
        WRITE_KIND_PREFIX + "-enrich"
    )


def page_concept_id(repo: str, page_id: str) -> str:
    """The content concept id for a page: ``wiki/pages/{repo}/{page_id}``."""
    return f"wiki/pages/{repo}/{page_id}"


def read_page_concept(bundle: Any, repo: str, page_id: str) -> Optional[Any]:
    """The promoted ``Wiki-Article`` concept for a page, or None.

    None means "no content": concept missing, unreadable, or of another
    type. Never raises — an unreadable concept is never fatal.
    """
    try:
        concept = bundle.read_concept(page_concept_id(repo, page_id))
    except Exception:
        return None
    if concept is None or concept.type != "Wiki-Article":
        return None
    return concept


def is_promoted(bundle: Any, repo: str, page_id: str) -> bool:
    """True when the page has readable promoted content.

    The one promotion check; never trust a stored state for this.
    """
    return read_page_concept(bundle, repo, page_id) is not None


def plan_facts(entry: Dict[str, Any], repo: str, diagrams: bool = False) -> Dict[str, Any]:
    """The work-order projection of a plan entry — the single facts shape
    every producer (queue, retry, enrich) builds, so the plan's copies of
    title/description/seeds cannot drift apart. Carries no lifecycle
    verdict, no content body, and no content sha: the body is read from
    the promoted concept at completion and provenance is resolved then.
    """
    facts: Dict[str, Any] = {
        "title": entry.get("title", ""),
        "description": entry.get("description", ""),
        "module": entry.get("module", ""),
        "seeds": entry.get("seeds", {}),
        "input_hash": entry.get("input_hash", ""),
        "repo": repo,
    }
    if diagrams:
        facts["diagrams"] = True
    return facts


def _chain_key(task: Any) -> str:
    """The chain key for a write task: qualified resources (``{repo}/
    {page_id}``, the current queue format) are used as-is; legacy bare
    page-id tasks compose their repo from facts when it is known."""
    if "/" in task.resource:
        return task.resource
    repo = (task.facts or {}).get("repo")
    return f"{repo}/{task.resource}" if repo else task.resource


def page_chains(bundle: Any) -> Dict[str, List[Any]]:
    """Composite key ``{repo}/{resource}`` -> the page's write-chain tasks.

    Chains group every task of the page's write kinds across all revise
    hops. Legacy tasks queued before repo-qualified resources (bare page-id
    resources) are keyed by the composite from their facts' repo; only a
    repo-less legacy task falls back to its bare resource key.
    """
    from ..llm.tasks import list_tasks

    chains: Dict[str, List[Any]] = {}
    for task in list_tasks(bundle):
        if not _is_write_kind(task.task_kind):
            continue
        chains.setdefault(_chain_key(task), []).append(task)
    return chains


def live_generation_tasks(bundle: Any) -> Dict[str, List[Any]]:
    """Composite key -> live (pending / in-progress) write-chain tasks.

    Enrichment tasks are deliberately excluded: an in-flight enrich is
    content maintenance and must neither block a plan's skip decision nor
    race a regeneration.
    """
    from ..llm.tasks import list_tasks

    live: Dict[str, List[Any]] = {}
    for task in list_tasks(bundle):
        if not _is_write_kind(task.task_kind):
            continue
        if task.status not in ("pending", "in-progress"):
            continue
        live.setdefault(_chain_key(task), []).append(task)
    return live


def _latest(tasks: List[Any]) -> Any:
    return max(tasks, key=lambda t: (t.created_at, t.attempt))


def _result_critic_status(bundle: Any, task_id: str) -> Optional[str]:
    """The chain task's result critic verdict, or None when it never landed
    (a done task with no verdict is a zombie: completion failed mid-flight)."""
    from ..llm.tasks import TASK_DIR

    try:
        result = bundle.read_concept(f"{TASK_DIR}/{task_id}.result")
    except Exception:
        return None
    return result.extensions.get("critic_status")


def derived_state(
    bundle: Any, repo: str, page_id: str, chain: List[Any]
) -> str:
    """The page's lifecycle state, derived at read time — never stored.

    Precedence: promoted (readable content) beats everything; then the
    live chain (in-progress, queued); then dropped; then failed — a
    terminal done task whose result has no passing critic verdict counts
    as failed, which rescues zombies (completion that died mid-flight)
    into ``wiki retry``'s reach; else planned (in the plan, never queued).
    """
    if is_promoted(bundle, repo, page_id):
        return "promoted"
    if any(t.status == "in-progress" for t in chain):
        return "in-progress"
    if any(t.status == "pending" for t in chain):
        return "queued"
    if any(t.status == "dropped" for t in chain):
        return "dropped"
    done = [t for t in chain if t.status == "done"]
    if done and _result_critic_status(bundle, _latest(done).id) != "passed":
        return "failed"
    return "planned"


def recorded_sha(bundle: Any, repo: str, page_id: str) -> Optional[str]:
    """The page content's provenance sha (concept extension), or None.

    Content-only by design: a page with no promoted content has no sha,
    so staleness never verdicts on non-existent content.
    """
    concept = read_page_concept(bundle, repo, page_id)
    if concept is None:
        return None
    return concept.extensions.get("commit_sha") or None


def staleness(recorded: Optional[str], head: Optional[str]) -> str:
    """fresh: recorded sha equals HEAD; stale: both present and differing;
    unknown: either side unavailable."""
    if not recorded or not head:
        return "unknown"
    return "fresh" if recorded == head else "stale"
