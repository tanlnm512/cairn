"""Gated pyright upgrades through the public graph-build path."""

from __future__ import annotations

import sqlite3
import json
import queue
import threading
from pathlib import Path
from types import SimpleNamespace
from io import BytesIO

import pytest

from cairn.graph.builder import build_graph


class FakeJsonRpcTransport:
    def __init__(self, resolved_response, unresolved_response):
        self.resolved_response = resolved_response
        self.unresolved_response = unresolved_response
        self.requests = []
        self.lifecycle = []
        self.closed = False

    def initialize(self):
        self.lifecycle.append("initialize")

    def notify(self, method, params):
        self.requests.append((method, params))

    def request(self, method, params):
        self.requests.append((method, params))
        line = params["position"]["line"]
        if line == 1:
            return [self.resolved_response]
        if line == 2:
            return self.unresolved_response
        return []

    def shutdown(self):
        self.lifecycle.append("shutdown")

    def close(self):
        self.closed = True


class FailingJsonRpcTransport:
    def initialize(self):
        raise RuntimeError("pyright exited before responding")

    def close(self):
        pass


class _FakeStdin:
    def __init__(self):
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.chunks.append(data)
        return len(data)

    def flush(self) -> None:
        return None


def _frame(message: dict) -> bytes:
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _workspace(tmp_path: Path, name: str) -> tuple[str, Path]:
    workspace = tmp_path / name
    repo = workspace / "demo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "calls.py").write_text(
        "def caller():\n"
        "    resolved_target()\n"
        "    tied_target()\n"
        "\n"
        "\n"
        "def guarded_caller():\n"
        "    guarded_target()\n"
    )
    implementation = repo / "implementation.py"
    implementation.write_text("def resolved_target():\n    return 1\n")
    (repo / "resolved_decoy.py").write_text(
        "def resolved_target():\n    return 2\n"
    )
    (repo / "tied_a.py").write_text("def tied_target():\n    return 3\n")
    (repo / "tied_b.py").write_text("def tied_target():\n    return 4\n")
    (repo / "guarded.py").write_text("def guarded_target():\n    return 5\n")
    return str(workspace), implementation


def _build(
    workspace: str, db_path: Path, transport: object
) -> tuple[dict, sqlite3.Connection]:
    summary = build_graph(
        workspace=workspace,
        db_path=str(db_path),
        verbose=False,
        lsp=True,
        lsp_transport=transport,
    )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return summary, conn


def _edge(conn: sqlite3.Connection, target: str) -> sqlite3.Row:
    return conn.execute(
        """
        SELECT e.target_id, e.target_name, e.resolution,
               tf.path AS target_file, t.qualified_name AS target_qname
        FROM edges e
        JOIN symbols source ON source.id = e.source_id
        JOIN files source_file ON source_file.id = source.file_id
        LEFT JOIN symbols t ON t.id = e.target_id
        LEFT JOIN files tf ON tf.id = t.file_id
        WHERE source_file.path = 'calls.py'
          AND e.kind = 'calls'
          AND (e.target_name = ? OR t.name = ?)
        """,
        (target, target),
    ).fetchone()


@pytest.mark.parametrize("explicit_false", [False, True], ids=["default", "false"])
def test_language_server_pass_is_flag_gated(
    tmp_path: Path, explicit_false: bool
) -> None:
    workspace, _ = _workspace(tmp_path, "flag_gated")
    transport = FakeJsonRpcTransport(None, None)
    kwargs = {"lsp_transport": transport}
    if explicit_false:
        kwargs["lsp"] = False

    summary = build_graph(
        workspace=workspace,
        db_path=str(tmp_path / "flag-gated.db"),
        verbose=False,
        **kwargs,
    )

    conn = sqlite3.connect(str(tmp_path / "flag-gated.db"))
    conn.row_factory = sqlite3.Row
    try:
        assert _edge(conn, "resolved_target")["resolution"] == "ambiguous"
        assert _edge(conn, "tied_target")["resolution"] == "ambiguous"
        assert _edge(conn, "guarded_target")["resolution"] == "exact"
    finally:
        conn.close()

    assert "lsp" not in summary
    assert transport.requests == []
    assert transport.lifecycle == []
    assert transport.closed is False


