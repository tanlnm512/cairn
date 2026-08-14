"""Cursor integration: config + install + uninstall, together.

Cursor reads ``.cursor/mcp.json`` (MCP), ``.cursor/rules/*.mdc`` (rules),
``.cursor/subagents/*.json`` (subagents), and ``.cursor/hooks.json`` (hooks).
"""
from __future__ import annotations

from pathlib import Path

from .._common import (
    InstallResult,
    _claude_hook_command,
    _read_template,
    _uninstall_bases,
    mcp_config_json,
)
from ..merge import (
    _merge_json_file,
    _rm_if_ours,
    _strip_cursor_hooks,
    _strip_mcp,
    _write_file,
)


def cursor_hooks_json() -> dict:
    """Cursor hooks.json content."""
    return {
        "hooks": {
            "afterFileEdit": [
                {"command": _claude_hook_command("post_edit"), "timeout": 10000}
            ],
            "afterSessionEnd": [
                {"command": _claude_hook_command("session_end"), "timeout": 60000}
            ],
        }
    }


def install_cursor(workspace: str, force: bool, dry_run: bool,
                   transport: str = "stdio", sse_url: str | None = None,
                   scope: str = "workspace") -> InstallResult:
    """Wire cairn into Cursor (.cursor/mcp.json, rules, subagents, hooks).

    ``scope="workspace"`` writes to ``<workspace>/.cursor/`` (default).
    ``scope="global"`` writes to ``~/.cursor/`` so all projects inherit.
    """
    ws = Path(workspace)
    base = ws if scope == "workspace" else Path.home()
    res = InstallResult("cursor")

    _merge_json_file(base / ".cursor" / "mcp.json", mcp_config_json(transport, sse_url), force, res, dry_run=dry_run)
    _write_file(base / ".cursor" / "rules" / "cairn.mdc",
                _read_template("cursor/cairn.mdc"), force, res, dry_run=dry_run)
    _write_file(base / ".cursor" / "subagents" / "cairn-explorer.json",
                _read_template("cursor/cairn-explorer.json"), force, res, dry_run=dry_run)
    _write_file(base / ".cursor" / "subagents" / "knowledge-steward.json",
                _read_template("cursor/knowledge-steward.json"), force, res, dry_run=dry_run)
    _merge_json_file(base / ".cursor" / "hooks.json", cursor_hooks_json(), force, res, dry_run=dry_run)

    res.notes.append("Restart Cursor (or reload the window) to load the new MCP server.")
    return res


def uninstall(ws: Path, res: InstallResult, scope: str = "workspace") -> None:
    """Remove cairn files/entries for Cursor.

    ``scope="workspace"`` (the default, historical behavior) strips
    ``<ws>/.cursor/``. ``scope="global"`` strips ``~/.cursor/`` -- where a
    ``--scope global`` install wrote mcp.json, rules, subagents, and hooks.
    ``scope="all"`` does both.

    Rules/subagents are only removed when byte-identical to what the
    installer writes, so a user's own file at the same path survives.
    """
    for base in _uninstall_bases(ws, scope):
        _strip_mcp(base / ".cursor" / "mcp.json", res)
        _rm_if_ours(base / ".cursor" / "rules" / "cairn.mdc",
                    _read_template("cursor/cairn.mdc"), res)
        _rm_if_ours(base / ".cursor" / "subagents" / "cairn-explorer.json",
                    _read_template("cursor/cairn-explorer.json"), res)
        _rm_if_ours(base / ".cursor" / "subagents" / "knowledge-steward.json",
                    _read_template("cursor/knowledge-steward.json"), res)
        _strip_cursor_hooks(base / ".cursor" / "hooks.json", res)
