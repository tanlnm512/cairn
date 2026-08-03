"""Annotation-coverage tests for the codegraph MCP tool surface (Phase 3.1).

Every ``@mcp.tool()`` registration now advertises MCP ``ToolAnnotations`` so a
client (Cursor, Claude Desktop, ...) can render read/write/destructive badges
and decide whether to ask for confirmation before invoking. These tests guard
against regressions where a tool is added or re-touched and silently drops the
``annotations=`` kwarg, or where a read-only graph tool is mislabeled as
mutating.

Source-scraped (``ast`` + regex, no server boot, no model/embedding deps) to
mirror the style of ``tests/test_agent_surface.py``: the tool decorators live in
plain ``tools_*.py`` modules, so we parse the ``@mcp.tool(...)`` call directly
rather than importing the live server. This keeps the tests fast and runnable
in minimal CI.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "src" / "codegraph" / "mcp_server"
TOOL_FILES = sorted(MCP_DIR.glob("tools_*.py"))

# The 10 graph-query tools that must be advertised read-only. visualize_graph is
# filed under L4 historically but is structurally a graph renderer and lives in
# tools_graph.py, so it is covered by the per-file read-only check below rather
# than enumerated here.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Matches the ``@mcp.tool(...)`` decorator line (possibly spanning the call's
# keyword arguments) and captures the tool name from the immediately following
# ``def``. Tolerates intervening decorator lines (``@instrument``) and blank
# lines, matching the scraper conventions used in test_agent_surface.py.
_TOOL_DECORATOR_RE = re.compile(
    r'@mcp\.tool\((?P<kw>[^\n]*)\)\s*\n'
    r'(?:[ \t]*@\w[^\n]*\n|[ \t]*\n)*?'
    r'(?:async\s+)?def (?P<name>\w+)\(',
    re.MULTILINE,
)


def _scrape_tool_decorators() -> dict[str, dict[str, bool]]:
    """Return ``{tool_name: {readOnlyHint, destructiveHint, idempotentHint}}``
    scraped from the ``@mcp.tool(annotations=ToolAnnotations(...))`` decorators.

    The hint values are parsed from the ``ToolAnnotations(...)`` keyword
    arguments with ``ast`` (after slicing the ``annotations=`` argument text out
    of the decorator call). Source-scraped so the test needs no live server /
    mcp import. Tools whose decorator carries no ``annotations=`` are omitted;
    test_tool_every_decorator_has_annotations uses this absence to fail loudly.
    """
    out: dict[str, dict[str, bool]] = {}
    for f in TOOL_FILES:
        for m in _TOOL_DECORATOR_RE.finditer(f.read_text(encoding="utf-8")):
            name = m.group("name")
            kw = m.group("kw")
            hints = _parse_annotations_hints(kw)
            if hints is not None:
                out[name] = hints
    return out


def _parse_annotations_hints(kw_text: str) -> dict[str, bool] | None:
    """Parse the ``readOnlyHint``/``destructiveHint``/``idempotentHint`` booleans
    from a ``@mcp.tool(...)`` call's keyword-argument text.

    ``kw_text`` is the raw text inside the parentheses (e.g.
    ``annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
    idempotentHint=True)``). We locate the ``annotations=ToolAnnotations(...)``
    sub-expression, then ``ast.parse`` it as an expression and walk the keyword
    arguments of the ``ToolAnnotations(...)`` call. Returns ``None`` when no
    ``annotations=`` keyword is present (so a missing annotation is detectable).
    """
    m = re.search(r"annotations\s*=\s*ToolAnnotations\s*\((.*?)\)", kw_text, re.DOTALL)
    if not m:
        return None
    inner = m.group(1)
    try:
        call = ast.parse(f"ToolAnnotations({inner})", mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(call, ast.Call):
        return None
    hints: dict[str, bool] = {}
    for kw in call.keywords:
        if kw.arg in {"readOnlyHint", "destructiveHint", "idempotentHint"}:
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool):
                hints[kw.arg] = kw.value.value
    return hints


# ---------------------------------------------------------------------------
# Test 1: every @mcp.tool() has an annotations= kwarg
# ---------------------------------------------------------------------------

def test_every_decorator_has_annotations_kwarg():
    """Every ``@mcp.tool()`` registration across the four ``tools_*.py`` files
    must carry an ``annotations=ToolAnnotations(...)`` keyword.

    Catches the regression where a tool is added (or a decorator is
    reformatted) and the ``annotations=`` kwarg is dropped -- silently
    re-advertising the tool as an un-annotated default, which defeats the
    Phase 3.1 client-rendering goal. We scrape every ``@mcp.tool(...)``
    decorator and fail loudly naming the offending tools.
    """
    # First, collect every tool name (decorator may or may not have annotations).
    all_tools: dict[str, str] = {}  # name -> file
    for f in TOOL_FILES:
        for m in _TOOL_DECORATOR_RE.finditer(f.read_text(encoding="utf-8")):
            all_tools[m.group("name")] = f.name

    assert all_tools, "no @mcp.tool(...) decorators found in tools_*.py"
    assert len(all_tools) == 26, (
        f"expected 26 @mcp.tool(...) registrations, found {len(all_tools)}: "
        f"{sorted(all_tools)}"
    )

    # Now find which ones lack the annotations= keyword.
    annotated = _scrape_tool_decorators()
    missing = sorted(set(all_tools) - set(annotated))
    assert not missing, (
        "these @mcp.tool(...) decorators are missing the annotations= kwarg: "
        + ", ".join(f"{t} ({all_tools[t]})" for t in missing)
    )


# ---------------------------------------------------------------------------
# Test 2: read-only graph tools advertise readOnlyHint=True
# ---------------------------------------------------------------------------

def test_read_only_graph_tools_advertise_read_only():
    """The nine read-only graph-query tools (plus visualize_graph) must set
    ``readOnlyHint=True`` (and ``destructiveHint=False``) in their annotations.

    Graph tools only ever read the SQLite index; mislabeling one as mutating
    would make a client prompt for confirmation before a harmless query, or
    worse, let an agent skip a verification step. We scrape the live hint
    values from the decorator source rather than importing the server, so this
    runs in minimal CI without the model/embedding stack.
    """
    annotated = _scrape_tool_decorators()
    assert annotated, "no annotations= hints scraped (parser regression?)"

    problems: list[str] = []
    for tool in sorted(_GRAPH_READ_ONLY_TOOLS):
        hints = annotated.get(tool)
        if hints is None:
            problems.append(f"{tool}: no annotations= kwarg found")
            continue
        if not hints.get("readOnlyHint"):
            problems.append(
                f"{tool}: readOnlyHint is {hints.get('readOnlyHint')}, expected True"
            )
        if hints.get("destructiveHint"):
            problems.append(
                f"{tool}: destructiveHint is {hints.get('destructiveHint')}, expected False"
            )

    assert not problems, (
        "read-only graph tools mislabeled in their ToolAnnotations:\n  "
        + "\n  ".join(problems)
    )
