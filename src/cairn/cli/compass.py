"""Compass CLI: the compass group (generate/list/validate/gaps)."""
from __future__ import annotations

import click
import json
import os
import subprocess
import sys
from pathlib import Path

from .main import DEFAULT_DB_PATH, DEFAULT_KNOWLEDGE_PATH, builder, get_db, main, queries, scanner_mod
from ._helpers import _human_bytes, _mods, _shorten  # noqa: F401

@main.group()
def compass():
    """Compass module navigation files."""


@compass.command("generate")
@click.argument("module")
@click.option("--repo", default=None, help="Repo name (auto-inferred if omitted).")
@click.option("--use-llm", is_flag=True, help="Agent-decoupled synthesis with revise loop.")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
@click.option("--dry-run", is_flag=True,
              help="Run generation + the critic and print the result; write nothing.")
@click.option("--show-rejections", is_flag=True,
              help="With --use-llm, print every revise cycle's critic verdict.")
def compass_generate(module, repo, db, knowledge, use_llm, dry_run, show_rejections):
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    if use_llm:
        import os

        backend = os.environ.get("CAIRN_LLM_BACKEND", "file-queue").lower()
        if backend in ("droid", "opencode", "claude"):
            # Synchronous subprocess: agent runs inline, revise loop completes.
            from ..compass.generator import generate_compass_with_llm
            from ..llm.client import get_client

            client = get_client(bundle)
            outcome = generate_compass_with_llm(module, conn, bundle, repo=repo, client=client)
            concept = outcome["concept"]

            # --show-rejections: surface the per-cycle critic trace that the
            # default path discards. This is the "agent being fact-checked" view.
            if show_rejections:
                trace = outcome.get("trace") or []
                click.echo(f"--- critic trace ({len(trace)} cycle(s)) ---")
                for t in trace:
                    cyc = t.get("cycle", "?")
                    errs = t.get("errors", [])
                    qual = t.get("quality", 0.0)
                    verdict = "REJECTED" if errs else "ok"
                    click.echo(f"  cycle {cyc}: quality={qual:.2f} "
                               f"({len(errs)} error(s)) [{verdict}]")
                    for e in errs[:3]:
                        click.echo(f"    - {e}")
                click.echo("")

            if dry_run:
                conn.close()
                click.echo(f"Would write compass ({outcome['mode']}): {concept.concept_id}")
                click.echo(f"  revise cycles: {outcome.get('cycles', 0)}")
                errs = outcome.get("fact_errors") or []
                click.echo(f"  fact errors: {len(errs)}")
                for e in errs[:5]:
                    click.echo(f"    {e}")
                click.echo("\n--- body (not written) ---\n")
                click.echo(concept.body)
                return

            bundle.write_concept(concept)
            conn.close()
            click.echo(f"Generated compass ({outcome['mode']}): {concept.concept_id}")
            click.echo(f"  revise cycles: {outcome.get('cycles', 0)}")
            errs = outcome.get("fact_errors") or []
            click.echo(f"  fact errors: {len(errs)}")
            for e in errs[:5]:
                click.echo(f"    {e}")
            click.echo("\n--- body ---\n")
            click.echo(concept.body)
            return
        # Decoupled file-queue: enqueue task and return (don't block).
        from ..compass.generator import _gather_facts
        from ..llm.tasks import create_task

        facts = _gather_facts(conn, module, repo)
        t = create_task(bundle, "compass-synthesize", module, facts=facts)
        conn.close()
        click.echo(f"Queued compass task: {t.id}")
        click.echo("Any agent with the cairn skill can process it:")
        click.echo(f"  cairn task show {t.id}        # view the task + facts")
        click.echo(f"  cairn task claim {t.id}       # claim it")
        click.echo(f"  cairn task complete {t.id} --result-file <path>   # submit result")
        click.echo("On completion, run: cairn compass validate  (deterministic critic)")
        return
    # Deterministic path.
    from ..compass.generator import generate_compass

    concept = generate_compass(module, conn, bundle, repo=repo)
    from ..compass.critic import critic_concept

    result = critic_concept(concept, conn)

    if dry_run:
        conn.close()
        click.echo(f"--- critic verdict (dry-run; nothing written) ---")
        click.echo(f"  passed: {result.passed}  quality: {result.quality_score:.2f}  "
                   f"errors: {len(result.errors)}  warnings: {len(result.warnings)}")
        for e in result.errors:
            click.echo(f"  ERROR: {e}")
        for w in result.warnings:
            click.echo(f"  warn: {w}")
        click.echo("\n--- body (not written) ---\n")
        click.echo(concept.body)
        return

    if not result.passed:
        # The deterministic body is built entirely from graph facts (filenames,
        # symbols, cross-deps all come from SQLite queries), so it should always
        # pass the critic. A failure here indicates a generator/critic bug or a
        # stale graph — don't ship a broken file; surface the errors instead.
        conn.close()
        click.echo(f"Compass rejected by critic (quality={result.quality_score:.2f}, "
                   f"{len(result.errors)} error(s)) — not written.")
        for e in result.errors:
            click.echo(f"  ERROR: {e}")
        for w in result.warnings:
            click.echo(f"  warn: {w}")
        click.echo("\nThe deterministic generator is graph-sourced, so this usually "
                   "means a stale graph (run `cairn build`) or a generator bug.")
        sys.exit(1)
    bundle.write_concept(concept)
    conn.close()
    click.echo(f"Generated compass: {concept.concept_id}")
    click.echo(f"  quality: {result.quality_score:.2f}, errors: {len(result.errors)}")
    for e in result.errors:
        click.echo(f"  ERROR: {e}")
    click.echo("\n--- body ---\n")
    click.echo(concept.body)


