#!/usr/bin/env python3
"""Verify a "comments/docstrings-only" change didn't alter executable code.

Compares the AST (docstrings blanked out) of every changed .py file between
HEAD and the working tree. If the only changes are comments and docstrings,
the AST dumps are identical and this exits 0. Any executable-code difference
(renamed variable, changed string literal, reordered logic) exits 1 with the
offending files listed.

Usage:
    # verify uncommitted working-tree changes are comment-only
    python scripts/verify_no_code_change.py

    # verify a specific commit vs its parent (e.g. before pushing)
    python scripts/verify_no_code_change.py HEAD~1

Why: sub-agents and bulk edits reliably self-report "no code changed" while
silently touching string literals, renaming variables, or dropping lines.
This is the programmatic check that catches it. It's the item behind the
"Agent / bulk-edit safety" section of docs/release-checklist.md.

Exit codes: 0 = clean (comment-only), 1 = executable code changed somewhere.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path


def _blank_docstrings(tree: ast.AST) -> ast.AST:
    """Set every docstring's string literal to '' so docstring edits don't
    register as AST differences. Only the *text* is blanked; structure is kept."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant)
                and isinstance(getattr(body[0].value, "value", None), str)
            ):
                body[0].value.value = ""
    return tree


def _dump(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return ast.dump(_blank_docstrings(tree))


def _changed_files(ref: str) -> tuple[list[str], str]:
    """Return (changed .py files, target ref).

    Compares `ref` against HEAD if the working tree is clean (verifying a
    just-made commit), or against the working tree if it's dirty (verifying
    uncommitted edits before staging). `target` is "" for the working tree.
    """
    dirty = subprocess.run(
        ["git", "diff", "--quiet", "HEAD"],
        capture_output=True,
    )
    target = "HEAD" if dirty.returncode == 0 else ""
    cmd = ["git", "diff", "--name-only"]
    cmd.append(f"{ref}..{target}" if target else ref)
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    files = [f for f in result.stdout.strip().splitlines() if f.endswith(".py")]
    return files, target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "ref",
        nargs="?",
        default="HEAD",
        help="Git ref to compare against (default: HEAD = uncommitted changes).",
    )
    args = parser.parse_args()

    files, target = _changed_files(args.ref)
    if not files:
        print("No changed .py files to verify.")
        return 0

    mismatches: list[str] = []
    for f in files:
        # Get the baseline (ref) version from git.
        base = subprocess.run(
            ["git", "show", f"{args.ref}:{f}"],
            capture_output=True,
            text=True,
        )
        if base.returncode != 0:
            continue  # new file — no baseline to compare.

        try:
            base_dump = ast.dump(_blank_docstrings(ast.parse(base.stdout)))
        except SyntaxError as e:
            print(f"  {f}: baseline parse error: {e}", file=sys.stderr)
            mismatches.append(f)
            continue

        # Get the new version: from git (target=HEAD) or the working tree.
        if target:
            new = subprocess.run(
                ["git", "show", f"{target}:{f}"],
                capture_output=True,
                text=True,
            )
            if new.returncode != 0:
                continue
            try:
                new_dump = ast.dump(_blank_docstrings(ast.parse(new.stdout)))
            except SyntaxError as e:
                print(f"  {f}: {target} parse error: {e}", file=sys.stderr)
                mismatches.append(f)
                continue
        else:
            try:
                new_dump = _dump(Path(f))
            except SyntaxError as e:
                print(f"  {f}: working-tree parse error: {e}", file=sys.stderr)
                mismatches.append(f)
                continue

        if base_dump != new_dump:
            mismatches.append(f)

    checked = len(files)
    if not mismatches:
        print(f"OK: {checked} changed .py file(s) — comment/docstring changes only (AST identical).")
        return 0

    print(
        f"FAIL: {len(mismatches)} of {checked} changed .py file(s) have executable-code "
        f"differences (docstrings stripped, AST still differs):",
        file=sys.stderr,
    )
    for f in mismatches:
        print(f"  {f}", file=sys.stderr)
    print(
        "\nRun `git diff <ref> -- <files>` and confirm whether the code change is intended. "
        "If it is, this check isn't the problem — the commit message is.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
