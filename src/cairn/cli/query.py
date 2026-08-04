"""Query CLI: def, callers, search, callees, impact, deps, tree."""
from __future__ import annotations

import click
import json
import sys

from .main import DEFAULT_DB_PATH, get_db, main, queries
from ._helpers import _human_bytes, _mods, _shorten  # noqa: F401

@main.command(name="def")
@click.argument("symbol")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def find_def(symbol, db, as_json):
    """Find where a SYMBOL is defined."""
    conn = get_db(db)
    try:
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
def callers(symbol, db, as_json, fuzzy):
    """Find all callers of SYMBOL.

    Default (precise) returns only callers of the exact resolved symbol.
    Use --fuzzy to also include call sites matched only by name (noisier, but
    catches unresolved/stdlib call sites).
    """
    conn = get_db(db)
    try:
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
def search(pattern, kind, db, as_json):
    """Search symbols by PATTERN (supports * wildcards)."""
    conn = get_db(db)
    try:
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
def callees(symbol, db, as_json, fuzzy):
    """Find what a SYMBOL calls."""
    conn = get_db(db)
    try:
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
    "--fuzzy",
    is_flag=True,
    help="Also traverse unresolved name-only edges. "
    "Default is precise: walk only resolved edges (no name-collision inflation).",
)
def impact(symbol, depth, db, as_json, fuzzy):
    """Recursive impact analysis: what breaks if SYMBOL changes."""
    conn = get_db(db)
    try:
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

