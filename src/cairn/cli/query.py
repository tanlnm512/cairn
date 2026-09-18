"""Query CLI: def, callers, search, callees, impact, deps, tree."""
from __future__ import annotations

import click
import json
import sys
from dataclasses import asdict

from .main import DEFAULT_DB_PATH, get_db, main, queries
from ._helpers import _mods, _shorten


def _refresh_conn(conn, refresh: bool | None = None):
    """Refresh the graph before a query; report unrepaired drift on stderr."""
    from ..graph.watcher import refresh_for_query

    report = refresh_for_query(conn, repair=refresh)
    if report.drifted_paths and not report.repaired:
        click.echo(report.banner(), err=True)
    return report


@main.command(name="def")
@click.argument("symbol")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
@click.option(
    "--refresh/--no-refresh",
    default=None,
    help="Reindex drifted files before answering.",
)
def find_def(symbol, db, as_json, refresh):
    """Find where a SYMBOL is defined."""
    conn = get_db(db)
    try:
        _refresh_conn(conn, refresh)
        rows = queries.find_definition(conn, symbol)
    finally:
        conn.close()
    if not rows:
        click.echo(f"No definition found for '{symbol}'.", err=True)
        sys.exit(1)
    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return
    for r in rows:
        mods = _mods(r["modifiers"])
        click.echo(f"{r['file_path']}:{r['line_start']}  "
                   f"{r['kind']} {r['qualified_name'] or r['name']}"
                   + (f"  [{', '.join(mods)}]" if mods else ""))


# --------------------------------------------------------------------------
# cairn callers
# --------------------------------------------------------------------------
@main.command()
@click.argument("symbol")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
@click.option(
    "--fuzzy",
    is_flag=True,
    help="Also match unresolved edges by name (pre-resolution behavior). "
    "Default is precise: only edges resolved to exactly one definition.",
)
@click.option(
    "--refresh/--no-refresh",
    default=None,
    help="Reindex drifted files before answering.",
)
def callers(symbol, db, as_json, fuzzy, refresh):
    """Find all callers of SYMBOL.

    Default (precise) returns only callers of the exact resolved symbol.
    Use --fuzzy to also include call sites matched only by name (noisier, but
    catches unresolved/stdlib call sites).
    """
    conn = get_db(db)
    try:
        _refresh_conn(conn, refresh)
        rows = queries.get_callers(conn, symbol, fuzzy=fuzzy)
    finally:
        conn.close()
    if not rows:
        click.echo(f"No callers found for '{symbol}'.", err=True)
        sys.exit(1)
    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return
    for r in rows:
        click.echo(f"{r['file_path']}:{r['edge_line']}  "
                   f"{r['caller_kind']} {r['caller_name']}  ({r['repo']})")


# --------------------------------------------------------------------------
# cairn search
# --------------------------------------------------------------------------
@main.command()
@click.argument("pattern")
@click.option("--kind", default=None, help="Filter by kind: class|function|method|...")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
@click.option(
    "--refresh/--no-refresh",
    default=None,
    help="Reindex drifted files before answering.",
)
def search(pattern, kind, db, as_json, refresh):
    """Search symbols by PATTERN (supports * wildcards)."""
    conn = get_db(db)
    try:
        _refresh_conn(conn, refresh)
        rows = queries.search_symbols(conn, pattern, kind=kind)
    finally:
        conn.close()
    if not rows:
        click.echo(f"No symbols matching '{pattern}'.", err=True)
        sys.exit(1)
    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return
    for r in rows:
        click.echo(f"{r['kind']:10} {r['name']:35} "
                   f"{r['file_path']}:{r['line_start']}  ({r['repo']})")


# --------------------------------------------------------------------------
# cairn federated-search
# --------------------------------------------------------------------------
@main.command(name="federated-search")
@click.argument("query")
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=20,
    show_default=True,
    help="Max merged hits.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
@click.option(
    "--shared-embed",
    is_flag=True,
    default=False,
    help="Opt into one shared embedding backend when every store's model "
    "stamp matches; never changes which hits are returned.",
)
def federated_search(query, limit, as_json, shared_embed):
    """Search every registered workspace store as one merged ranking.

    Each hit names the workspace store it came from; stores that are
    missing, locked, or unindexed are named in the report, never silently
    skipped.
    """
    from ..graph.federation import federated_search as run_federated

    try:
        result = run_federated(query, limit=limit, shared_embed=shared_embed)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(asdict(result), indent=2, default=str))
        if not result.states:
            sys.exit(1)
        return
    if not result.states:
        click.echo("No stores are registered.", err=True)
        sys.exit(1)
    for hit in result.hits:
        click.echo(f"{hit['kind']:10} {hit['name']:35} "
                   f"{hit['file_path']}  ({hit['repo']})  "
                   f"[{hit['workspace']}]  {hit['score']:g}  {hit['provenance']}")
    if result.dropped:
        click.echo(f"\nDropped stores ({len(result.dropped)}):")
        for ws_path in result.dropped:
            click.echo(f"  {result.states[ws_path]:10} {ws_path}")
    if not result.hits:
        click.echo(f"No hits for '{query}'.", err=True)
        sys.exit(1)


