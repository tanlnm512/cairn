"""Taint warning surfaces over the change-intersect fixture: `cairn blast`
and the explore tool.

Each test copies the fixture workspace from specs/taint-tracking/fixtures
into tmp_path, builds the graph into a tmp DB, and scopes every CAIRN_*
variable to the sandbox, so no store outside tmp_path is read or written
(CONSTITUTION C-04; cairn.cli / cairn.mcp_server are imported lazily inside
the tests). The blast tests commit the copy as a real git repo: blast seeds
its changed symbols from the working-tree diff against HEAD.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.graph.builder import build_graph

FIXTURES_ROOT = (
    Path(__file__).resolve().parents[1] / "specs" / "taint-tracking" / "fixtures"
)

FOOTNOTE = "degraded: rung 3 (server_down): check the embedding server"

# The sql-flow chain inside change-intersect, hop by hop with labels.
PATH_HOPS = (
    ":handle_request [exact]",
    ":validate_input [exact]",
    ":run_query [exact]",
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _fixture_repo(tmp_path: Path) -> Path:
    """change-intersect copy committed at HEAD, so a working-tree edit diffs."""
    ws = tmp_path / "change-intersect"
    shutil.copytree(FIXTURES_ROOT / "change-intersect", ws)
    _git(ws, "init")
    _git(ws, "config", "user.name", "Test")
    _git(ws, "config", "user.email", "test@example.invalid")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-m", "baseline")
    return ws


def _build_db(ws: Path, tmp_path: Path) -> Path:
    db = tmp_path / "graph.kg"
    build_graph(workspace=str(ws), db_path=str(db))
    return db


def _touch(repo: Path, old: str, new: str) -> None:
    app = repo / "app.py"
    text = app.read_text()
    assert old in text
    app.write_text(text.replace(old, new))


def _invoke_blast(ws: Path, db: Path):
    from click.testing import CliRunner

    from cairn.cli.blast import blast

    return CliRunner().invoke(
        blast,
        ["--db", str(db)],
        env={"CAIRN_WORKSPACE": str(ws)},
    )


@pytest.fixture
def explore_env(tmp_path, monkeypatch) -> Path:
    """change-intersect graph reachable through the server's env seams."""
    ws = tmp_path / "change-intersect"
    shutil.copytree(FIXTURES_ROOT / "change-intersect", ws)
    (ws / ".git").mkdir(exist_ok=True)
    db = tmp_path / "graph.kg"
    build_graph(workspace=str(ws), db_path=str(db))
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    monkeypatch.setenv("CAIRN_DB", str(db))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(knowledge))
    monkeypatch.setenv("CAIRN_WORKSPACE", str(ws))
    return ws


@pytest.fixture(autouse=True)
def _fresh_ladder():
    """Each test starts from a never-evaluated (healthy) ladder cache."""
    from cairn.graph import embed_ladder

    embed_ladder.reset_cache()
    yield
    embed_ladder.reset_cache()


# ---------------------------------------------------------------------------
# TC-007 shape: blast warns when a changed symbol intersects a taint path
# ---------------------------------------------------------------------------


def test_blast_warns_when_changed_symbol_sits_on_a_taint_path(tmp_path):
    ws = _fixture_repo(tmp_path)
    db = _build_db(ws, tmp_path)
    _touch(
        ws,
        "    return cursor.execute(query)",
        "    return cursor.execute(query, log=True)",
    )
    result = _invoke_blast(ws, db)
    assert result.exit_code == 0
    assert "Taint path: " in result.output
    for hop in PATH_HOPS:
        assert hop in result.output


def test_blast_stays_silent_for_off_path_symbol(tmp_path):
    ws = _fixture_repo(tmp_path)
    db = _build_db(ws, tmp_path)
    _touch(ws, 'return "[" + label + "]"', 'return "[" + str(label) + "]"')
    result = _invoke_blast(ws, db)
    assert result.exit_code == 0
    assert "format_label" in result.output
    assert "taint" not in result.output.lower()


# ---------------------------------------------------------------------------
# TC-008 shape: explore warns when a queried symbol intersects a taint path
# ---------------------------------------------------------------------------


def test_explore_warns_when_queried_symbol_ends_a_taint_path(explore_env):
    from cairn.mcp_server import tools_graph

    out = tools_graph.explore("run_query")
    assert "=== Taint paths ===" in out
    assert "Taint path: " in out
    for hop in PATH_HOPS:
        assert hop in out


def test_explore_stays_silent_for_off_path_symbol(explore_env):
    from cairn.mcp_server import tools_graph

    out = tools_graph.explore("format_label")
    assert "format_label" in out
    assert "taint" not in out.lower()


def test_footnote_stays_last_line_over_the_taint_warning(explore_env):
    from cairn.graph import embed_ladder
    from cairn.mcp_server import tools_graph

    embed_ladder._LADDER_CACHE["state"] = embed_ladder.LadderState(
        rung=3,
        reason="server_down",
        detail="check the embedding server",
        adopted_model=None,
        active=True,
    )
    out = tools_graph.explore("run_query")
    assert "Taint path: " in out
    assert out.count("degraded: rung 3 (server_down)") == 1
    assert out.splitlines()[-1] == FOOTNOTE


# ---------------------------------------------------------------------------
# The warning rides existing tools: the count pin follows the server constant
# ---------------------------------------------------------------------------


def test_mcp_tool_count_stays_25():
    from cairn.mcp_server.server import _EXPECTED_TOOL_COUNT, verify_tool_count

    assert _EXPECTED_TOOL_COUNT == 25
    verify_tool_count()
