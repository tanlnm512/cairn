"""opencode integration: config + install + uninstall, together.

opencode schema (https://opencode.ai/docs/mcp-servers):
- Reads ``opencode.json`` at the project root (and ``~/.config/opencode/``
  globally). It does NOT read a standalone ``.opencode/mcp.json``.
- MCP servers live under a top-level ``"mcp"`` key (NOT ``"mcpServers"``),
  keyed by name: ``{"mcp": {"<name>": {"type": "local", "command": [...]}}}``.
- ``type`` is ``"local"`` (stdio) or ``"remote"`` (sse/http); for local, the full
  invocation is a single ``command`` array.
"""
from __future__ import annotations

from pathlib import Path

from .._common import InstallResult, resolve_cg_command
from ..merge import _merge_json_file, _strip_mcp_opencode


def _opencode_config_path(workspace: str, scope: str = "workspace") -> Path:
    """opencode.json location for the install scope.

    ``scope="workspace"`` (default) targets ``<workspace>/opencode.json`` at
    the project root; ``scope="global"`` targets
    ``~/.config/opencode/opencode.json`` -- the global path opencode reads
    and that ``check_installed`` probes, so a global install lands where it
    is both read and detected.
    """
    if scope == "global":
        return Path.home() / ".config" / "opencode" / "opencode.json"
    return Path(workspace) / "opencode.json"


def opencode_mcp_config_json(transport: str = "stdio", sse_url: str | None = None) -> dict:
    """MCP server config in opencode's format.

    opencode differs from the Claude/Cursor ``mcpServers`` shape in three ways
    (per https://opencode.ai/docs/mcp-servers):

    1. Servers live under a top-level ``"mcp"`` key (not ``"mcpServers"``),
       in ``opencode.json`` at the project root (or ``~/.config/opencode/``
       globally). opencode does NOT read a standalone ``.opencode/mcp.json``.
    2. Each server is keyed by name with ``"type": "local"`` (stdio) or
       ``"type": "remote"`` (sse/http), plus ``"enabled": true``.
    3. For local servers, the full invocation is a single ``"command"`` ARRAY
       (e.g. ``["cairn", "serve"]``) -- not separate ``command``/``args`` fields.

    Args:
        transport: "stdio" (default) or "sse" (shared daemon).
        sse_url: when transport="sse", the URL clients connect to.
    """
    from ...mcp_server import lifecycle as lc

    if transport == "sse":
        url = sse_url or f"http://127.0.0.1:{lc.DEFAULT_PORT}/sse"
        return {"mcp": {"cairn": {"type": "remote", "url": url, "enabled": True}}}
    cmd = resolve_cg_command() + ["serve"]
    return {"mcp": {"cairn": {"type": "local", "command": cmd, "enabled": True}}}


def install_opencode(workspace: str, force: bool = False, dry_run: bool = False,
                     transport: str = "stdio", sse_url: str | None = None,
                     scope: str = "workspace") -> InstallResult:
    """Wire cairn into opencode.

    Reach: opencode discovers skills from ``.agents/skills/`` (written by
    install_cross_tool), so the golden rules + tool-behaviors reach opencode
    agents via that fallback. Slash commands (``.agents/commands/``) and
    subagents are NOT discovered by opencode (it reads ``.opencode/commands/``
    and agents in opencode.json only).

    Args:
        transport: "stdio" (default) or "sse" (shared daemon).
        sse_url: when transport="sse", the URL clients connect to.
        scope: "workspace" (default) writes <workspace>/opencode.json;
            "global" writes ~/.config/opencode/opencode.json (the path
            check_installed probes, so a global install is detected).
    """
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
    """Remove cairn entries from opencode.json and a stray ``.opencode/mcp.json`` if present.

    ``scope="workspace"`` (default, historical) strips ``<ws>/opencode.json``;
    ``scope="global"`` strips ``~/.config/opencode/opencode.json``;
    ``scope="all"`` strips both.
    """
    scopes = ["workspace", "global"] if scope == "all" else [scope]
    for s in scopes:
        _strip_mcp_opencode(_opencode_config_path(str(ws), s), res)
