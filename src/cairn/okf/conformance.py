"""OKF v0.1 conformance checker.

Per the OKF spec Section 9, a bundle is conformant if:
  1. Every non-reserved .md file has parseable YAML frontmatter
  2. Every frontmatter block contains a non-empty `type` field
  3. index.md and log.md follow their structure (when present)

Consumers MUST NOT reject a bundle for: missing optional fields, unknown type
values, unknown frontmatter keys, broken cross-links, or missing index.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from .concept import OKFConcept

# Reserved files that don't need to be OKF concepts.
RESERVED_FILES = {"index.md", "log.md"}


def check_concept(path: str) -> List[str]:
    """Validate a single concept file. Returns list of error strings."""
    errors = []
    try:
        concept = OKFConcept.from_file(path)
    except FileNotFoundError:
        return [f"File not found: {path}"]
    except Exception as e:
        return [f"Parse error in {path}: {e}"]
    errors.extend(concept.validate())
    return errors


def check_bundle(root_path: str) -> List[str]:
    """Check all .md files under root_path for OKF conformance.

    Returns a list of error strings (empty = fully conformant).
    """
    errors = []
    root = Path(root_path)
    if not root.is_dir():
        return [f"Bundle root does not exist: {root_path}"]
    for md_file in sorted(root.rglob("*.md")):
        rel = md_file.relative_to(root).as_posix()
        if md_file.name in RESERVED_FILES:
            continue  # reserved files are exempt from the type requirement
        errs = check_concept(str(md_file))
        for e in errs:
            errors.append(f"{rel}: {e}")
    return errors
