"""Hooks + Viz CLI: git hook management and graph visualization."""
from __future__ import annotations

import click
from pathlib import Path

from .main import DEFAULT_DB_PATH, get_db, main, scanner_mod

@main.group()
def hooks():
    """Git hook management."""


@hooks.command("install")
@click.option("--workspace", default=scanner_mod.DEFAULT_WORKSPACE)
@click.option("--cairn-dir", default=str(DEFAULT_DB_PATH.parent))
def hooks_install(workspace, cairn_dir):
    from ..graph import scanner as scanner_mod
    from ..hooks.git_hooks import install_hooks

    repos = [r.name for r in scanner_mod.discover_repos(workspace)]
    installed = install_hooks(repos, workspace, cairn_dir)
    click.echo(f"Installed post-commit hooks in {len(installed)} repos: {', '.join(installed)}")


@hooks.command("uninstall")
@click.option("--workspace", default=scanner_mod.DEFAULT_WORKSPACE)
def hooks_uninstall(workspace):
    from ..graph import scanner as scanner_mod
    from ..hooks.git_hooks import uninstall_hooks

    repos = [r.name for r in scanner_mod.discover_repos(workspace)]
    removed = uninstall_hooks(repos, workspace)
    click.echo(f"Removed hooks from {len(removed)} repos: {', '.join(removed)}")


# --------------------------------------------------------------------------
# cairn viz (visualization)
# --------------------------------------------------------------------------
@main.command()
@click.option("--format", "fmt", type=click.Choice(["mermaid", "dot", "json"]), default="mermaid")
@click.option("--scope", type=click.Choice(["symbol", "module", "impact", "repo", "deps"]), default="symbol")
@click.option("--symbol", default=None, help="Symbol name (scope=symbol/impact)")
@click.option("--module", default=None, help="Module path (scope=module)")
@click.option("--repo", default=None, help="Repo name (scope=repo)")
@click.option("--depth", default=3, type=int, help="Traversal depth (scope=impact)")
@click.option("--output", default=None, help="Write to file instead of stdout")
@click.option("--embed", "do_embed", is_flag=True, help="Wrap in OKF markdown block")
@click.option("--db", default=str(DEFAULT_DB_PATH))
def viz(fmt, scope, symbol, module, repo, depth, output, do_embed, db):
    """Generate visual diagrams from the graph."""
    from ..viz import query as vq
    from ..viz import renderers as vr

    conn = get_db(db)
    try:
        if scope == "symbol":
            graph = vq.get_symbol_graph(conn, symbol or "")
        elif scope == "impact":
            graph = vq.get_impact_graph(conn, symbol or "", max_depth=depth)
        elif scope == "module":
            graph = vq.get_module_graph(conn, module or "")
        elif scope == "repo":
            graph = vq.get_repo_graph(conn, repo or "")
        elif scope == "deps":
            graph = vq.get_deps_graph(conn)
        else:
            graph = {"nodes": [], "edges": [], "metadata": {}}
    finally:
        conn.close()

    if fmt == "mermaid":
        out = vr.embed(graph) if do_embed else vr.to_mermaid(graph)
    elif fmt == "dot":
        out = vr.to_dot(graph)
    else:
        out = vr.to_json(graph)

    if output:
        Path(output).write_text(out, encoding="utf-8")
        click.echo(f"Wrote {output}")
    else:
        click.echo(out)


@main.command(name="import-scip")
@click.argument("scip_file", type=click.Path(exists=True))
@click.option("--db", default=None, help="Database path (default: resolved).")
@click.option("--repo", default="default", help="Repo ID prefix.")
def import_scip(scip_file, db, repo):
    """Import compiler-grade symbol bindings from a SCIP index file."""
    from ..parsers.scip_importer import import_scip_file

    conn = get_db(db)
    try:
        stats = import_scip_file(conn, scip_file, repo_id=repo)
    finally:
        conn.close()
    click.echo(
        f"Imported SCIP index: {stats['files_added']} files, {stats['symbols_added']} symbols, {stats['edges_added']} exact edges."
    )


