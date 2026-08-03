"""opencode integration: config + install + uninstall, together.

opencode schema (https://opencode.ai/docs/mcp-servers):
- Reads ``opencode.json`` at the project root (and ``~/.config/opencode/``
  globally). It does NOT read a standalone ``.opencode/mcp.json``.
- MCP servers live under a top-level ``"mcp"`` key (NOT ``"mcpServers"``),
  keyed by name: ``{"mcp": {"<name>": {"type": "local", "command": [...]}}}``.
- ``type`` is ``"local"`` (stdio) or ``"remote"`` (sse/http); for local, the full
  invocation is a single ``command`` array.

Co-locating the generator, installer, and uninstaller here is what prevents
the class of bug where install writes one shape and uninstall expects another.
"""
from __future__ import annotations

from pathlib import Path

from .._common import InstallResult, resolve_cg_command
from ..merge import _merge_json_file, _strip_mcp_opencode


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
    """
    res = InstallResult("opencode")
    # Write to opencode.json at the project root -- the file opencode actually
    # reads. config_key="opencode" routes the merge through the opencode branch
    # of _already_installed / _deep_merge (mcp.<name>, command-as-array).
    p = Path(workspace) / "opencode.json"
    _merge_json_file(p, opencode_mcp_config_json(transport, sse_url), force, res,
                     config_key="opencode", dry_run=dry_run)
    res.notes.append("MCP server written to opencode.json under the `mcp` key (opencode's schema).")
    res.notes.append("Skill (golden rules + tool-behaviors) reaches opencode via the .agents/skills/ fallback.")
    return res


def uninstall(ws: Path, res: InstallResult) -> None:
    """Remove cairn entries from opencode.json and a stray ``.opencode/mcp.json`` if present."""
    _strip_mcp_opencode(ws / "opencode.json", res)
