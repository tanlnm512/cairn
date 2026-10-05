"""MCP `path` tool contracts over the on-demand-paths fixtures.

Workspace copies live in tmp_path, the graph in a tmp DB, and every CAIRN_*
variable is scoped to the sandbox, so no store outside tmp_path is read or
written (CONSTITUTION C-04; cairn.mcp_server is imported lazily in tests).
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from cairn.graph.builder import build_graph

FIXTURES_ROOT = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "on-demand-paths"
)


def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> Path:
    # Empty dirs do not survive clones, so the .git repo marker is re-created.
    ws = tmp_path / name
    shutil.copytree(FIXTURES_ROOT / name, ws)
    (ws / ".git").mkdir(exist_ok=True)
    db = tmp_path / f"{name}.kg"
    build_graph(workspace=str(ws), db_path=str(db))
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setenv("CAIRN_DB", str(db))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(knowledge))
    monkeypatch.setenv("CAIRN_WORKSPACE", str(ws))
    return db


def _line_of(db: Path, name: str) -> int:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT line_start FROM symbols WHERE name = ?", (name,)
        ).fetchone()["line_start"]
    finally:
        conn.close()


def test_chain_query_renders_the_cli_hop_chain(tmp_path, monkeypatch):
    db = _sandbox(tmp_path, monkeypatch, "chain")
    from cairn.mcp_server import tools_graph

    expected = ["path 1 (chain_a -> chain_d, 4 hops):"] + [
        f"  chain.py:{_line_of(db, name)} {name} [exact]"
        for name in ("chain_a", "chain_b", "chain_c", "chain_d")
    ]
    out = tools_graph.path("chain_a", "chain_d")
    assert out.splitlines() == expected
    # Endpoint patterns match case-insensitively (the header echoes the
    # pattern as typed, so only the hop chain must agree).
    assert tools_graph.path("Chain_A", "Chain_D").splitlines()[1:] == expected[1:]


def test_no_path_reports_the_depth_bound_message(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch, "islands")
    from cairn.mcp_server import tools_graph

    assert tools_graph.path("isl_a1", "isl_b2") == (
        "no path within 4 hops from 'isl_a1' to 'isl_b2'"
    )
    assert tools_graph.path("isl_a1", "isl_b2", max_depth=2) == (
        "no path within 2 hops from 'isl_a1' to 'isl_b2'"
    )


def test_fuzzy_bridges_the_ambiguous_hop(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch, "fuzzy")
    from cairn.mcp_server import tools_graph

    assert tools_graph.path("fz_start", "fz_end") == (
        "no path within 4 hops from 'fz_start' to 'fz_end'"
    )
    out = tools_graph.path("fz_start", "fz_end", fuzzy=True)
    lines = out.splitlines()
    assert lines[0] == "path 1 (fz_start -> fz_end, 3 hops):"
    # fz_shared has two same-repo definitions; the ambiguous hop lands on one.
    assert [line.split()[-2] for line in lines[1:]] == [
        "fz_start",
        "fz_shared",
        "fz_end",
    ]
    assert lines[2].split()[-1] == "[ambiguous]"


def test_unknown_endpoints_stay_clean(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch, "chain")
    from cairn.mcp_server import tools_graph

    assert tools_graph.path("no_such_symbol", "chain_a") == (
        "no symbols match from pattern 'no_such_symbol'"
    )
    assert tools_graph.path("ghost_a", "ghost_b") == (
        "no symbols match from pattern 'ghost_a'\n"
        "no symbols match to pattern 'ghost_b'"
    )


def test_limit_caps_printed_paths(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch, "fanout")
    from cairn.mcp_server import tools_graph

    out = tools_graph.path("fan_4", "fan_sink", limit=2)
    lines = out.splitlines()
    # "fan_4" substring-matches fan_4 and fan_40..fan_49, so the cap bites.
    assert len(lines) == 6
    assert lines[0] == "path 1 (fan_4 -> fan_sink, 2 hops):"
    assert lines[3] == "path 2 (fan_4 -> fan_sink, 2 hops):"
    assert sum(1 for line in lines if line.startswith("path ")) == 2
    assert all(line.endswith("[exact]") for line in lines[1::3])
def test_path_defaults_match_shared_constants():
    """D-014 drift guard: literal signature defaults equal the shared constants."""
    from cairn.graph.taint import CLOSURE_MAX_DEPTH, DEFAULT_PATH_LIMIT

    from cairn.mcp_server.tools_graph import path

    import inspect

    sig = inspect.signature(path)
    assert sig.parameters["max_depth"].default == CLOSURE_MAX_DEPTH == 4
    assert sig.parameters["limit"].default == DEFAULT_PATH_LIMIT == 50
