"""Freshness contracts for graph-query CLI commands and MCP tools."""

from __future__ import annotations

import json
import shutil
import sqlite3
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner


OLD_NAME = "oldName"
NEW_NAME = "refreshedName"
DRIFTED_FILE = "Drifted.kt"
CLI_COMMANDS = ("def", "callers", "callees", "impact", "search", "context")
MCP_TOOLS = (
    "find_definition",
    "get_callers",
    "get_callees",
    "impact_analysis",
    "explore",
    "semantic_search",
    "search_symbols",
    "cross_repo_deps",
)


@pytest.fixture
def freshness(tmp_path, monkeypatch, hash_backend):
    from cairn.graph import embeddings
    from cairn.graph.builder import build_graph
    from cairn.graph.cross_repo import _reset_namespaces_cache
    from cairn.graph.incremental import reindex_paths
    from cairn.graph.schema import get_db

    workspace = tmp_path / "workspace"
    demo = workspace / "demo"
    library = workspace / "library"
    demo.mkdir(parents=True)
    library.mkdir(parents=True)
    (demo / ".git").mkdir()
    (library / ".git").mkdir()
    drifted_file = demo / DRIFTED_FILE
    drifted_file.write_text(
        "package demo\n"
        "\n"
        "import legacy.example.Dependency\n"
        "\n"
        "class Repository {\n"
        "    fun oldName(): Int {\n"
        "        return Dependency().help()\n"
        "    }\n"
        "\n"
        "    fun caller(): Int {\n"
        "        return oldName()\n"
        "    }\n"
        "}\n"
    )
    (demo / "Stable.kt").write_text(
        "package demo\n"
        "\n"
        "class Stable {\n"
        "    fun unchanged(): Int = 1\n"
        "}\n"
    )
    (library / "Dependency.kt").write_text(
        "package legacy.example\n"
        "\n"
        "class Dependency {\n"
        "    fun help(): Int = 1\n"
        "}\n"
    )

    monkeypatch.setenv("CAIRN_WORKSPACE", str(workspace))
    monkeypatch.setenv(
        "CAIRN_REPO_NAMESPACES",
        json.dumps(
            {"legacy.example": "library", "fresh.example": "library"}
        ),
    )
    _reset_namespaces_cache()
    baseline_db = tmp_path / "baseline.db"
    build_graph(workspace=str(workspace), db_path=str(baseline_db), verbose=False)
    conn = get_db(str(baseline_db))
    try:
        embeddings.embed_all(conn)
    finally:
        conn.close()

    drifted_file.write_text(
        "package demo\n"
        "\n"
        "import fresh.example.Dependency\n"
        "\n"
        "class Repository {\n"
        "    fun refreshedName(): Int {\n"
        "        return Dependency().help()\n"
        "    }\n"
        "\n"
        "    fun caller(): Int {\n"
        "        return refreshedName()\n"
        "    }\n"
        "}\n"
    )

    reindexed_paths: list[str] = []

    def recording_reindex(conn, workspace, paths):
        for path in paths:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = workspace / candidate
            reindexed_paths.append(str(candidate.resolve()))
        return reindex_paths(conn, workspace, paths)

    monkeypatch.setattr(
        "cairn.graph.incremental.reindex_paths", recording_reindex
    )
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()

    def activate(name: str) -> Path:
        active_db = tmp_path / f"{name}.db"
        shutil.copyfile(baseline_db, active_db)
        monkeypatch.setenv("CAIRN_DB", str(active_db))
        return active_db

    return SimpleNamespace(
        workspace=workspace,
        drifted_file=drifted_file,
        baseline_db=baseline_db,
        knowledge=knowledge,
        reindexed_paths=reindexed_paths,
        activate=activate,
    )


def _symbol_names(db_path: Path) -> set[str]:
    from cairn.graph.schema import get_db

    conn = get_db(str(db_path))
    try:
        return {
            row["name"]
            for row in conn.execute(
                "SELECT symbols.name FROM symbols "
                "JOIN files ON symbols.file_id = files.id "
                "WHERE files.path = ?",
                (DRIFTED_FILE,),
            )
        }
    finally:
        conn.close()


def _data(result):
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if isinstance(result, dict):
        return result
    return {"text": str(result)}


def _assert_graph_refreshed(db_path: Path) -> None:
    names = _symbol_names(db_path)
    assert NEW_NAME in names
    assert OLD_NAME not in names


def _assert_only_drifted_file_reindexed(freshness) -> None:
    assert freshness.reindexed_paths == [
        str(freshness.drifted_file.resolve())
    ]


