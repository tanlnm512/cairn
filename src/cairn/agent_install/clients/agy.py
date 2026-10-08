"""agy (Antigravity CLI) integration: config + install + uninstall, together."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .._common import InstallResult, default_sse_url, resolve_cg_command
from ..merge import _merge_json_file, _strip_mcp
from ...paths import cairn_home_env


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


def agy_mcp_config_json(transport: str = "stdio", sse_url: str | None = None) -> dict:
    """Return an AGY MCP entry using serverUrl for remote transports."""
    if transport == "sse":
        return {"mcpServers": {"cairn": {"serverUrl": default_sse_url(sse_url)}}}
    cmd = resolve_cg_command()
    if len(cmd) == 1:
        entry: dict = {"command": cmd[0], "args": ["serve"]}
    else:
        command, *prefix = cmd
        entry = {"command": command, "args": [*prefix, "serve"]}
    env = cairn_home_env()
    if env:
        entry["env"] = env
    return {"mcpServers": {"cairn": entry}}


def install_agy(workspace: str, force: bool, dry_run: bool,
                transport: str = "stdio", sse_url: str | None = None,
                scope: str = "workspace") -> InstallResult:
    """Wire cairn into agy (global mcp_config.json)."""
    res = InstallResult("agy")
    cfg = agy_config_path()
    _merge_json_file(cfg, agy_mcp_config_json(transport, sse_url), force, res, dry_run=dry_run)
    res.notes.append(f"Global MCP config: {cfg}")
    res.notes.append("Start or restart `agy` CLI to load the server.")
    return res


def uninstall(ws: Path, res: InstallResult, scope: str = "workspace") -> None:
    """Remove cairn from agy's single global MCP config."""
    _strip_mcp(agy_config_path(), res)
