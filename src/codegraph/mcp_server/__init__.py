"""MCP server: exposes the codegraph tool surface to AI agents.

Public API:

    from codegraph.mcp_server import run

The 26 tools live in split modules (tools_graph, tools_memory, tools_knowledge,
tools_compass) and register on the shared FastMCP instance from _server_core
when server.py imports them at boot.
"""
from codegraph.mcp_server.server import run

__all__ = ["run"]
