"""MCP server: exposes the cairn tool surface to AI agents.

Public API:

    from cairn.mcp_server import run

The 26 tools live in split modules (tools_graph, tools_memory, tools_knowledge,
tools_compass) and register on the shared FastMCP instance from _server_core
when server.py imports them at boot.
"""
from cairn.mcp_server.server import run

__all__ = ["run"]
