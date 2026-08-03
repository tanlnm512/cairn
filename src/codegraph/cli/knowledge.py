"""Knowledge CLI: the knowledge group + workflow subgroup."""
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
def knowledge():
    """Business knowledge document ingestion and search."""
    pass


@knowledge.command("add")
@click.option("--file", "file_path", default=None, help="Read body from file.")
@click.option("--body", "body_text", default=None, help="Inline body text.")
@click.option("--title", required=True, help="Document title.")
@click.option("--type", "doc_type", default="spec", help="business-rule|spec|decision")
@click.option("--tags", default="", help="Comma-separated tags.")
@click.option("--affects", default="", help="Comma-separated repos (graph bridge).")
@click.option("--affects-modules", default="", help="Comma-separated module paths.")
@click.option("--epic", default="", help="Jira epic link.")
@click.option("--resource", default="", help="Canonical URI (Jira/Confluence).")
def knowledge_add(file_path, body_text, title, doc_type, tags, affects,
                  affects_modules, epic, resource):
    """Ingest a business knowledge document."""
    from codegraph.knowledge.store import add_document
    from codegraph.okf.bundle import OKFBundle
    from ..paths import resolve_store

    # Read body from file or flag
    if file_path:
        body = Path(file_path).read_text(encoding="utf-8")
    elif body_text:
        body = body_text
    else:
        click.echo("Error: --file or --body required.", err=True)
        sys.exit(1)

    store = resolve_store()
    store.ensure()
    bundle = OKFBundle(str(store.knowledge))

    def _split(s):
        return [x.strip() for x in s.split(",") if x.strip()]

    cid = add_document(
        bundle, title=title, body=body, doc_type=doc_type,
        tags=_split(tags), affects_modules=_split(affects_modules),
        affects_repos=_split(affects), resource=resource or None,
        epic_link=epic or None,
    )
    click.echo(f"Stored: {cid}")


@knowledge.command("import")
@click.argument("dir_path")
@click.option("--type", "doc_type", default="spec")
@click.option("--tags", default="")
@click.option("--affects", default="")
def knowledge_import(dir_path, doc_type, tags, affects):
    """Batch-ingest all .md files from a directory."""
    from codegraph.knowledge.store import import_directory
    from codegraph.okf.bundle import OKFBundle
    from ..paths import resolve_store

    store = resolve_store()
    store.ensure()
    bundle = OKFBundle(str(store.knowledge))

    def _split(s):
        return [x.strip() for x in s.split(",") if x.strip()]

    imported = import_directory(
        bundle, dir_path, doc_type=doc_type,
        tags=_split(tags), affects_repos=_split(affects),
    )
    click.echo(f"Imported {len(imported)} document(s).")
    for cid in imported:
        click.echo(f"  {cid}")


@knowledge.command("search")
@click.argument("query")
@click.option("--limit", default=20, type=int)
@click.option("--threshold", default=0.3, type=float)
@click.option("--json", "as_json", is_flag=True)
@click.option("--db", default=str(DEFAULT_DB_PATH))
def knowledge_search(query, limit, threshold, as_json, db):
    """Search knowledge docs (lexical + semantic + graph bridge)."""
    from codegraph.knowledge.search import search_knowledge
    from codegraph.okf.bundle import OKFBundle
    from ..paths import resolve_store

    store = resolve_store()
    bundle = OKFBundle(str(store.knowledge))
    conn = get_db(db)
    try:
        results = search_knowledge(conn, bundle, query, limit=limit, threshold=threshold)
    finally:
        conn.close()

    if as_json:
        click.echo(json.dumps(results, indent=2, default=str))
        return
    if not results:
        click.echo(f"No knowledge documents matching '{query}'.")
        return
    click.echo(f"{len(results)} knowledge doc(s) for '{query}':")
    for r in results:
        click.echo(f"  [{r['provenance']}] {r['title']} ({r['doc_type']})")


@knowledge.command("list")
@click.option("--type", "doc_type", default=None)
@click.option("--status", default=None)
@click.option("--tag", default=None)
def knowledge_list(doc_type, status, tag):
    """List knowledge documents."""
    from codegraph.knowledge.store import list_documents
    from codegraph.okf.bundle import OKFBundle
    from ..paths import resolve_store

    bundle = OKFBundle(str(resolve_store().knowledge))
    docs = list_documents(bundle, doc_type=doc_type, status=status, tag=tag)
    if not docs:
        click.echo("No knowledge documents found.")
        return
    for c in docs:
        st = c.extensions.get("doc_status", "?")
        click.echo(f"  [{st}] {c.title}  ({c.concept_id})")


