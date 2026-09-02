"""Ask + Context CLI: NL routing and file-context loading."""
from __future__ import annotations

import click
import json

from .main import DEFAULT_DB_PATH, get_db, main

@main.command()
@click.argument("question")
@click.option("--db", default=str(DEFAULT_DB_PATH))
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
@click.option("--json", "as_json", is_flag=True)
def ask(question, db, knowledge, as_json):
    """Natural-language question across all layers (compass router)."""
    from ..compass.router import route_query
    from ..okf.bundle import OKFBundle

    conn = get_db(db)
    try:
        bundle = OKFBundle(knowledge)
        result = route_query(question, conn, bundle)
    finally:
        conn.close()
    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return
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


# --------------------------------------------------------------------------
# cairn context
# --------------------------------------------------------------------------
@main.command()
@click.argument("file_path")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def context(file_path, knowledge):
    """Load relevant context (compass + memory + wiki) for a file."""
    from ..okf.bundle import OKFBundle

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


