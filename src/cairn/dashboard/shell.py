"""Server-rendered context for the dashboard shell chrome (topbar, nav).

Everything every page shares lives here as pure functions over plain
rows — no starlette imports, so importing this module never loads the
server stack (the same guard the dashboard package is held to, pinned by
test). Two owners:

- :func:`shell_context` is the single source of truth for NAVIGATION:
  the sidebar renders from ``nav.sections`` and the command palette's
  view list from ``palette.views`` — both derive from NAV_SECTIONS, so
  the two can never drift apart, and hrefs are composed here with the
  store param riding exactly like the sidebar's old hand-written anchors.
- :func:`selector_context` turns :func:`enumerate_stores
  <cairn.dashboard.workspaces.enumerate_stores>` rows (stat-only, never
  probed) into the topbar workspace selector's options; the label policy
  — basename of the registered workspace path, key fallback for orphan
  stores — is one function with one table-driven test.
"""
from __future__ import annotations

from typing import List, Optional
from urllib.parse import quote

# The label shown for the no-selection option: the launch store the CLI
# resolved (the dashboard process's own db), which the selector can always
# return to.
LAUNCH_LABEL = "Launch workspace"

# Sidebar + palette view grouping. Order within a section is the display
# order; the section list is the sidebar's top-to-bottom order. A None
# label renders a standalone item group with no header — Workspaces leads
# the nav ungrouped (FR-001's overview-first contract) above the scoped
# groups. The Overview landing stays out — it is the brand link, not a
# nav item.
NAV_SECTIONS: tuple = (
    (None, ("workspaces",)),
    ("Explore", ("projects", "graph")),
    ("Knowledge", ("wiki", "memory", "tasks")),
    ("Activity", ("history", "tokens", "chains")),
    ("System", ("health", "embeddings", "settings")),
)

NAV_LABELS: dict = {
    "workspaces": "Workspaces",
    "projects": "Projects",
    "graph": "Graph",
    "history": "History",
    "tokens": "Tokens",
    "chains": "Chains",
    "health": "Health",
    "memory": "Memory",
    "wiki": "Wiki",
    "tasks": "Tasks",
    "embeddings": "Embeddings",
    "settings": "Settings",
}


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
    options = _populated_options(stores)
    return {"options": options, "selected": store_key, "launch_label": launch_label}


def _populated_options(stores: List[dict]) -> List[dict]:
    return [
        {
            "key": row["key"],
            "label": workspace_label(row.get("path"), row["key"]),
            "path": row.get("path") or "",
        }
        for row in stores
        if row.get("state") == "populated"
    ]


def shell_context(
    stores: List[dict], store_key: str, path: str
) -> dict:
    """Everything base.html's chrome renders from, for one request.

    ``nav.sections`` carries the grouped sidebar (each item's ``href``
    already rides the selected store, matching the pre-shell anchors:
    bare hrefs when nothing is selected); ``active`` flags derive from
    the request path exactly as the old hand-written startswith checks
    did. ``palette`` seeds the command palette: the same view list plus
    the populated workspaces (the palette switches stores through the
    same URL-rewrite behavior as the topbar selector).
    """
    nav_query = "?store=" + quote(store_key, safe="") if store_key else ""
    sections: List[dict] = []
    palette_views: List[dict] = []
    for section_label, view_ids in NAV_SECTIONS:
        items: List[dict] = []
        for view_id in view_ids:
            label = NAV_LABELS[view_id]
            href = "/" + view_id + nav_query
            items.append(
                {
                    "id": view_id,
                    "label": label,
                    "href": href,
                    "active": path.startswith("/" + view_id),
                }
            )
            palette_views.append({"label": label, "href": href})
        sections.append({"label": section_label, "items": items})
    return {
        "selector": selector_context(stores, store_key),
        "nav": {"sections": sections},
        "palette": {
            "views": palette_views,
            "workspaces": _populated_options(stores),
        },
    }
