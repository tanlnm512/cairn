"""opencode integration: config + install + uninstall, together."""
from __future__ import annotations

from pathlib import Path

from .._common import InstallResult, opencode_format_mcp_config_json
from ..merge import _merge_json_file, _strip_mcp_opencode


def _opencode_config_path(workspace: str, scope: str = "workspace") -> Path:
    """Return the opencode.json path for workspace or global scope."""
    if scope == "global":
        return Path.home() / ".config" / "opencode" / "opencode.json"
    return Path(workspace) / "opencode.json"


def opencode_mcp_config_json(transport: str = "stdio", sse_url: str | None = None) -> dict:
    """Return cairn's OpenCode MCP entry with an array command."""
    return opencode_format_mcp_config_json(transport, sse_url)


def install_opencode(workspace: str, force: bool = False, dry_run: bool = False,
                     transport: str = "stdio", sse_url: str | None = None,
                     scope: str = "workspace") -> InstallResult:
    """Install OpenCode config plus shared skills for agent reach."""
    res = InstallResult("opencode")
    # Write the opencode.json the chosen scope reads -- config_key="opencode"
    # routes the merge through the opencode branch of _already_installed /
    # _deep_merge (mcp.<name>, command-as-array).
    p = _opencode_config_path(workspace, scope)
    _merge_json_file(p, opencode_mcp_config_json(transport, sse_url), force, res,
                     config_key="opencode", dry_run=dry_run)
    res.notes.append(f"MCP server written to {p} under the `mcp` key (opencode's schema).")
    res.notes.append("Skill (golden rules + tool-behaviors) reaches opencode via the .agents/skills/ fallback.")
    return res


def uninstall(ws: Path, res: InstallResult, scope: str = "workspace") -> None:
    """Remove cairn from opencode.json and legacy MCP files."""
    scopes = ["workspace", "global"] if scope == "all" else [scope]
    for s in scopes:
        _strip_mcp_opencode(_opencode_config_path(str(ws), s), res)
