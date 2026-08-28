"""kilo (Kilo Code CLI) integration: config + install + uninstall, together.

kilo CLI schema (https://kilo.ai/docs/automate/mcp/using-in-cli) — the
opencode config format: ``kilo.json`` at the project root (or
``~/.config/kilo/kilo.json`` globally), MCP servers under a top-level
``"mcp"`` key keyed by name: ``{"mcp": {"<name>": {"type": "local",
"command": [...]} or {"type": "remote", "url": ...}}}``. The CLI also
accepts ``kilo.jsonc``/``config.json`` globally and ``.kilo/kilo.json``
per-project; we write the recommended names only.
"""
from __future__ import annotations

from pathlib import Path

from .._common import InstallResult, default_sse_url, resolve_cg_command
from ..merge import _merge_json_file, _strip_mcp_kilo
from ...paths import cairn_home_env


def kilo_mcp_config_json(transport: str = "stdio", sse_url: str | None = None) -> dict:
    """MCP server config in kilo's opencode-format schema.

    Args:
        transport: "stdio" (default) or "sse" (shared daemon).
        sse_url: when transport="sse", the URL clients should connect to.

    stdio entries embed ``env: {CAIRN_HOME: <expanded path>}`` when the
    resolved CAIRN_HOME is non-default; the default home adds no env key.
    """
    if transport == "sse":
        return {"mcp": {"cairn": {"type": "remote", "url": default_sse_url(sse_url), "enabled": True}}}
    entry: dict = {"type": "local", "command": resolve_cg_command() + ["serve"], "enabled": True}
    env = cairn_home_env()
    if env:
        entry["env"] = env
    return {"mcp": {"cairn": entry}}


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
