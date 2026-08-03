"""agy (Antigravity CLI) integration: config + install + uninstall, together.

agy is MCP-only: it reads a single global ``~/.gemini/config/mcp_config.json``
and has no skill/command/subagent directories. Uses the shared ``mcpServers``
shape.
"""
from __future__ import annotations

from pathlib import Path

from .._common import InstallResult, mcp_config_json
from ..merge import _merge_json_file, _strip_mcp


def install_agy(workspace: str, force: bool, dry_run: bool,
                transport: str = "stdio", sse_url: str | None = None,
                scope: str = "workspace") -> InstallResult:
    """Wire codegraph into agy (global mcp_config.json)."""
    res = InstallResult("agy")
    cfg = Path.home() / ".gemini" / "config" / "mcp_config.json"
    _merge_json_file(cfg, mcp_config_json(transport, sse_url), force, res, dry_run=dry_run)
    res.notes.append(f"Global MCP config: {cfg}")
    res.notes.append("Start or restart `agy` CLI to load the server.")
    return res


def uninstall(ws: Path, res: InstallResult) -> None:
    """Remove codegraph entries for agy."""
    _strip_mcp(Path.home() / ".gemini" / "config" / "mcp_config.json", res)
