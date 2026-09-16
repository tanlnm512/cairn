"""Skill CLI: the skill group (generate)."""
from __future__ import annotations

import os
from pathlib import Path

import click

from ..skillgen.assembly import DEFAULT_TOP_K, DEFAULT_TOP_N
from .main import DEFAULT_DB_PATH, get_db, main


@main.group()
def skill():
    """Generated codebase skills."""


@skill.command("generate")
@click.argument("selectors", nargs=-1, required=True)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Target directory (default: <workspace>/.agents/skills/cairn-<slug>/).",
)
@click.option(
    "--top-k",
    type=click.IntRange(min=1),
    default=DEFAULT_TOP_K,
    show_default=True,
    help="Maximum symbols packaged.",
)
@click.option(
    "--top-n",
    type=click.IntRange(min=1),
    default=DEFAULT_TOP_N,
    show_default=True,
    help="Maximum memories packaged.",
)
@click.option("--db", default=None, help="SQLite DB path (default: resolved store).")
@click.option(
    "--polish",
    is_flag=True,
    help="Queue a polish task through the LLM task queue; the completed result "
    "replaces SKILL.md only after the critic gate accepts it. No model runs in-process.",
)
@click.option(
    "--knowledge",
    default=None,
    help="Knowledge bundle backing the --polish task queue "
    "(default: CAIRN_KNOWLEDGE or the store-adjacent .knowledge).",
)
def generate(selectors, output, top_k, top_n, db, polish, knowledge):
    """Generate a SKILL.md from SELECTORS (module, directory prefix, or symbol list)."""
    from ..paths import resolve_workspace
    from ..skillgen import resolve_selector
    from ..skillgen.assembly import assemble_draft
    from ..skillgen.emitter import landing_dir, render_skill, slugify, split_references

    conn = get_db(db)
    polish_outcome = None
    try:
        resolution = resolve_selector(conn, " ".join(selectors))
        _fail_fast(resolution)
        draft = assemble_draft(conn, resolution, top_k=top_k, top_n=top_n)

        stem = draft.module or selectors[0]
        slug = slugify(stem) or "skill"
        target = (
            Path(output)
            if output is not None
            else landing_dir(resolve_workspace(), slug)
        )
        inline, references = split_references(_compose_sections(draft))
        description = (
            f"Module context for {stem}. Load when asked about {stem} structure, "
            "call graphs, or blast radius before editing its files."
        )
        # The skill name matches the landing directory name; loaders key on it.
        rendered = render_skill(f"cairn-{slug}", description, inline)
        _verify_or_abort(conn, rendered)
        if polish:
            polish_outcome = _polish_stage(
                conn, knowledge or _default_knowledge(), slug, rendered, target
            )
    finally:
        conn.close()

    _write(target, rendered, references)
    click.echo(f"Wrote SKILL.md: {target / 'SKILL.md'}")
    if polish_outcome is not None:
        _settle_polish(polish_outcome, target)


def _default_knowledge() -> str:
    """--polish queue bundle when --knowledge is omitted: CAIRN_KNOWLEDGE,
    else the store-adjacent default `cairn task` reads."""
    from_env = os.environ.get("CAIRN_KNOWLEDGE")
    if from_env:
        return from_env
    return str(DEFAULT_DB_PATH.parent / ".knowledge")


def _polish_stage(conn, knowledge: str, slug: str, rendered: str, target: Path):
    """Enqueue/consume the skill-polish task. Imports stay inside the
    --polish branch: the default path never loads the queue stack."""
    from ..okf.bundle import OKFBundle
    from ..skillgen.polish import run_polish_stage

    return run_polish_stage(
        OKFBundle(knowledge), conn, slug, rendered, str(target / "SKILL.md")
    )


def _settle_polish(outcome, target: Path) -> None:
    """Apply or report the polish outcome against the written skill."""
    if outcome.state == "applied":
        (target / "SKILL.md").write_text(outcome.rendered, encoding="utf-8")
        click.echo(
            f"Applied polished SKILL.md (task {outcome.task_id} passed the critic gate)."
        )
        return
    if outcome.state == "queued":
        click.echo(
            f"Queued polish task {outcome.task_id}; any agent with the cairn "
            "skill can process it:"
        )
        click.echo("  cairn task list --kind skill-polish --status pending")
        click.echo("  cairn task claim <id> && cairn task complete <id> --result-file <path>")
        click.echo("Rerun 'cairn skill generate --polish' after completion to apply the result.")
        return
    if outcome.state == "pending":
        click.echo(
            f"Polish task {outcome.task_id} is still in flight; rerun "
            "'cairn skill generate --polish' once it completes."
        )
        return
    # rejected | unavailable: the deterministic skill stands.
    detail = outcome.reason
    if outcome.failing_refs:
        listed = ", ".join(outcome.failing_refs)
        detail = f"{detail}; unverified references: {listed}" if detail else listed
    click.echo(
        f"Polish task {outcome.task_id} not applied ({detail}); "
        "the deterministic skill stands.",
        err=True,
    )


def _fail_fast(resolution) -> None:
    """Refuse to generate when a selector token resolved nothing; nothing is written."""
    if resolution.unmatched or not resolution.candidates:
        listed = ", ".join(resolution.unmatched) or "no candidates resolved"
        raise click.ClickException(f"Unresolved selector: {listed}; no skill written.")


def _verify_or_abort(conn, rendered: str) -> None:
    """Critic gate on the exact bytes destined for disk; rejection writes nothing."""
    from ..skillgen.gate import verify_draft

    result = verify_draft(conn, rendered)
    if not result:
        listed = ", ".join(result.failing_refs)
        raise click.ClickException(
            f"Unverified references: {listed}; no skill written."
        )


def _compose_sections(draft) -> list[tuple[str, str]]:
    """Ordered (heading, markdown) sections; empty knowledge sections are omitted."""
    sections: list[tuple[str, str]] = []
    if draft.compass_body:
        sections.append(("Module Compass", draft.compass_body))
    if draft.symbols:
        sections.append(
            ("Key Symbols", "\n".join(f"- {symbol}" for symbol in draft.symbols))
        )
    if draft.memories:
        sections.append(
            ("Module Memory", "\n".join(f"- {memory}" for memory in draft.memories))
        )
    return sections


def _write(target: Path, rendered: str, references) -> Path:
    """Write SKILL.md plus any references/ files into the target directory."""
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(rendered, encoding="utf-8")
    for filename, content in references:
        path = target / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return target