@knowledge.command("embed")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--batch-size", default=64, type=int)
def knowledge_embed(db, batch_size):
    """Build the knowledge embedding index."""
    from . import display
    from codegraph.graph import embeddings as emb
    from codegraph.okf.bundle import OKFBundle
    from ..paths import resolve_store

    if not emb.embeddings_available():
        display.error("Semantic backend unavailable")
        display.dim(emb.install_hint())
        sys.exit(1)

    bundle = OKFBundle(str(resolve_store().knowledge))
    conn = get_db(db)
    try:
        display.info(f"Embedding knowledge docs with {emb.current_model()}")

        bar_state = {"bar": None, "task": None}
        def progress(done, total):
            bar = bar_state["bar"]
            if bar is None:
                return
            task = bar_state["task"]
            if bar.tasks[task].total is None or bar.tasks[task].total != total:
                bar.update(task, total=total)
            bar.update(task, completed=done)

        import time
        t0 = time.time()
        with display.progress_bar(description="Embedding docs", total=None, unit="docs") as bar:
            bar_state["bar"] = bar
            bar_state["task"] = bar._cg_task_id
            summary = emb.embed_knowledge(conn, bundle, batch_size=batch_size, progress=progress)
            bar_state["bar"] = None
        elapsed = time.time() - t0

        display.summary_panel(
            title=f"Embedded {summary['embedded']:,} docs in {elapsed:.1f}s",
            kv_pairs=[
                ("embedded", f"{summary['embedded']:,}"),
                ("skipped", f"{summary['skipped']:,}"),
            ],
        )
    finally:
        conn.close()


@knowledge.command("impact")
@click.argument("query")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--limit", default=10, type=int)
def knowledge_impact(query, db, limit):
    """Search knowledge + full graph impact bridge."""
    from codegraph.knowledge.search import search_knowledge
    from codegraph.okf.bundle import OKFBundle
    from ..paths import resolve_store

    bundle = OKFBundle(str(resolve_store().knowledge))
    conn = get_db(db)
    try:
        results = search_knowledge(conn, bundle, query, limit=limit)
    finally:
        conn.close()

    if not results:
        click.echo(f"No knowledge documents matching '{query}'.")
        return

    click.echo(f"=== Knowledge impact: '{query}' ===")
    for r in results:
        click.echo(f"\n[{r['provenance']}] {r['title']}")
        click.echo(f"  doc: {r['concept_id']}")
        if r.get("affects_repos"):
            click.echo(f"  affects_repos: {', '.join(r['affects_repos'])}")
        if r.get("graph_deps"):
            for repo, deps in r["graph_deps"].items():
                if isinstance(deps, dict):
                    if deps.get("depends_on"):
                        click.echo(f"  {repo} → depends on: {', '.join(deps['depends_on'])}")


@knowledge.command("remove")
@click.argument("doc_id")
@click.option("--db", default=str(DEFAULT_DB_PATH))
def knowledge_remove(doc_id, db):
    """Delete a knowledge document and its embedding rows."""
    from codegraph.knowledge.store import delete_document
    from codegraph.okf.bundle import OKFBundle
    from ..paths import resolve_store

    bundle = OKFBundle(str(resolve_store().knowledge))
    conn = get_db(db)
    try:
        ok = delete_document(bundle, doc_id, conn=conn)
    finally:
        conn.close()
    if ok:
        click.echo(f"Deleted knowledge document: {doc_id}")
    else:
        click.echo(f"Not found: '{doc_id}'.", err=True)
        sys.exit(1)


@knowledge.command("status")
@click.argument("doc_id")
@click.argument("new_status")
def knowledge_status(doc_id, new_status):
    """Update doc_status on a knowledge document (active/superseded/archived)."""
    from codegraph.knowledge.store import update_status
    from codegraph.okf.bundle import OKFBundle
    from ..paths import resolve_store

    bundle = OKFBundle(str(resolve_store().knowledge))
    ok = update_status(bundle, doc_id, new_status)
    if ok:
        click.echo(f"Updated '{doc_id}' status -> {new_status}")
    else:
        click.echo(
            f"Not found, unknown status, or backward transition rejected: '{doc_id}' -> '{new_status}'.",
            err=True,
        )
        sys.exit(1)


@knowledge.command(name="export")
@click.option("--out", "out_path", required=True, help="Destination directory or tarball path.")
def knowledge_export(out_path):
    """Export the .knowledge bundle to a directory or tar.gz file."""
    import shutil
    import tarfile

    store_paths = resolve_store()
    kn_dir = store_paths.knowledge
    if not kn_dir.exists():
        click.echo("Knowledge bundle directory does not exist.")
        return

    dst = Path(out_path)
    if out_path.endswith(".tar.gz") or out_path.endswith(".tgz") or out_path.endswith(".tar"):
        dst.parent.mkdir(parents=True, exist_ok=True)
        mode = "w:gz" if out_path.endswith("gz") else "w"
        with tarfile.open(dst, mode) as tar:
            tar.add(kn_dir, arcname=".knowledge")
        click.echo(f"Exported knowledge bundle tarball to {dst}")
    else:
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copytree(kn_dir, dst / ".knowledge", dirs_exist_ok=True)
        click.echo(f"Exported knowledge bundle directory to {dst}")


