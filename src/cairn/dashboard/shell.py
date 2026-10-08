"""Server-rendered context for the dashboard shell chrome (topbar, nav)."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

# Fallback label for the no-selection option: the launch db is a custom
# path outside the store registry, so no workspace name is derivable.
LAUNCH_LABEL = "Launch workspace"

# Sidebar order follows sections; None renders the ungrouped workspaces entry.
NAV_SECTIONS: tuple = (
    (None, ("workspaces",)),
    ("Explore", ("projects", "graph", "communities")),
    ("Knowledge", ("knowledge", "wiki", "memory", "tasks")),
    ("Activity", ("history", "tokens", "chains")),
    ("System", ("health", "embeddings", "database", "settings")),
)

NAV_LABELS: dict = {
    "workspaces": "Workspaces",
    "projects": "Projects",
    "graph": "Graph",
    "communities": "Communities",
    "history": "History",
    "tokens": "Tokens",
    "chains": "Chains",
    "health": "Health",
    "memory": "Memory",
    "knowledge": "Knowledge",
    "wiki": "Wiki",
    "tasks": "Tasks",
    "embeddings": "Embeddings",
    "database": "Database",
    "settings": "Settings",
}

# Palette-only sub-views share the nav store-param composition.
PALETTE_EXTRA_VIEWS: tuple = (("/knowledge/graph", "Knowledge Graph"),)


def workspace_label(path: Optional[str], key: str) -> str:
    """Human-readable selector label for one store row: the basename of
    the registered workspace path, falling back to the 16-hex key for an
    orphan store no registry entry points at."""
    if path:
        name = str(path).rstrip("/").rsplit("/", 1)[-1]
        if name:
            return name
    return key


def launch_option_label(stores: List[dict], launch_db: Optional[str]) -> str:
    """Name a registered launch store; fall back for an unregistered --db path."""
    if not launch_db:
        return LAUNCH_LABEL
    key = Path(launch_db).parent.name
    for row in stores:
        if row["key"] == key and row.get("state") == "populated":
            return f"{workspace_label(row.get('path'), key)} (launch)"
    return LAUNCH_LABEL


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
    stores: List[dict], store_key: str, path: str, launch_db: Optional[str] = None
) -> dict:
    """Return nav, selector, and palette context for one request."""
    nav_query = "?store=" + quote(store_key, safe="") if store_key else ""
    options = _populated_options(stores)
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
    for extra_href, extra_label in PALETTE_EXTRA_VIEWS:
        palette_views.append({"label": extra_label, "href": extra_href + nav_query})
    return {
        "selector": {
            "options": options,
            "selected": store_key,
            "launch_label": launch_option_label(stores, launch_db),
        },
        "nav": {"sections": sections},
        "palette": {"views": palette_views, "workspaces": options},
    }
