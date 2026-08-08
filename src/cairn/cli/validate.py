"""Validate CLI: OKF conformance + stale path detection."""
from __future__ import annotations

import sys

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
    # Exit non-zero so CI/scripts can detect conformance failure.
    sys.exit(1)


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
    # Exit non-zero so CI/scripts can detect stale concepts.
    sys.exit(1)


# --------------------------------------------------------------------------
# cairn verify <doc-path> (single-concept critic verdict, any type)
# --------------------------------------------------------------------------
@main.command()
@click.argument("doc_path")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def verify(doc_path, db, knowledge):
    """Run the deterministic critic on a single compass/wiki/memory concept.

    DOC_PATH is a concept id relative to the .knowledge/ bundle WITHOUT the
    .md suffix (e.g. `compass/some_module`). Prints the verdict -- passed,
    errors (blocking, e.g. a file ref not in the graph), warnings (non-blocking,
    e.g. an unknown symbol ref), and quality score -- with each offending
    reference listed.

    Read-only: it does not write. This is the user-facing front to the critic
    gate that promise #2 of the verification contract rests on. (For scanning
    all compass concepts at once, see `cairn compass validate`; for stale-path
    detection across all types, see `cairn validate-paths`.)
    """
    from cairn.compass.critic import critic_concept
    from cairn.okf.bundle import OKFBundle

    conn = get_db(db)
    try:
        bundle = OKFBundle(knowledge)
        try:
            concept = bundle.read_concept(doc_path)
        except Exception as e:
            click.echo(f"Could not read concept '{doc_path}': {e}", err=True)
            sys.exit(2)
        result = critic_concept(concept, conn)
    finally:
        conn.close()

    status = "OK" if result.passed else "FAIL"
    click.echo(f"[{status}] {doc_path} (quality={result.quality_score:.2f})")
    for e in result.errors:
        click.echo(f"  ERROR: {e}")
    for w in result.warnings:
        click.echo(f"  warn: {w}")
    # Non-zero exit on blocking errors so scripts/CI can detect a failed verify.
    if not result.passed:
        sys.exit(1)


