"""Deterministic repository orientation map contracts."""

from __future__ import annotations

import json
import shutil
import socket
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence

import pytest


_FileSpec = tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]
_GraphSpec = Mapping[str, Sequence[_FileSpec]]

_MULTI_REPO_GRAPH: _GraphSpec = {
    "alpha": (
        (
            "services/core.py",
            ("alpha_hub", "alpha_tie_b", "alpha_tie_a", "alpha_caller"),
            (
                ("alpha_caller", "alpha_hub"),
                ("alpha_caller", "alpha_tie_b"),
                ("alpha_caller", "alpha_tie_a"),
            ),
        ),
        (
            "services/client.py",
            ("client_one", "client_two"),
            (("client_one", "alpha_hub"), ("client_two", "alpha_hub")),
        ),
        (
            "api/routes.py",
            ("route_handler", "route_helper"),
            (("route_handler", "beta_hub"), ("route_helper", "alpha_tie_a")),
        ),
    ),
    "beta": (
        (
            "engine/engine.py",
            ("beta_hub", "beta_caller"),
            (("beta_caller", "beta_hub"),),
        ),
        ("engine/adapter.py", ("adapter",), (("adapter", "beta_hub"),)),
        (
            "ui/screen.py",
            ("screen",),
            (("screen", "alpha_hub"),),
        ),
        (
            "ui/admin.py",
            ("admin",),
            (("admin", "beta_hub"),),
        ),
    ),
}

_SINGLE_REPO_GRAPH: _GraphSpec = {
    "solo": (
        (
            "src/feature/one.py",
            ("feature_hub", "feature_caller"),
            (("feature_caller", "feature_hub"),),
        ),
        ("src/feature/two.py", ("feature_two",), ()),
        ("src/feature/empty.py", (), ()),
        ("src/shared/three.py", ("shared_three",), ()),
        ("api/four.py", ("api_four",), ()),
    ),
}


def _seed_graph(workspace: Path, db_path: Path, graph: _GraphSpec) -> None:
    from cairn.graph.schema import _apply_schema

    workspace.mkdir(parents=True, exist_ok=True)
    single_repo = len(graph) == 1
    if single_repo:
        (workspace / ".git").mkdir()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _apply_schema(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        symbol_ids: dict[str, str] = {}
        for repo_id, files in graph.items():
            repo_root = workspace if single_repo else workspace / repo_id
            if not single_repo:
                (repo_root / ".git").mkdir(parents=True)
            conn.execute(
                "INSERT INTO repos (id, name, path) VALUES (?, ?, ?)",
                (repo_id, repo_id, repo_id),
            )
            for relative_path, symbols, _edges in files:
                source = repo_root / relative_path
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text("# orientation fixture\n", encoding="utf-8")
                stat = source.stat()
                file_id = f"file:{repo_id}:{relative_path}"
                conn.execute(
                    """
                    INSERT INTO files (
                        id, repo_id, path, language, line_count, size, mtime
                    ) VALUES (?, ?, ?, 'python', 1, ?, ?)
                    """,
                    (
                        file_id,
                        repo_id,
                        relative_path,
                        stat.st_size,
                        stat.st_mtime,
                    ),
                )
                for name in symbols:
                    symbol_id = f"symbol:{name}"
                    symbol_ids[name] = symbol_id
                    conn.execute(
                        """
                        INSERT INTO symbols (
                            id, file_id, name, qualified_name, kind,
                            line_start, line_end
                        ) VALUES (?, ?, ?, ?, 'function', 1, 1)
                        """,
                        (symbol_id, file_id, name, name),
                    )

        for repo_files in graph.values():
            for _path, _symbols, edges in repo_files:
                for source_name, target_name in edges:
                    conn.execute(
                        """
                        INSERT INTO edges (
                            id, source_id, target_id, target_name, kind,
                            resolution
                        ) VALUES (?, ?, ?, ?, 'calls', 'exact')
                        """,
                        (
                            f"edge:{source_name}:{target_name}",
                            symbol_ids[source_name],
                            symbol_ids[target_name],
                            target_name,
                        ),
                    )
        conn.commit()
    finally:
        conn.close()


def _map_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workspace_name: str,
    graph: _GraphSpec,
) -> SimpleNamespace:
    workspace = tmp_path / workspace_name
    db_path = tmp_path / f"{workspace_name}.db"
    _seed_graph(workspace, db_path, graph)
    monkeypatch.setenv("CAIRN_WORKSPACE", str(workspace))
    monkeypatch.setenv("CAIRN_DB", str(db_path))
    return SimpleNamespace(workspace=workspace, db_path=db_path)


