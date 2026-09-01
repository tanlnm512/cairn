"""Wiki CLI: the wiki group (generate/search/status/retry)."""
from __future__ import annotations

import click
import sys

from .main import DEFAULT_DB_PATH, get_db, main
from ..utils.git import get_repo_head

# Display states (hyphenated) for `wiki status`; the manifest stores
# "in_progress" with an underscore.
_DISPLAY_STATES = ("queued", "in-progress", "promoted", "failed", "dropped")


@main.group()
def wiki():
    """Architectural wiki."""


@wiki.command("generate")
@click.option("--repo", default=None, help="Repo name (all repos if omitted).")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
@click.option("--dry-run", is_flag=True,
              help="Run generation + the critic and print each verdict; write nothing.")
@click.option("--show-rejections", is_flag=True,
              help="Print the critic errors/warnings for each generated concept.")
@click.option("--llm", is_flag=True,
              help="Queue one wiki-page task per planned page for agent-decoupled writing.")
@click.option("--pages", default=10, type=int,
              help="With --llm, max pages to plan, overview included (default 10).")
@click.option("--diagrams", is_flag=True,
              help="With --llm, instruct writers to include Mermaid diagrams.")
@click.option("--refine-catalog", is_flag=True,
              help="With --llm, queue a wiki-catalog refinement task first; "
                   "re-run after it completes to queue the page tasks from "
                   "the validated refined outline.")
@click.option("--force", is_flag=True,
              help="With --llm, re-queue every planned page even when "
                   "unchanged and promoted.")
def wiki_generate(repo, db, knowledge, dry_run, show_rejections, llm, pages,
                  diagrams, refine_catalog, force):
    from ..okf.bundle import OKFBundle
    from ..wiki.generator import generate_wiki_with_critic

    conn = get_db(db)
    try:
        bundle = OKFBundle(knowledge)
        repos = [repo] if repo else [r["id"] for r in conn.execute("SELECT id FROM repos")]
        if llm:
            from ..wiki.catalog import WikiPlannerError, build_page_plan
            from ..wiki.manifest import load_manifest
            from ..wiki.pipeline import run_wiki_generate

            if not repos:
                click.echo("No repos indexed; run 'cairn build' first.", err=True)
                sys.exit(1)
            # Plan every repo before queueing anything: a planner failure must
            # leave the queue untouched.
            try:
                plans = [(r, build_page_plan(conn, r, pages_cap=pages)) for r in repos]
            except WikiPlannerError as exc:
                click.echo(f"Cannot plan wiki pages: {exc}", err=True)
                sys.exit(1)
            queued_total = 0
            skipped_total = 0
            catalog_total = 0
            for r, _ in plans:
                result = run_wiki_generate(conn, bundle, r, pages_cap=pages,
                                           force=force,
                                           diagrams=diagrams,
                                           refine_catalog=refine_catalog)
                catalog_id = result.get("catalog_task_id")
                if catalog_id:
                    catalog_total += 1
                    if result.get("catalog_pending"):
                        click.echo(f"wiki-catalog task {catalog_id} still "
                                   f"pending ({r}); the page tasks queue once "
                                   "it completes:")
                    else:
                        click.echo(f"Queued wiki-catalog task {catalog_id}: "
                                   f"refine the wiki outline ({r})")
                        click.echo("Any agent with the cairn skill can process it:")
                    click.echo(f"  cairn task show {catalog_id}        # view the task + facts")
                    click.echo(f"  cairn task claim {catalog_id}       # claim it")
                    click.echo(f"  cairn task complete {catalog_id} --result-file <path>   # submit result")
                    continue
                queued_ids = set(result["queued_task_ids"])
                rows = load_manifest(bundle).get("pages", {})
                for page in result["plan"]:
                    task_id = rows.get(f"{r}/{page['page_id']}", {}).get("task_id")
                    if task_id in queued_ids:
                        queued_total += 1
                        click.echo(f"Queued wiki-page task {task_id}: {page['title']} ({r})")
                    else:
                        skipped_total += 1
                        click.echo(f"Up to date, skipped: {page['title']} ({r})")
            if catalog_total:
                click.echo("Re-run 'cairn wiki generate --llm --refine-catalog' "
                           "after the catalog task completes to queue the page "
                           "tasks.")
                return
            click.echo(f"Queued {queued_total} new wiki-page task(s); "
                       f"{skipped_total} page(s) already up to date. "
                       "Any agent with the cairn skill can process them:")
            click.echo("  cairn task list --kind wiki-page --status pending")
            click.echo("  cairn task claim <id> && cairn task complete <id> --result-file <path>")
            return
        total = 0
        seen = 0  # concepts examined (dry-run reports this instead of writes)
        for r in repos:
            concepts, critic_results = generate_wiki_with_critic(r, conn, bundle)
            for c, result in zip(concepts, critic_results):
                seen += 1
                if dry_run:
                    click.echo(f"--- {c.concept_id} (dry-run; not written) ---")
                    click.echo(f"  passed: {result.passed}  quality: {result.quality_score:.2f}  "
                               f"errors: {len(result.errors)}  warnings: {len(result.warnings)}")
                    if result.errors:
                        for e in result.errors:
                            click.echo(f"    ERROR: {e}")
                    if result.warnings:
                        for w in result.warnings:
                            click.echo(f"    warn: {w}")
                    continue
                if show_rejections or not result.passed:
                    # Surface the verdict even on a normal write when --show-rejections
                    # is set, or whenever the critic found problems.
                    click.echo(f"  generated wiki: {c.concept_id} "
                               f"(quality={result.quality_score:.2f}, "
                               f"errors={len(result.errors)}, warnings={len(result.warnings)})")
                    if not result.passed:
                        for e in result.errors:
                            click.echo(f"    ERROR: {e}")
                        for w in result.warnings:
                            click.echo(f"    warn: {w}")
                    bundle.write_concept(c)
                    total += 1
                else:
                    bundle.write_concept(c)
                    total += 1
                    click.echo(f"  generated wiki: {c.concept_id}")
    finally:
        conn.close()
    if dry_run:
        click.echo(f"Dry-run: examined {seen} wiki concept(s); wrote nothing.")
    else:
        click.echo(f"Generated {total} wiki concepts.")


