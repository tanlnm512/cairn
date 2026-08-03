"""Claude Code (CLI) integration: config + install + uninstall, together.

Claude Code reads workspace-scoped files: ``.mcp.json`` (MCP), ``.claude/``
(skills, commands, agents), ``.claude/settings.json`` (hooks), and
``CLAUDE.md`` (instructions). This module owns all of those for Claude Code
so the shape that install writes matches the shape uninstall strips.
"""
from __future__ import annotations

from pathlib import Path

from .._common import (
    InstallResult,
    _TEMPLATE_DIR,
    _SLASH_COMMANDS,
    _claude_agent_md,
    _claude_command_md,
    _claude_hook_command,
    _claude_instructions,
    mcp_config_json,
)
from ..merge import (
    _merge_json_file,
    _rm_if_exists,
    _rm_tree_if_cairn,
    _strip_hooks,
    _write_file,
    _write_tree,
)


def claude_hooks_block() -> dict:
    """Claude Code hooks block (flat hooks.<Event> shape, merged into settings)."""
    return {
        "PostToolUse": [
            {
                "matcher": "Edit|Write|MultiEdit",
                "hooks": [
                    {"type": "command", "command": _claude_hook_command("post_edit")}
                ],
            }
        ],
        "Stop": [
            {
                "hooks": [
                    {"type": "command", "command": _claude_hook_command("session_end")}
                ]
            }
        ],
    }


def install_claude(workspace: str, force: bool, dry_run: bool,
                   transport: str = "stdio", sse_url: str | None = None,
                   scope: str = "workspace") -> InstallResult:
    """Wire cairn into Claude Code (.mcp.json, .claude/, CLAUDE.md).

    ``scope="workspace"`` writes to ``<workspace>/.claude/`` and
    ``<workspace>/.mcp.json`` (default). ``scope="global"`` writes to
    ``~/.claude/`` and ``~/.mcp.json`` so all projects inherit cairn
    without per-workspace installation.
    """
    ws = Path(workspace)
    res = InstallResult("claude")

    # Base dir: workspace root (default) or home (global scope).
    base = ws if scope == "workspace" else Path.home()

    # MCP config at base root (shared with zcode).
    _merge_json_file(base / ".mcp.json", mcp_config_json(transport, sse_url), force, res, dry_run=dry_run)

    # Skill (whole package: SKILL.md + references/ + scripts/ + evals/).
    _write_tree(base / ".claude" / "skills" / "cairn", _TEMPLATE_DIR / "skill", force, res, dry_run=dry_run)

    # Slash commands (with frontmatter).
    for name in _SLASH_COMMANDS:
        _write_file(base / ".claude" / "commands" / f"{name}.md",
                    _claude_command_md(name), force, res, dry_run=dry_run)

    # Subagent (translated from Cursor JSON to Claude md).
    _write_file(base / ".claude" / "agents" / "cairn-explorer.md",
                _claude_agent_md(), force, res, dry_run=dry_run)
    _write_file(base / ".claude" / "agents" / "knowledge-steward.md",
                _claude_agent_md("cursor/knowledge-steward.json"), force, res, dry_run=dry_run)

    # Hooks (merged into settings.json under "hooks", preserving others).
    _merge_json_file(base / ".claude" / "settings.json", {"hooks": claude_hooks_block()}, force, res, dry_run=dry_run)

    # Instruction file (workspace scope only — CLAUDE.md is per-project).
    if scope == "workspace":
        claude_md = ws / "CLAUDE.md"
        if not claude_md.exists():
            _write_file(claude_md, _claude_instructions(), force=False, result=res, dry_run=dry_run)
        else:
            res.skipped.append(str(claude_md) + " (exists; not overwritten)")

    return res


def uninstall(ws: Path, res: InstallResult) -> None:
    """Remove cairn files/entries for Claude Code."""
    from ..merge import _strip_mcp

    _strip_mcp(ws / ".mcp.json", res)
    _rm_tree_if_cairn(ws / ".claude" / "skills" / "cairn", res)
    for n in _SLASH_COMMANDS:
        _rm_if_exists(ws / ".claude" / "commands" / f"{n}.md", res)
    _rm_if_exists(ws / ".claude" / "agents" / "cairn-explorer.md", res)
    _rm_if_exists(ws / ".claude" / "agents" / "knowledge-steward.md", res)
    _rm_if_exists(ws / ".claude" / "agents" / "cairn-agent.md", res)  # renamed; clean up the old filename
    _strip_hooks(ws / ".claude" / "settings.json", res)
