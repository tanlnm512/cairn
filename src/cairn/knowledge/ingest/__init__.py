"""Staged document ingestion into the knowledge store.

Documentation sources — fed markdown files, repository doc trees, fed
binary documents converted to markdown — pass one pipeline: normalize to
markdown, parse and classify, stage an OKF outbox plus a dry-run manifest
for review. The knowledge store is written only after explicit approval;
staging never touches it.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from cairn.knowledge.ingest.adapters import (
    FED_ORIGIN,
    FED_REPO,
    FedBinaryAdapter,
    FedMarkdownAdapter,
    RepoScanAdapter,
)
from cairn.knowledge.ingest.classifier import Classification, classify_doc
from cairn.knowledge.ingest.identity import build_identity
from cairn.knowledge.ingest.parser import ParsedDoc, parse_source_doc
from cairn.knowledge.ingest.staging import StagedEntry, stage_outbox
from cairn.paths import resolve_workspace

__all__ = ["run_ingest"]


def run_ingest(
    files: Iterable[Path | str],
    dirs: Iterable[Path | str],
    outbox: Path | str | None = None,
    include_drafts: bool = False,
    repos: Iterable[Path | str] = (),
) -> dict:
    """Run the stage-only pipeline over fed markdown and repo scans.

    Composition: source adapters -> parse -> classify (skips recorded
    with reasons, workspace overrides layered per FR-010) -> identity ->
    stage_outbox. Stops after staging; the knowledge store is never
    touched (dry-run default).
    """
    from cairn.knowledge.ingest.config import load_ingest_config

    overrides = load_ingest_config()
    outbox_dir = Path(outbox) if outbox is not None else _default_outbox()
    docs, scan_skips = _sourced(files, dirs, repos, overrides)
    seen_slugs: set[str] = set()
    entries: list[StagedEntry] = []
    for repo, relpath, text, origin in sorted(docs):
        parsed = parse_source_doc(text)
        classification = classify_doc(
            parsed, relpath, include_drafts, rules=overrides.classification
        )
        _apply_fed_tag(origin, parsed, classification)
        entries.append(
            StagedEntry(
                repo=repo,
                relpath=relpath,
                origin=origin,
                parsed=parsed,
                classification=classification,
                identity=build_identity(repo, relpath, parsed, seen_slugs),
            )
        )
    for repo, relpath, reason in scan_skips:
        entries.append(_scan_skip_entry(repo, relpath, reason))
    return stage_outbox(entries, outbox_dir)


def _sourced(
    files: Iterable[Path | str],
    dirs: Iterable[Path | str],
    repos: Iterable[Path | str],
    overrides=None,
) -> tuple[list, list]:
    """Collect (docs, adapter-level skips) from every source."""
    from cairn.knowledge.ingest.convert import CONVERT_SUFFIXES

    fed = [Path(p) for p in files]
    binary = [p for p in fed if p.suffix.lower() in CONVERT_SUFFIXES]
    markdown = [p for p in fed if p.suffix.lower() not in CONVERT_SUFFIXES]

    docs = list(FedMarkdownAdapter([*markdown, *dirs]).iter_docs())
    skips: list[tuple[str, str, str]] = []
    if binary:
        converter = FedBinaryAdapter(binary)
        docs.extend(converter.iter_docs())
        skips.extend(
            (FED_REPO, relpath, reason) for relpath, reason in converter.skipped
        )
    for repo_root in repos:
        skip_add = tuple(overrides.skip_add) if overrides else ()
        skip_disable = tuple(overrides.skip_disable) if overrides else ()
        adapter = RepoScanAdapter(
            repo_root, skip_add=skip_add, skip_disable=skip_disable
        )
        docs.extend(adapter.iter_docs())
        skips.extend(
            (Path(repo_root).name, relpath, reason)
            for relpath, reason in adapter.skipped
        )
    return docs, skips


def _scan_skip_entry(repo: str, relpath: str, reason: str) -> StagedEntry:
    """A skip-listed scan doc: manifest skip row, never staged or parsed."""
    return StagedEntry(
        repo=repo,
        relpath=relpath,
        origin=repo,
        parsed=ParsedDoc(),
        classification=Classification(skip_reason=reason),
        identity=build_identity(repo, relpath, ParsedDoc()),
    )


def _default_outbox() -> Path:
    """D-008: ``<workspace-root>/.cairn/ingest-outbox/``."""
    return resolve_workspace() / ".cairn" / "ingest-outbox"


def _apply_fed_tag(origin: str, parsed: ParsedDoc, classification: Classification) -> None:
    """Origin tags: status-less fed docs get ``fed``; converted get ``converted``."""
    from cairn.knowledge.ingest.adapters import CONVERTED_ORIGIN

    if origin == CONVERTED_ORIGIN:
        if CONVERTED_ORIGIN not in classification.extra_tags:
            classification.extra_tags.append(CONVERTED_ORIGIN)
        return
    if origin != FED_ORIGIN or parsed.status is not None:
        return
    if FED_ORIGIN not in classification.extra_tags:
        classification.extra_tags.append(FED_ORIGIN)
