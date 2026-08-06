"""Wiki CLI: the wiki group (generate/search)."""
from __future__ import annotations

import click

from .main import DEFAULT_DB_PATH, get_db, main

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
def wiki_generate(repo, db, knowledge, dry_run, show_rejections):
    from ..okf.bundle import OKFBundle
    from ..wiki.generator import generate_wiki_with_critic

    conn = get_db(db)
    try:
        bundle = OKFBundle(knowledge)
        repos = [repo] if repo else [r["id"] for r in conn.execute("SELECT id FROM repos")]
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

