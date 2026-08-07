"""Boot-time freshness: catches up the graph with disk edits at `cairn serve` start.

**A long-running `cairn serve` process does NOT see source edits made after it
started.** This module runs once at boot to absorb edits made while the server
was down; it does not provide per-query freshness. ``ensure_fresh_force`` does
a stat()-based check of the files table vs disk and re-indexes only changed
files. ``invalidate_gitignore_cache`` clears the scanner's gitignore cache
when a .gitignore changes.
"""
from __future__ import annotations

from pathlib import Path

from . import scanner as scanner_mod


# ---------------------------------------------------------------------------
# Gitignore cache invalidation
# ---------------------------------------------------------------------------

def invalidate_gitignore_cache(path: str):
    """Clear the gitignore cache for the repo containing `path`.

    Called when a .gitignore file itself changes, so subsequent scans
    pick up the new ignore rules. The cache is keyed by repo root.
    """
    p = Path(path)
    # Walk up to find the repo root (dir containing .git).
    for parent in p.parents:
        if (parent / ".git").exists():
            key = str(parent)
            scanner_mod._gitignore_cache.pop(key, None)
            return


# ---------------------------------------------------------------------------
# Core freshness check
# ---------------------------------------------------------------------------

def _detect_changed(conn, workspace: str) -> list[str]:
    """Compare files table (size, mtime) against disk. Return changed paths.

    The scan loop behind `ensure_fresh_force` (and `_do_catch_up` generally).
    """
    changed: list[str] = []

    for repo_path in scanner_mod.discover_repos(workspace):
        repo_name = repo_path.name
        try:
            file_rows = conn.execute(
                "SELECT path, size, mtime FROM files WHERE repo_id = ?",
                (repo_name,),
            ).fetchall()
        except Exception:
            continue

        # If this repo has no rows, it likely hasn't been indexed yet (or its
        # repo_id key doesn't match after a path/portability migration). Skip it
        # rather than fall back to ALL rows: a broad fallback would mis-classify
        # every other repo's file as "new" for this repo and trigger a full
        # workspace reindex on every boot. The repo will be picked up by a
        # later `cairn build`/`cairn update`.
        if not file_rows:
            continue

        for row in file_rows:
            # files.path is repo-relative; resolve to absolute via the chokepoint.
            p = Path(scanner_mod.resolve_file_path(workspace, repo_name, row["path"]))
            try:
                st = p.stat()
            except OSError:
                # File deleted/moved since index.
                changed.append(str(p))
                continue
            if st.st_size != (row["size"] or 0):
                # Size changed — definitely different content.
                changed.append(str(p))
            elif abs(st.st_mtime - (row["mtime"] or 0.0)) > 0.5:
                # mtime changed but size same — could be touch-only or
                # real edit with same byte count. Re-index to be safe.
                changed.append(str(p))

        # Detect NEW source files not yet in the DB. Storage is repo-relative;
        # the scanner yields absolute, so compare on both forms.
        existing = {row["path"] for row in file_rows}
        for src in scanner_mod.iter_source_files(repo_path):
            rel = str(src.relative_to(repo_path)) if str(src).startswith(str(repo_path)) else str(src)
            if rel not in existing and str(src) not in existing:
                changed.append(str(src))

    return changed


def ensure_fresh_force(conn, workspace: str) -> int:
    """Detect and re-index disk changes. Called once at `cairn serve` boot."""
    return _do_catch_up(conn, workspace)


def _do_catch_up(conn, workspace: str) -> int:
    """Detect changed files and re-index them. Returns count."""
    changed = _detect_changed(conn, workspace)
    if not changed:
        return 0

    # Invalidate gitignore cache if any .gitignore files changed
    gitignore_changes = [p for p in changed if p.endswith(".gitignore")]
    for gitignore_path in gitignore_changes:
        invalidate_gitignore_cache(gitignore_path)

    from .incremental import reindex_paths

    result = reindex_paths(conn, workspace, changed)
    return result["reindexed"] + result["deleted"]
