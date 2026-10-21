"""agy (Antigravity CLI) integration: config + install + uninstall, together."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .._common import InstallResult, _url_entry
from ..merge import _merge_json_file, _strip_mcp


def agy_config_path() -> Path:
    """Return agy's per-OS global MCP config path."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home())))
        return base / "gemini" / "config" / "mcp_config.json"
    return Path.home() / ".gemini" / "config" / "mcp_config.json"


def agy_workspace_config_path(workspace: str) -> Path:
    """Return agy's workspace MCP config path (.agents/mcp_config.json)."""
    return Path(workspace) / ".agents" / "mcp_config.json"


def agy_mcp_config_json() -> dict:
    """Return an AGY MCP entry (serverUrl surface; no ``type`` field)."""
    return {"mcpServers": {"cairn": _url_entry("serverUrl", include_type=False)}}


def install_agy(workspace: str, force: bool, dry_run: bool,
                scope: str = "workspace") -> InstallResult:
    """Wire cairn into agy (workspace or global mcp_config.json)."""
    res = InstallResult("agy")
    cfg = agy_config_path() if scope == "global" else agy_workspace_config_path(workspace)
    _merge_json_file(cfg, agy_mcp_config_json(), force, res, dry_run=dry_run)
    res.notes.append(f"MCP config: {cfg}")
    res.notes.append("Start or restart `agy` CLI to load the server.")
    return res


def uninstall(ws: Path, res: InstallResult, scope: str = "workspace") -> None:
    """Remove cairn from agy's workspace and global MCP configs."""
    _strip_mcp(agy_workspace_config_path(str(ws)), res)
    _strip_mcp(agy_config_path(), res)
