"""TC-001: the streamable-HTTP transport serves the same tool catalog as stdio.

Also covers the HTTP auth gate, bind/transport-security policy, /healthz
probe, stateless mode, and the serve-CLI flag wiring (TC-002..TC-026).
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

from cairn.graph.schema import _apply_schema

_STREAMABLE_HTTP_PATH = "/mcp"
_BOOT = (
    "import sys; from cairn.mcp_server.server import run; "
    "run(transport=sys.argv[1], port=int(sys.argv[2]))"
)
_BOOT_HTTP = (
    "import sys; from cairn.mcp_server.server import run; "
    "run(transport='http', port=int(sys.argv[1]), host=sys.argv[2], "
    "api_key=sys.argv[3] or None, stateless=sys.argv[4] == '1')"
)
_SSE_HEADERS = {"Accept": "application/json, text/event-stream"}
_TEST_KEY = "http-transport-test-key"
_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "http-parity-test", "version": "0"},
    },
}
_TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def _scratch_store(tmp_path) -> str:
    db_path = tmp_path / "parity-store.db"
    conn = sqlite3.connect(str(db_path))
    _apply_schema(conn)
    conn.close()
    return str(db_path)


def _child_env(db_path: str, read_only: bool) -> dict:
    # _hermetic_env already sandboxed HOME/CAIRN_HOME; children inherit it.
    env = dict(os.environ)
    env["CAIRN_DB"] = db_path
    if read_only:
        env["CAIRN_READ_ONLY"] = "1"
    else:
        env.pop("CAIRN_READ_ONLY", None)
    return env


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _rpc_result_tools(message: dict) -> list[str]:
    return [tool["name"] for tool in message["result"]["tools"]]


def _stdio_tool_catalog(env: dict) -> list[str]:
    messages = [
        _INITIALIZE,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _TOOLS_LIST,
    ]
    last: subprocess.CompletedProcess | None = None
    for _ in range(2):
        last = subprocess.run(
            [sys.executable, "-c", _BOOT, "stdio", "0"],
            input="".join(json.dumps(m) + "\n" for m in messages),
            capture_output=True,
            text=True,
            env=env,
            timeout=90,
            check=False,
        )
        tools = None
        for line in last.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            message = json.loads(line)
            if message.get("id") == 2 and "result" in message:
                tools = _rpc_result_tools(message)
        if tools is not None:
            return tools
    assert False, (
        f"stdio session never answered tools/list (rc={last.returncode}); "
        f"stdout tail: {last.stdout[-2000:]}; "
        f"stderr tail: {last.stderr[-2000:]}"
    )


def _http_post(url: str, payload: dict, headers: dict | None = None):
    """POST JSON and return (status, headers, body); non-2xx answers are returned, never raised."""
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers or {}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.headers, response.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read().decode()


def _http_get(url: str, headers: dict | None = None):
    """GET a URL and return (status, headers, body); non-2xx answers are returned, never raised."""
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.headers, response.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read().decode()


def _post_rpc(url: str, payload: dict, session_id: str | None = None):
    headers = dict(_SSE_HEADERS)
    headers["Content-Type"] = "application/json"
    if session_id:
        headers["mcp-session-id"] = session_id
    _, headers_out, body = _http_post(url, payload, headers)
    return headers_out, body


def _rpc_messages(body: str) -> list[dict]:
    body = body.strip()
    if body.startswith("{"):
        return [json.loads(body)]
    # SSE framing: each response rides a `data:` line.
    return [
        json.loads(line[len("data:"):].strip())
        for line in body.splitlines()
        if line.startswith("data:")
    ]


def _wait_http_ready(url: str, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return  # any HTTP answer means uvicorn is serving
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.25)
    raise AssertionError(f"HTTP server never accepted connections on {url}")


def _http_tool_catalog(port: int, env: dict, log_path) -> list[str]:
    with open(log_path, "w") as server_log:
        proc = subprocess.Popen(
            [sys.executable, "-c", _BOOT, "http", str(port)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=server_log,
        )
        try:
            url = f"http://127.0.0.1:{port}{_STREAMABLE_HTTP_PATH}"
            _wait_http_ready(url)
            headers, _ = _post_rpc(url, _INITIALIZE)
            session_id = headers.get("mcp-session-id")
            assert session_id, "initialize response carried no mcp-session-id"
            _post_rpc(
                url,
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                session_id,
            )
            _, body = _post_rpc(url, _TOOLS_LIST, session_id)
            for message in _rpc_messages(body):
                if message.get("id") == 2 and "result" in message:
                    return _rpc_result_tools(message)
            raise AssertionError(f"tools/list over HTTP got no result: {body[:2000]}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def test_http_tool_catalog_matches_stdio(tmp_path):
    """tools/list over streamable HTTP equals tools/list over stdio, same store."""
    db_path = _scratch_store(tmp_path)
    port = _free_port()

    stdio_tools = _stdio_tool_catalog(_child_env(db_path, read_only=False))
    http_tools = _http_tool_catalog(
        port, _child_env(db_path, read_only=True), tmp_path / "http-server.log"
    )

    assert stdio_tools, "stdio session offered no tools"
    http_only = sorted(set(http_tools) - set(stdio_tools))
    stdio_only = sorted(set(stdio_tools) - set(http_tools))
    assert not http_only and not stdio_only, (
        f"tool catalogs diverge: http-only={http_only} stdio-only={stdio_only}"
    )


# --- Shared fixtures/helpers for the HTTP policy suites ---------------------


@pytest.fixture
def _http_singleton_settings():
    """Snapshot/restore the FastMCP singleton settings an http-branch test mutates."""
    import cairn.mcp_server._server_core as core

    settings = core.mcp.settings
    saved = (
        settings.host,
        settings.port,
        settings.stateless_http,
        settings.transport_security,
    )
    yield core.mcp
    settings.host, settings.port, settings.stateless_http, settings.transport_security = saved


def _auth_headers(key: str | None) -> dict:
    """POST headers for the MCP endpoint; key None means no Authorization header."""
    headers = dict(_SSE_HEADERS)
    headers["Content-Type"] = "application/json"
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    return headers


@contextmanager
def _http_server(
    db_path: str,
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    api_key: str | None = None,
    stateless: bool = False,
):
    """Boot one foreground HTTP server child; yields (port, proc, log_path)."""
    from pathlib import Path

    port = port or _free_port()
    log_path = Path(db_path).parent / "http-server.log"
    env = _child_env(db_path, read_only=True)
    env.pop("CAIRN_MCP_API_KEY", None)
    with open(log_path, "w") as log:
        proc = subprocess.Popen(
            [
                sys.executable, "-c", _BOOT_HTTP,
                str(port), host, api_key or "", "1" if stateless else "0",
            ],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            _wait_http_ready(f"http://127.0.0.1:{port}{_STREAMABLE_HTTP_PATH}")
            yield port, proc, log_path
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


# --- Bearer auth middleware (ASGI boundary: rejection precedes the app) -----


class _RecordingApp:
    def __init__(self):
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _asgi_scope(scope_type="http", path="/mcp", headers=None):
    return {
        "type": scope_type,
        "path": path,
        "headers": [
            (k.encode("latin-1"), v.encode("latin-1"))
            for k, v in (headers or {}).items()
        ],
    }


async def _drive_asgi(app, scope):
    sent = []

    async def receive():
        raise AssertionError("middleware under test must not read the body")

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _middleware(inner_app=None):
    from cairn.mcp_server.server import _BearerAuthMiddleware

    return _BearerAuthMiddleware(inner_app or _RecordingApp(), _TEST_KEY)


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Bearer wrong-key", "wrong-key", "Basic dXNlcjpwYXNz", "Bearer"],
)
def test_bearer_middleware_rejects_before_invoking_app(authorization, caplog):
    inner = _RecordingApp()
    headers = {"authorization": authorization} if authorization is not None else {}
    sent = asyncio.run(_drive_asgi(_middleware(inner), _asgi_scope(headers=headers)))
    assert inner.scopes == [], "401 must be sent without reaching the wrapped app"
    assert sent[0]["status"] == 401
    assert (b"www-authenticate", b"Bearer") in sent[0]["headers"]
    assert sent[-1]["body"] == b'{"error":"unauthorized"}'
    assert any(rec.levelname == "WARNING" for rec in caplog.records)


def test_bearer_middleware_allows_valid_key_and_lifespan():
    inner = _RecordingApp()
    mw = _middleware(inner)
    sent = asyncio.run(
        _drive_asgi(mw, _asgi_scope(headers={"authorization": f"Bearer {_TEST_KEY}"}))
    )
    assert len(inner.scopes) == 1
    assert sent[0]["status"] == 200
    # lifespan scopes (session manager startup/shutdown) pass through untouched
    asyncio.run(_drive_asgi(mw, _asgi_scope(scope_type="lifespan")))
    assert inner.scopes[-1]["type"] == "lifespan"


def test_bearer_middleware_healthz_is_exempt_and_bare_token_accepted():
    exempt_inner = _RecordingApp()
    asyncio.run(_drive_asgi(_middleware(exempt_inner), _asgi_scope(path="/healthz")))
    assert len(exempt_inner.scopes) == 1, "/healthz must pass without a credential"

    bare_inner = _RecordingApp()
    asyncio.run(
        _drive_asgi(
            _middleware(bare_inner), _asgi_scope(headers={"authorization": _TEST_KEY})
        )
    )
    assert len(bare_inner.scopes) == 1, "bare token (no Bearer prefix) is accepted"


# --- Binding policy + transport-security settings ---------------------------


def test_transport_security_settings_match_bind_class():
    from cairn.mcp_server.server import _transport_security_for

    loopback = _transport_security_for("127.0.0.1")
    assert loopback.enable_dns_rebinding_protection is True
    assert loopback.allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    assert loopback.allowed_origins == [
        "http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*",
    ]

    specific = _transport_security_for("192.168.1.5")
    assert specific.enable_dns_rebinding_protection is True
    assert specific.allowed_hosts == [
        "192.168.1.5", "192.168.1.5:*", "127.0.0.1:*", "localhost:*", "[::1]:*",
    ]
    assert specific.allowed_origins == []

    wildcard = _transport_security_for("0.0.0.0")
    assert wildcard.enable_dns_rebinding_protection is False
    assert wildcard.allowed_hosts == []
    assert wildcard.allowed_origins == []


def test_refuse_predicate_exits_unkeyed_non_loopback(capsys):
    from cairn.mcp_server.server import _require_http_api_key

    _require_http_api_key("127.0.0.1", None)  # keyless loopback allowed
    _require_http_api_key("0.0.0.0", "k")     # keyed wildcard allowed
    with pytest.raises(SystemExit) as excinfo:
        _require_http_api_key("0.0.0.0", None)
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "refusing to serve HTTP" in err and "0.0.0.0" in err
    assert "CAIRN_MCP_API_KEY" in err  # remediation names the source, never a value


def test_build_http_app_refuses_unkeyed_wildcard(_http_singleton_settings):
    from cairn.mcp_server.server import _build_http_app

    with pytest.raises(SystemExit) as excinfo:
        _build_http_app(host="0.0.0.0", port=9876, api_key=None, stateless=False)
    assert excinfo.value.code == 1


def test_healthz_route_registers_exactly_once(_http_singleton_settings):
    from cairn.mcp_server.server import _HEALTHZ_PATH, _register_healthz_route

    _register_healthz_route()
    _register_healthz_route()
    matches = [
        route
        for route in _http_singleton_settings._custom_starlette_routes
        if getattr(route, "path", None) == _HEALTHZ_PATH
    ]
    assert len(matches) == 1


def test_stateless_flag_flips_singleton_setting(_http_singleton_settings):
    from cairn.mcp_server.server import _build_http_app

    _build_http_app(host="127.0.0.1", port=0, api_key=None, stateless=True)
    assert _http_singleton_settings.settings.stateless_http is True
    _build_http_app(host="127.0.0.1", port=0, api_key=None, stateless=False)
    assert _http_singleton_settings.settings.stateless_http is False


# --- /healthz payload --------------------------------------------------------


def test_healthz_payload_shape_on_reachable_store(tmp_path, monkeypatch):
    from cairn.mcp_server._server_core import healthz_payload

    monkeypatch.setenv("CAIRN_DB", _scratch_store(tmp_path))
    monkeypatch.setenv("CAIRN_READ_ONLY", "1")
    payload = healthz_payload()
    assert set(payload) == {"status", "store_reachable", "read_only", "degradations"}
    assert payload["status"] == "ok"
    assert payload["store_reachable"] is True
    assert payload["read_only"] is True
    assert isinstance(payload["degradations"], int) and payload["degradations"] >= 0


def test_healthz_payload_unhealthy_when_store_missing(tmp_path, monkeypatch):
    from cairn.mcp_server._server_core import healthz_payload

    missing = tmp_path / "gone" / "store.db"
    missing.parent.mkdir()
    monkeypatch.setenv("CAIRN_DB", str(missing))
    monkeypatch.setenv("CAIRN_READ_ONLY", "1")
    payload = healthz_payload()
    assert set(payload) == {"status", "store_reachable", "read_only", "degradations"}
    assert payload["status"] == "unhealthy"
    assert payload["store_reachable"] is False


# --- serve CLI flag wiring ---------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({}, {"transport": "stdio", "port": None, "read_only": "0", "stateless": False}),
        ({"port": 8000}, {"transport": "sse", "port": 8000, "read_only": "1", "stateless": False}),
        ({"transport": "http"}, {"transport": "http", "port": 9876, "read_only": "1", "stateless": False}),
        ({"transport": "http", "port": 1234}, {"transport": "http", "port": 1234, "read_only": "1", "stateless": False}),
        ({"transport": "stdio", "port": 8000}, {"transport": "stdio", "port": 8000, "read_only": "1", "stateless": False}),
        ({"stateless": True}, {"transport": "stdio", "port": None, "read_only": "0", "stateless": True}),
    ],
)
def test_serve_foreground_maps_flags_to_run(monkeypatch, kwargs, expected):
    from cairn.cli import serve as serve_cli

    captured = {}
    monkeypatch.setattr("cairn.mcp_server.server.run", lambda **kw: captured.update(kw))
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)

    serve_cli._serve_foreground(
        db=None,
        port=kwargs.get("port"),
        read_only=kwargs.get("read_only"),
        transport=kwargs.get("transport"),
        host=kwargs.get("host"),
        api_key=kwargs.get("api_key"),
        stateless=kwargs.get("stateless", False),
    )

    assert captured["transport"] == expected["transport"]
    assert captured["port"] == expected["port"]
    assert captured["stateless"] == expected["stateless"]
    assert captured["host"] == kwargs.get("host")
    assert os.environ["CAIRN_READ_ONLY"] == expected["read_only"]


def test_serve_foreground_resolves_api_key_flag_over_env(monkeypatch):
    from cairn.cli import serve as serve_cli

    captured = {}
    monkeypatch.setattr("cairn.mcp_server.server.run", lambda **kw: captured.update(kw))

    monkeypatch.setenv("CAIRN_MCP_API_KEY", "env-key")
    serve_cli._serve_foreground(
        db=None, port=None, read_only=None, transport="http",
        host=None, api_key="flag-key", stateless=False,
    )
    assert captured["api_key"] == "flag-key"

    serve_cli._serve_foreground(
        db=None, port=None, read_only=None, transport="http",
        host=None, api_key=None, stateless=False,
    )
    assert captured["api_key"] == "env-key"

    monkeypatch.delenv("CAIRN_MCP_API_KEY")
    serve_cli._serve_foreground(
        db=None, port=None, read_only=None, transport="http",
        host=None, api_key=None, stateless=False,
    )
    assert captured["api_key"] is None


# --- Live HTTP servers (subprocess boots) ------------------------------------


def test_keyed_loopback_rejects_unauthenticated_and_serves_keyed(tmp_path):
    """Keyed loopback: 401 before store access, forged Host 421, valid key serves."""
    db_path = _scratch_store(tmp_path)
    with _http_server(db_path, host="127.0.0.1", api_key=_TEST_KEY) as (port, proc, log_path):
        url = f"http://127.0.0.1:{port}{_STREAMABLE_HTTP_PATH}"
        health_url = f"http://127.0.0.1:{port}/healthz"

        status, _, body = _http_get(health_url)
        assert status == 200, "/healthz must answer unauthenticated"
        payload = json.loads(body)
        assert set(payload) == {"status", "store_reachable", "read_only", "degradations"}
        assert payload["status"] == "ok"
        assert payload["store_reachable"] is True

        status, _, _ = _http_post(url, _INITIALIZE, _auth_headers(None))
        assert status == 401, "missing bearer must be rejected"
        status, _, _ = _http_post(url, _INITIALIZE, _auth_headers("wrong-key"))
        assert status == 401, "wrong bearer must be rejected"
        # the auth gate is outermost: it answers before Host validation
        status, _, _ = _http_post(url, _INITIALIZE, {**_auth_headers(None), "Host": "evil.com"})
        assert status == 401
        # loopback bind: Host-header protection rejects a forged Host despite a valid key
        status, _, _ = _http_post(url, _INITIALIZE, {**_auth_headers(_TEST_KEY), "Host": "evil.com"})
        assert status == 421

        status, headers, _ = _http_post(
            url, _INITIALIZE, {**_auth_headers(_TEST_KEY), "Host": f"127.0.0.1:{port}"}
        )
        assert status == 200
        session_id = headers.get("mcp-session-id")
        assert session_id, "stateful initialize must issue a session id"
        _http_post(
            url,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {**_auth_headers(_TEST_KEY), "mcp-session-id": session_id},
        )
        status, _, body = _http_post(
            url, _TOOLS_LIST, {**_auth_headers(_TEST_KEY), "mcp-session-id": session_id}
        )
        assert status == 200
        assert any(m.get("id") == 2 and "result" in m for m in _rpc_messages(body))

    log_text = log_path.read_text()
    assert "listening on" in log_text and str(port) in log_text


def test_keyed_wildcard_stateless_serves_session_less_requests(tmp_path):
    """Wildcard bind: Host protection off (key is the gate); --stateless drops session affinity."""
    db_path = _scratch_store(tmp_path)
    with _http_server(db_path, host="0.0.0.0", api_key=_TEST_KEY, stateless=True) as (
        port, proc, log_path
    ):
        url = f"http://127.0.0.1:{port}{_STREAMABLE_HTTP_PATH}"

        status, headers, _ = _http_post(
            url, _INITIALIZE, {**_auth_headers(_TEST_KEY), "Host": "evil.com:1234"}
        )
        assert status == 200, "wildcard bind must accept any Host when the key is valid"

        status, _, body = _http_post(url, _TOOLS_LIST, _auth_headers(_TEST_KEY))
        assert status == 200, "session-less tools/list must succeed in stateless mode"
        results = [m for m in _rpc_messages(body) if m.get("id") == 2 and "result" in m]
        assert results and results[0]["result"]["tools"]

        status, _, _ = _http_post(url, _INITIALIZE, _auth_headers(_TEST_KEY))
        assert status == 200, "sequential session-less initialize must succeed independently"


def test_unkeyed_non_loopback_refuses_to_start_before_binding(tmp_path):
    db_path = _scratch_store(tmp_path)
    port = _free_port()
    proc = subprocess.run(
        [sys.executable, "-c", _BOOT_HTTP, str(port), "0.0.0.0", "", "0"],
        capture_output=True,
        text=True,
        env=_child_env(db_path, read_only=True),
        timeout=90,
        check=False,
    )
    assert proc.returncode == 1
    assert "refusing to serve HTTP" in proc.stderr
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0, "refusal must precede binding"
