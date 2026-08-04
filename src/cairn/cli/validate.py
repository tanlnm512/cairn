"""Validate CLI: OKF conformance + stale path detection."""
from __future__ import annotations

import click

from .main import DEFAULT_DB_PATH, get_db, main
from ._helpers import _human_bytes, _mods, _shorten  # noqa: F401

@main.command()
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def validate(knowledge):
    """Check OKF conformance of the .knowledge/ bundle."""
    from ..okf.conformance import check_bundle

    errors = check_bundle(knowledge)
    if not errors:
        click.echo(f"OKF bundle at {knowledge} is conformant (0 errors).")
        return
    click.echo(f"{len(errors)} conformance errors:")
    for e in errors:
        click.echo(f"  {e}")


# --------------------------------------------------------------------------
# cairn validate-paths (stale reference detection against the graph)
# --------------------------------------------------------------------------
@main.command(name="validate-paths")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
@click.option("--mark", is_flag=True, help="Mark stale concepts (set stale=true in extensions).")
def validate_paths(db, knowledge, mark):
    """Check all concepts for stale file/symbol references against the graph."""
    from cairn.compass.critic import validate_paths as _validate
    from cairn.okf.bundle import OKFBundle

    conn = get_db(db)
    bundle = OKFBundle(knowledge)
    stale = _validate(conn, bundle)
    conn.close()

    if not stale:
        click.echo("All concepts have valid references (0 stale).")
        return

    for entry in stale:
        cid = entry["concept_id"]
        score = entry["verified"]
        click.echo(f"  [STALE] {cid}  (verified: {score})")
        if mark:
            try:
                c = bundle.read_concept(cid)
                c.extensions["stale"] = True
                bundle.write_concept(c)
            except Exception as e:
                click.echo(f"    (mark failed: {e})")

    action = "marked" if mark else "found"
    click.echo(f"\n{action} {len(stale)} stale concept(s).")


