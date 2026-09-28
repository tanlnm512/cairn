"""Annotation-coverage tests for the cairn MCP tool surface.

Every ``@mcp.tool()`` registration advertises MCP ``ToolAnnotations`` so a
client (Cursor, Claude Desktop, ...) can render read/write/destructive badges
and decide whether to ask for confirmation before invoking. These tests guard
against regressions where a tool is added or re-touched and silently drops the
``annotations=`` kwarg, or where a read-only graph tool is mislabeled as
mutating. Assertions run against the live FastMCP registrations."""
from __future__ import annotations

from cairn.mcp_server.server import verify_tool_count

# The graph-query tools that must be advertised read-only: they only ever
# read the SQLite index.
_GRAPH_READ_ONLY_TOOLS = {
    "find_definition",
    "get_callers",
    "get_callees",
    "impact_analysis",
    "explore",
    "semantic_search",
    "search_symbols",
    "cross_repo_deps",
    "visualize_graph",
}


def _tools_by_name():
    from cairn.mcp_server._server_core import mcp

    return {t.name: t for t in mcp._tool_manager.list_tools()}


def test_every_tool_advertises_annotations():
    """Every registered tool carries ToolAnnotations; verify_tool_count also
    pins the full surface against drift."""
    verify_tool_count()
    missing = sorted(
        name for name, tool in _tools_by_name().items()
        if tool.annotations is None
    )
    assert not missing, f"tools missing annotations=: {missing}"


def test_read_only_graph_tools_advertise_read_only():
    """The read-only graph-query tools must set ``readOnlyHint=True`` (and
    ``destructiveHint=False``): mislabeling one as mutating would make a
    client prompt for confirmation before a harmless query."""
    tools = _tools_by_name()
    problems: list[str] = []
    for name in sorted(_GRAPH_READ_ONLY_TOOLS):
        annotations = tools[name].annotations
        if annotations is None:
            problems.append(f"{name}: no annotations")
            continue
        if not annotations.readOnlyHint:
            problems.append(
                f"{name}: readOnlyHint is {annotations.readOnlyHint}, expected True"
            )
        if annotations.destructiveHint:
            problems.append(
                f"{name}: destructiveHint is {annotations.destructiveHint}, "
                f"expected False"
            )
    assert not problems, (
        "read-only graph tools mislabeled in their ToolAnnotations:\n  "
        + "\n  ".join(problems)
    )