@pytest.fixture
def multi_repo_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    return _map_fixture(
        tmp_path, monkeypatch, "workspace", _MULTI_REPO_GRAPH
    )


@pytest.fixture
def single_repo_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    return _map_fixture(tmp_path, monkeypatch, "solo", _SINGLE_REPO_GRAPH)


@pytest.fixture
def capped_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    files: list[_FileSpec] = []
    for index in range(20):
        target = f"target_{index:02d}"
        caller = f"caller_{index:02d}"
        files.append(
            (
                f"cluster{index:02d}/target.py",
                (target, caller),
                ((caller, target),),
            )
        )
    hub_names = tuple(f"hub_{letter}" for letter in "abcde")
    files.append(
        (
            "hubfarm/farm.py",
            ("farm_caller", *hub_names),
            tuple(("farm_caller", hub) for hub in hub_names),
        )
    )
    return _map_fixture(
        tmp_path, monkeypatch, "caps", {"caps": tuple(files)}
    )


@pytest.fixture
def empty_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    from cairn.graph.schema import _apply_schema

    workspace = tmp_path / "empty"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(db_path))
    try:
        _apply_schema(conn)
        conn.execute(
            "INSERT INTO repos (id, name, path) "
            "VALUES ('empty', 'empty', 'empty')"
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("CAIRN_WORKSPACE", str(workspace))
    monkeypatch.setenv("CAIRN_DB", str(db_path))
    return SimpleNamespace(workspace=workspace, db_path=db_path)


@pytest.fixture
def drifted_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hash_backend
) -> SimpleNamespace:
    from cairn.graph.builder import build_graph

    workspace = tmp_path / "drifted-map-workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    source = workspace / "orientation.py"
    source.write_text(
        "def old_name():\n"
        "    return 'orientation'\n"
        "\n"
        "\n"
        "def caller():\n"
        "    return old_name()\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CAIRN_WORKSPACE", str(workspace))
    baseline_db = tmp_path / "drifted-map-baseline.db"
    build_graph(
        workspace=str(workspace), db_path=str(baseline_db), verbose=False
    )
    source.write_text(
        "def new_name():\n"
        "    return 'orientation'\n"
        "\n"
        "\n"
        "def added_symbol():\n"
        "    return 'orientation'\n"
        "\n"
        "\n"
        "def caller():\n"
        "    return new_name()\n",
        encoding="utf-8",
    )

    def activate(name: str) -> Path:
        active_db = tmp_path / f"{name}.db"
        shutil.copyfile(baseline_db, active_db)
        monkeypatch.setenv("CAIRN_DB", str(active_db))
        return active_db

    return SimpleNamespace(workspace=workspace, activate=activate)


def _symbol_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM symbols")}
    finally:
        conn.close()


def _run_map(fixture: SimpleNamespace, *arguments: str):
    from click.testing import CliRunner

    from cairn.cli import map as map_command
    from cairn.cli import main

    assert map_command.map_orientation.name == "map"
    return CliRunner().invoke(
        main,
        ["map", "--json", "--db", str(fixture.db_path), *arguments],
        catch_exceptions=False,
    )


def _orientation(fixture: SimpleNamespace) -> dict:
    result = _run_map(fixture)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _orientation_with_arguments(
    fixture: SimpleNamespace, *arguments: str
) -> dict:
    result = _run_map(fixture, *arguments)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _scope(payload: dict, repo: str) -> dict:
    matching = [item for item in payload["repos"] if item["repo"] == repo]
    assert len(matching) == 1
    return matching[0]


