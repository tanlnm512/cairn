"""Document knowledge storage and lifecycle."""
from __future__ import annotations

import logging
from typing import List, Optional
from pathlib import Path

from cairn.okf.concept import OKFConcept
from cairn.okf.bundle import OKFBundle
from cairn.okf.provenance import Tier
from cairn.okf.utils import slugify
from cairn.knowledge.relationships import normalize_relationships
from ..memory.privacy import strip_private_data

logger = logging.getLogger(__name__)

# Maximum file size for import (10MB) to prevent excessive memory usage
IMPORT_MAX_FILE_SIZE = 10 * 1024 * 1024


def doc_type_slug(doc_type: str) -> str:
    """The path-safe doc_type for knowledge ids: slugified, "general" fallback."""
    return slugify(doc_type) or "general"


def _redact_step_descriptions(steps: List[dict]) -> List[dict]:
    """Return copied steps with only free-text descriptions redacted."""
    out = []
    for step in steps:
        desc = step.get("description")
        if isinstance(desc, str) and desc:
            step = {**step, "description": strip_private_data(desc)}
        out.append(step)
    return out


def normalize_doc_id(bundle: OKFBundle, concept_id: str) -> str:
    """Normalize a doc id to its bare bundle-relative concept id."""
    cid = concept_id[:-3] if concept_id.endswith(".md") else concept_id
    try:
        rel = str(
            (bundle.root / f"{cid}.md").resolve().relative_to(bundle.root.resolve())
        )
    except (ValueError, OSError):
        return cid
    # The probe path carries the .md suffix; the normalized id is bare,
    # matching the knowledge_embeddings.doc_id convention.
    return rel[:-3] if rel.endswith(".md") else rel


def _refuse_out_of_namespace(
    bundle: OKFBundle, doc_id: str, concept: Optional[OKFConcept]
) -> None:
    """Raise when an out-of-namespace target is about to be acted on."""
    if concept is not None:
        resolved = normalize_doc_id(bundle, concept.concept_id)
    else:
        resolved = normalize_doc_id(bundle, doc_id)
        try:
            file_path = bundle._validate_concept_path(resolved)
        except ValueError:
            return  # escapes root -> the delete path below refuses anyway
        if not file_path.exists():
            return  # nothing to act on; caller reports not-found as before
    if resolved == "knowledge" or resolved.startswith("knowledge/"):
        return
    raise ValueError(
        f"Refused: '{doc_id}' resolves to '{resolved}', outside the "
        f"knowledge/ namespace. The knowledge store only manages knowledge/ "
        f"concepts (compass/wiki/memory documents cannot be modified or "
        f"deleted through it)."
    )


def add_document(
    bundle: OKFBundle,
    title: str,
    body: str,
    doc_type: str,              # "business-rule", "spec", "decision", "workflow"
    tags: Optional[List[str]] = None,
    affects_modules: Optional[List[str]] = None,
    affects_repos: Optional[List[str]] = None,
    resource: Optional[str] = None,   # canonical URI (Jira, Confluence)
    owner: Optional[str] = None,
    epic_link: Optional[str] = None,
    steps: Optional[List[dict]] = None,
    description: Optional[str] = None,  # one-line summary; defaults to title
    doc_source: str = "manual",    # "manual" or "imported"
    relationships: Optional[List[dict]] = None,  # {concept_id, relation, kind}
    verified_refs: Optional[List[dict]] = None,  # {ref, kind: file|symbol, verified}
) -> str:
    """Redact, normalize, and store one document; return its concept id."""
    title = strip_private_data(title)
    body = strip_private_data(body)
    # Explicit descriptions are redacted here; an absent one falls back to
    # the already-redacted title below (no double-redaction needed).
    description = strip_private_data(description) if description else None
    if steps:
        steps = _redact_step_descriptions(steps)
    slug = slugify(title)
    safe_doc_type = doc_type_slug(doc_type)
    concept_id = f"knowledge/{safe_doc_type}/{slug}"

    extensions: dict = {
        "tier": Tier.ASSERTED.value,
        "doc_status": "active",
        "doc_owner": owner or "",
        "doc_source": doc_source,
        "epic_link": epic_link or "",
        "affects_modules": affects_modules or [],
        "affects_repos": affects_repos or [],
    }
    # Only add the steps key when actually given.
    if steps:
        extensions["steps"] = steps
    # Author-declared relationships (D1.1) ride under relates_to.
    if relationships:
        extensions["relates_to"] = normalize_relationships(relationships)

    concept = OKFConcept(
        type=f"Knowledge-{doc_type}",
        title=title,
        description=description or title,
        resource=resource,
        tags=tags or [],
        concept_id=concept_id,
        body=body,
        extensions=extensions,
        # Verified doc->code refs (D1.3): OKF v0.2 family, wiki pattern.
        verified=list(verified_refs) if verified_refs else None,
    )
    bundle.write_concept(concept)
    return concept_id


