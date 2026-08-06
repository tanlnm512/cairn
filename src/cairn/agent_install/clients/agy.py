"""agy (Antigravity CLI) integration: config + install + uninstall, together.

agy is MCP-only: it reads a single global ``mcp_config.json`` and has no
skill/command/subagent directories. Uses the shared ``mcpServers``
shape. The location of that file is OS-specific (see :func:`agy_config_path`).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .._common import InstallResult, mcp_config_json
from ..merge import _merge_json_file, _strip_mcp


def agy_config_path() -> Path:
    """Location of agy's global MCP config file, per-OS.

    agy (Antigravity CLI) reads a single global ``config/mcp_config.json``:

      macOS:   ~/.gemini/config/mcp_config.json
      Windows: %APPDATA%/gemini/config/mcp_config.json
      Linux:   $XDG_CONFIG_HOME/gemini/config/mcp_config.json
               (falling back to ~/.config/gemini/config/mcp_config.json)
    """
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home())))
        return base / "gemini" / "config" / "mcp_config.json"
    if sys.platform == "darwin":
        return Path.home() / ".gemini" / "config" / "mcp_config.json"
    # Linux / other Unix: respect XDG_CONFIG_HOME, default to ~/.config.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "gemini" / "config" / "mcp_config.json"


def install_agy(workspace: str, force: bool, dry_run: bool,
                transport: str = "stdio", sse_url: str | None = None,
                scope: str = "workspace") -> InstallResult:
    """Wire cairn into agy (global mcp_config.json)."""
    res = InstallResult("agy")
    cfg = agy_config_path()
    _merge_json_file(cfg, mcp_config_json(transport, sse_url), force, res, dry_run=dry_run)
    res.notes.append(f"Global MCP config: {cfg}")
    res.notes.append("Start or restart `agy` CLI to load the server.")
    return res


def uninstall(ws: Path, res: InstallResult) -> None:
    """Remove cairn entries for agy."""
    _strip_mcp(agy_config_path(), res)
