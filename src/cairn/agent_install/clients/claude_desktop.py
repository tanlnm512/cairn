"""Claude Desktop (GUI app) integration: config + install + uninstall, together."""
from __future__ import annotations

from pathlib import Path

from .._common import InstallResult, mcp_config_json
from ..detect import claude_desktop_config_path
from ..merge import _merge_json_file, _strip_mcp
from ...paths import cairn_home_env


def mcp_config_json_desktop(workspace: str, transport: str = "stdio",
                            sse_url: str | None = None) -> dict:
    """Return Claude Desktop's stdio-only MCP config with workspace pinned."""
    cfg = mcp_config_json(transport="stdio")
    env = {"CAIRN_WORKSPACE": str(Path(workspace).resolve())}
    env.update(cairn_home_env())
    cfg["mcpServers"]["cairn"]["env"] = env
    return cfg


def install_claude_desktop(workspace: str, force: bool, dry_run: bool,
                           transport: str = "stdio", sse_url: str | None = None,
                           scope: str = "workspace") -> InstallResult:
    """Install Claude Desktop's global stdio MCP registration."""
    res = InstallResult("claude-desktop")
    cfg = claude_desktop_config_path()
    # transport/sse_url intentionally ignored — Claude Desktop is stdio-only.
    _merge_json_file(cfg, mcp_config_json_desktop(workspace), force, res, dry_run=dry_run)
    res.notes.append(f"Global MCP config: {cfg}")
    res.notes.append("Quit and reopen Claude Desktop to load the server.")
    res.notes.append("Claude Desktop is MCP-only (stdio): skills/commands/hooks/SSE are not supported.")
    return res


def uninstall(ws: Path, res: InstallResult, scope: str = "workspace") -> None:
    """Remove cairn from Claude Desktop's single global config."""
    _strip_mcp(claude_desktop_config_path(), res)
