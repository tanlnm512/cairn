"""MCP server: exposes the cairn tool surface to AI agents.

Public API:

    from cairn.mcp_server import run

The 25 tools live in split modules (tools_compass, tools_federation,
tools_graph, tools_knowledge, tools_memory, tools_wiki) and register on the
shared FastMCP instance from _server_core when server.py imports them at boot.
"""
from cairn.mcp_server.server import run

__all__ = ["run"]
