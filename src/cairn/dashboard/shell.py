"""Server-rendered context for the dashboard shell chrome (topbar, nav).

Everything every page shares lives here as pure functions over plain
rows — no starlette imports, so importing this module never loads the
server stack (the same guard the dashboard package is held to, pinned by
test). :func:`selector_context` turns :func:`enumerate_stores
<cairn.dashboard.workspaces.enumerate_stores>` rows (stat-only, never
probed) into the topbar workspace selector's options; the label policy —
basename of the registered workspace path, key fallback for orphan
stores — is one function with one table-driven test.
"""
from __future__ import annotations

from typing import List, Optional

# The label shown for the no-selection option: the launch store the CLI
# resolved (the dashboard process's own db), which the selector can always
# return to.
LAUNCH_LABEL = "Launch workspace"


def workspace_label(path: Optional[str], key: str) -> str:
    """Human-readable selector label for one store row: the basename of
    the registered workspace path, falling back to the 16-hex key for an
    orphan store no registry entry points at."""
    if path:
        name = str(path).rstrip("/").rsplit("/", 1)[-1]
        if name:
            return name
    return key


def selector_context(
    stores: List[dict], store_key: str, launch_label: str = LAUNCH_LABEL
) -> dict:
    """The topbar selector's render context: populated stores only (the
    switch targets the same validated set resolve_selection serves), each
    as ``{key, label, path}`` with the registry path kept for the option's
    title tooltip; ``selected`` is the active key ("" = launch store)."""
    options = [
        {
            "key": row["key"],
            "label": workspace_label(row.get("path"), row["key"]),
            "path": row.get("path") or "",
        }
        for row in stores
        if row.get("state") == "populated"
    ]
    return {"options": options, "selected": store_key, "launch_label": launch_label}
