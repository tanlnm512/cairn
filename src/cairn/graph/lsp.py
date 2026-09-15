"""Optional pyright upgrade pass for ambiguous Python call edges."""

from __future__ import annotations

import json
import os
import queue
import shutil
import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname


class PyrightUnavailable(RuntimeError):
    pass


class JsonRpcTimeout(TimeoutError):
    pass


_AUTO_TRANSPORT = object()


class PyrightStdioTransport:
    def __init__(self, workspace: str, request_timeout: float = 10.0):
        executable = shutil.which("pyright")
        if executable is None:
            raise PyrightUnavailable("pyright is not on PATH")
        self.request_timeout = request_timeout
        self._process = subprocess.Popen(
            [executable, "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._next_id = 1
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._reader = threading.Thread(
            target=self._read_responses, name="cairn-pyright-reader", daemon=True
        )
        self._reader.start()
        self._workspace = Path(workspace).resolve()

    def _read_responses(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            return
        while True:
            headers: dict[str, str] = {}
            while True:
                line = stdout.readline()
                if not line:
                    return
                if line in (b"\r\n", b"\n"):
                    break
                name, sep, value = line.decode("ascii", "replace").partition(":")
                if sep:
                    headers[name.strip().lower()] = value.strip()
            try:
                length = int(headers["content-length"])
                body = stdout.read(length)
                message = json.loads(body.decode("utf-8"))
            except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            request_id = message.get("id")
            if isinstance(request_id, int):
                with self._lock:
                    pending = self._pending.get(request_id)
                if pending is not None:
                    pending.put(message)

    def _send(self, method: str, params: Any, request_id: int | None) -> None:
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        if request_id is not None:
            message["id"] = request_id
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        frame = (
            f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        )
        stdin = self._process.stdin
        if stdin is None:
            raise RuntimeError("pyright stdin is closed")
        try:
            stdin.write(frame)
            stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise RuntimeError("pyright exited before accepting a request") from error

    def request(self, method: str, params: Any) -> Any:
        with self._lock:
            if self._process.poll() is not None:
                raise RuntimeError("pyright exited before responding")
            request_id = self._next_id
            self._next_id += 1
            responses: queue.Queue[dict[str, Any]] = queue.Queue()
            self._pending[request_id] = responses
        try:
            self._send(method, params, request_id)
            message = responses.get(timeout=self.request_timeout)
        except queue.Empty as error:
            raise JsonRpcTimeout(f"pyright did not answer {method}") from error
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        if "error" in message:
            rpc_error = message["error"]
            raise RuntimeError(f"pyright {method} failed: {rpc_error}")
        return message.get("result")

    def notify(self, method: str, params: Any) -> None:
        self._send(method, params, None)

    def initialize(self) -> None:
        params = {
            "processId": os.getpid(),
            "rootUri": self._workspace.as_uri(),
            "workspaceFolders": [
                {"uri": self._workspace.as_uri(), "name": self._workspace.name}
            ],
            "capabilities": {},
        }
        result = self.request("initialize", params)
        if not isinstance(result, dict):
            raise RuntimeError("pyright returned an invalid initialize result")
        self.notify("initialized", {})

    def shutdown(self) -> None:
        self.request("shutdown", None)
        self.notify("exit", None)

    def close(self) -> None:
        process = self._process
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
        except Exception:
            pass
        finally:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            self._reader.join(timeout=1)


def _stored_path(workspace: str, repo_path: str, stored_path: str) -> Path:
    root = Path(workspace).resolve()
    path = Path(stored_path)
    if path.is_absolute():
        return path.resolve()
    repo = Path(repo_path)
    if not repo.is_absolute():
        repo = root / repo
    return (repo / path).resolve()


def _location_path(location: dict[str, Any]) -> Path | None:
    uri = location.get("uri") or location.get("targetUri")
    if not isinstance(uri, str):
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return Path(unquote(url2pathname(parsed.path))).resolve()


def _location_start(location: dict[str, Any]) -> dict[str, Any] | None:
    source_range = (
        location.get("targetSelectionRange")
        or location.get("targetRange")
        or location.get("range")
    )
    if not isinstance(source_range, dict):
        return None
    start = source_range.get("start")
    return start if isinstance(start, dict) else None


def _symbol_index(conn, workspace: str) -> dict[Path, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT s.id, s.line_start, s.line_end, f.path AS file_path, r.path AS repo_path
        FROM symbols AS s
        JOIN files AS f ON f.id = s.file_id
        JOIN repos AS r ON r.id = f.repo_id
        WHERE f.language = 'python'
        """
    ).fetchall()
    index: dict[Path, list[dict[str, Any]]] = {}
    for row in rows:
        path = _stored_path(workspace, row["repo_path"], row["file_path"])
        index.setdefault(path, []).append(dict(row))
    return index


def _ambiguous_edges(conn) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT e.id, e.line, e.column, f.path AS file_path, r.path AS repo_path
        FROM edges AS e
        JOIN symbols AS s ON s.id = e.source_id
        JOIN files AS f ON f.id = s.file_id
        JOIN repos AS r ON r.id = f.repo_id
        WHERE e.resolution = 'ambiguous'
          AND e.kind = 'calls'
          AND e.target_id IS NULL
          AND f.language = 'python'
        ORDER BY e.line, e.column, e.id
        """
    ).fetchall()


def _unique_symbol(
    symbols: list[dict[str, Any]] | None, start: dict[str, Any]
) -> str | None:
    try:
        definition_line = int(start["line"])
    except (KeyError, TypeError, ValueError):
        return None
    graph_line = definition_line + 1
    candidates = [
        symbol
        for symbol in symbols or []
        if symbol["line_start"] is not None
        and symbol["line_end"] is not None
        and symbol["line_start"] <= graph_line <= symbol["line_end"]
    ]
    if not candidates:
        return None
    smallest_span = min(
        symbol["line_end"] - symbol["line_start"] for symbol in candidates
    )
    innermost = [
        symbol
        for symbol in candidates
        if symbol["line_end"] - symbol["line_start"] == smallest_span
    ]
    return innermost[0]["id"] if len(innermost) == 1 else None


def _open_document(transport: Any, path: Path, opened: set[Path]) -> bool:
    if path in opened:
        return True
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    transport.notify(
        "textDocument/didOpen",
        {
            "textDocument": {
                "uri": path.as_uri(),
                "languageId": "python",
                "version": 1,
                "text": text,
            }
        },
    )
    opened.add(path)
    return True


def upgrade_ambiguous_edges(
    conn,
    workspace: str,
    *,
    transport: Any = _AUTO_TRANSPORT,
    request_timeout: float = 10.0,
) -> dict[str, Any]:
    """Upgrade uniquely resolvable ambiguous Python calls without downgrades."""
    edges = _ambiguous_edges(conn)
    if transport is None:
        return {
            "considered": len(edges),
            "upgraded": 0,
            "notices": ["pyright unavailable: language-server pass skipped"],
        }
    if transport is _AUTO_TRANSPORT:
        try:
            transport = PyrightStdioTransport(
                workspace, request_timeout=request_timeout
            )
        except Exception as error:
            return {
                "considered": len(edges),
                "upgraded": 0,
                "notices": [f"pyright unavailable: {error}; pass skipped"],
            }

    conn.execute("SAVEPOINT cairn_lsp_upgrade")
    upgraded = 0
    try:
        transport.initialize()
        symbols = _symbol_index(conn, workspace)
        opened: set[Path] = set()
        for edge in edges:
            source_path = _stored_path(
                workspace, edge["repo_path"], edge["file_path"]
            )
            if edge["line"] is None or edge["line"] < 1 or not _open_document(
                transport, source_path, opened
            ):
                continue
            character = edge["column"] if edge["column"] is not None else 0
            result = transport.request(
                "textDocument/definition",
                {
                    "textDocument": {"uri": source_path.as_uri()},
                    "position": {
                        "line": int(edge["line"]) - 1,
                        "character": int(character),
                    },
                },
            )
            locations = result if isinstance(result, list) else [result]
            locations = [
                location for location in locations if isinstance(location, dict)
            ]
            if len(locations) != 1:
                continue
            location_path = _location_path(locations[0])
            start = _location_start(locations[0])
            if location_path is None or start is None:
                continue
            target_id = _unique_symbol(symbols.get(location_path), start)
            if target_id is None:
                continue
            updated = conn.execute(
                """
                UPDATE edges
                   SET target_id = ?, target_name = NULL, resolution = 'exact'
                 WHERE id = ? AND resolution = 'ambiguous' AND target_id IS NULL
                """,
                (target_id, edge["id"]),
            ).rowcount
            upgraded += int(updated)
        transport.shutdown()
    except Exception as error:
        conn.execute("ROLLBACK TO cairn_lsp_upgrade")
        conn.execute("RELEASE cairn_lsp_upgrade")
        return {
            "considered": len(edges),
            "upgraded": 0,
            "notices": [f"pyright pass failed: {error}; changes rolled back"],
        }
    finally:
        transport.close()

    conn.execute("RELEASE cairn_lsp_upgrade")
    return {"considered": len(edges), "upgraded": upgraded, "notices": []}