# --------------------------------------------------------------------------
# cg knowledge workflow (procedural ontology -- see src/knowledge/workflow.py)
# --------------------------------------------------------------------------
# `cg knowledge list --type workflow`, `cg knowledge status`, `cg knowledge
# remove`, and `cg knowledge search` already work for workflows unchanged --
# a workflow is just a knowledge doc with doc_type="workflow". Only `add`
# (needs a way to specify ordered steps) and `trace` (the ordered-steps
# query, not a generic search) are genuinely workflow-specific.
@knowledge.group("workflow")
def knowledge_workflow():
    """Ordered procedural workflows (codegraph's answer to a LeanKG-style
    procedural ontology -- see src/knowledge/workflow.py for the design
    rationale)."""


@knowledge_workflow.command("add")
@click.option("--title", required=True, help="Workflow title.")
@click.option(
    "--step",
    "steps_raw",
    multiple=True,
    help="Repeatable. Format: 'name::description[::symbol[::file]]'. "
    "Use --steps-file instead for richer step data.",
)
@click.option(
    "--steps-file",
    default=None,
    help="Path to a YAML or JSON file containing a list of step dicts "
    "(each with name/description/symbol/file keys). Overrides --step.",
)
@click.option("--tags", default="", help="Comma-separated tags.")
@click.option("--affects", default="", help="Comma-separated repos (graph bridge).")
@click.option("--affects-modules", default="", help="Comma-separated module paths.")
@click.option("--resource", default="", help="Canonical URI (Jira/Confluence/runbook).")
def knowledge_workflow_add(title, steps_raw, steps_file, tags, affects, affects_modules, resource):
    """Add a workflow with an ordered list of steps."""
    from codegraph.knowledge.workflow import add_workflow
    from codegraph.okf.bundle import OKFBundle
    from ..paths import resolve_store

    if steps_file:
        import yaml as _yaml

        with open(steps_file, "r", encoding="utf-8") as fh:
            steps = _yaml.safe_load(fh)
        if not isinstance(steps, list):
            click.echo("Error: --steps-file must contain a YAML/JSON list of step dicts.", err=True)
            sys.exit(1)
    elif steps_raw:
        steps = []
        for raw in steps_raw:
            parts = raw.split("::")
            step = {"name": parts[0]}
            if len(parts) > 1 and parts[1]:
                step["description"] = parts[1]
            if len(parts) > 2 and parts[2]:
                step["symbol"] = parts[2]
            if len(parts) > 3 and parts[3]:
                step["file"] = parts[3]
            steps.append(step)
    else:
        click.echo("Error: --step (repeatable) or --steps-file required.", err=True)
        sys.exit(1)

    def _split(s):
        return [x.strip() for x in s.split(",") if x.strip()]

    store = resolve_store()
    store.ensure()
    bundle = OKFBundle(str(store.knowledge))
    try:
        cid = add_workflow(
            bundle, title=title, steps=steps,
            tags=_split(tags), affects_modules=_split(affects_modules),
            affects_repos=_split(affects), resource=resource or None,
        )
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    click.echo(f"Stored: {cid} ({len(steps)} step(s))")


@knowledge_workflow.command("trace")
@click.argument("ref")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def knowledge_workflow_trace(ref, as_json):
    """Trace a workflow's ordered steps by title, slug, or concept_id."""
    from codegraph.knowledge.workflow import trace_workflow
    from codegraph.okf.bundle import OKFBundle
    from ..paths import resolve_store

    bundle = OKFBundle(str(resolve_store().knowledge))
    result = trace_workflow(bundle, ref)
    if result is None:
        click.echo(
            f"No workflow found matching '{ref}'. Try `cg knowledge search {ref}` "
            "or `cg knowledge list --type workflow`.",
            err=True,
        )
        sys.exit(1)

    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    status = result["doc_status"]
    status_note = f" [{status}]" if status != "active" else ""
    click.echo(f"{result['title']}{status_note} ({result['concept_id']})")
    for i, step in enumerate(result["steps"], start=1):
        click.echo(f"  {i}. {step.get('name', f'Step {i}')}")
        if step.get("description"):
            click.echo(f"     {step['description']}")
        if step.get("symbol"):
            click.echo(f"     symbol: {step['symbol']}")
        if step.get("file"):
            click.echo(f"     file: {step['file']}")