@compass.command("list")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def compass_list(knowledge):
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    ids = bundle.list_concepts(prefix="compass/")
    if not ids:
        click.echo("No compass files. Generate with: cairn compass generate <module>")
        return
    for cid in ids:
        try:
            c = bundle.read_concept(cid)
            click.echo(f"  {c.title:40} {cid}")
        except Exception as e:
            click.echo(f"  {cid} (read error: {e})")


@compass.command("validate")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def compass_validate(db, knowledge):
    from ..compass.critic import critic_concept
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    ids = bundle.list_concepts(prefix="compass/")
    if not ids:
        click.echo("No compass files to validate.")
        return
    total_errors = 0
    for cid in ids:
        concept = bundle.read_concept(cid)
        result = critic_concept(concept, conn)
        status = "OK" if result.passed else "FAIL"
        click.echo(f"  [{status}] {cid} (quality={result.quality_score:.2f})")
        for e in result.errors:
            click.echo(f"      ERROR: {e}")
            total_errors += 1
        for w in result.warnings:
            click.echo(f"      warn: {w}")
    conn.close()
    click.echo(f"\n{total_errors} total errors across {len(ids)} compass files.")


@compass.command("gaps")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def compass_gaps(db, knowledge):
    from ..compass.gaps import detect_gaps
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    gaps = detect_gaps(conn, bundle)
    conn.close()
    if not gaps:
        click.echo("No coverage gaps (all modules have compass files).")
        return
    click.echo(f"{len(gaps)} modules without compass coverage:")
    for g in gaps:
        click.echo(f"  {g}")


@compass.command("flow")
@click.argument("entry")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
@click.option("--dry-run", is_flag=True, help="Trace and print the flow without writing any file.")
@click.option("--as-workflow", is_flag=True,
              help="Also generate a Knowledge-workflow doc with the traced steps as ordered, editable steps.")
@click.option("--max-steps", default=20, type=int,
              help="With --as-workflow, cap the number of workflow steps (default 20).")
@click.option("--use-llm", is_flag=True,
              help="Queue the flow for agent-decoupled LLM synthesis (file-queue).")
