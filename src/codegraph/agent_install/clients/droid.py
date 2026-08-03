"""Droid/Factory integration: config + install + uninstall, together.

Droid reads the ``.factory/`` tree (skills, commands, droids) and registers
MCP via ``droid mcp add`` when the CLI is present, falling back to a
``.factory/mcp.json`` file otherwise.
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
    mcp_config_json,
    resolve_cg_command,
    _read_template,
)
from ..merge import (
    _merge_json_file,
    _rm_if_exists,
    _rm_tree_if_codegraph,
    _strip_mcp,
    _write_file,
    _write_tree,
)


def install_droid(workspace: str, force: bool, dry_run: bool,
                  transport: str = "stdio", sse_url: str | None = None,
                  scope: str = "workspace") -> InstallResult:
    """Wire codegraph into Droid/Factory (.factory/ tree + droid mcp add)."""
    ws = Path(workspace)
    res = InstallResult("droid")

    _write_tree(ws / ".factory" / "skills" / "codegraph", _TEMPLATE_DIR / "skill", force, res, dry_run=dry_run)
    for name in _SLASH_COMMANDS:
        _write_file(ws / ".factory" / "commands" / f"{name}.md",
                    _read_template(f"commands/{name}.md"), force, res, dry_run=dry_run)
    # Single shared explorer agent (same definition as Claude Code), so every
    # client wires the one codegraph-explorer agent rather than a separate one.
    _write_file(ws / ".factory" / "droids" / "codegraph-explorer.md",
                _claude_agent_md(), force, res, dry_run=dry_run)
    _write_file(ws / ".factory" / "droids" / "knowledge-steward.md",
                _claude_agent_md("cursor/knowledge-steward.json"), force, res, dry_run=dry_run)

    # MCP: prefer `droid mcp add` if the CLI is available; else write a config file.
    if shutil.which("droid") and not dry_run:
        cmd = resolve_cg_command()
        try:
            subprocess.run(
                ["droid", "mcp", "add", "codegraph", *cmd, "serve"],
                capture_output=True, timeout=10, check=False,
            )
            res.notes.append("Registered MCP via `droid mcp add`.")
        except (subprocess.SubprocessError, OSError):
            res.notes.append("`droid mcp add` failed; no config file written for MCP.")
    elif not shutil.which("droid"):
        # No droid CLI: write a .factory/mcp.json so it's present when droid is installed.
        _merge_json_file(ws / ".factory" / "mcp.json", mcp_config_json(transport, sse_url), force, res, dry_run=dry_run)
        res.notes.append("droid CLI not found; wrote .factory/mcp.json (registers on next droid run).")

    res.notes.append("Cron automation (e.g. `droid cron create`) requires the droid CLI on PATH.")
    return res


def uninstall(ws: Path, res: InstallResult) -> None:
    """Remove codegraph files/entries for Droid/Factory."""
    _rm_tree_if_codegraph(ws / ".factory" / "skills" / "codegraph", res)
    for n in _SLASH_COMMANDS:
        _rm_if_exists(ws / ".factory" / "commands" / f"{n}.md", res)
    _rm_if_exists(ws / ".factory" / "droids" / "codegraph-explorer.md", res)
    _rm_if_exists(ws / ".factory" / "droids" / "knowledge-steward.md", res)
    _rm_if_exists(ws / ".factory" / "droids" / "codegraph-agent.md", res)  # renamed; clean up the old filename
    _strip_mcp(ws / ".factory" / "mcp.json", res)
