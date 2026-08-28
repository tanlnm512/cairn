"""ZCode integration: config + install + uninstall, together.

ZCode reads ``.zcode/config.json`` with a nested
``{"mcp": {"servers": {"cairn": {...}}}}`` shape (an explicit ``"type"``
field) — NOT the top-level ``mcpServers`` format used by Claude/Cursor —
for workspace scope; the user (global) scope MCP file is
``~/.zcode/cli/config.json`` (same nested shape). This module owns those
shapes so install and uninstall agree.
"""
from __future__ import annotations

from pathlib import Path

from .._common import (
    InstallResult,
    _TEMPLATE_DIR,
    _SLASH_COMMANDS,
    _agents_instructions,
    _claude_agent_md,
    _claude_command_md,
    _uninstall_bases,
    default_sse_url,
    resolve_cg_command,
)
from ..merge import (
    _merge_json_file,
    _rm_if_ours,
    _rm_tree_if_cairn,
    _strip_mcp_zcode,
    _write_file,
    _write_tree,
)
from ...paths import cairn_home_env


def zcode_mcp_config_json(transport: str = "stdio", sse_url: str | None = None) -> dict:
    """MCP server config in ZCode format (nested mcp.servers.<name> with explicit type).

    ZCode reads <workspace>/.zcode/config.json and expects the nested shape
    ``{"mcp": {"servers": {"name": {...}}}}`` with an explicit ``"type"`` field.
    This differs from the Claude/Cursor/OpenAI format (``{"mcpServers": {...}}``).

    stdio entries embed ``env: {CAIRN_HOME: <expanded path>}`` when the
    resolved CAIRN_HOME is non-default; the default home adds no env key.

    Args:
        transport: "stdio" (default) or "sse" (shared daemon).
        sse_url: when transport="sse", the URL clients should connect to.
    """
    if transport == "sse":
        return {"mcp": {"servers": {"cairn": {"type": "sse", "url": default_sse_url(sse_url)}}}}
    cmd = resolve_cg_command()
    if len(cmd) == 1:
        entry: dict = {"type": "stdio", "command": cmd[0], "args": ["serve"]}
    else:
        command, *prefix = cmd
        entry = {"type": "stdio", "command": command, "args": [*prefix, "serve"]}
    env = cairn_home_env()
    if env:
        entry["env"] = env
    return {"mcp": {"servers": {"cairn": entry}}}


def install_zcode(workspace: str, force: bool, dry_run: bool,
                  transport: str = "stdio", sse_url: str | None = None,
                  scope: str = "workspace") -> InstallResult:
    """Wire cairn into ZCode (.zcode/ config + skill, commands, agents).

    ZCode reads ``<workspace>/.zcode/config.json`` and expects the nested shape
    ``{"mcp": {"servers": {"cairn": {...}}}}`` — NOT the top-level
    ``mcpServers`` format used by Claude/Cursor.

    ``scope="workspace"`` writes to ``<workspace>/.zcode/`` (default).
    ``scope="global"`` writes the MCP entry to ``~/.zcode/cli/config.json`` —
    the ZCode CLI's user-scope MCP file — while skills/commands/agents go to
    ``~/.zcode/`` (the CLI reads those from the top level). A legacy cairn
    entry in ``~/.zcode/config.json`` (written by installers before this fix;
    the CLI does not read that file for MCP) is stripped on install.
    """
    ws = Path(workspace)
    base = ws if scope == "workspace" else Path.home()
    res = InstallResult("zcode")

    # MCP: workspace scope -> <ws>/.zcode/config.json; global scope ->
    # ~/.zcode/cli/config.json (user-scope MCP file the ZCode CLI reads).
    mcp_path = base / ".zcode" / "config.json"
    if scope == "global":
        mcp_path = base / ".zcode" / "cli" / "config.json"
        if not dry_run:
            _strip_mcp_zcode(base / ".zcode" / "config.json", res)
    _merge_json_file(mcp_path, zcode_mcp_config_json(transport, sse_url), force, res, config_key="zcode", dry_run=dry_run)

    _write_tree(base / ".zcode" / "skills" / "cairn", _TEMPLATE_DIR / "skill", force, res, dry_run=dry_run)
    for name in _SLASH_COMMANDS:
        _write_file(base / ".zcode" / "commands" / f"{name}.md",
                    _claude_command_md(name), force, res, dry_run=dry_run)

    # Single shared explorer agent (same definition as Claude Code).
    _write_file(base / ".zcode" / "agents" / "cairn-explorer.md",
                _claude_agent_md(), force, res, dry_run=dry_run)
    _write_file(base / ".zcode" / "agents" / "knowledge-steward.md",
                _claude_agent_md("cursor/knowledge-steward.json"), force, res, dry_run=dry_run)

    # ZCode uses AGENTS.md as its instruction file.
    agents_md = ws / "AGENTS.md"
    if not agents_md.exists():
        _write_file(agents_md, _agents_instructions(transport, sse_url), force=False, result=res, dry_run=dry_run)
    else:
        res.skipped.append(str(agents_md) + " (exists; not overwritten)")

    res.notes.append("ZCode hook schema differs; use `cairn hooks install` for git automation.")
    return res


def uninstall(ws: Path, res: InstallResult, scope: str = "workspace") -> None:
    """Remove cairn files/entries for ZCode.

    ``scope="workspace"`` (the default, historical behavior) strips
    ``<ws>/.zcode/``. ``scope="global"`` strips ``~/.zcode/`` -- where a
    ``--scope global`` install wrote skills, commands, and agents -- plus the
    MCP entry in ``~/.zcode/cli/config.json`` and any legacy entry in
    ``~/.zcode/config.json``. ``scope="all"`` does both. AGENTS.md is never
    removed (install writes it create-if-absent only).

    Commands/agents are only removed when byte-identical to what the
    installer writes, so a user's own file at the same path survives.
    """
    for base in _uninstall_bases(ws, scope):
        _strip_mcp_zcode(base / ".zcode" / "cli" / "config.json", res)
        _strip_mcp_zcode(base / ".zcode" / "config.json", res)
        _rm_tree_if_cairn(base / ".zcode" / "skills" / "cairn", res)
        for n in _SLASH_COMMANDS:
            _rm_if_ours(base / ".zcode" / "commands" / f"{n}.md",
                        _claude_command_md(n), res)
        _rm_if_ours(base / ".zcode" / "agents" / "cairn-explorer.md",
                    _claude_agent_md(), res)
        _rm_if_ours(base / ".zcode" / "agents" / "knowledge-steward.md",
                    _claude_agent_md("cursor/knowledge-steward.json"), res)
