"""Document knowledge storage and lifecycle.

Business documents (policies, specs, design docs) stored as OKF concepts in
the .knowledge/knowledge/ subtree. Scoped via concept_id prefix "knowledge/".
"""
from __future__ import annotations

import logging
from typing import List, Optional
from pathlib import Path

from cairn.okf.concept import OKFConcept
from cairn.okf.bundle import OKFBundle
from cairn.okf.provenance import Tier
from cairn.okf.utils import slugify

logger = logging.getLogger(__name__)

# Maximum file size for import (10MB) to prevent excessive memory usage
IMPORT_MAX_FILE_SIZE = 10 * 1024 * 1024


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
    doc_source: str = "manual",    # "manual" or "imported"
) -> str:
    """Ingest a document. Returns the concept_id.

    concept_id pattern: knowledge/{doc_type}/{slug}.

    ``steps`` is an optional ordered list of step dicts stored under the
    ``steps`` extension (intended for ``doc_type="workflow"``; see
    ``src/knowledge/workflow.py``).
    """
    slug = slugify(title)
    safe_doc_type = slugify(doc_type) or "general"
    concept_id = f"knowledge/{safe_doc_type}/{slug}"

    extensions = {
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

    concept = OKFConcept(
        type=f"Knowledge-{doc_type}",
        title=title,
        description=title,  # one-line summary; caller can override via body
        resource=resource,
        tags=tags or [],
        concept_id=concept_id,
        body=body,
        extensions=extensions,
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


# The valid status values, enforced as a forward-only lifecycle.
DOC_STATUSES = ("active", "superseded", "archived")


def update_status(bundle: OKFBundle, doc_id: str, new_status: str) -> bool:
    """Update doc_status, enforcing the documented forward-only lifecycle.

    Rejects (returns False, doesn't raise):
      - unknown status values (only "active"/"superseded"/"archived" valid)
      - backward transitions (e.g. archived -> active); re-add as a fresh
        document instead.
    A missing/unknown current status is treated as "active".
    """
    if new_status not in DOC_STATUSES:
        return False
    with bundle.lock():
        concept = get_document(bundle, doc_id)
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
    """Delete a knowledge document and its embedding rows.

    Removes the .md file from the bundle and optionally cleans up
    knowledge_embeddings rows in the database.
    """
    with bundle.lock():
        concept = get_document(bundle, doc_id)
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
    """Batch-ingest all .md files in a directory. Returns list of concept_ids.

    Validates file size and skips oversized files (above IMPORT_MAX_FILE_SIZE).
    Imported docs default to doc_source='imported' for lower authority.
    """
    imported = []
    for md_file in sorted(Path(dir_path).rglob("*.md")):
        # Validate file size
        file_size = md_file.stat().st_size
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
