#!/usr/bin/env python3
"""Check that every checkable link in the docs set resolves to a real file.

Scope: ``docs/**/*.md`` (recursive), the repo-root ``README.md``, and
``benchmarks/README.md``. An enumerated scope file that does not exist yet
is reported and skipped, not failed on -- a missing file is not a broken
link.

Algorithm (order matters -- stripping code spans before extraction is what
keeps inline-code placeholders such as ``[-> postmortem](postmortems/...)``
from counting as rendered links):

1. strip `` `inline code` `` spans;
2. extract ``](target)`` inline-link targets (an optional trailing title is
   dropped);
3. drop ``http*`` / ``mailto:`` / ``#anchor`` targets -- only relative
   file-to-file links are checkable against the working tree;
4. split any ``#fragment`` off, resolve the rest relative to the containing
   file, and require the resolved path to exist.

Back-link report (advisory): every top-level ``docs/*.md`` page other than
the ``docs/README.md`` index itself should carry at least one link that
*resolves* to ``docs/README.md``. The test is resolution, not string
matching, so ``../README.md`` -- the repo root -- never satisfies it by
accident. Pages without such a link are listed under a warning heading;
the listing does not affect the exit code.

Output is deterministic: one line per broken link, then ``TOTAL broken: N``.

Usage:
    python3 scripts/check_doc_links.py

Exit codes:
    0  green -- every checkable relative link resolved
    1  at least one broken link
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
DOCS_INDEX = DOCS_DIR / "README.md"

EXTRA_SCOPE = (REPO_ROOT / "README.md", REPO_ROOT / "benchmarks" / "README.md")

# Targets that point off-repo or within a page: not checkable on disk.
UNRESOLVABLE_PREFIXES = ("http", "mailto:", "#")

CODE_SPAN = re.compile(r"`[^`]*`")
LINK_TARGET = re.compile(r"]\(([^)]+)\)")


def scope_files() -> list[Path]:
    files = list(DOCS_DIR.glob("**/*.md"))
    for path in EXTRA_SCOPE:
        if path.is_file():
            files.append(path)
        else:
            print(f"note: {os.path.relpath(path, REPO_ROOT)} not present; skipped")
    return sorted(files)


def link_targets(text: str) -> list[str]:
    """Extract checkable relative targets, in document order."""
    stripped = CODE_SPAN.sub(" ", text)
    targets = []
    for raw in LINK_TARGET.findall(stripped):
        # Drop an optional link title: ``](path "title")`` checks only `path`.
        parts = raw.split()
        target = parts[0] if parts else ""
        if target and not target.startswith(UNRESOLVABLE_PREFIXES):
            targets.append(target)
    return targets


def resolve_target(from_file: Path, target: str) -> Path:
    """Resolve a relative target (minus its fragment) from a file's dir."""
    return (from_file.parent / target.split("#", 1)[0]).resolve()


def main() -> int:
    broken = 0
    for md in scope_files():
        for target in link_targets(md.read_text()):
            resolved = resolve_target(md, target)
            if not resolved.exists():
                broken += 1
                print(
                    f"{os.path.relpath(md, REPO_ROOT)}: "
                    f"broken link [{target}] -> {os.path.relpath(resolved, REPO_ROOT)}"
                )

    no_backlink = []
    for page in sorted(DOCS_DIR.glob("*.md")):
        if page == DOCS_INDEX:
            continue
        targets = link_targets(page.read_text())
        if not any(resolve_target(page, t) == DOCS_INDEX for t in targets):
            no_backlink.append(os.path.relpath(page, REPO_ROOT))
    if no_backlink:
        print(
            "warning (advisory, does not affect exit): "
            f"{len(no_backlink)} docs/ page(s) have no link resolving to "
            "docs/README.md:"
        )
        for page in no_backlink:
            print(f"  {page}")

    print(f"TOTAL broken: {broken}")
    return 0 if broken == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
