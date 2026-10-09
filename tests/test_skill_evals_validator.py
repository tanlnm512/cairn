"""Tests for scripts/run_skill_evals.py's expected_calls tool validator."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "run_skill_evals.py"

_spec = importlib.util.spec_from_file_location("run_skill_evals", SCRIPT)
rse = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("run_skill_evals", rse)
_spec.loader.exec_module(rse)

_MCP_TOOLS = {"impact_analysis", "rebuild_graph"}
_CLI: dict[str, set[str]] = {}
_SCRIPTS: set[str] = set()


def test_fully_qualified_mcp_tail_must_be_registered():
    ok, reason = rse.resolve_tool(
        "mcp__cairn__rebuild_graph", _MCP_TOOLS, _CLI, _SCRIPTS
    )
    assert ok
    assert "registered" in reason

    ok, reason = rse.resolve_tool(
        "mcp__cairn__rebuild_graphh", _MCP_TOOLS, _CLI, _SCRIPTS
    )
    assert not ok, "typo'd fully-qualified tail must not pass the validator"
    assert "unregistered MCP tool" in reason


def test_unregistered_tail_allowed_only_for_wrong_calls():
    ok, _ = rse.resolve_tool(
        "mcp__cairn__no_such_tool",
        _MCP_TOOLS,
        _CLI,
        _SCRIPTS,
        allow_unregistered_mcp=True,
    )
    assert ok
