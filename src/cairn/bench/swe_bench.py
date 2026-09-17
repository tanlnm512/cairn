"""SWE-bench task loader for the pinned Lite subset.

The pin manifest (``benchmarks/datasource/swe-bench-subset.json``) carries
the dataset revision sha and the ordered instance-id subset; validation
runs before any fetch. ``fetch_dataset_rows`` is the network touchpoint:
``load_dataset("princeton-nlp/SWE-bench_Lite", split="test",
revision=<manifest sha>)`` through a lazy ``datasets`` import that raises
an actionable ImportError when the ``bench`` extra is not installed.

``load_tasks`` slices the fetched split to the manifest subset, in manifest
order, projecting each row to the five-field task contract
(:data:`TASK_FIELDS`); gold-patch fields never cross this seam. This module
is the only one that knows the HF row schema.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from .datasource import load_manifest

# Schema tag of the subset manifest; aligns with the committed pin file.
PIN_MANIFEST_SCHEMA = "cairn-bench-swe-bench-subset"

DATASET_NAME = "princeton-nlp/SWE-bench_Lite"
SPLIT = "test"

# The five-field task contract: everything downstream consumes; the
# gold-patch fields (patch, test_patch, FAIL_TO_PASS, PASS_TO_PASS) stay
# behind this seam.
TASK_FIELDS = (
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "environment_setup_commit",
)

_REQUIRED_PIN_KEYS = ("schema", "dataset_revision_sha", "subset")
_REVISION_SHA = re.compile(r"[0-9a-f]{40}")


def validate_pin_manifest(manifest: object) -> list[str]:
    """Check a subset manifest against its schema; [] means valid.

    Required: ``schema`` == :data:`PIN_MANIFEST_SCHEMA`,
    ``dataset_revision_sha`` a 40-char hex commit sha, ``subset`` a
    non-empty list of unique string instance ids. Unknown keys are ignored
    so the schema can grow without invalidating existing manifests.
    """
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return [f"manifest: expected a JSON object, got {type(manifest).__name__}"]
    for key in _REQUIRED_PIN_KEYS:
        if key not in manifest:
            errors.append(f"manifest: missing required key '{key}'")
    schema = manifest.get("schema")
    if "schema" in manifest and schema != PIN_MANIFEST_SCHEMA:
        errors.append(f"schema: expected {PIN_MANIFEST_SCHEMA!r}, got {schema!r}")
    revision = manifest.get("dataset_revision_sha")
    if "dataset_revision_sha" in manifest and (
        not isinstance(revision, str) or not _REVISION_SHA.fullmatch(revision)
    ):
        errors.append(
            "dataset_revision_sha: expected a 40-char hex commit sha, "
            f"got {revision!r}"
        )
    subset = manifest.get("subset")
    if "subset" in manifest:
        if not isinstance(subset, list) or not subset:
            errors.append("subset: expected a non-empty list of instance ids")
        else:
            seen: set[str] = set()
            for instance_id in subset:
                if not isinstance(instance_id, str):
                    errors.append(
                        f"subset: expected string instance ids, got {instance_id!r}"
                    )
                elif instance_id in seen:
                    errors.append(f"subset: duplicate instance id {instance_id!r}")
                else:
                    seen.add(instance_id)
    return errors


def load_pin_manifest(path: Path | str) -> dict:
    """Read and validate a pin manifest; return it as a dict.

    Reuses :func:`cairn.bench.datasource.load_manifest` for the I/O:
    unreadable files raise from the load (FileNotFoundError, ValueError for
    invalid JSON); readable-but-invalid content raises ValueError listing
    every validation error.
    """
    manifest = load_manifest(path)
    errors = validate_pin_manifest(manifest)
    if errors:
        raise ValueError(f"pin manifest {path} is invalid: " + "; ".join(errors))
    return manifest


def fetch_dataset_rows(revision: str) -> list[dict]:
    """Load the SWE-bench Lite ``test`` split at the pinned revision.

    The ``datasets`` import is lazy: the dependency is only needed on this
    path, and a missing install raises an ImportError naming the package
    and the ``bench`` extra that provides it.
    """
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise ImportError(
            "fetching SWE-bench rows needs the optional 'datasets' package; "
            "install it with the bench extra: pip install 'cairn[bench]'"
        ) from exc
    dataset = load_dataset(DATASET_NAME, split=SPLIT, revision=revision)
    return [dict(row) for row in dataset]


def load_tasks(
    manifest: dict,
    *,
    fetch: Callable[[str], list[dict]] | None = None,
) -> list[dict]:
    """Assemble the task list: the manifest subset, in manifest order.

    Validates the manifest before any fetch. ``fetch`` defaults to
    :func:`fetch_dataset_rows` and receives the manifest's
    ``dataset_revision_sha``; injecting it keeps assembly offline. Each row
    is projected to :data:`TASK_FIELDS`. Raises ValueError when the
    manifest is invalid, a fetched row lacks a contract field, or a pinned
    instance id is absent from the fetched rows. The result is a pure
    function of (manifest, fetched rows).
    """
    errors = validate_pin_manifest(manifest)
    if errors:
        raise ValueError("pin manifest is invalid: " + "; ".join(errors))
    if fetch is None:
        fetch = fetch_dataset_rows
    rows = fetch(manifest["dataset_revision_sha"])
    by_id = {row["instance_id"]: row for row in rows}
    tasks: list[dict] = []
    for instance_id in manifest["subset"]:
        row = by_id.get(instance_id)
        if row is None:
            raise ValueError(
                f"pinned instance id {instance_id!r} is absent from "
                f"{DATASET_NAME} {SPLIT} at the pinned revision"
            )
        missing = [field for field in TASK_FIELDS if field not in row]
        if missing:
            raise ValueError(
                f"row {instance_id!r} is missing required field(s): "
                + ", ".join(missing)
            )
        tasks.append({field: row[field] for field in TASK_FIELDS})
    return tasks
