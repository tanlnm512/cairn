"""Wiki CLI: the wiki group (generate/search/status/retry/export/enrich)."""
from __future__ import annotations

from pathlib import Path

import click
import sys

from .main import DEFAULT_DB_PATH, get_db, main
from ..utils.git import get_repo_head


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


@wiki.command("status")
@click.option("--repo", default=None, help="Only pages of this repo.")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def wiki_status(repo, knowledge):
    """Show per-page wiki state with aggregate counts.

    State is derived at read time (promoted content beats the live chain;
    a done task with no passing critic verdict derives failed), never from
    a stored verdict — the plan kind keeps none."""
    from ..wiki.lifecycle import (
        DERIVED_STATES,
        derived_state,
        page_chains,
        recorded_sha,
        staleness,
    )

    bundle, manifest = _load_manifest_or_exit(knowledge)
    pages = manifest.get("pages", {})
    if not pages:
        click.echo("No wiki pages planned; run 'cairn wiki generate --llm' first.")
        return
    chains = page_chains(bundle)
    counts = dict.fromkeys(DERIVED_STATES, 0)
    staleness_counts = dict.fromkeys(("fresh", "stale", "unknown"), 0)
    heads = {}
    shown = 0
    for key in sorted(pages):
        page_repo, page_id = _split_page_key(key)
        if repo and page_repo != repo:
            continue
        row = pages[key]
        chain = chains.get(key, [])
        state = derived_state(bundle, page_repo, page_id, chain)
        if page_repo not in heads:
            heads[page_repo] = get_repo_head(page_repo)
        page_staleness = staleness(
            recorded_sha(bundle, page_repo, page_id), heads[page_repo]
        )
        shown += 1
        if state in counts:
            counts[state] += 1
        staleness_counts[page_staleness] += 1
        click.echo(f"  {key:<36} {state:<12} {page_staleness:<8} "
                   f"queue_attempts={row.get('queue_attempts', 0)}")
    totals = "  ".join(f"{state}={n}" for state, n in counts.items())
    freshness = "  ".join(f"{s}={n}" for s, n in staleness_counts.items())
    click.echo(f"Wiki pages: {shown}  {totals}  {freshness}")


@wiki.command("retry")
@click.option("--repo", default=None, help="Only pages of this repo.")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def wiki_retry(repo, knowledge):
    """Re-queue failed pages as fresh task chains; promoted pages untouched.

    Failure is derived (a done task whose result lacks a passing critic
    verdict counts — the zombie rescue), never read from a stored state."""
    from ..llm.tasks import create_task
    from ..wiki.lifecycle import derived_state, page_chains, plan_facts
    from ..wiki.manifest import save_manifest

    bundle, manifest = _load_manifest_or_exit(knowledge)
    pages = manifest.get("pages", {})
    if not pages:
        click.echo("No wiki pages planned; run 'cairn wiki generate --llm' first.")
        return
    chains = page_chains(bundle)
    failed = []
    for key in sorted(pages):
        page_repo, page_id = _split_page_key(key)
        if repo and page_repo != repo:
            continue
        if derived_state(bundle, page_repo, page_id, chains.get(key, [])) == "failed":
            failed.append((key, page_repo, page_id))
    if not failed:
        click.echo("Nothing to retry: no failed wiki pages.")
        return
    for key, page_repo, page_id in failed:
        row = pages[key]
        task = create_task(
            bundle,
            "wiki-page",
            key,
            facts=plan_facts(row, page_repo, diagrams=bool(row.get("diagrams"))),
        )
        row["task_id"] = task.id
        row["queue_attempts"] = int(row.get("queue_attempts", 0)) + 1
        click.echo(f"Re-queued wiki-page task {task.id}: {key} "
                   f"(attempt {row['queue_attempts']})")
    if not save_manifest(bundle.root, manifest):
        click.echo("Failed to write the wiki manifest.", err=True)
        sys.exit(1)
    click.echo(f"Re-queued {len(failed)} failed wiki page(s). "
               "Any agent with the cairn skill can process them:")
    click.echo("  cairn task list --kind wiki-page --status pending")
    click.echo("  cairn task claim <id> && cairn task complete <id> --result-file <path>")


@wiki.command("export")
@click.option("--dir", "out_dir", required=True, type=click.Path(path_type=Path),
              help="Directory to write the exported pages into.")
@click.option("--force", is_flag=True,
              help="Overwrite files in an existing non-empty target directory.")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def export(out_dir: Path, force, knowledge):
    """Write every promoted page as DIR/{repo}/{page_id}.md."""
    from ..wiki.lifecycle import read_page_concept

    if out_dir.is_dir() and any(out_dir.iterdir()) and not force:
        click.echo(f"Refusing to export into non-empty directory {out_dir}; "
                   "pass --force to overwrite.", err=True)
        sys.exit(1)
    bundle, manifest = _load_manifest_or_exit(knowledge)
    exported = 0
    for key in sorted(manifest.get("pages", {})):
        page_repo, page_id = _split_page_key(key)
        concept = read_page_concept(bundle, page_repo, page_id)
        if concept is None:
            continue
        concept.to_file(str(out_dir / page_repo / f"{page_id}.md"))
        exported += 1
    click.echo(f"Exported {exported} page(s) to {out_dir}")


@wiki.command("enrich")
@click.argument("page_id", required=False)
@click.option("--repo", default=None, help="Scope the enrichment to one repo.")
@click.option("--all", "enrich_all", is_flag=True,
              help="Queue one enrichment per promoted page across repos.")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def enrich(page_id, repo, enrich_all, knowledge):
    """Queue wiki-page-enrich tasks appending new sections to promoted pages."""
    from ..wiki.pipeline import queue_enrich_tasks

    if page_id is not None and enrich_all:
        click.echo("Enrich one PAGE_ID or --all, not both.", err=True)
        sys.exit(1)
    if page_id is None and not enrich_all:
        click.echo("Enrich one PAGE_ID or --all.", err=True)
        sys.exit(1)
    bundle, _ = _load_manifest_or_exit(knowledge)
    queued = queue_enrich_tasks(bundle, repo=repo, page_id=page_id)
    if not queued and not enrich_all:
        scope = f" in repo '{repo}'" if repo else ""
        click.echo(f"Cannot enrich '{page_id}': no promoted wiki page "
                   f"for it{scope}.", err=True)
        sys.exit(1)
    for task in queued:
        click.echo(f"Queued wiki-page-enrich task {task.id}: {task.resource} "
                   f"({task.facts['repo']})")
    if not queued:
        scope = f" in repo '{repo}'" if repo else ""
        click.echo(f"Nothing to enrich: no promoted wiki pages{scope}.")
        return
    click.echo(f"Queued {len(queued)} wiki-page-enrich task(s). "
               "Any agent with the cairn skill can process them:")
    click.echo("  cairn task list --kind wiki-page-enrich --status pending")
    click.echo("  cairn task claim <id> && cairn task complete <id> --result-file <path>")