@wiki.command("search")
@click.argument("query")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def wiki_search(query, knowledge):
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    results = bundle.search(query)
    if not results:
        click.echo(f"No wiki matching '{query}'.")
        return
    for c in results:
        click.echo(f"  {c.title}  ({c.concept_id})")
        if c.description:
            click.echo(f"      {c.description}")


def _load_manifest_or_exit(knowledge):
    from ..okf.bundle import OKFBundle
    from ..wiki.manifest import load_manifest

    bundle = OKFBundle(knowledge)
    try:
        return bundle, load_manifest(bundle)
    except ValueError as exc:
        click.echo(f"Cannot read wiki manifest: {exc}", err=True)
        sys.exit(1)


def _split_page_key(key: str) -> tuple:
    """A manifest key ``"{repo}/{page_id}"`` -> ``(repo, page_id)``."""
    repo, _, page_id = str(key).partition("/")
    return repo, page_id


def _is_promoted(bundle, repo, page_id):
    try:
        bundle.read_concept(f"wiki/pages/{repo}/{page_id}")
        return True
    except Exception:
        return False


def _recorded_sha(bundle, row, repo, page_id):
    """The page's recorded commit sha: the promoted concept's extension is
    the source of truth; a page with no concept falls back to the manifest
    row's queue-time sha."""
    try:
        concept = bundle.read_concept(f"wiki/pages/{repo}/{page_id}")
    except Exception:
        return row.get("commit_sha") or None
    return concept.extensions.get("commit_sha") or None


def _staleness(recorded_sha, head):
    """fresh: recorded sha equals HEAD; stale: both present and differing;
    unknown: either side unavailable."""
    if not recorded_sha or not head:
        return "unknown"
    return "fresh" if recorded_sha == head else "stale"


def _wiki_chains(bundle):
    """Page id -> every wiki-page task for it, across all revise hops."""
    from ..llm.tasks import list_tasks

    chains = {}
    for task in list_tasks(bundle):
        if task.task_kind.startswith("wiki-page"):
            chains.setdefault(task.resource, []).append(task)
    return chains


def _latest(tasks):
    return max(tasks, key=lambda t: (t.created_at, t.attempt))


def _result_critic_status(bundle, task_id):
    from ..llm.tasks import TASK_DIR

    try:
        result = bundle.read_concept(f"{TASK_DIR}/{task_id}.result")
    except Exception:
        return None
    return result.extensions.get("critic_status")


