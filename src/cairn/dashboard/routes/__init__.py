"""Shared context for dashboard route controllers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class DashboardContext:
    """Dependencies and launch configuration shared by route controllers."""

    db_path: str | None
    knowledge_dir: str | None
    render: Callable[..., Any]
    resolve_selection: Callable[..., tuple[str | None, str, str]]
    templates: Any
    is_hx_request: Callable[[Any], bool]
    resolve_window: Callable[[str | None], tuple[str, float | None]]
    server_probe_once: Callable[[], bool | None]
    static_dir: Path