def compass_flow(entry, db, knowledge, dry_run, as_workflow, max_steps, use_llm):
    """Generate a compass for a business FLOW, traced from an entry-point symbol.

    Unlike `compass generate <module>` (which answers "what lives in this
    directory?"), this traces the downward call chain from an entry point --
    an HTTP handler, CLI command, Activity.onCreate, or any public symbol --
    across module boundaries, and synthesizes a narrative of what happens
    when it runs.

    The traced chain, branch points, and terminal calls all come from the L1
    graph (no LLM required for the deterministic path). The critic gates the
    write just as it does for module compasses.

    Use --as-workflow to also generate a Knowledge-workflow doc (procedural
    knowledge) from the same trace. The workflow's steps are the traced call
    chain, each anchored to its symbol/file — editable via `cairn knowledge
    workflow trace` after generation.

    Use --use-llm to queue the flow for agent-decoupled synthesis instead of
    the deterministic template. An agent with the cairn skill can then
    process it via `cairn task claim/complete`.
    """
    from ..compass.generator import _gather_flow_facts, generate_flow_compass, generate_flow_workflow
    from ..compass.critic import critic_concept
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)

    # Trace first (cheap) so we can report the chain before generating prose.
    facts = _gather_flow_facts(conn, entry)
    # total_steps counts the entry itself; a flow worth documenting needs at
    # least one outgoing call (chain depth > 0).
    if facts["total_steps"] <= 1:
        conn.close()
        click.echo(f"No outgoing calls traced from `{entry}` -- nothing to document.")
        click.echo("Verify the symbol exists and has resolved callees (run `cairn build` first).")
        sys.exit(1)

    # --use-llm: queue the flow for agent-decoupled synthesis (file-queue).
    # Mirrors `compass generate --use-llm`: gathers facts, enqueues a task,
    # returns immediately. An agent session later processes it via the
    # skill's task-queue loop (cairn task list/show/claim/complete).
    if use_llm:
        from ..llm.tasks import create_task
        t = create_task(bundle, "flow-synthesize", entry, facts=facts)
        conn.close()
        click.echo(f"Traced {facts['total_steps']} step(s) from `{entry}` "
                   f"across {len(facts['modules'])} module(s).")
        click.echo(f"Queued flow task: {t.id}")
        click.echo("Any agent with the cairn skill can process it:")
        click.echo(f"  cairn task show {t.id}        # view the task + facts")
        click.echo(f"  cairn task claim {t.id}       # claim it")
        click.echo(f"  cairn task complete {t.id} --result-file <path>   # submit result")
        click.echo("On completion, the critic auto-promotes to compass/flow-{entry}.")
        return

    click.echo(f"Traced {facts['total_steps']} step(s) from `{entry}` "
               f"across {len(facts['modules'])} module(s).")
    if facts["branches"]:
        click.echo(f"  branch points: {len(facts['branches'])}")
    if facts["leaves"]:
        click.echo(f"  terminal calls: {len(facts['leaves'])}")
    click.echo("")

    # --- Workflow generation (procedural knowledge) ---
    if as_workflow:
        from ..knowledge.workflow import flow_to_workflow
        steps = flow_to_workflow(facts, max_steps=max_steps)
        if dry_run:
            click.echo("--- workflow steps (not written) ---")
            for i, step in enumerate(steps, 1):
                click.echo(f"  {i}. {step['name']}")
                if step.get("description"):
                    click.echo(f"     {step['description']}")
            click.echo("")
        else:
            cid = generate_flow_workflow(entry, conn, bundle, max_steps=max_steps)
            click.echo(f"Generated flow workflow: {cid} ({len(steps)} steps)")
            click.echo(f"  Trace with: cairn knowledge workflow trace \"Flow: {entry}\"")
            click.echo("")

    # --- Compass generation (declarative knowledge) ---
    concept = generate_flow_compass(entry, conn, bundle)

    # Critic gate (same contract as the module compass). Run it BEFORE the
    # dry-run return so --dry-run reflects the real verdict, not a pre-critic
    # body. (Previously dry-run returned here without ever calling the critic.)
    result = critic_concept(concept, conn)
    conn.close()

    if dry_run:
        click.echo("--- flow trace ---")
        for line in facts["chain"]:
            click.echo(f"  {line}")
        click.echo(f"\n--- critic verdict (dry-run; nothing written) ---")
        click.echo(f"  passed: {result.passed}  quality: {result.quality_score:.2f}  "
                   f"errors: {len(result.errors)}  warnings: {len(result.warnings)}")
        for e in result.errors:
            click.echo(f"  ERROR: {e}")
        for w in result.warnings:
            click.echo(f"  warn: {w}")
        click.echo("\n--- body (not written) ---\n")
        click.echo(concept.body)
        return

    if not result.passed:
        click.echo(f"Flow compass rejected by critic (quality={result.quality_score:.2f}, "
                   f"{len(result.errors)} error(s)) -- not written.")
        for e in result.errors:
            click.echo(f"  ERROR: {e}")
        for w in result.warnings:
            click.echo(f"  warn: {w}")
        click.echo("\nThe flow body is graph-sourced, so this usually means a stale "
                   "graph (run `cairn build`) or a generator bug.")
        sys.exit(1)

    bundle.write_concept(concept)
    click.echo(f"Generated flow compass: {concept.concept_id}")
    click.echo(f"  quality: {result.quality_score:.2f}, errors: {len(result.errors)}")
    click.echo("\n--- body ---\n")
    click.echo(concept.body)


