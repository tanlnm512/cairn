"""Git utilities: remote URL and current commit hash for a repo path."""
from __future__ import annotations

import subprocess
from typing import Optional


def _run_git(args: list, cwd: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def get_remote_url(repo_path: str) -> Optional[str]:
    """Return the origin remote URL, or None if unavailable."""
    return _run_git(["config", "--get", "remote.origin.url"], repo_path)


def get_current_commit(repo_path: str) -> Optional[str]:
    """Return the current HEAD commit hash, or None."""
    return _run_git(["rev-parse", "HEAD"], repo_path)


def get_repo_head(repo_name: str, workspace: Optional[str] = None) -> Optional[str]:
    """Return the named repo's HEAD commit hash under the workspace, or None.

    The repos table stores workspace-relative paths, so the repo directory is
    resolved through the workspace root rather than any stored path. Lazy
    imports: utils must not import the graph package at module load.
    """
    from ..graph.scanner import resolve_repo_path
    from ..paths import resolve_store

    try:
        ws = str(resolve_store(workspace).workspace)
        return get_current_commit(str(resolve_repo_path(ws, repo_name)))
    except OSError:
        return None
