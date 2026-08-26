"""Stable identity derivation for ingested source documents.

Implements FR-007 / D-006: the path-derived stable ID, the
``"{stable ID} — {title}"`` display title, deterministic slugs with a
``({repo})`` suffix on collisions, the source-tag union, and real
description extraction (never the title).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from cairn.knowledge.ingest.parser import (
    ParsedDoc,
    _BOLD_STATUS_RE,
    _HEADING_RE,
    _HEADING_STATUS_BARE_RE,
    _HEADING_STATUS_RE,
)
from cairn.okf.utils import slugify

# slugify truncates to 60 chars (src/cairn/okf/utils.py:13-20); every slug
# built here must keep the stable-id prefix (and any collision suffix)
# inside that bound.
_SLUG_MAX = 60


@dataclass
class DocIdentity:
    """Derived identity of one source document, consumed by staging/executor."""

    stable_id: str
    title: str
    slug: str
    tags: list[str]
    affects_repos: list[str]
    affects_modules: list[str]
    description: str


def build_identity(
    repo: str,
    relpath: str,
    parsed: ParsedDoc,
    seen_slugs: set[str] | None = None,
) -> DocIdentity:
    """Derive the identity of one source document.

    ``seen_slugs`` is the caller's accumulating slug set: pass one set while
    processing rows in sorted ``(repo, relpath)`` order (D-006) and each
    final slug is added to it, so collision suffixing stays deterministic.
    Omitted -> no collision resolution (pure function of the inputs).
    """
    stable_id = slugify(f"{repo}/{relpath}")
    title = _display_title(stable_id, parsed.title)
    slug = slugify(title)
    if seen_slugs is not None:
        if slug in seen_slugs:
            slug = _collision_slug(slug, repo)
        seen_slugs.add(slug)
    tags = list(parsed.tags)
    for extra in (stable_id, repo):
        if extra and extra not in tags:
            tags.append(extra)
    parent = PurePosixPath(relpath).parent.as_posix()
    return DocIdentity(
        stable_id=stable_id,
        title=title,
        slug=slug,
        tags=tags,
        affects_repos=[repo],
        # Root-level docs ("README.md", "") have no module dir.
        affects_modules=[] if parent in ("", ".") else [parent],
        description=_description(repo, relpath, parsed),
    )


def _display_title(stable_id: str, doc_title: str | None) -> str:
    """``"{stable ID} — {title}"`` with the title capped to the slug budget.

    len(slugify(x)) <= len(x), so capping raw characters keeps the whole
    slug <= 60 with the stable-id prefix intact.
    """
    if not doc_title:
        return stable_id
    budget = _SLUG_MAX - len(stable_id) - 1  # 1 for the joining hyphen
    if budget <= 0:
        return stable_id
    return f"{stable_id} — {doc_title[:budget]}"


def _collision_slug(base: str, repo: str) -> str:
    """Slug with the ``({repo})`` suffix; the suffix survives the 60-char cap."""
    suffix = slugify(repo)
    room = _SLUG_MAX - len(suffix) - 1
    if room <= 0:
        return suffix
    trimmed = base[:room].rstrip("-")
    return f"{trimmed}-{suffix}" if trimmed else suffix


def _description(repo: str, relpath: str, parsed: ParsedDoc) -> str:
    """Frontmatter description, else first meaningful body paragraph, else a
    provenance line — never the title."""
    if parsed.description:
        return parsed.description
    paragraph = _first_meaningful_paragraph(parsed.body)
    if paragraph:
        return paragraph
    return f"Imported from {repo}/{relpath}"


def _first_meaningful_paragraph(body: str) -> str | None:
    """First body paragraph that is non-empty, non-heading, non-marker.

    Fenced code blocks are skipped whole (open through close). The
    paragraph's lines are joined into one line.
    """
    lines: list[str] = []
    in_fence = False
    for raw in body.splitlines():
        stripped = raw.strip()
        if in_fence:
            if stripped.startswith("```"):
                in_fence = False
            continue
        if stripped.startswith("```"):
            in_fence = True
            continue
        if not stripped:
            if lines:
                break
            continue
        if lines:
            lines.append(stripped)
        elif not (_HEADING_RE.match(stripped) or _is_status_marker(stripped)):
            lines.append(stripped)
    return " ".join(lines) if lines else None


def _is_status_marker(line: str) -> bool:
    return bool(
        _BOLD_STATUS_RE.match(line)
        or _HEADING_STATUS_RE.match(line)
        or _HEADING_STATUS_BARE_RE.match(line)
    )
