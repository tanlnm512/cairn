"""omp (oh-my-pi CLI, https://omp.sh) integration: config + install + uninstall.

omp discovers MCP servers from ``.omp/mcp.json`` (project) or
``~/.omp/agent/mcp.json`` (user), schema-compatible with the shared
``mcpServers.cairn`` shape cairn already writes for claude/cursor/droid
(stdio: command+args; sse: type+url) -- see
https://omp.sh/docs/mcp and mcp-config.md / mcp-protocol-transports.md in the
oh-my-pi source (omp.sh itself blocks non-browser fetches, so the source
docs are the primary reference). Reuses the default ``mcpServers`` merge/strip
helpers unchanged.

Subagents are native task-agent files -- one Markdown file per agent under
``.omp/agents/<name>.md`` (project) or ``~/.omp/agent/agents/<name>.md``
(user); frontmatter is `name`/`description` (required) + `tools` (CSV). omp's
discovery explicitly skips `.claude/agents`, `.codex/agents`, `.gemini/agents`
(different frontmatter contract), so this writes the ``.omp`` native format
directly rather than relying on the cross-tool ``.agents/`` fallback -- see
https://omp.sh/docs/subagents and docs/task-agent-discovery.md upstream.
"""
from __future__ import annotations

from pathlib import Path

from .._common import InstallResult, _omp_agent_md, mcp_config_json
from ..merge import _merge_json_file, _rm_if_ours, _strip_mcp, _write_file


def _omp_mcp_path(workspace: str, scope: str = "workspace") -> Path:
    """.omp/mcp.json location for the install scope.

    ``scope="workspace"`` (default) targets ``<workspace>/.omp/mcp.json``;
    ``scope="global"`` targets ``~/.omp/agent/mcp.json`` -- omp's documented
    user-level MCP config path.
    """
    if scope == "global":
        return Path.home() / ".omp" / "agent" / "mcp.json"
    return Path(workspace) / ".omp" / "mcp.json"


def _omp_agents_dir(workspace: str, scope: str = "workspace") -> Path:
    """.omp/agents/ location for the install scope.

    ``scope="workspace"`` targets ``<workspace>/.omp/agents``; ``scope="global"``
    targets ``~/.omp/agent/agents`` -- omp's documented user-level agent dir.
    """
    if scope == "global":
        return Path.home() / ".omp" / "agent" / "agents"
    return Path(workspace) / ".omp" / "agents"


def install_omp(workspace: str, force: bool = False, dry_run: bool = False,
                transport: str = "stdio", sse_url: str | None = None,
                scope: str = "workspace") -> InstallResult:
    """Wire cairn into omp (.omp/mcp.json + .omp/agents/*.md)."""
    res = InstallResult("omp")

    mcp_path = _omp_mcp_path(workspace, scope)
    _merge_json_file(mcp_path, mcp_config_json(transport, sse_url), force, res, dry_run=dry_run)
    res.notes.append(f"MCP server written to {mcp_path} under the `mcpServers` key.")

    agents_dir = _omp_agents_dir(workspace, scope)
    _write_file(agents_dir / "cairn-explorer.md", _omp_agent_md(), force, res, dry_run=dry_run)
    _write_file(agents_dir / "knowledge-steward.md",
                _omp_agent_md("cursor/knowledge-steward.json"), force, res, dry_run=dry_run)
    res.notes.append("Skill (golden rules + tool-behaviors) may reach omp via the .agents/skills/ fallback "
                     "(omp discovers `.agent[s]/skills`).")
    return res


def uninstall(ws: Path, res: InstallResult, scope: str = "workspace") -> None:
    """Remove cairn entries/files for omp.

    ``scope="workspace"`` (default) strips ``<ws>/.omp/``; ``scope="global"``
    strips ``~/.omp/agent/``; ``scope="all"`` does both.
    """
    scopes = ["workspace", "global"] if scope == "all" else [scope]
    for s in scopes:
        _strip_mcp(_omp_mcp_path(str(ws), s), res)
        agents_dir = _omp_agents_dir(str(ws), s)
        _rm_if_ours(agents_dir / "cairn-explorer.md", _omp_agent_md(), res)
        _rm_if_ours(agents_dir / "knowledge-steward.md",
                    _omp_agent_md("cursor/knowledge-steward.json"), res)
