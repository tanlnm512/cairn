"""agy (Antigravity CLI) integration: config + install + uninstall, together.

agy is MCP-only: it reads a single global ``mcp_config.json`` and has no
skill/command/subagent directories. Uses the shared ``mcpServers``
shape. The location of that file is OS-specific (see :func:`agy_config_path`).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .._common import InstallResult, default_sse_url, resolve_cg_command
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


def agy_mcp_config_json(transport: str = "stdio", sse_url: str | None = None) -> dict:
    """MCP server config in agy (Antigravity) format.

    agy nests servers under ``mcpServers`` like Claude/Cursor, but its
    transport fields differ: remote servers use ``serverUrl`` and there is
    no ``type`` field — the transport is implied by which field is present
    (per https://antigravity.google/docs/mcp/, legacy ``url``/``httpUrl``
    fields are NOT supported). stdio servers use ``command``/``args``
    like the shared shape.
    """
    if transport == "sse":
        return {"mcpServers": {"cairn": {"serverUrl": default_sse_url(sse_url)}}}
    cmd = resolve_cg_command()
    if len(cmd) == 1:
        return {"mcpServers": {"cairn": {"command": cmd[0], "args": ["serve"]}}}
    command, *prefix = cmd
    return {"mcpServers": {"cairn": {"command": command, "args": [*prefix, "serve"]}}}


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
    """Remove cairn entries for agy.

    agy is single-scope (one global config file), so ``scope`` is accepted
    only for signature parity with the other uninstallers and ignored: the
    global mcp_config.json is always stripped.
    """
    _strip_mcp(agy_config_path(), res)
