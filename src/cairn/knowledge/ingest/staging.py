"""OKF outbox staging and the dry-run manifest (D-009 v1).

Each staged entry is one source document carrying the four upstream
contracts, constructible in a single expression:

    StagedEntry(repo, relpath, origin, (p := parse_source_doc(text)),
                classify_doc(p, relpath, include_drafts),
                build_identity(repo, relpath, p))

``stage_outbox`` writes one OKF markdown file per accepted document at
``knowledge/{doc_type}/{slug}.md`` (OKFConcept + to_markdown own the
frontmatter key order; the YAML is never hand-rolled) plus
``manifest.json``:

* top level: version, generated_at, workspace (the outbox directory),
  counts {accepted, skipped, by_type, by_repo} -- by_* count accepted
  documents only
* rows sorted by (repo, relpath); accepted rows carry every
  add_document argument plus origin/repo/source_path, the body with its
  ``Source:`` provenance line, and the staged-file path; skipped rows
  carry source_path + skip reason and stage no file
* each staged file's frontmatter title/type is cross-checked against
  its row at staging time (D-009) -- a mismatch raises

Store parity (audit 2026-08): the doc_type is slugified exactly as
``knowledge.store.add_document`` does (``slugify(doc_type) or "general"``),
so a workspace-config doc_type (``cairn.json`` ``ingest.classification``
only checks non-emptiness) can never traverse out of the outbox; and
title/body/description are routed through ``strip_private_data`` BEFORE
the OKFConcept and manifest rows are built, so the dry-run outbox holds
precisely what an approved run would keep (the store re-redacts
idempotently). Provenance fields -- resource/source_path, the ``Source:``
line, tags, affects_* -- stay verbatim.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

from cairn.knowledge.ingest.classifier import Classification
from cairn.knowledge.ingest.identity import DocIdentity
from cairn.knowledge.ingest.parser import ParsedDoc
from cairn.memory.privacy import strip_private_data
from cairn.okf.concept import OKFConcept
from cairn.okf.provenance import Tier
from cairn.okf.utils import slugify

#: D-009 manifest schema version.
MANIFEST_VERSION = 1

#: Manifest file name at the outbox root.
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class StagedEntry:
    """One source document ready to stage: T001 tuple + T002-T004 outputs."""

    repo: str
    relpath: str
    origin: str
    parsed: ParsedDoc
    classification: Classification
    identity: DocIdentity


def stage_outbox(entries: Iterable[StagedEntry], outbox_dir: Path) -> dict:
    """Stage the OKF outbox and manifest; return the parsed manifest.

    Rows are emitted in sorted (repo, relpath) order so re-run diffs are
    deterministic (D-008).
    """
    outbox_dir = Path(outbox_dir)
    outbox_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    by_type: dict[str, int] = {}
    by_repo: dict[str, int] = {}
    accepted = 0
    skipped = 0
    for entry in sorted(entries, key=lambda e: (e.repo, e.relpath)):
        source_path = _source_path(entry)
        if entry.classification.skip_reason is not None:
            rows.append(
                {"source_path": source_path, "skip": entry.classification.skip_reason}
            )
            skipped += 1
            continue
        rows.append(_stage_document(entry, source_path, outbox_dir))
        accepted += 1
        doc_type = _safe_doc_type(entry.classification.doc_type)
        by_type[doc_type] = by_type.get(doc_type, 0) + 1
        by_repo[entry.repo] = by_repo.get(entry.repo, 0) + 1

    manifest = {
        "version": MANIFEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(outbox_dir),
        "counts": {
            "accepted": accepted,
            "skipped": skipped,
            "by_type": by_type,
            "by_repo": by_repo,
        },
        "rows": rows,
    }
    manifest_file = outbox_dir / MANIFEST_NAME
    with manifest_file.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=False)
        fh.write("\n")
    return json.loads(manifest_file.read_text(encoding="utf-8"))


def _source_path(entry: StagedEntry) -> str:
    """``{repo}/{relpath}`` posix -- the row's source_path and resource."""
    return f"{entry.repo}/{PurePosixPath(entry.relpath).as_posix()}"


def _safe_doc_type(doc_type: str) -> str:
    """Slugified doc_type, mirroring ``knowledge.store.add_document``.

    doc_type can come from workspace config (``cairn.json``
    ``ingest.classification``), which is only checked for non-emptiness,
    so an unsanitized value like ``"../../escaped"`` must never reach a
    staged path. ``slugify`` reduces it to path-safe alphanumerics and
    the ``or "general"`` fallback matches the store exactly, so the
    staged path and the stored concept_id always agree.
    """
    return slugify(doc_type) or "general"


def _stage_document(entry: StagedEntry, source_path: str, outbox_dir: Path) -> dict:
    """Write one accepted document's OKF file; return its manifest row."""
    doc_type = _safe_doc_type(entry.classification.doc_type)
    identity = entry.identity
    concept_id = f"knowledge/{doc_type}/{identity.slug}"
    staged_path = f"{concept_id}.md"
    title = strip_private_data(identity.title)
    description = strip_private_data(identity.description)
    stripped_body = entry.parsed.body.rstrip()
    if stripped_body:
        body = f"{stripped_body}\n\nSource: {source_path}\n"
    else:
        body = f"Source: {source_path}\n"
    # Redact the composed body (Source line included) so the staged row
    # equals what the store would keep -- add_document redacts the full
    # body it is handed, and strip_private_data is idempotent.
    body = strip_private_data(body)
    tags = _merged_tags(identity.tags, entry.classification.extra_tags)
    concept = OKFConcept(
        type=f"Knowledge-{doc_type}",
        title=title,
        description=description,
        resource=source_path,
        tags=tags,
        concept_id=concept_id,
        body=body,
        extensions={
            "tier": Tier.ASSERTED.value,
            "doc_status": "active",
            "doc_source": "imported",
            "affects_modules": list(identity.affects_modules),
            "affects_repos": list(identity.affects_repos),
        },
    )
    concept.to_file(str(outbox_dir / staged_path))
    _cross_check(outbox_dir / staged_path, title, doc_type, staged_path)
    return {
        "concept_id": concept_id,
        "title": title,
        "doc_type": doc_type,
        "tags": tags,
        "description": description,
        "resource": source_path,
        "affects_repos": list(identity.affects_repos),
        "affects_modules": list(identity.affects_modules),
        "origin": entry.origin,
        "repo": entry.repo,
        "source_path": source_path,
        "body": body,
        "staged_path": staged_path,
    }


def _merged_tags(base: list[str], extra: list[str]) -> list[str]:
    """Identity tags with classification extras appended, deduped in order."""
    merged = list(base)
    merged.extend(tag for tag in extra if tag not in merged)
    return merged


def _cross_check(
    staged_file: Path, title: str, doc_type: str, staged_path: str
) -> None:
    """D-009 staging-time check: the staged frontmatter matches its row."""
    staged = OKFConcept.from_file(str(staged_file))
    expected_type = f"Knowledge-{doc_type}"
    if staged.title != title or staged.type != expected_type:
        raise ValueError(
            f"staging cross-check failed for {staged_path}: expected "
            f"title={title!r} type={expected_type!r}, staged "
            f"title={staged.title!r} type={staged.type!r}"
        )
