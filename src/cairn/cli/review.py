"""Review loop CLI command."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..graph.blast import BlastBaseError
from ..review.engine import (
    build_pack,
    build_pre_submit,
    has_blocking_matches,
    render_pack,
    render_pre_submit,
)
from .main import DEFAULT_DB_PATH, get_db, main


@main.command("review")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option(
    "--base",
    default=None,
    metavar="<ref>",
    help="Build the review pack for the diff against this ref's merge base.",
)
@click.option(
    "--pre-submit",
    is_flag=True,
    help="Check the diff against recorded mistake and pattern memories.",
)
@click.option(
    "--capture-event",
    default=None,
    metavar="<file>",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Record a resolved review-comment event JSON as a memory.",
)
@click.option(
    "--gate",
    is_flag=True,
    help="With --pre-submit, exit non-zero when a mistake or pattern matches the diff.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "markdown"]),
    default="text",
    help="Pack rendering format.",
)
@click.option(
    "--fuzzy",
    is_flag=True,
    help="Also traverse unresolved name-only edges.",
)
@click.option(
    "--refresh/--no-refresh",
    default=None,
    help="Reindex drifted files before diffing.",
)
def review(db, base, pre_submit, capture_event, gate, output_format, fuzzy, refresh):
    """Review-context pack and review-loop hooks for a change."""
    given = [
        flag
        for flag, present in (
            ("--base", base is not None),
            ("--pre-submit", pre_submit),
            ("--capture-event", capture_event is not None),
        )
        if present
    ]
    if len(given) > 1:
        raise click.UsageError(
            f"{', '.join(given)} are mutually exclusive; pass exactly one."
        )
    if not given:
        raise click.UsageError(
            "Pass exactly one mode: --base <ref>, --pre-submit, "
            "or --capture-event <file>."
        )
    if gate and not pre_submit:
        raise click.UsageError("--gate requires --pre-submit.")

    if base is not None:
        from ..paths import resolve_workspace

        try:
            conn = get_db(db)
            try:
                pack = build_pack(
                    conn,
                    str(resolve_workspace()),
                    base=base,
                    fuzzy=fuzzy,
                    refresh=refresh,
                )
            finally:
                conn.close()
        except BlastBaseError as error:
            raise click.ClickException(str(error)) from error
        click.echo(render_pack(pack, output_format))
    elif pre_submit:
        from ..paths import resolve_workspace

        try:
            conn = get_db(db)
            try:
                result = build_pre_submit(
                    conn,
                    str(resolve_workspace()),
                    fuzzy=fuzzy,
                    refresh=refresh,
                )
            finally:
                conn.close()
        except BlastBaseError as error:
            raise click.ClickException(str(error)) from error
        rendered = render_pre_submit(result, output_format)
        if rendered:
            click.echo(rendered)
        if gate and has_blocking_matches(result):
            sys.exit(1)
    else:
        import json

        from ..okf.bundle import OKFBundle
        from ..paths import resolve_store
        from ..review.capture import capture_review_event

        try:
            payload = json.loads(capture_event.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise click.ClickException(
                f"cannot read event file {capture_event}: {error}"
            ) from error

        bundle = OKFBundle(str(resolve_store().knowledge))
        conn = get_db(db)
        try:
            result = capture_review_event(conn, bundle, payload)
            if result["recorded"]:
                from ..graph.embeddings import embed_memory_concepts

                embed_memory_concepts(conn, bundle, [result["path"]])
            conn.commit()
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        finally:
            conn.close()
        if result["recorded"]:
            click.echo(
                f"Recorded {result['type']} '{result['title']}' -> "
                f"{result['path']} (tier={result['tier']})"
            )
        else:
            click.echo("Comment is not resolved; nothing recorded.")
