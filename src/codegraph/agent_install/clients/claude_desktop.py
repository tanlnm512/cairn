"""Claude Desktop (GUI app) integration: config + install + uninstall, together.

Claude Desktop is MCP-only (stdio) with a single GLOBAL config file
(``claude_desktop_config.json``), unlike Claude Code (the CLI) which is
workspace-scoped. No skills, slash commands, hooks, or subagents. The
workspace is pinned via ``CODEGRAPH_WORKSPACE`` because the app has no
cwd/workspace notion.
"""
from __future__ import annotations

from pathlib import Path

from .._common import InstallResult, mcp_config_json
from ..detect import claude_desktop_config_path
from ..merge import _merge_json_file, _strip_mcp


def mcp_config_json_desktop(workspace: str, transport: str = "stdio",
                            sse_url: str | None = None) -> dict:
    """MCP config for Claude Desktop.

    IMPORTANT: Claude Desktop (the app) does NOT support SSE/HTTP transports
    in claude_desktop_config.json — only stdio. (Claude Code, the CLI, is a
    separate product that does support SSE via `claude mcp add --transport sse`.)
    This function ALWAYS emits a stdio config regardless of the `transport`
    argument, with the workspace pinned via CODEGRAPH_WORKSPACE (the desktop
    app has no cwd/workspace notion).

    The `transport`/`sse_url` args are accepted for API symmetry with
    mcp_config_json() but ignored here.
    """
    cfg = mcp_config_json(transport="stdio")
    cfg["mcpServers"]["codegraph"]["env"] = {
        "CODEGRAPH_WORKSPACE": str(Path(workspace).resolve())
    }
    return cfg


def install_claude_desktop(workspace: str, force: bool, dry_run: bool,
                           transport: str = "stdio", sse_url: str | None = None,
                           scope: str = "workspace") -> InstallResult:
    """Wire codegraph into Claude Desktop (global claude_desktop_config.json).

    Claude Desktop (the app) supports **stdio MCP servers only** — no SSE/HTTP,
    no skills, slash commands, hooks, or subagents (those are Claude Code
    features). The transport/sse_url args are accepted for symmetry but
    ignored: this always writes a stdio config. See mcp_config_json_desktop().

    Because Claude Desktop's config is a single global file outside the
    workspace, the workspace is pinned via CODEGRAPH_WORKSPACE on the server
    entry.
    """
    res = InstallResult("claude-desktop")
    cfg = claude_desktop_config_path()
    # transport/sse_url intentionally ignored — Claude Desktop is stdio-only.
    _merge_json_file(cfg, mcp_config_json_desktop(workspace), force, res, dry_run=dry_run)
    res.notes.append(f"Global MCP config: {cfg}")
    res.notes.append("Quit and reopen Claude Desktop to load the server.")
    res.notes.append("Claude Desktop is MCP-only (stdio): skills/commands/hooks/SSE are not supported.")
    return res


def uninstall(ws: Path, res: InstallResult) -> None:
    """Remove codegraph entries for Claude Desktop."""
    _strip_mcp(claude_desktop_config_path(), res)