def test_map_groups_counts_and_hubs_by_repo_scope(multi_repo_map) -> None:
    payload = _orientation(multi_repo_map)

    assert [item["repo"] for item in payload["repos"]] == ["alpha", "beta"]
    alpha = _scope(payload, "alpha")
    beta = _scope(payload, "beta")
    assert [
        (item["path"], item["files"], item["symbols"], item["edges"])
        for item in alpha["clusters"]
    ] == [("services", 2, 6, 5), ("api", 1, 2, 2)]
    assert alpha["dropped_clusters"] == 0
    assert [
        (item["path"], item["files"], item["symbols"], item["edges"])
        for item in beta["clusters"]
    ] == [("engine", 2, 3, 2), ("ui", 2, 2, 2)]
    assert beta["dropped_clusters"] == 0

    services = alpha["clusters"][0]
    assert services["hubs"] == [
        {
            "qualified_name": "alpha_hub",
            "path": "services/core.py",
            "incoming": 4,
        },
        {
            "qualified_name": "alpha_tie_a",
            "path": "services/core.py",
            "incoming": 2,
        },
        {
            "qualified_name": "alpha_tie_b",
            "path": "services/core.py",
            "incoming": 1,
        },
    ]
    assert services["dropped_hubs"] == 0
    assert beta["clusters"][0]["hubs"] == [
        {
            "qualified_name": "beta_hub",
            "path": "engine/engine.py",
            "incoming": 4,
        }
    ]
    assert beta["clusters"][0]["dropped_hubs"] == 0
    assert payload["hotspots"] == [
        {
            "repo": "alpha",
            "qualified_name": "alpha_hub",
            "path": "services/core.py",
            "incoming": 4,
        },
        {
            "repo": "beta",
            "qualified_name": "beta_hub",
            "path": "engine/engine.py",
            "incoming": 4,
        },
        {
            "repo": "alpha",
            "qualified_name": "alpha_tie_a",
            "path": "services/core.py",
            "incoming": 2,
        },
        {
            "repo": "alpha",
            "qualified_name": "alpha_tie_b",
            "path": "services/core.py",
            "incoming": 1,
        },
    ]
    assert payload["dropped_hotspots"] == 0


def test_map_output_is_byte_identical_across_repeats(multi_repo_map) -> None:
    first = _run_map(multi_repo_map)
    second = _run_map(multi_repo_map)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert first.stdout == second.stdout


