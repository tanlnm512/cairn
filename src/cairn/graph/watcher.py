"""Boot-time freshness: catches up the graph with disk edits at `cairn serve` start.

**A long-running `cairn serve` process does NOT see source edits made after it
started.** This module runs once at boot to absorb edits made while the
server was down; it does not provide per-query freshness. Restart the server
(or run `cairn build`/`cairn update` from the CLI, which re-indexes directly
rather than going through this module) to pick up mid-session changes.

Architecture:
    1. `ensure_fresh_force(conn, workspace)` — called once, at `cairn serve`
       boot (`server.run()`). stat()-based check of files table vs disk;
       re-indexes only changed files. No caching -- always runs when called.
    2. `invalidate_gitignore_cache(path)` — clears scanner's gitignore cache
       when a .gitignore changes, so subsequent scans pick up new rules.
       Called by `_do_catch_up` when .gitignore files are in the changed set.
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

        # build_graph stores repo_id='' for single-repo workspaces (workspace
        # root has no relative-path component to derive a repo name from), so
        # the repo_name-keyed lookup above returns nothing for that case. Fall
        # back to matching ALL file rows (regardless of repo_id) when the
        # named lookup is empty — otherwise every file looks "new" on every
        # boot, and ensure_fresh_force re-indexes the entire workspace each
        # time (20s+ on a large repo, which blocks cairn serve's stdio startup
        # past the MCP client's connect timeout).
        if not file_rows:
            try:
                file_rows = conn.execute(
                    "SELECT path, size, mtime FROM files"
                ).fetchall()
            except Exception:
                continue

        for row in file_rows:
            p = Path(row["path"])
            if not p.exists():
                # The path in the DB may be repo-relative (build stores relative
                # paths); resolve against the repo root before stat-ing.
                if not p.is_absolute():
                    p = repo_path / row["path"]
                if not p.exists():
                    changed.append(str(p))
                    continue
            try:
                st = p.stat()
                if st.st_size != (row["size"] or 0):
                    # Size changed — definitely different content.
                    changed.append(str(p))
                elif abs(st.st_mtime - (row["mtime"] or 0.0)) > 0.5:
                    # mtime changed but size same — could be touch-only or
                    # real edit with same byte count. Re-index to be safe.
                    changed.append(str(p))
            except OSError:
                continue

        # Detect NEW source files not yet in the DB.
        existing = {row["path"] for row in file_rows}
        for src in scanner_mod.iter_source_files(repo_path):
            # Match both the absolute and repo-relative forms (build stores
            # relative; the scanner yields absolute).
            rel = str(src.relative_to(repo_path)) if str(src).startswith(str(repo_path)) else str(src)
            if str(src) not in existing and rel not in existing:
                changed.append(str(src))

    return changed


def ensure_fresh_force(conn, workspace: str) -> int:
    """Detect and re-index disk changes. The only freshness entry point that
    actually runs today -- called once, at `cairn serve` boot. See module
    docstring: there is no per-query equivalent currently wired in.
    """
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