def test_server_requests_do_not_consume_pending_responses() -> None:
    from cairn.graph.lsp import PyrightStdioTransport

    transport = object.__new__(PyrightStdioTransport)
    transport._pending = {}
    transport._lock = threading.Lock()
    transport._write_lock = threading.Lock()
    response = queue.Queue()
    with transport._lock:
        transport._pending[2] = response

    stdin = _FakeStdin()
    payload = b"".join(
        [
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": "server-1",
                    "method": "workspace/configuration",
                    "params": {"items": []},
                }
            ),
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": [{"uri": "file:///tmp/one.py"}],
                }
            ),
            _frame({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics"}),
        ]
    )
    transport._process = SimpleNamespace(stdout=BytesIO(payload), stdin=stdin)

    transport._read_responses()

    assert response.get_nowait()["result"] == [{"uri": "file:///tmp/one.py"}]
    written = b"".join(stdin.chunks)
    header, _, body = written.partition(b"\r\n\r\n")
    assert header.startswith(b"Content-Length: ")
    rejection = json.loads(body)
    assert rejection["id"] == "server-1"
    assert rejection["error"]["code"] == -32601


@pytest.mark.parametrize(
    "unresolved_response",
    [
        [],
        [
            {
                "uri": "file:///workspace/one.py",
                "range": {"start": {"line": 0, "character": 0}},
            },
            {
                "uri": "file:///workspace/two.py",
                "range": {"start": {"line": 0, "character": 0}},
            },
        ],
    ],
    ids=["no-definitions", "multiple-definitions"],
)
def test_unique_definition_upgrades_only_that_ambiguous_edge(
    tmp_path: Path, unresolved_response: list
) -> None:
    workspace, implementation = _workspace(tmp_path, "upgrade")
    transport = FakeJsonRpcTransport(
        {
            "uri": implementation.as_uri(),
            "range": {"start": {"line": 1, "character": 0}},
        },
        unresolved_response,
    )

    summary, conn = _build(workspace, tmp_path / "upgrade.db", transport)
    try:
        upgraded = _edge(conn, "resolved_target")
        assert upgraded["resolution"] == "exact"
        assert upgraded["target_file"] == "implementation.py"
        assert upgraded["target_qname"] == "resolved_target"
        assert upgraded["target_name"] is None
        assert _edge(conn, "tied_target")["resolution"] == "ambiguous"
        assert _edge(conn, "guarded_target")["resolution"] == "exact"
    finally:
        conn.close()

    report = summary["lsp"]
    assert report["considered"] == 2
    assert report["upgraded"] == 1
    assert report["notices"] == []
    assert summary["resolution"] == {"exact": 2, "ambiguous": 1, "unresolved": 0}
    definition_requests = [
        params
        for method, params in transport.requests
        if method == "textDocument/definition"
    ]
    assert [
        (params["position"]["line"], params["position"]["character"])
        for params in definition_requests
    ] == [(1, 0), (2, 0)]
    assert transport.lifecycle == ["initialize", "shutdown"]
    assert transport.closed


@pytest.mark.parametrize(
    "transport",
    [None, FailingJsonRpcTransport()],
    ids=["missing-pyright", "failing-pyright"],
)
def test_unavailable_or_failing_pyright_is_not_a_graph_failure(
    tmp_path: Path, transport: object
) -> None:
    workspace, _ = _workspace(tmp_path, "degraded")

    summary, conn = _build(workspace, tmp_path / "degraded.db", transport)
    try:
        assert _edge(conn, "resolved_target")["resolution"] == "ambiguous"
        assert _edge(conn, "tied_target")["resolution"] == "ambiguous"
        assert _edge(conn, "guarded_target")["resolution"] == "exact"
    finally:
        conn.close()

    report = summary["lsp"]
    assert report["considered"] == 2
    assert report["upgraded"] == 0
    assert report["notices"]
    assert "pyright" in " ".join(report["notices"]).lower()
    assert summary["files"] == 6


def test_exact_edges_survive_present_absent_and_failing_pyright(
    tmp_path: Path,
) -> None:
    cases = ["present", "absent", "failing"]
    reports = []

    for case in cases:
        workspace, implementation = _workspace(tmp_path, f"guarded_{case}")
        if case == "present":
            transport = FakeJsonRpcTransport(
                {
                    "uri": implementation.as_uri(),
                    "range": {"start": {"line": 1, "character": 0}},
                },
                [],
            )
        elif case == "absent":
            transport = None
        else:
            transport = FailingJsonRpcTransport()

        summary, conn = _build(workspace, tmp_path / f"guarded-{case}.db", transport)
        try:
            exact = _edge(conn, "guarded_target")
            assert exact["resolution"] == "exact"
            assert exact["target_file"] == "guarded.py"
            assert exact["target_qname"] == "guarded_target"
        finally:
            conn.close()
        reports.append(summary["lsp"])

    assert len(reports) == 3