# --------------------------------------------------------------------------
# cairn callees
# --------------------------------------------------------------------------
@main.command()
@click.argument("symbol")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
@click.option(
    "--fuzzy",
    is_flag=True,
    help="Also include unresolved outgoing calls (named-only). "
    "Default is precise: only calls resolved to a workspace symbol.",
)
@click.option(
    "--refresh/--no-refresh",
    default=None,
    help="Reindex drifted files before answering.",
)
def callees(symbol, db, as_json, fuzzy, refresh):
    """Find what a SYMBOL calls."""
    conn = get_db(db)
    try:
        _refresh_conn(conn, refresh)
        rows = queries.get_callees(conn, symbol, fuzzy=fuzzy)
    finally:
        conn.close()
    if not rows:
        click.echo(f"No callees found for '{symbol}'.", err=True)
        sys.exit(1)
    if as_json:
        click.echo(json.dumps([dict(r) for r in rows], indent=2, default=str))
        return
    for r in rows:
        resolved = "" if r["resolved"] else "  (unresolved)"
        click.echo(f"{r['callee_kind']:10} {r['callee_name']:35} "
                   f"{r['file_path']}:{r['edge_line']}{resolved}")


# --------------------------------------------------------------------------
# cairn impact
# --------------------------------------------------------------------------
@main.command()
@click.argument("symbol")
@click.option("--depth", default=10, help="Max traversal depth.")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
@click.option(
    "--refresh/--no-refresh",
    default=None,
    help="Reindex drifted files before answering.",
)
@click.option(
    "--fuzzy",
    is_flag=True,
    help="Also traverse unresolved name-only edges. "
    "Default is precise: walk only resolved edges (no name-collision inflation).",
)
def impact(symbol, depth, db, as_json, refresh, fuzzy):
    """Recursive impact analysis: what breaks if SYMBOL changes."""
    conn = get_db(db)
    try:
        _refresh_conn(conn, refresh)
        result = queries.impact_analysis(conn, symbol, max_depth=depth, fuzzy=fuzzy)
    finally:
        conn.close()
    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return
    if result["cycles"]:
        cycle_names = [c["symbol"] for c in result["cycles"]]
        if len(cycle_names) <= 15:
            click.echo(f"Cycles detected: {cycle_names}")
        else:
            click.echo(f"Cycles detected ({len(cycle_names)}): {cycle_names[:15]} ...")
    if result["total"] == 0 and not result["impacted"]:
        click.echo(f"No impacted symbols for '{symbol}'.", err=True)
        sys.exit(1)
    click.echo(f"Total impacted: {result['total']}")
    # Group by depth for readability.
    by_depth: dict[int, list] = {}
    for r in result["impacted"]:
        by_depth.setdefault(r["depth"], []).append(r)
    for d in sorted(by_depth):
        click.echo(f"\nDepth {d} ({len(by_depth[d])}):")
        for r in by_depth[d][:20]:
            short = _shorten(r["file"])
            click.echo(f"  {r['symbol']:30} {short}  ({r['repo']})")
        if len(by_depth[d]) > 20:
            click.echo(f"  ... and {len(by_depth[d]) - 20} more")


# --------------------------------------------------------------------------
# cairn deps
# --------------------------------------------------------------------------
@main.command()
@click.argument("repo")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def deps(repo, db, as_json):
    """Cross-repo dependencies for REPO."""
    conn = get_db(db)
    try:
        result = queries.cross_repo_deps(conn, repo)
    finally:
        conn.close()
    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return
    click.echo(f"=== {repo} depends on ===")
    if result["dependencies"]:
        for d in result["dependencies"]:
            click.echo(f"  {d['repo']:18} ({d['type']}: {d['evidence']}) x{d['count']}")
    else:
        click.echo("  (none)")
    click.echo(f"\n=== Dependents of {repo} ===")
    if result["dependents"]:
        for d in result["dependents"]:
            click.echo(f"  {d['repo']:18} x{d['count']}")
    else:
        click.echo("  (none)")
    if not result["dependencies"] and not result["dependents"]:
        sys.exit(1)