def _page_state(bundle, row, repo, page_id, chain):
    """Derived state: promoted by concept (never the stored row), else the
    live chain, else dropped when an explicitly dropped task is in the chain
    (terminal -- retry's failed-only selection never resurrects it), else
    failed when the row says so or the chain dropped -- terminal done task
    whose result failed the critic with no successor."""
    if _is_promoted(bundle, repo, page_id):
        return "promoted"
    if any(t.status == "in-progress" for t in chain):
        return "in-progress"
    if any(t.status == "pending" for t in chain):
        return "queued"
    if any(t.status == "dropped" for t in chain):
        return "dropped"
    if row.get("state") == "failed":
        return "failed"
    done = [t for t in chain if t.status == "done"]
    if done and _result_critic_status(bundle, _latest(done).id) == "failed":
        return "failed"
    return str(row.get("state", "planned")).replace("_", "-")


@wiki.command("status")
@click.option("--repo", default=None, help="Only pages of this repo.")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def wiki_status(repo, knowledge):
    """Show per-page wiki state with aggregate counts."""
    bundle, manifest = _load_manifest_or_exit(knowledge)
    pages = manifest.get("pages", {})
    if not pages:
        click.echo("No wiki pages planned; run 'cairn wiki generate --llm' first.")
        return
    chains = _wiki_chains(bundle)
    counts = dict.fromkeys(_DISPLAY_STATES, 0)
    staleness_counts = dict.fromkeys(("fresh", "stale", "unknown"), 0)
    heads = {}
    shown = 0
    for key in sorted(pages):
        page_repo, page_id = _split_page_key(key)
        if repo and page_repo != repo:
            continue
        row = pages[key]
        state = _page_state(bundle, row, page_repo, page_id,
                            chains.get(page_id, []))
        if page_repo not in heads:
            heads[page_repo] = get_repo_head(page_repo)
        staleness = _staleness(_recorded_sha(bundle, row, page_repo, page_id),
                               heads[page_repo])
        shown += 1
        if state in counts:
            counts[state] += 1
        staleness_counts[staleness] += 1
        click.echo(f"  {key:<36} {state:<12} {staleness:<8} "
                   f"attempts={row.get('attempts', 0)}")
    totals = "  ".join(f"{state}={n}" for state, n in counts.items())
    freshness = "  ".join(f"{s}={n}" for s, n in staleness_counts.items())
    click.echo(f"Wiki pages: {shown}  {totals}  {freshness}")


@wiki.command("retry")
@click.option("--repo", default=None, help="Only pages of this repo.")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def wiki_retry(repo, knowledge):
    """Re-queue failed pages as fresh task chains; promoted pages untouched."""
    from ..llm.tasks import create_task
    from ..wiki.manifest import save_manifest

    bundle, manifest = _load_manifest_or_exit(knowledge)
    pages = manifest.get("pages", {})
    if not pages:
        click.echo("No wiki pages planned; run 'cairn wiki generate --llm' first.")
        return
    chains = _wiki_chains(bundle)
    failed = []
    for key in sorted(pages):
        page_repo, page_id = _split_page_key(key)
        if repo and page_repo != repo:
            continue
        if _page_state(bundle, pages[key], page_repo, page_id,
                       chains.get(page_id, [])) == "failed":
            failed.append((key, page_repo, page_id))
    if not failed:
        click.echo("Nothing to retry: no failed wiki pages.")
        return
    for key, page_repo, page_id in failed:
        row = pages[key]
        facts = {
            "title": row.get("title", page_id),
            "description": row.get("description", ""),
            "module": row.get("module", ""),
            "seeds": row.get("seeds", []),
            "input_hash": row.get("input_hash", ""),
            "repo": page_repo,
        }
        if row.get("diagrams"):
            facts["diagrams"] = True
        task = create_task(bundle, "wiki-page", page_id, facts=facts)
        row["task_id"] = task.id
        row["state"] = "queued"
        row["attempts"] = int(row.get("attempts", 0)) + 1
        click.echo(f"Re-queued wiki-page task {task.id}: {key} "
                   f"(attempt {row['attempts']})")
    if not save_manifest(bundle.root, manifest):
        click.echo("Failed to write the wiki manifest.", err=True)
        sys.exit(1)
    click.echo(f"Re-queued {len(failed)} failed wiki page(s). "
               "Any agent with the cairn skill can process them:")
    click.echo("  cairn task list --kind wiki-page --status pending")
    click.echo("  cairn task claim <id> && cairn task complete <id> --result-file <path>")

