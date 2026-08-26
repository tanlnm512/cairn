"""Stable identity derivation for ingested source documents.

Implements FR-007 / D-006: the path-derived stable ID, the
``"{stable ID} — {title}"`` display title, deterministic slugs with a
``({repo})`` suffix on collisions (numbered when the suffix itself
collides), the source-tag union, and real description extraction (never
the title).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

from cairn.knowledge.ingest.parser import (
    ParsedDoc,
    _BOLD_STATUS_RE,
    _HEADING_RE,
    _HEADING_STATUS_BARE_RE,
    _HEADING_STATUS_RE,
)
# _NON_ALNUM is imported (not re-defined) so the untruncated slug computed
# in _stable_id always matches slugify's charset by construction.
from cairn.okf.utils import _NON_ALNUM, slugify

# slugify truncates to 60 chars (src/cairn/okf/utils.py:13-20); every slug
# built here must keep the stable-id prefix (and any collision suffix)
# inside that bound.
_SLUG_MAX = 60

# sha1 fragment (content key, not security -- usedforsecurity=False)
# appended to stable IDs whose slugified path exceeded the cap; matches the
# symbol-disambiguation fragment in parsers/scip_importer.py.
_HASH_LEN = 8


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
    stable_id = _stable_id(repo, relpath)
    title = _display_title(stable_id, parsed.title)
    slug = slugify(title)
    if seen_slugs is not None:
        if slug in seen_slugs:
            slug = _unique_slug(slug, repo, seen_slugs)
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


def _stable_id(repo: str, relpath: str) -> str:
    """``slugify(repo/relpath)``, made collision-resistant when the 60-char
    cap bites: truncation silently discards the path tail, so distinct long
    paths (".../design.md" vs ".../deploy.md") can slugify identically and
    would merge identities. When the untruncated slug exceeds the cap,
    re-anchor it with a short sha1 fragment of the full slug: identical
    paths hash identically, distinct paths stay distinct, and the result
    still fits the cap. Paths that fit keep the exact plain slug.
    """
    full = _NON_ALNUM.sub("-", f"{repo}/{relpath}".lower()).strip("-")
    if len(full) <= _SLUG_MAX:
        return full
    digest = hashlib.sha1(
        full.encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:_HASH_LEN]
    room = _SLUG_MAX - len(digest) - 1
    return f"{full[:room].rstrip('-')}-{digest}"


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


def _unique_slug(base: str, repo: str, seen: set[str]) -> str:
    """First collision slug not already taken: ``-({repo})``, then
    ``-({repo})-2``, ``-({repo})-3``... The single-suffix form can itself
    collide (same repo/relpath/title fed three times), and a repeated slug
    means one staged file silently overwrites another — so keep numbering
    until the candidate is fresh."""
    counter = 1
    candidate = _collision_slug(base, repo)
    while candidate in seen:
        counter += 1
        candidate = _collision_slug(base, repo, counter)
    return candidate


def _collision_slug(base: str, repo: str, counter: int = 1) -> str:
    """Slug with the ``({repo})`` suffix (plus ``-{n}`` for counter > 1);
    the suffix survives the 60-char cap."""
    parts = [slugify(repo)]
    if counter > 1:
        parts.append(str(counter))
    suffix = "-".join(part for part in parts if part)
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