def _pending_sync_count(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM pending_sync").fetchone()[0]
    finally:
        conn.close()


def _file_digest(db_path: Path) -> str:
    return sha256(db_path.read_bytes()).hexdigest()


def _assert_stored_answer_with_banner(result, db_path: Path) -> None:
    payload = json.loads(result.stdout)
    assert payload
    assert payload[0]["name"] == OLD_NAME
    assert "Stale graph" in result.stderr
    assert DRIFTED_FILE in result.stderr
    assert _pending_sync_count(db_path) == 0


def test_no_refresh_env_answers_from_storage_with_probe_banner(
    freshness, monkeypatch
):
    from cairn.cli import main

    db_path = freshness.activate("no-refresh-env")
    monkeypatch.setenv("CAIRN_NO_REFRESH", "1")

    result = CliRunner().invoke(
        main,
        ["def", OLD_NAME, "--db", str(db_path), "--json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    _assert_stored_answer_with_banner(result, db_path)
    assert _symbol_names(db_path) == _symbol_names(freshness.baseline_db)
    assert freshness.reindexed_paths == []


def test_no_refresh_flag_answers_from_storage_with_probe_banner(freshness):
    from cairn.cli import main

    db_path = freshness.activate("no-refresh-flag")

    result = CliRunner().invoke(
        main,
        [
            "def",
            OLD_NAME,
            "--db",
            str(db_path),
            "--no-refresh",
            "--json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    _assert_stored_answer_with_banner(result, db_path)
    assert _symbol_names(db_path) == _symbol_names(freshness.baseline_db)
    assert freshness.reindexed_paths == []


def test_read_only_store_answers_with_drift_report_and_no_writes(
    freshness, monkeypatch
):
    import cairn.mcp_server.tools_graph as tools_graph

    db_path = freshness.activate("read-only")
    monkeypatch.setenv("CAIRN_READ_ONLY", "1")
    before = _file_digest(db_path)

    result = tools_graph.find_definition(OLD_NAME)

    assert OLD_NAME in result
    assert NEW_NAME not in result
    assert "Stale graph" in result
    assert DRIFTED_FILE in result
    assert _file_digest(db_path) == before
    assert _pending_sync_count(db_path) == 0
    assert freshness.reindexed_paths == []


def test_clean_tree_is_quiet_and_does_not_reindex(freshness, monkeypatch):
    from cairn.cli import main

    db_path = freshness.activate("clean-tree")
    repaired = CliRunner().invoke(
        main,
        ["def", NEW_NAME, "--db", str(db_path)],
        catch_exceptions=False,
    )
    assert repaired.exit_code == 0
    _assert_only_drifted_file_reindexed(freshness)

    monkeypatch.setenv("CAIRN_NO_REFRESH", "1")
    quiet = CliRunner().invoke(
        main,
        ["def", NEW_NAME, "--db", str(db_path), "--json"],
        catch_exceptions=False,
    )
    payload = json.loads(quiet.stdout)
    assert payload[0]["name"] == NEW_NAME
    assert "Stale graph" not in quiet.stderr
    assert _pending_sync_count(db_path) == 0
    assert freshness.reindexed_paths == [
        str(freshness.drifted_file.resolve())
    ]


@pytest.mark.parametrize("command", CLI_COMMANDS)
def test_cli_query_commands_refresh_a_drifted_file(command, freshness):
    from cairn.cli import main

    db_path = freshness.activate(f"cli-{command}")
    runner = CliRunner()

    if command == "context":
        before = _symbol_names(db_path)
        result = runner.invoke(
            main,
            [
                "context",
                f"demo/{DRIFTED_FILE}",
                "--knowledge",
                str(freshness.knowledge),
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Context for demo/Drifted.kt" in result.stdout
        after = _symbol_names(db_path)
        assert NEW_NAME in after
        assert OLD_NAME not in after
        assert OLD_NAME in before
        assert NEW_NAME not in before
    else:
        result = runner.invoke(
            main,
            [command, NEW_NAME, "--db", str(db_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert result.stdout
        assert OLD_NAME not in result.stdout
        if command == "impact":
            assert "Total impacted: 1" in result.stdout
        elif command not in ("callers", "callees"):
            assert NEW_NAME in result.stdout

        old_result = runner.invoke(
            main,
            [command, OLD_NAME, "--db", str(db_path)],
            catch_exceptions=False,
        )
        assert old_result.exit_code != 0
        assert old_result.stdout == ""

    _assert_graph_refreshed(db_path)
    _assert_only_drifted_file_reindexed(freshness)


@pytest.mark.parametrize("tool_name", MCP_TOOLS)
def test_mcp_graph_tools_refresh_a_drifted_file(tool_name, freshness):
    import cairn.mcp_server.tools_graph as tools_graph

    db_path = freshness.activate(f"mcp-{tool_name}")
    tool = getattr(tools_graph, tool_name)

    if tool_name == "find_definition":
        new_result = _data(tool(name=NEW_NAME))
        assert NEW_NAME in new_result["text"]
    elif tool_name == "get_callers":
        new_result = _data(tool(name=NEW_NAME, structured=True))
        assert new_result["symbol"] == NEW_NAME
        assert new_result["count"] > 0
    elif tool_name == "get_callees":
        new_result = _data(tool(name=NEW_NAME, structured=True))
        assert new_result["symbol"] == NEW_NAME
        assert new_result["count"] > 0
    elif tool_name == "impact_analysis":
        new_result = _data(tool(name=NEW_NAME, structured=True))
        assert new_result["symbol"] == NEW_NAME
        assert new_result["total"] > 0
    elif tool_name == "explore":
        new_result = _data(tool(query=NEW_NAME))
        assert "1 symbol(s) matched." in new_result["text"]
        assert NEW_NAME in new_result["text"]
        assert OLD_NAME not in new_result["text"]
    elif tool_name == "semantic_search":
        new_result = _data(
            tool(query=NEW_NAME, structured=True, rerank=False)
        )
        assert new_result["count"] > 0
    elif tool_name == "search_symbols":
        new_result = _data(tool(pattern=NEW_NAME, structured=True))
        assert new_result["count"] > 0
    else:
        new_result = _data(tool(repo="demo"))
        assert "fresh.example" in new_result["text"]
        assert "legacy.example" not in new_result["text"]

    if tool_name == "find_definition":
        old_result = _data(tool(name=OLD_NAME))
        assert "No definition found" in old_result["text"]
    elif tool_name == "get_callers":
        old_result = _data(tool(name=OLD_NAME, structured=True))
        assert old_result["count"] == 0
    elif tool_name == "get_callees":
        old_result = _data(tool(name=OLD_NAME, structured=True))
        assert old_result["count"] == 0
    elif tool_name == "impact_analysis":
        old_result = _data(tool(name=OLD_NAME, structured=True))
        assert old_result["total"] == 0
    elif tool_name == "explore":
        old_result = _data(tool(query=OLD_NAME))
        assert "No symbols matching" in old_result["text"]
    elif tool_name == "semantic_search":
        old_result = _data(
            tool(query=OLD_NAME, structured=True, rerank=False)
        )
        assert old_result["count"] == 0
    elif tool_name == "search_symbols":
        old_result = _data(tool(pattern=OLD_NAME, structured=True))
        assert old_result["count"] == 0

    _assert_graph_refreshed(db_path)
    _assert_only_drifted_file_reindexed(freshness)


def test_refresh_is_limited_to_the_drifted_file(freshness):
    from cairn.cli import main

    db_path = freshness.activate("bounded-refresh")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        untouched_files = [
            dict(row)
            for row in conn.execute(
                "SELECT id, path, language, hash, line_count, indexed_at, "
                "size, mtime FROM files WHERE path != ?",
                (DRIFTED_FILE,),
            )
        ]
        untouched_symbols = [
            dict(row)
            for row in conn.execute(
                "SELECT symbols.id, symbols.name FROM symbols "
                "JOIN files ON symbols.file_id = files.id "
                "WHERE files.path != ? ORDER BY symbols.id",
                (DRIFTED_FILE,),
            )
        ]
    finally:
        conn.close()

    result = CliRunner().invoke(
        main, ["def", NEW_NAME, "--db", str(db_path)], catch_exceptions=False
    )
    assert result.exit_code == 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        after_files = [
            dict(row)
            for row in conn.execute(
                "SELECT id, path, language, hash, line_count, indexed_at, "
                "size, mtime FROM files WHERE path != ?",
                (DRIFTED_FILE,),
            )
        ]
        after_symbols = [
            dict(row)
            for row in conn.execute(
                "SELECT symbols.id, symbols.name FROM symbols "
                "JOIN files ON symbols.file_id = files.id "
                "WHERE files.path != ? ORDER BY symbols.id",
                (DRIFTED_FILE,),
            )
        ]
    finally:
        conn.close()

    assert after_files == untouched_files
    assert after_symbols == untouched_symbols
    _assert_graph_refreshed(db_path)
    _assert_only_drifted_file_reindexed(freshness)
