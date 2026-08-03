"""ZCode integration: config + install + uninstall, together.

ZCode reads ``.zcode/config.json`` with a nested
``{"mcp": {"servers": {"cairn": {...}}}}`` shape (an explicit ``"type"``
field) — NOT the top-level ``mcpServers`` format used by Claude/Cursor. This
module owns that shape so install and uninstall agree.
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
    resolve_cg_command,
)
from ..merge import (
    _merge_json_file,
    _rm_if_exists,
    _rm_tree_if_cairn,
    _strip_mcp_zcode,
    _write_file,
    _write_tree,
)


def zcode_mcp_config_json(transport: str = "stdio", sse_url: str | None = None) -> dict:
    """MCP server config in ZCode format (nested mcp.servers.<name> with explicit type).

    ZCode reads <workspace>/.zcode/config.json and expects the nested shape
    ``{"mcp": {"servers": {"name": {...}}}}`` with an explicit ``"type"`` field.
    This differs from the Claude/Cursor/OpenAI format (``{"mcpServers": {...}}``).

    Args:
        transport: "stdio" (default) or "sse" (shared daemon).
        sse_url: when transport="sse", the URL clients should connect to.
    """
    from ...mcp_server import lifecycle as lc

    if transport == "sse":
        url = sse_url or f"http://127.0.0.1:{lc.DEFAULT_PORT}/sse"
        return {"mcp": {"servers": {"cairn": {"type": "sse", "url": url}}}}
    cmd = resolve_cg_command()
    if len(cmd) == 1:
        return {"mcp": {"servers": {"cairn": {"type": "stdio", "command": cmd[0], "args": ["serve"]}}}}
    command, *prefix = cmd
    return {"mcp": {"servers": {"cairn": {"type": "stdio", "command": command, "args": [*prefix, "serve"]}}}}


def install_zcode(workspace: str, force: bool, dry_run: bool,
                  transport: str = "stdio", sse_url: str | None = None,
                  scope: str = "workspace") -> InstallResult:
    """Wire cairn into ZCode (.zcode/config.json, .zcode/ skill + commands).

    ZCode reads <base>/.zcode/config.json and expects the nested shape
    ``{"mcp": {"servers": {"cairn": {...}}}}`` — NOT the top-level
    ``mcpServers`` format used by Claude/Cursor.

    ``scope="workspace"`` writes to ``<workspace>/.zcode/`` (default).
    ``scope="global"`` writes to ``~/.zcode/``.
    """
    ws = Path(workspace)
    base = ws if scope == "workspace" else Path.home()
    res = InstallResult("zcode")

    # MCP: ZCode reads .zcode/config.json (nested mcp.servers format).
    _merge_json_file(base / ".zcode" / "config.json", zcode_mcp_config_json(transport, sse_url), force, res, config_key="zcode", dry_run=dry_run)

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
        _write_file(agents_md, _agents_instructions(), force=False, result=res, dry_run=dry_run)
    else:
        res.skipped.append(str(agents_md) + " (exists; not overwritten)")

    res.notes.append("ZCode hook schema differs; use `cairn hooks install` for git automation.")
    return res


def uninstall(ws: Path, res: InstallResult) -> None:
    """Remove cairn files/entries for ZCode."""
    _strip_mcp_zcode(ws / ".zcode" / "config.json", res)
    _rm_tree_if_cairn(ws / ".zcode" / "skills" / "cairn", res)
    for n in _SLASH_COMMANDS:
        _rm_if_exists(ws / ".zcode" / "commands" / f"{n}.md", res)
    _rm_if_exists(ws / ".zcode" / "agents" / "cairn-explorer.md", res)
    _rm_if_exists(ws / ".zcode" / "agents" / "knowledge-steward.md", res)
