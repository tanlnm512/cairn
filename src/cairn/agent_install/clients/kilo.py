"""kilo (Kilo Code CLI) integration: config + install + uninstall, together."""
from __future__ import annotations

from pathlib import Path

from .._common import InstallResult, opencode_format_mcp_config_json
from ..merge import _merge_json_file, _strip_mcp_kilo


def kilo_mcp_config_json(transport: str = "stdio", sse_url: str | None = None) -> dict:
    """MCP server config in kilo's opencode-format schema (shared builder)."""
    return opencode_format_mcp_config_json(transport, sse_url)


def _kilo_config_path(workspace: str, scope: str = "workspace") -> Path:
    """kilo.json location for the install scope.

    ``scope="workspace"`` (default) targets ``<workspace>/kilo.json`` at the
    project root; ``scope="global"`` targets ``~/.config/kilo/kilo.json``
    — kilo's recommended global config path, which check_installed probes
    so a global install is detected.
    """
    if scope == "global":
        return Path.home() / ".config" / "kilo" / "kilo.json"
    return Path(workspace) / "kilo.json"


def install_kilo(workspace: str, force: bool = False, dry_run: bool = False,
                 transport: str = "stdio", sse_url: str | None = None,
                 scope: str = "workspace") -> InstallResult:
    """Wire cairn into the kilo CLI.

    Reach: like opencode, MCP is wired via the config file; skills reach
    kilo agents via the ``.agents/skills/`` fallback written by
    install_cross_tool (kilo's config format is opencode-derived).
    """
    res = InstallResult("kilo")
    # config_key="kilo" routes the merge through the opencode-format branch
    # of _already_installed / _deep_merge (mcp.<name>, command-as-array).
    p = _kilo_config_path(workspace, scope)
    _merge_json_file(p, kilo_mcp_config_json(transport, sse_url), force, res,
                     config_key="kilo", dry_run=dry_run)
    res.notes.append(f"MCP server written to {p} under the `mcp` key (kilo's schema).")
    res.notes.append("Skill (golden rules + tool-behaviors) may reach kilo via the .agents/skills/ fallback.")
    return res


def uninstall(ws: Path, res: InstallResult, scope: str = "workspace") -> None:
    """Remove cairn entries from kilo.json files.

    ``scope="workspace"`` (default, historical) strips ``<ws>/kilo.json``;
    ``scope="global"`` strips ``~/.config/kilo/kilo.json``; ``scope="all"``
    strips both.
    """
    scopes = ["workspace", "global"] if scope == "all" else [scope]
    for s in scopes:
        _strip_mcp_kilo(_kilo_config_path(str(ws), s), res)
