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
    _uninstall_bases,
    default_sse_url,
    mcp_config_json,
    resolve_cg_command,
)
from ..merge import (
    _merge_json_file,
    _rm_if_exists,
    _rm_if_ours,
    _rm_tree_if_cairn,
    _strip_hooks,
    _write_file,
    _write_tree,
)
from ...paths import cairn_home_env


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
            if transport == "sse":
                url = default_sse_url(sse_url)
                argv = ["claude", "mcp", "add", "--transport", "sse",
                        "--scope", "user", "cairn", url]
            else:
                # `claude mcp add <name> [-e KEY=value] -- <command> [args...]`:
                # `--` ends option parsing; everything after it is the server
                # command stored verbatim as the registration argv. `-e`
                # entries persist into the user-scope registration's env
                # block; a non-default home must ride along or the spawned
                # server resolves the default store.
                argv = ["claude", "mcp", "add", "cairn", "--scope", "user"]
                for key, value in cairn_home_env().items():
                    argv += ["-e", f"{key}={value}"]
                argv += ["--", *resolve_cg_command(), "serve"]
            try:
                proc = subprocess.run(argv, capture_output=True, timeout=15, check=False)
            except (subprocess.SubprocessError, OSError) as e:
                res.notes.append(
                    f"WARNING: `claude mcp add --scope user` failed ({e}); "
                    "MCP not registered globally. Re-run with the claude CLI on PATH "
                    "or install per-workspace (scope=workspace)."
                )
            else:
                if proc.returncode == 0:
                    res.notes.append("Registered MCP globally via `claude mcp add --scope user`.")
                else:
                    err = (proc.stderr or proc.stdout or "").strip()
                    res.notes.append(
                        f"WARNING: `claude mcp add --scope user` exited "
                        f"{proc.returncode}: {err[:200]}; MCP not registered "
                        "globally. Re-run with the claude CLI on PATH or install "
                        "per-workspace (scope=workspace)."
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


def _mcp_remove_user_scope(res: InstallResult) -> None:
    """Undo the user-scope MCP registration a global install created.

    Mirrors install's ``claude mcp add --scope user`` subprocess pattern
    (list-args, capture_output, timeout, check=False). Best-effort: a missing
    CLI or a failed call is recorded as a note, never a crash.
    """
    if not shutil.which("claude"):
        res.notes.append(
            "NOTE: could not remove a user-scope MCP registration -- the "
            "`claude` CLI was not found on PATH."
        )
        return
    try:
        subprocess.run(
            ["claude", "mcp", "remove", "cairn", "--scope", "user"],
            capture_output=True, timeout=15, check=False,
        )
        res.notes.append("Removed user-scope MCP registration via `claude mcp remove cairn --scope user`.")
    except (subprocess.SubprocessError, OSError) as e:
        res.notes.append(
            f"WARNING: `claude mcp remove cairn --scope user` failed ({e}); "
            "the registration may remain."
        )


def uninstall(ws: Path, res: InstallResult, scope: str = "workspace") -> None:
    """Remove cairn files/entries for Claude Code.

    ``scope="workspace"`` (the default, and the historical behavior) strips
    only ``<ws>/.mcp.json`` and ``<ws>/.claude/`` entries. ``scope="global"``
    strips the home-dir tree a global install wrote (``~/.claude/`` skills,
    commands, agents, hooks) and removes the user-scope MCP registration via
    ``claude mcp remove --scope user``. ``scope="all"`` does both.

    Commands/agents are only removed when byte-identical to what the
    installer writes -- a user's own file at the same path (which install
    skipped) survives.
    """
    from ..merge import _strip_mcp

    for base in _uninstall_bases(ws, scope):
        _strip_mcp(base / ".mcp.json", res)
        _rm_tree_if_cairn(base / ".claude" / "skills" / "cairn", res)
        for n in _SLASH_COMMANDS:
            _rm_if_ours(base / ".claude" / "commands" / f"{n}.md",
                        _claude_command_md(n), res)
        _rm_if_ours(base / ".claude" / "agents" / "cairn-explorer.md",
                    _claude_agent_md(), res)
        _rm_if_ours(base / ".claude" / "agents" / "knowledge-steward.md",
                    _claude_agent_md("cursor/knowledge-steward.json"), res)
        _rm_if_exists(base / ".claude" / "agents" / "cairn-agent.md", res)  # legacy filename cleanup
        _strip_hooks(base / ".claude" / "settings.json", res)
    if scope in ("global", "all"):
        _mcp_remove_user_scope(res)