@knowledge_workflow.command("sync")
@click.argument("ref", required=False)
@click.option("--all", "sync_all", is_flag=True, help="Sync all workflows.")
@click.option("--dry-run", is_flag=True, help="Show staleness report without writing.")
@click.option("--max-steps", default=20, type=int, help="Cap workflow steps (default 20).")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def knowledge_workflow_sync(ref, sync_all, dry_run, max_steps, db, knowledge):
    """Detect and refresh stale workflows after code changes.

    Checks each workflow's step anchors (symbol/file) against the current
    graph. With --dry-run, reports stale steps without writing. Without
    --dry-run, re-traces the flow and rebuilds the steps from the current
    call graph.

    \b
    Examples:
      cg knowledge workflow sync "Flow: login"     # sync one
      cg knowledge workflow sync --all              # sync all
      cg knowledge workflow sync --all --dry-run    # report only
    """
    from codegraph.knowledge.workflow import (
        check_all_workflows, check_workflow_staleness, sync_workflow,
    )
    from codegraph.knowledge.store import list_documents
    from codegraph.okf.bundle import OKFBundle

    if not ref and not sync_all:
        click.echo("Error: provide a workflow ref or use --all.", err=True)
        sys.exit(1)

    conn = get_db(db)
    bundle = OKFBundle(knowledge)

    if dry_run:
        # Staleness report mode.
        if sync_all:
            reports = check_all_workflows(conn, bundle)
            conn.close()
            if not reports:
                click.echo("All workflows are up to date (no stale steps found).")
                return
            click.echo(f"{len(reports)} workflow(s) with stale steps:")
            for r in reports:
                click.echo(f"\n  {r['title']} ({r['concept_id']})")
                click.echo(f"    {r['stale_count']}/{r['total_steps']} steps stale:")
                for d in r["stale_details"][:8]:
                    issues = []
                    if not d["symbol_ok"]:
                        issues.append(f"symbol '{d['symbol']}' not found")
                    if not d["file_ok"]:
                        issues.append(f"file '{d['file']}' not found")
                    click.echo(f"      {d['step']}: {', '.join(issues)}")
                if len(r["stale_details"]) > 8:
                    click.echo(f"      ... and {len(r['stale_details']) - 8} more")
        else:
            report = check_workflow_staleness(conn, bundle, ref)
            conn.close()
            if report is None:
                click.echo(f"No workflow found matching '{ref}'.", err=True)
                sys.exit(1)
            if report["stale_count"] == 0:
                click.echo(f"'{report['title']}' is up to date ({report['total_steps']} steps, no staleness).")
                return
            click.echo(f"'{report['title']}' ({report['concept_id']})")
            click.echo(f"  {report['stale_count']}/{report['total_steps']} steps stale:")
            for d in report["stale_details"]:
                issues = []
                if not d["symbol_ok"]:
                    issues.append(f"symbol '{d['symbol']}' not found")
                if not d["file_ok"]:
                    issues.append(f"file '{d['file']}' not found")
                click.echo(f"    {d['step']}: {', '.join(issues)}")
            click.echo("\nRun without --dry-run to sync.")
        return

    # Sync mode: re-trace and rebuild.
    if sync_all:
        workflows = list_documents(bundle, doc_type="workflow")
        conn.close()
        if not workflows:
            click.echo("No workflows to sync.")
            return
        click.echo(f"Syncing {len(workflows)} workflow(s)...")
        synced = 0
        errors = 0
        for w in workflows:
            conn = get_db(db)
            result = sync_workflow(conn, bundle, w.title or w.concept_id, max_steps=max_steps)
            conn.close()
            if result is None:
                continue
            if result.get("error"):
                click.echo(f"  SKIP  {result['title']}: {result['error']}")
                errors += 1
            else:
                click.echo(f"  OK    {result['title']}: "
                           f"{result['old_step_count']} -> {result['new_step_count']} steps"
                           f" (+{len(result['added'])}, -{len(result['removed'])})")
                synced += 1
        click.echo(f"\nSync complete: {synced} synced, {errors} skipped.")
    else:
        result = sync_workflow(conn, bundle, ref, max_steps=max_steps)
        conn.close()
        if result is None:
            click.echo(f"No workflow found matching '{ref}'.", err=True)
            sys.exit(1)
        if result.get("error"):
            click.echo(f"Could not sync '{result['title']}': {result['error']}")
            sys.exit(1)
        click.echo(f"Synced '{result['title']}' ({result['concept_id']})")
        click.echo(f"  steps: {result['old_step_count']} -> {result['new_step_count']}")
        if result["added"]:
            click.echo(f"  added: {', '.join(result['added'][:8])}")
        if result["removed"]:
            click.echo(f"  removed: {', '.join(result['removed'][:8])}")


if __name__ == "__main__":
    main()