@compass.command("flow-gaps")
@click.option("--min-edges", default=5, type=int,
              help="Minimum resolved outgoing edges to qualify as a flow (default 5).")
@click.option("--generate", is_flag=True,
              help="Generate a flow compass for each undocumented flow (batch mode).")
@click.option("--limit", default=0, type=int,
              help="With --generate, cap the number of flows generated (0 = all).")
@click.option("--dry-run", is_flag=True,
              help="With --generate, show what would be generated without writing.")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def compass_flow_gaps(min_edges, generate, limit, dry_run, db, knowledge):
    """Find business flows (rich call chains) that lack a flow compass.

    The flow equivalent of `compass gaps` (which finds uncovered modules).
    Lists functions/methods with >= --min-edges resolved outgoing calls that
    don't yet have a `compass/flow-*` file, sorted by richness (most callees
    first). Already-documented flows are marked [DONE].

    Use --generate to batch-generate flow compasses for all undocumented flows.
    Name collisions (e.g. multiple `handleCommand` methods) are resolved by
    symbol ID, so each gets its own compass file.
    """
    from ..compass.flow_gaps import detect_flow_gaps
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    result = detect_flow_gaps(conn, bundle, min_edges=min_edges)

    uncovered = result["uncovered"]
    covered = result["covered"]

    if not uncovered and not covered:
        conn.close()
        click.echo(f"No candidate flows found (min-edges={min_edges}). "
                   "Run `cairn build` first, or lower --min-edges.")
        return

    # --generate: batch-generate flow compasses for uncovered candidates.
    if generate:
        from ..compass.generator import generate_flow_compass
        from ..compass.critic import critic_concept

        targets = uncovered if limit <= 0 else uncovered[:limit]
        click.echo(f"Generating {len(targets)} flow compass(es) "
                   f"(min-edges={min_edges}, {len(covered)} already documented)...")
        if dry_run:
            click.echo("(dry-run: nothing will be written)")
        click.echo("")

        generated = 0
        rejected = 0
        skipped = 0
        for entry in targets:
            name = entry["name"]
            sym_id = entry.get("id")
            fname = entry["file"].split("/")[-1]
            resource = entry.get("resource", name)
            # Disambiguate title for collisions.
            if entry.get("colliding"):
                display_title = f"Flow: {name} ({fname.replace('.kt', '').replace('.py', '')})"
            else:
                display_title = f"Flow: {name}"

            # Trace first to check it's non-trivial.
            from ..compass.generator import _gather_flow_facts
            facts = _gather_flow_facts(conn, name, entry_id=sym_id)
            if facts["total_steps"] <= 1:
                click.echo(f"  SKIP  {name:40} (no outgoing calls)")
                skipped += 1
                continue

            concept = generate_flow_compass(
                name, conn, bundle,
                entry_id=sym_id, resource=resource, title=display_title,
            )

            if dry_run:
                click.echo(f"  WOULD  {display_title}")
                generated += 1
                continue

            # Critic gate.
            result_critic = critic_concept(concept, conn)
            if not result_critic.passed:
                click.echo(f"  REJECT {display_title} (quality={result_critic.quality_score:.2f})")
                rejected += 1
                continue

            bundle.write_concept(concept)
            click.echo(f"  OK    {display_title}  "
                       f"(steps={facts['total_steps']}, quality={result_critic.quality_score:.2f})")
            generated += 1

        conn.close()
        click.echo("")
        action = "would generate" if dry_run else "generated"
        click.echo(f"Batch complete: {action} {generated}, {rejected} rejected, {skipped} skipped.")
        return

    conn.close()

    # Default: list mode (no --generate).
    if uncovered:
        click.echo(f"{len(uncovered)} undocumented flow(s) "
                   f"(min-edges={min_edges}, {len(covered)} already documented):")
        for entry in uncovered:
            fname = entry["file"].split("/")[-1]
            click.echo(f"  {entry['name']:40} out={entry['out_edges']:3}  {fname}")
    else:
        click.echo(f"All candidate flows are documented ({len(covered)} total).")

    if covered:
        click.echo(f"\nAlready documented ({len(covered)}):")
        for entry in covered:
            fname = entry["file"].split("/")[-1]
            click.echo(f"  {entry['name']:40} out={entry['out_edges']:3}  {fname}  [DONE]")