def list_documents(
    bundle: OKFBundle,
    doc_type: Optional[str] = None,
    status: Optional[str] = None,
    tag: Optional[str] = None,
) -> List[OKFConcept]:
    """List knowledge documents. Filters by type, status, tag."""
    # Trailing slash for path-segment matching (bundle uses startswith).
    prefix = f"knowledge/{doc_type}/" if doc_type else "knowledge/"
    cids = bundle.list_concepts(prefix=prefix)
    results = []
    for cid in cids:
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        if status and concept.extensions.get("doc_status") != status:
            continue
        if tag and tag not in concept.tags:
            continue
        results.append(concept)
    return results


def get_document(bundle: OKFBundle, doc_id: str) -> Optional[OKFConcept]:
    """Read a knowledge document by concept_id."""
    try:
        return bundle.read_concept(doc_id)
    except Exception:
        return None


def resolve_knowledge_doc(bundle: OKFBundle, doc_id: str) -> OKFConcept:
    """Resolve a knowledge doc or reject missing and foreign ids."""
    concept = get_document(bundle, doc_id)
    if concept is None:
        raise ValueError(
            f"Unknown knowledge document: '{doc_id}'. "
            "Run `cairn knowledge list` for stored doc ids."
        )
    _refuse_out_of_namespace(bundle, doc_id, concept)
    return concept


# The valid status values, enforced as a forward-only lifecycle.
DOC_STATUSES = ("active", "superseded", "archived")


def update_status(bundle: OKFBundle, doc_id: str, new_status: str) -> bool:
    """Apply a forward-only status transition and return success."""
    if new_status not in DOC_STATUSES:
        return False
    with bundle.lock():
        concept = get_document(bundle, doc_id)
        _refuse_out_of_namespace(bundle, doc_id, concept)
        if not concept:
            return False
        current = concept.extensions.get("doc_status", "active")
        if current not in DOC_STATUSES:
            current = "active"
        if DOC_STATUSES.index(new_status) < DOC_STATUSES.index(current):
            return False
        concept.extensions["doc_status"] = new_status
        bundle.write_concept(concept)
        return True


def delete_document(bundle: OKFBundle, doc_id: str, conn=None) -> bool:
    """Delete a document and optional embeddings without committing."""
    with bundle.lock():
        concept = get_document(bundle, doc_id)
        _refuse_out_of_namespace(bundle, doc_id, concept)
        if concept is not None:
            doc_id = concept.concept_id
        # Route the file path through the write-path validator so a malformed /
        # malicious doc_id can't escape the bundle root. Raises ValueError on
        # escape; treat that as "nothing to delete".
        try:
            file_path = bundle._validate_concept_path(doc_id)
        except ValueError:
            return False
        if not file_path.exists():
            return False
        file_path.unlink()
        # Unlink bypasses write_concept: drop the cached index or the deleted
        # concept stays searchable.
        bundle.invalidate_search_index()
    # Clean up embeddings in DB. Normalize doc_id to relative for DB lookup.
    try:
        rel_id = str(Path(doc_id).relative_to(bundle.root))
    except ValueError:
        rel_id = doc_id
    # Clean up embeddings in DB. Do NOT commit here -- the caller owns the
    # transaction boundary; committing a connection we don't own can either
    # commit an in-flight caller transaction or hit "database is locked".
    if conn is not None:
        conn.execute("DELETE FROM knowledge_embeddings WHERE doc_id = ?", (rel_id,))
    return True


def import_directory(
    bundle: OKFBundle,
    dir_path: str,
    doc_type: str = "spec",
    tags: Optional[List[str]] = None,
    affects_modules: Optional[List[str]] = None,
    affects_repos: Optional[List[str]] = None,
) -> List[str]:
    """Import in-size markdown files as lower-authority knowledge docs."""
    imported = []
    for md_file in sorted(Path(dir_path).rglob("*.md")):
        # Validate file size; one vanished file skips, never aborts the import.
        try:
            file_size = md_file.stat().st_size
        except OSError as e:
            logger.warning("Skipping vanished file %s: %s", md_file, e)
            continue
        if file_size > IMPORT_MAX_FILE_SIZE:
            logger.warning(
                "Skipping oversized file %s (%d bytes, max %d bytes)",
                md_file, file_size, IMPORT_MAX_FILE_SIZE
            )
            continue

        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read file %s: %s", md_file, e)
            continue

        title = md_file.stem.replace("-", " ").replace("_", " ").title()
        try:
            cid = add_document(
                bundle,
                title=title,
                body=text,
                doc_type=doc_type,
                tags=tags,
                affects_modules=affects_modules,
                affects_repos=affects_repos,
                doc_source="imported",  # Imported docs get lower authority
            )
            imported.append(cid)
        except Exception as e:
            logger.warning("Failed to import document from %s: %s", md_file, e)
            continue
    return imported
