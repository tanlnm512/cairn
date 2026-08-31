"""Wiki manifest: incremental-regeneration state for the page plan.

A JSON document at ``<knowledge>/_wiki/manifest.json`` with a schema
marker, keyed by ``{repo}/{page_id}``; each row is the page's plan entry
plus ``task_id``, ``state`` (one of ``PAGE_STATES``), and the cumulative
``attempts`` counter. A schema-1 document (keyed by page id alone) is
upgraded in memory on load: the repo is recovered from the row task's
facts, else the promoted concept path, else the row is dropped with a
warning (a later generate re-plans it). Loads never write back; the next
save persists the current schema. The ``_wiki/`` directory holds no
``.md`` files, so ``OKFBundle.list_concepts`` never lists the manifest as
a concept. Writes are atomic: mkstemp inside the target directory, flush
+ fsync before ``os.replace``, unlink the temp file on any failure,
``False`` on ``OSError``. "Promoted" is never trusted from a stored row
-- readers derive it by reading the ``wiki/pages/{repo}/{page_id}``
concept.
"""
from __future__ import annotations

import json
import os
import tempfile
import warnings
from pathlib import Path
from typing import Any, Dict

from ..okf.bundle import OKFBundle

MANIFEST_SCHEMA = "cairn-wiki-manifest-2"

PAGE_STATES = (
    "planned",
    "queued",
    "in_progress",
    "promoted",
    "failed",
)

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
    AND the promoted concept (``wiki/pages/{repo}/{page_id}``) to be
    readable; an unreadable concept is treated as not promoted. Never
    raises. ``--force`` is the caller's concern.
    """
    if page_row.get("input_hash") != current_plan_entry.get("input_hash"):
        return False
    page_id = current_plan_entry["page_id"]
    try:
        bundle.read_concept(f"wiki/pages/{repo}/{page_id}")
    except Exception:
        return False
    return True
