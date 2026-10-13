"""Claude Desktop (GUI app) integration: unsupported-report install plus legacy uninstall."""
from __future__ import annotations

from pathlib import Path

from .._common import InstallResult
from ..detect import claude_desktop_config_path
from ..merge import _strip_mcp


def install_claude_desktop(workspace: str, force: bool, dry_run: bool,
                           scope: str = "workspace") -> InstallResult:
    """Report Claude Desktop as unsupported; write no configuration."""
    res = InstallResult("claude-desktop")
    res.notes.append(
        "Claude Desktop supports no remote/URL MCP registration — "
        "unsupported, skipped (no config written)."
    )
    return res


def uninstall(ws: Path, res: InstallResult, scope: str = "workspace") -> None:
    """Remove cairn from Claude Desktop's single global config."""
    _strip_mcp(claude_desktop_config_path(), res)
