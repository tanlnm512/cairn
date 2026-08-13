"""Claude Code (CLI) integration: config + install + uninstall, together.

Claude Code reads workspace-scoped files: ``.mcp.json`` (MCP), ``.claude/``
(skills, commands, agents), ``.claude/settings.json`` (hooks), and
``CLAUDE.md`` (instructions). This module owns all of those for Claude Code
so the shape that install writes matches the shape uninstall strips.

Scope note: ``scope="global"`` installs the ``.claude/`` tree to ``~/.claude/``
(which Claude Code does read for global skills/commands/agents), but the MCP
server cannot be registered globally by writing ``~/.mcp.json`` — Claude Code
only reads a workspace ``.mcp.json``. The global MCP equivalent is
``claude mcp add --scope user``, so global installs register MCP via that
subprocess when the ``claude`` CLI is present.
"""
from __future__ import annotations

import shutil
import subprocess
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
    resolve_cg_command,
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
    ``~/.claude/`` so all projects inherit cairn's skills/commands/agents
    without per-workspace installation. Global MCP registration uses
    ``claude mcp add --scope user`` (see module docstring).
    """
    ws = Path(workspace)
    res = InstallResult("claude")

    # Base dir: workspace root (default) or home (global scope).
    base = ws if scope == "workspace" else Path.home()

    # --- MCP registration -------------------------------------------------
    # Claude Code only reads a *workspace* `.mcp.json`; a global `~/.mcp.json`
    # is NOT picked up. For workspace scope we write the file; for global scope
    # we register via `claude mcp add --scope user`. The subprocess is
    # best-effort: if the `claude` CLI is absent we record a warning.
    if scope == "workspace":
        _merge_json_file(base / ".mcp.json", mcp_config_json(transport, sse_url), force, res, dry_run=dry_run)
    else:
        if dry_run:
            res.notes.append("[dry-run] Would register MCP globally via `claude mcp add --scope user`.")
        elif shutil.which("claude"):
            cmd = resolve_cg_command()
            try:
                subprocess.run(
                    ["claude", "mcp", "add", "cairn", "--scope", "user", *cmd, "serve"],
                    capture_output=True, timeout=15, check=False,
                )
                res.notes.append("Registered MCP globally via `claude mcp add --scope user`.")
            except (subprocess.SubprocessError, OSError) as e:
                res.notes.append(
                    f"WARNING: `claude mcp add --scope user` failed ({e}); "
                    "MCP not registered globally. Re-run with the claude CLI on PATH "
                    "or install per-workspace (scope=workspace)."
                )
        else:
            res.notes.append(
                "WARNING: global MCP requires the `claude` CLI on PATH "
                "(`claude mcp add --scope user`); it was not found, so MCP was NOT "
                "registered. Claude Code does not read a global ~/.mcp.json. "
                "Re-run with claude on PATH or use scope=workspace."
            )

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
            _write_file(claude_md, _claude_instructions(transport, sse_url), force=False, result=res, dry_run=dry_run)
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
    _rm_if_exists(ws / ".claude" / "agents" / "cairn-agent.md", res)  # legacy filename cleanup
    _strip_hooks(ws / ".claude" / "settings.json", res)
