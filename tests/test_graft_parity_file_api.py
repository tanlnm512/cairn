"""Body-free symbol-surface contract for stored graph rows."""

from __future__ import annotations

import json
import sqlite3

import pytest


ORDERED_FILE = "src/orders.py"
EMPTY_FILE = "src/empty.py"
DUPLICATE_FILE = "src/duplicate.py"
FULL_SIGNATURE = "format_total(amount: int, currency: str = \"USD\") -> str"
PARTIAL_SIGNATURE = "Order.label(quantity: int)"
BODY_MARKERS = (
    'return f"{currency}{amount:.2f}"',
    'return f"Order of {quantity}"',
    'quantity = max(quantity, 1)',
)


@pytest.fixture
def graph_conn(tmp_path):
    from cairn.graph.schema import _apply_schema

    conn = sqlite3.connect(tmp_path / "graph.db")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('r', 'repo', '/tmp/repo')"
    )
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('r2', 'repo2', '/tmp/repo2')"
    )
    conn.executemany(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES (?, 'r', ?, 'python')",
        [
            ("ordered", ORDERED_FILE),
            ("empty", EMPTY_FILE),
        ],
    )
    conn.executemany(
        "INSERT INTO symbols "
        "(id, file_id, name, qualified_name, kind, line_start, line_end, "
        "parameters, return_type, body) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "function",
                "ordered",
                "format_total",
                "format_total",
                "function",
                1,
                2,
                'amount: int, currency: str = "USD"',
                "str",
                BODY_MARKERS[0],
            ),
            (
                "class",
                "ordered",
                "Order",
                "Order",
                "class",
                4,
                7,
                None,
                None,
                BODY_MARKERS[2],
            ),
            (
                "method",
                "ordered",
                "label",
                "Order.label",
                "method",
                5,
                6,
                "quantity: int",
                None,
                BODY_MARKERS[1],
            ),
        ],
    )
    conn.commit()
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES ('duplicate-r', 'r', ?, 'python')",
        (DUPLICATE_FILE,),
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES ('duplicate-r2', 'r2', ?, 'python')",
        (DUPLICATE_FILE,),
    )
    conn.executemany(
        "INSERT INTO symbols "
        "(id, file_id, name, qualified_name, kind, line_start, line_end, "
        "parameters, return_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                "duplicate-r-symbol",
                "duplicate-r",
                "duplicate",
                "repo_duplicate",
                "function",
                1,
                2,
                "left: int",
                None,
            ),
            (
                "duplicate-r2-symbol",
                "duplicate-r2",
                "duplicate",
                "repo2_duplicate",
                "function",
                1,
                2,
                "right: int",
                "str",
            ),
        ],
    )
    conn.commit()
    yield conn
    conn.close()


def _file_symbols(conn, path, repo=None):
    from cairn.graph.file_api import file_api

    return file_api(conn, path, repo=repo)


def test_file_surface_returns_every_symbol_without_bodies(graph_conn):
    symbols = _file_symbols(graph_conn, ORDERED_FILE)

    by_qualified_name = {symbol["qualified_name"]: symbol for symbol in symbols}
    assert set(by_qualified_name) == {"format_total", "Order", "Order.label"}
    assert by_qualified_name["format_total"] == {
        "name": "format_total",
        "kind": "function",
        "qualified_name": "format_total",
        "signature": FULL_SIGNATURE,
        "line_start": 1,
        "line_end": 2,
    }
    assert by_qualified_name["Order.label"] == {
        "name": "label",
        "kind": "method",
        "qualified_name": "Order.label",
        "signature": PARTIAL_SIGNATURE,
        "line_start": 5,
        "line_end": 6,
    }
    assert by_qualified_name["Order"]["kind"] == "class"
    assert by_qualified_name["Order"]["line_start"] == 4
    assert by_qualified_name["Order"]["line_end"] == 7
    serialized = json.dumps(symbols, sort_keys=True)
    assert not [marker for marker in BODY_MARKERS if marker in serialized]


def test_missing_signature_signals_degrade_to_name_and_span(graph_conn):
    symbols = _file_symbols(graph_conn, ORDERED_FILE)

    degraded = next(
        symbol for symbol in symbols if symbol["qualified_name"] == "Order"
    )
    assert degraded == {
        "name": "Order",
        "kind": "class",
        "qualified_name": "Order",
        "signature": None,
        "line_start": 4,
        "line_end": 7,
    }


def test_symbolless_file_returns_an_empty_surface(graph_conn):
    assert _file_symbols(graph_conn, EMPTY_FILE) == []


def test_duplicate_relative_path_requires_repo(graph_conn):
    with pytest.raises(ValueError) as error:
        _file_symbols(graph_conn, DUPLICATE_FILE)

    message = str(error.value)
    assert "repositories: r, r2" in message
    assert "repo" in message


def test_repo_selects_one_duplicate_relative_path(graph_conn):
    symbols = _file_symbols(graph_conn, DUPLICATE_FILE, repo="r2")

    assert [symbol["qualified_name"] for symbol in symbols] == [
        "repo2_duplicate"
    ]


def test_mcp_file_api_returns_the_domain_symbol_surface(
    graph_conn, monkeypatch
):
    expected = _file_symbols(graph_conn, ORDERED_FILE)
    import cairn.mcp_server.tools_graph as tools_graph
    from cairn.graph.watcher import FreshnessReport

    file_api = getattr(tools_graph, "file_api", None)
    if file_api is None:
        pytest.fail("MCP file_api tool is missing")
    monkeypatch.setattr(tools_graph, "_conn", lambda: graph_conn)
    monkeypatch.setattr(
        tools_graph, "_fresh_graph", lambda _conn: FreshnessReport((), False)
    )

    result = file_api(path=ORDERED_FILE, structured=True)
    payload = (
        result.model_dump() if hasattr(result, "model_dump") else result
    )

    assert payload == expected


def test_mcp_file_api_passes_repo_disambiguation(graph_conn, monkeypatch):
    import cairn.mcp_server.tools_graph as tools_graph
    from cairn.mcp_server._server_core import mcp
    from cairn.graph.watcher import FreshnessReport

    registered = {
        tool.name: tool for tool in mcp._tool_manager.list_tools()
    }
    assert "repo" in registered["file_api"].parameters["properties"]
    assert "repo" not in registered["file_api"].parameters["required"]
    monkeypatch.setattr(tools_graph, "_conn", lambda: graph_conn)
    monkeypatch.setattr(
        tools_graph, "_fresh_graph", lambda _conn: FreshnessReport((), False)
    )

    result = tools_graph.file_api(
        path=DUPLICATE_FILE, repo="r2", structured=True
    )
    payload = (
        result.model_dump() if hasattr(result, "model_dump") else result
    )

    assert [symbol["qualified_name"] for symbol in payload] == [
        "repo2_duplicate"
    ]
