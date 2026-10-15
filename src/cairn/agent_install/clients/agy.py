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
    if sys.platform == "darwin":
        return Path.home() / ".gemini" / "config" / "mcp_config.json"
    # Linux / other Unix: respect XDG_CONFIG_HOME, default to ~/.config.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "gemini" / "config" / "mcp_config.json"


def agy_mcp_config_json() -> dict:
    """Return an AGY MCP entry (serverUrl surface) pointing at the daemon URL."""
    return {"mcpServers": {"cairn": _url_entry("serverUrl")}}


def install_agy(workspace: str, force: bool, dry_run: bool,
                scope: str = "workspace") -> InstallResult:
    """Wire cairn into agy (global mcp_config.json)."""
    res = InstallResult("agy")
    cfg = agy_config_path()
    _merge_json_file(cfg, agy_mcp_config_json(), force, res, dry_run=dry_run)
    res.notes.append(f"Global MCP config: {cfg}")
    if scope == "workspace":
        res.notes.append(
            "agy reads only a global MCP config; the global path was written "
            "even though workspace scope was requested."
        )
    res.notes.append("Start or restart `agy` CLI to load the server.")
    return res


def uninstall(ws: Path, res: InstallResult, scope: str = "workspace") -> None:
    """Remove cairn from agy's single global MCP config."""
    _strip_mcp(agy_config_path(), res)
