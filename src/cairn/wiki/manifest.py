"""Wiki manifest: incremental-regeneration state for the page plan.

This file is the PLAN kind of the two-kind wiki contract. It records
pipeline intent only: which pages should exist (the plan entry: identity,
title/description, module, seeds, input hash) and where their queue work
stands (``task_id`` and the cumulative ``queue_attempts`` counter). It never
describes content — no bodies, no provenance, no lifecycle verdicts. What
exists is decided solely by promoted content concepts
(``wiki/pages/{repo}/{page_id}``, see :mod:`cairn.wiki.lifecycle`), and
lifecycle state is derived at read time, never stored.

A JSON document at ``<knowledge>/_wiki/manifest.json`` with a schema
marker, keyed by ``{repo}/{page_id}``. Older documents upgrade in memory on
load: a schema-1 document (keyed by page id alone) is re-keyed by
recovering the repo from the row task's facts, else the promoted concept
path, else the row is dropped with a warning (a later generate re-plans
it); schema-2 rows carried ``state``/``commit_sha``/``attempts``, which the
plan kind no longer owns — normalization strips the retired fields and
renames ``attempts`` to ``queue_attempts``. Loads never write back; the
next save persists the current schema. The ``_wiki/`` directory holds no
``.md`` files, so ``OKFBundle.list_concepts`` never lists the manifest as
a concept. Writes are atomic: mkstemp inside the target directory, flush
+ fsync before ``os.replace``, unlink the temp file on any failure,
``False`` on ``OSError``.
"""
from __future__ import annotations

import json
import os
import tempfile
import warnings
from pathlib import Path
from typing import Any, Dict

from ..okf.bundle import OKFBundle

MANIFEST_SCHEMA = "cairn-wiki-manifest-3"

MANIFEST_DIR = "_wiki"
MANIFEST_FILENAME = "manifest.json"


def _root_of(bundle_or_knowledge_root: Any) -> Path:
    if isinstance(bundle_or_knowledge_root, OKFBundle):
        return Path(bundle_or_knowledge_root.root)
    return Path(bundle_or_knowledge_root)


def _manifest_path(knowledge_root: str | os.PathLike) -> Path:
    return Path(knowledge_root) / MANIFEST_DIR / MANIFEST_FILENAME


def _repo_from_task(bundle: Any, task_id: Any) -> str | None:
    if not isinstance(bundle, OKFBundle) or not task_id:
        return None
    try:
        from ..llm.tasks import get_task

        task = get_task(bundle, str(task_id))
    except Exception:
        return None
    repo = (task.facts or {}).get("repo") if task else None
    return repo if isinstance(repo, str) and repo else None


def _repo_from_concepts(bundle_or_knowledge_root: Any, page_id: str) -> str | None:
    pages_dir = _root_of(bundle_or_knowledge_root) / "wiki" / "pages"
    if not pages_dir.is_dir():
        return None
    for repo_dir in sorted(p for p in pages_dir.iterdir() if p.is_dir()):
        if (repo_dir / f"{page_id}.md").is_file():
            return repo_dir.name
    return None


def _migrate_v1(doc: Dict[str, Any], bundle_or_knowledge_root: Any) -> None:
    """Re-key schema-1 rows (page_id alone) to ``{repo}/{page_id}`` in
    place; rows whose repo cannot be recovered are dropped with a
    warning."""
    doc["schema"] = MANIFEST_SCHEMA
    pages = doc.get("pages")
    if not isinstance(pages, dict):
        return
    migrated: Dict[str, Any] = {}
    for key, row in pages.items():
        if "/" in str(key):
            migrated[str(key)] = row
            continue
        page_id = str((row or {}).get("page_id") or key)
        repo = _repo_from_task(bundle_or_knowledge_root, (row or {}).get("task_id"))
        if repo is None:
            repo = _repo_from_concepts(bundle_or_knowledge_root, page_id)
        if repo is None:
            warnings.warn(
                f"wiki manifest row {key!r} has no recoverable repo; "
                "dropped (re-planned on the next generate)"
            )
            continue
        migrated[f"{repo}/{page_id}"] = row
    doc["pages"] = migrated


def _normalize_rows(doc: Dict[str, Any]) -> None:
    """Strip the fields the plan kind no longer owns, in memory.

    Schema-2 rows carried ``state`` (a lifecycle verdict — derived at read
    time by :mod:`cairn.wiki.lifecycle`, never stored) and ``commit_sha``
    (content provenance — owned by the promoted concept alone), and named
    their counter ``attempts``. Idempotent; runs after any key migration.
    """
    for row in doc.get("pages", {}).values():
        if not isinstance(row, dict):
            continue
        row.pop("state", None)
        row.pop("commit_sha", None)
        if "attempts" in row:
            row["queue_attempts"] = row.pop("attempts")


def load_manifest(bundle_or_knowledge_root: Any) -> Dict[str, Any]:
    """Load the manifest for a bundle or knowledge root.

    Accepts an ``OKFBundle``, a ``Path``, or a path string. A missing
    manifest file returns the empty document (not an error); a document
    without a ``pages`` section loads with empty pages; unknown top-level
    sections are preserved. A document in a schema older than
    :data:`MANIFEST_SCHEMA` is upgraded in memory (never written back) --
    an ``OKFBundle`` source also lets the row's task facts identify the
    repo. Malformed JSON raises ``ValueError``.
    """
    path = _manifest_path(_root_of(bundle_or_knowledge_root))
    if not path.is_file():
        return {"schema": MANIFEST_SCHEMA, "pages": {}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"wiki manifest {path} is not valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise ValueError(f"wiki manifest {path} must contain a JSON object")
    doc.setdefault("pages", {})
    if doc.get("schema") != MANIFEST_SCHEMA:
        _migrate_v1(doc, bundle_or_knowledge_root)
    _normalize_rows(doc)
    return doc


def save_manifest(knowledge_root: Any, manifest: Dict[str, Any]) -> bool:
    """Write the manifest atomically; returns True on success.

    Creates ``<knowledge_root>/_wiki/`` when absent. On ``OSError`` leaves
    any previous manifest intact with no temp file behind and returns
    ``False``.
    """
    directory = _manifest_path(_root_of(knowledge_root)).parent
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(directory), prefix=".manifest-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2, sort_keys=True)
                fh.write("\n")
                # fsync BEFORE the replace: os.replace is atomic within the
                # filesystem but not durable, so a crash must never persist
                # the directory entry over unwritten data blocks.
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, directory / MANIFEST_FILENAME)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        return False
    return True


def should_skip(
    page_row: Dict[str, Any],
    current_plan_entry: Dict[str, Any],
    bundle: Any,
    repo: str,
) -> bool:
    """True when a page needs no new writing task.

    Skip requires the recorded input hash to equal the current plan hash
    AND promoted content to exist (the gated ``Wiki-Article`` concept at
    ``wiki/pages/{repo}/{page_id}``); without content the page is not
    promoted. Never raises. ``--force`` is the caller's concern.
    """
    if page_row.get("input_hash") != current_plan_entry.get("input_hash"):
        return False
    from .lifecycle import is_promoted

    return is_promoted(bundle, repo, current_plan_entry["page_id"])
