"""ZCode integration: config + install + uninstall, together."""
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
    """Return a ZCode MCP entry nested under mcp.servers.<name>."""
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
    """Install ZCode MCP and top-level skill, command, and agent assets."""
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
    """Remove ZCode files and registrations for the selected scope."""
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
