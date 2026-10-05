"""Open-PR triage command: gh PR state fused with the local graph."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

import click

from ..graph.blast import BlastBaseError, compute_pr_impact, pr_seed_symbols
from ..graph.prs import GhError, community_overlaps, list_open_prs
from ..graph.prs_fetch import fetch_pr_diff, fetch_pr_view, store_build_age
from .main import DEFAULT_DB_PATH, get_db, main

_LIST_COLUMNS = ("PR", "Title", "Branch", "Author", "CI", "Review")


def _list_row(pr: dict) -> tuple[str, ...]:
    return (
        f"#{pr['number']}",
        pr["title"],
        pr["head_ref"],
        pr["author"] or "-",
        pr["ci_state"],
        pr["review_decision"] or "-",
    )


def _render_list(prs: list[dict]) -> list[str]:
    if not prs:
        return ["No open PRs"]
    rows = [_list_row(pr) for pr in prs]
    widths = [
        max(len(column), *(len(row[i]) for row in rows))
        for i, column in enumerate(_LIST_COLUMNS)
    ]
    lines = ["  ".join(c.ljust(w) for c, w in zip(_LIST_COLUMNS, widths))]
    lines.extend(
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip()
        for row in rows
    )
    return lines


def _render_impact(store: dict, impact: dict) -> list[str]:
    lines = [f"Store: local index (built {store['built'] or 'never'})"]
    lines.append(f"Impact (base {impact['basis']['base']}):")
    lines.append("Changed symbols:")
    if impact["seeds"]:
        lines.extend(
            f"  {seed['name']} ({seed['file_path']}:{seed['line_start']}-{seed['line_end']})"
            for seed in impact["seeds"]
        )
    else:
        lines.append("  (none)")
    lines.append("Dependents:")
    if impact["radius"]:
        lines.extend(
            f"  {row['symbol']} (depth {row['depth']}) {row['file']} [{row['resolution']}]"
            for row in impact["radius"]
        )
    else:
        lines.append("  No dependents")
    if impact["unindexed_files"]:
        lines.append("Unindexed changed files:")
        lines.extend(f"  {path}" for path in impact["unindexed_files"])
    if impact["deleted_files"]:
        lines.append("Deleted files:")
        lines.extend(f"  {path}" for path in impact["deleted_files"])
    return lines


def _render_conflicts(overlaps: dict) -> list[str]:
    pairs = overlaps.get("pairs", [])
    hint = overlaps.get("hint")
    if not pairs and hint:
        return [f"Merge-order risk: unavailable — {hint}"]
    lines = ["Merge-order risk:"]
    if not pairs:
        lines.append("  no shared-community PR pairs")
        return lines
    for pair in pairs:
        communities = ", ".join(pair["communities"])
        lines.append(
            f"  #{pair['a']} + #{pair['b']}: {pair['shared']} shared ({communities})"
        )
    return lines


def _render(payload: dict) -> str:
    lines = _render_list(payload["prs"])
    if payload.get("impact") is not None:
        lines.extend([""] + _render_impact(payload["store"], payload["impact"]))
    if payload.get("conflicts") is not None:
        lines.extend([""] + _render_conflicts(payload["conflicts"]))
    return "\n".join(lines)


def _per_pr_seeds(conn, repo: Path, records) -> dict[int, list[str]]:
    per_pr: dict[int, list[str]] = {}
    for record in records:
        diff_text = fetch_pr_diff(repo, record.number)
        seeds, _unindexed = pr_seed_symbols(conn, str(repo), diff_text)
        per_pr[record.number] = [seed["id"] for seed in seeds]
    return per_pr


def _assemble(repo: Path, db: str, impact_ref: str | None, with_conflicts: bool) -> dict:
    conn = get_db(db, read_only=True)
    try:
        records = list_open_prs(repo)
        impact = None
        if impact_ref is not None:
            number, _head_ref, base_ref = fetch_pr_view(repo, impact_ref)
            diff_text = fetch_pr_diff(repo, number)
            impact = compute_pr_impact(
                conn, str(repo), diff_text=diff_text, base_ref=base_ref
            )
        conflicts = None
        if with_conflicts:
            conflicts = community_overlaps(conn, _per_pr_seeds(conn, repo, records))
        built = store_build_age(conn)
    finally:
        conn.close()
    return {
        "store": {"index": "local", "built": built},
        "prs": [asdict(record) for record in records],
        "impact": impact,
        "conflicts": conflicts,
    }


@main.command("prs")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option(
    "--impact",
    "impact_ref",
    metavar="PR|BRANCH",
    default=None,
    help="Compute graph impact for one open PR (number or branch).",
)
@click.option(
    "--conflicts",
    "with_conflicts",
    is_flag=True,
    help="Rank PR pairs by shared-community overlap.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the full report as JSON.",
)
def prs(db, impact_ref, with_conflicts, as_json):
    """List open PRs with CI and review state from gh."""
    try:
        payload = _assemble(Path.cwd(), db, impact_ref, with_conflicts)
    except GhError as exc:
        raise click.ClickException(str(exc)) from exc
    except BlastBaseError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    except sqlite3.Error as exc:
        raise click.ClickException(f"cannot read store {db}: {exc}") from exc
    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo(_render(payload))
