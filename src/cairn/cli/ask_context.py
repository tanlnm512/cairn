"""Ask + Context CLI: NL routing and file-context loading."""
from __future__ import annotations

import click
import json

from .main import DEFAULT_DB_PATH, get_db, main


def _echo_route_result(result):
    """Render one route_query result in the ask output style."""
    click.echo(f"Intent: {result['intent']} (routed to {result['layer']})")
    click.echo(f"Layers queried: {', '.join(result['layers_queried'])}")
    for layer, data in result["results"].items():
        click.echo(f"\n--- {layer} ---")
        if isinstance(data, dict):
            for k, v in data.items():
                click.echo(f"  {k}: {v}")
        elif isinstance(data, list):
            for item in data:
                click.echo(f"  {item}")


def _ask_all_repos(question, as_json):
    """Route the question across every registered workspace store and
    print one per-repo attributed answer per reachable store.

    Unavailable stores are named with their state and never abort the
    query; an empty registry is stated plainly.
    """
    from ..compass.router import route_query
    from ..graph.federation import _classify_store, iter_stores
    from ..graph.schema import get_db as open_store_db
    from ..okf.bundle import OKFBundle

    if not question.strip():
        raise click.UsageError("Question must not be empty.")
    answers = {}
    states = {}
    dropped = []
    for ws_path, store in iter_stores():
        state = _classify_store(store)
        if state == "ok":
            try:
                conn = open_store_db(str(store.db), read_only=True)
            except Exception:
                state = "locked"
            else:
                try:
                    bundle = OKFBundle(str(store.knowledge))
                    answers[ws_path] = route_query(question, conn, bundle)
                except Exception:
                    state = "locked"
                finally:
                    conn.close()
        states[ws_path] = state
        if state != "ok":
            dropped.append(ws_path)
    if as_json:
        payload = {"answers": answers, "states": states, "dropped": dropped}
        click.echo(json.dumps(payload, indent=2, default=str))
        return
    if not states:
        click.echo("No stores are registered.")
        return
    for ws_path, answer in answers.items():
        click.echo(f"=== {ws_path} ===")
        _echo_route_result(answer)
    if dropped:
        click.echo("\nDropped stores:")
        for ws_path in dropped:
            click.echo(f"  {states[ws_path]}: {ws_path}")


@main.command()
@click.argument("question")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
@click.option("--json", "as_json", is_flag=True)
@click.option(
    "--all-repos",
    "all_repos",
    is_flag=True,
    help="Route across every registered workspace store.",
)
def ask(question, db, knowledge, as_json, all_repos):
    """Natural-language question across all layers (compass router)."""
    from ..compass.router import route_query
    from ..okf.bundle import OKFBundle

    if all_repos:
        _ask_all_repos(question, as_json)
        return
    conn = get_db(db)
    try:
        bundle = OKFBundle(knowledge)
        result = route_query(question, conn, bundle)
    finally:
        conn.close()
    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return
    _echo_route_result(result)


# --------------------------------------------------------------------------
# cairn context
# --------------------------------------------------------------------------
@main.command()
@click.argument("file_path")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
@click.option(
    "--refresh/--no-refresh",
    default=None,
    help="Reindex drifted files before answering.",
)
def context(file_path, knowledge, refresh):
    """Load relevant context (compass + memory + wiki) for a file."""
    from ..paths import resolve_store
    from .query import _refresh_conn
    from ..okf.bundle import OKFBundle

    conn = get_db(str(resolve_store().db))
    try:
        _refresh_conn(conn, refresh)
    finally:
        conn.close()

    bundle = OKFBundle(knowledge)
    parts = [p for p in file_path.split("/") if p]
    module_guess = "/".join(parts[:4]) if len(parts) >= 4 else file_path
    out = [f"Context for {file_path}:", f"  inferred module: {module_guess}"]
    for cid in bundle.list_concepts(prefix="compass/"):
        c = bundle.read_concept(cid)
        if c.resource and (c.resource in file_path or file_path in c.resource):
            out.append(f"\n# Compass: {c.title}\n{c.body}")
            break
    seg = parts[-1].replace(".kt", "").replace(".java", "") if parts else ""
    if seg:
        for c in bundle.search(seg, limit=3):
            if c.type in ("Wiki-Article", "Wiki-Feature"):
                out.append(f"\n# Wiki: {c.title}\n{c.body[:500]}...")
                break
    if seg:
        for c in bundle.search(seg, limit=3):
            if c.concept_id.startswith("memory/"):
                out.append(f"\n# Memory: {c.title}")
                if c.description:
                    out.append(f"  {c.description}")
                break
    click.echo("\n".join(out))
