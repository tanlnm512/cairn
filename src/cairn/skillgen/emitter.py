"""SKILL.md emitter: skillgen's single format-aware layer.

Renders the skill layout clients already load -- ``---``-fenced YAML
frontmatter (``name``, ``description``) over a markdown body, mirroring the
static package ``cairn/agent_integration/skill/SKILL.md`` -- plus the
default landing path and an optional ``references/`` split for long
sections.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Sequence, Tuple

import yaml

__all__ = [
    "REFERENCE_SPLIT_CHARS",
    "landing_dir",
    "render_skill",
    "slugify",
    "split_references",
]

# Sections longer than this many characters move to references/<slug>.md.
REFERENCE_SPLIT_CHARS = 4000

_SKILLS_HOME = Path(".agents") / "skills"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Normalize text to a filesystem slug: lowercase, non-alphanumeric runs
    collapsed to one hyphen, leading/trailing hyphens stripped."""
    return _SLUG_RE.sub("-", text.lower()).strip("-")


def landing_dir(workspace: str | Path, slug: str) -> Path:
    """Default landing directory ``<workspace>/.agents/skills/cairn-<slug>/``.

    ``slug`` must be a single path segment; callers derive it from the
    selector stem (``slugify``).
    """
    if not slug or slug.strip() != slug or slug in (".", ".."):
        raise ValueError(f"slug must be a non-empty path segment: {slug!r}")
    if "/" in slug or "\\" in slug:
        raise ValueError(f"slug must be a single path segment: {slug!r}")
    return Path(workspace) / _SKILLS_HOME / f"cairn-{slug}"


def render_skill(
    name: str, description: str, sections: Sequence[Tuple[str, str]]
) -> str:
    """Render the exact SKILL.md bytes.

    ``sections`` are ordered ``(heading, markdown)`` pairs rendered under
    ``##`` headings in the given order. The frontmatter parses back to
    exactly ``{"name": name, "description": description}``; the description
    carries the load-trigger wording supplied by the caller.
    """
    if not name.strip():
        raise ValueError("skill name must be non-empty")
    if not description.strip():
        raise ValueError("skill description must be non-empty")
    sections = list(sections)
    for heading, _ in sections:
        if not heading.strip():
            raise ValueError("section headings must be non-empty")

    frontmatter = yaml.safe_dump(
        {"name": name, "description": description},
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    lines: List[str] = [f"# {name}", ""]
    for heading, markdown in sections:
        lines += [f"## {heading}", "", markdown.rstrip(), ""]
    body = "\n".join(lines).rstrip("\n") + "\n"
    return f"---\n{frontmatter}\n---\n\n{body}"


def split_references(
    sections: Sequence[Tuple[str, str]],
    max_chars: int = REFERENCE_SPLIT_CHARS,
) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """Split sections into ``(inline_sections, reference_files)``.

    Sections at or under ``max_chars`` stay inline; longer ones become
    ``(references/<slug>.md, content)`` files (content re-fenced under its
    heading) and are replaced inline by a one-line pointer. The pointer
    names the path without backticks: the deterministic critic rejects
    backtick-quoted paths it cannot verify against the graph.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    inline: List[Tuple[str, str]] = []
    refs: List[Tuple[str, str]] = []
    used: set = set()
    for heading, markdown in sections:
        if len(markdown) <= max_chars:
            inline.append((heading, markdown))
            continue
        base = slugify(heading) or "section"
        filename = f"references/{base}.md"
        counter = 2
        while filename in used:
            filename = f"references/{base}-{counter}.md"
            counter += 1
        used.add(filename)
        refs.append((filename, f"# {heading}\n\n{markdown.rstrip()}\n"))
        inline.append((heading, f"Full section: {filename}."))
    return inline, refs