def test_map_refreshes_a_drifted_store_before_answering(
    drifted_map,
) -> None:
    from click.testing import CliRunner

    from cairn.cli import main

    db_path = drifted_map.activate("map-refresh")
    result = CliRunner().invoke(
        main,
        ["map", "--json", "--db", str(db_path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    rendered = json.dumps(json.loads(result.stdout))
    assert "new_name" in rendered
    assert "old_name" not in rendered
    names = _symbol_names(db_path)
    assert "new_name" in names
    assert "old_name" not in names


def test_map_no_refresh_answers_from_storage_with_banner(
    drifted_map,
) -> None:
    from click.testing import CliRunner

    from cairn.cli import main

    db_path = drifted_map.activate("map-no-refresh")
    result = CliRunner().invoke(
        main,
        ["map", "--json", "--db", str(db_path), "--no-refresh"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    rendered = json.dumps(json.loads(result.stdout))
    assert "old_name" in rendered
    assert "new_name" not in rendered
    assert "Stale graph" in result.stderr
    assert "orientation.py" in result.stderr


def test_map_read_only_answers_from_storage_with_banner(
    drifted_map, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from cairn.cli import main

    db_path = drifted_map.activate("map-read-only")
    monkeypatch.setenv("CAIRN_READ_ONLY", "1")
    result = CliRunner().invoke(
        main,
        ["map", "--json", "--db", str(db_path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    rendered = json.dumps(json.loads(result.stdout))
    assert "old_name" in rendered
    assert "new_name" not in rendered
    assert "Stale graph" in result.stderr
    assert "orientation.py" in result.stderr
    names = _symbol_names(db_path)
    assert "old_name" in names
    assert "new_name" not in names


def test_map_needs_no_llm_or_network(
    multi_repo_map, monkeypatch: pytest.MonkeyPatch
) -> None:
    def blocked_socket(*_args, **_kwargs):
        raise AssertionError("map created a network socket")

    def blocked_client(*_args, **_kwargs):
        raise AssertionError("map requested an LLM client")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    from cairn.llm import client as llm_client

    monkeypatch.setattr(llm_client, "get_client", blocked_client)
    monkeypatch.setattr(llm_client, "LLMClient", blocked_client)

    payload = _orientation(multi_repo_map)
    assert payload["hotspots"]


def test_map_reports_dropped_counts_at_every_cap(capped_map) -> None:
    payload = _orientation(capped_map)
    scope = _scope(payload, "caps")

    assert [cluster["path"] for cluster in scope["clusters"]] == [
        "hubfarm",
        *(f"cluster{index:02d}" for index in range(15)),
    ]
    assert scope["dropped_clusters"] == 5
    assert scope["clusters"][0]["hubs"] == [
        {
            "qualified_name": f"hub_{letter}",
            "path": "hubfarm/farm.py",
            "incoming": 1,
        }
        for letter in "abc"
    ]
    assert scope["clusters"][0]["dropped_hubs"] == 2
    assert all(
        cluster["dropped_hubs"] == 0
        for cluster in scope["clusters"][1:]
    )
    assert [entry["qualified_name"] for entry in payload["hotspots"]] == [
        f"target_{index:02d}" for index in range(12)
    ]
    assert payload["dropped_hotspots"] == 13


def test_map_cap_flags_are_applied_and_reported(capped_map) -> None:
    payload = _orientation_with_arguments(
        capped_map,
        "--max-clusters",
        "2",
        "--max-hubs",
        "1",
        "--max-hotspots",
        "2",
    )
    scope = _scope(payload, "caps")

    assert [cluster["path"] for cluster in scope["clusters"]] == [
        "hubfarm",
        "cluster00",
    ]
    assert scope["dropped_clusters"] == 19
    assert [hub["qualified_name"] for hub in scope["clusters"][0]["hubs"]] == [
        "hub_a"
    ]
    assert scope["clusters"][0]["dropped_hubs"] == 4
    assert [entry["qualified_name"] for entry in payload["hotspots"]] == [
        "target_00",
        "target_01",
    ]
    assert payload["dropped_hotspots"] == 23


def test_mcp_repo_map_returns_the_cli_orientation(
    multi_repo_map, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cairn.mcp_server.tools_graph as tools_graph

    repo_map = getattr(tools_graph, "repo_map", None)
    if repo_map is None:
        pytest.fail("MCP repo_map tool is missing")
    cli_payload = _orientation(multi_repo_map)
    connection = sqlite3.connect(str(multi_repo_map.db_path))
    connection.row_factory = sqlite3.Row
    monkeypatch.setattr(
        tools_graph, "_conn", lambda: connection
    )
    result = repo_map(structured=True)
    mcp_payload = (
        result.model_dump() if hasattr(result, "model_dump") else result
    )

    assert mcp_payload["repos"] == cli_payload["repos"]
    assert mcp_payload["hotspots"] == cli_payload["hotspots"]
    assert (
        mcp_payload["dropped_hotspots"] == cli_payload["dropped_hotspots"]
    )


def test_mcp_tool_count_and_inventory_include_graph_surfaces() -> None:
    import cairn.mcp_server.server as server
    from cairn.mcp_server._server_core import mcp

    tool_names = {
        tool.name for tool in mcp._tool_manager.list_tools()
    }
    assert {"repo_map", "file_api"} <= tool_names
    assert server._EXPECTED_TOOL_COUNT == 24
    server.verify_tool_count()


def test_single_repo_map_splits_a_dominant_first_segment(
    single_repo_map,
) -> None:
    payload = _orientation(single_repo_map)

    assert [item["repo"] for item in payload["repos"]] == ["solo"]
    scope = _scope(payload, "solo")
    assert [
        (item["path"], item["files"], item["symbols"], item["edges"])
        for item in scope["clusters"]
    ] == [
        ("src/feature", 3, 3, 1),
        ("api", 1, 1, 0),
        ("src/shared", 1, 1, 0),
    ]
    assert scope["clusters"][0]["hubs"] == [
        {
            "qualified_name": "feature_hub",
            "path": "src/feature/one.py",
            "incoming": 1,
        }
    ]
    assert scope["clusters"][2]["hubs"] == []


def test_empty_repo_map_returns_an_empty_orientation(empty_map) -> None:
    payload = _orientation(empty_map)
    scope = _scope(payload, "empty")

    assert scope["clusters"] == []
    assert scope["dropped_clusters"] == 0
    assert payload["hotspots"] == []
    assert payload["dropped_hotspots"] == 0
