#!/usr/bin/env python3
"""Fixture schema + normalization library shared by the rust-migration oracles."""

from __future__ import annotations

import http.client
import json
import os
import re
import sys
from http.client import HTTPConnection
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(Path(__file__).resolve().parent),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCHEMA = "cairn-rust-golden-mcp/1"
DEFAULT_OUT_DIR = REPO_ROOT / "rust" / "golden" / "mcp"
HTTP_TIMEOUT_S = 120.0
PROTOCOL_VERSION = "2025-03-26"
# CAIRN_WATCH=0 keeps the live watcher from reindexing the workspace mid-capture.
ENV_PINS = {"CAIRN_EMBED_BACKEND": "hash", "CAIRN_RERANK": "0", "CAIRN_WATCH": "0"}
_POP_KEYS = (
    "CAIRN_READ_ONLY", "CAIRN_MCP_API_KEY", "PYTEST_CURRENT_TEST",
    "CAIRN_DB", "CAIRN_KNOWLEDGE",
)


# --- client -------------------------------------------------------------------


class McpClient:
    """Streamable-HTTP MCP session: initialize -> initialized -> tools/call over keep-alive."""

    def __init__(self, url: str):
        self.url = url
        self.host, self.port_path = url[len("http://") :].split("/", 1)
        self.path = "/" + self.port_path
        self.conn: HTTPConnection | None = None
        self.session_id: str | None = None
        self._id = 0

    def _post(self, payload: dict) -> tuple[dict, object]:
        if self.conn is None:
            self.conn = HTTPConnection(self.host, timeout=HTTP_TIMEOUT_S)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        body = json.dumps(payload)
        self.conn.request("POST", self.path, body=body, headers=headers)
        resp = self.conn.getresponse()
        raw = resp.read().decode()
        messages = self._parse_messages(raw)
        return messages, resp

    @staticmethod
    def _parse_messages(raw: str) -> list[dict]:
        raw = raw.strip()
        if raw.startswith("{"):
            return [json.loads(raw)]
        # rmcp inserts empty SSE keepalive data frames; python never does.
        return [
            json.loads(payload)
            for line in raw.splitlines()
            if line.startswith("data:")
            for payload in (line[len("data:") :].strip(),)
            if payload
        ]

    def _reconnect(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except OSError:
                pass
        self.conn = None

    def _rpc(self, payload: dict) -> list[dict]:
        for attempt in (1, 2):
            try:
                messages, _resp = self._post(payload)
                return messages
            except (OSError, http.client.HTTPException):
                if attempt == 2:
                    raise
                self._reconnect()
        raise OSError("unreachable")

    def start(self) -> list[str]:
        """Initialize the session and return the tools/list catalog."""
        self._id = 0
        messages, resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "rust-migration-baseline", "version": "0"},
                },
            }
        )
        self.session_id = resp.getheader("mcp-session-id")
        if not self.session_id:
            raise RuntimeError("initialize carried no mcp-session-id")
        self._rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listing = self._rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        return [t["name"] for t in listing[0]["result"]["tools"]]

    def call(self, tool: str, args: dict) -> tuple[bool, str]:
        """One tools/call; returns (is_error, first_content_text_snippet)."""
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
        messages = self._rpc(payload)
        msg = messages[0] if messages else {}
        if "error" in msg:
            return True, str(msg["error"])[:200]
        result = msg.get("result") or {}
        content = result.get("content") or []
        text = content[0].get("text", "") if content else ""
        return bool(result.get("isError")), text[:200]


class GoldenClient(McpClient):
    """McpClient with bearer auth and full-fidelity result capture."""

    def __init__(self, url: str, api_key: str):
        super().__init__(url)
        self.api_key = api_key

    def _post(self, payload: dict) -> tuple[list[dict], object]:
        if self.conn is None:
            self.conn = HTTPConnection(self.host, timeout=HTTP_TIMEOUT_S)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.api_key}",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        body = json.dumps(payload)
        self.conn.request("POST", self.path, body=body, headers=headers)
        resp = self.conn.getresponse()
        raw = resp.read().decode()
        return self._parse_messages(raw), resp

    def capture(self, tool: str, args: dict) -> dict:
        """One tools/call; the full result block (content + structuredContent)."""
        self._id += 1
        messages = self._rpc({
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        })
        msg = messages[0] if messages else {}
        if "error" in msg:
            return {
                "isError": True,
                "content": [{
                    "type": "text",
                    "text": json.dumps(msg["error"], sort_keys=True),
                }],
                "structuredContent": None,
            }
        result = msg.get("result") or {}
        return {
            "isError": bool(result.get("isError")),
            "content": result.get("content") or [],
            "structuredContent": result.get("structuredContent"),
        }


# --- normalization ----------------------------------------------------------


def _round_float(m: re.Match) -> str:
    return f"{float(m.group()):.4f}"


def scrub_rules(store_home: Path, home: Path, workspace: Path) -> list[tuple]:
    """Ordered (name, pattern, replacement); longest-path rules first, each in
    unresolved + realpath variants (macOS /private symlink prefix)."""
    variants = [("store_home", store_home, "<STORE_HOME>"),
                ("home", home, "<CAIRN_HOME>"),
                ("workspace", workspace, "<WORKSPACE>")]
    resolved: list[tuple] = []
    bases: list[tuple] = []
    for name, base, placeholder in variants:
        rp = os.path.realpath(base)
        if rp != str(base):
            resolved.append((f"{name}_resolved", re.compile(re.escape(rp)),
                             placeholder))
        bases.append((name, re.compile(re.escape(str(base))), placeholder))
    resolved.sort(key=lambda r: -len(r[1].pattern))
    bases.sort(key=lambda r: -len(r[1].pattern))
    rules: list[tuple] = resolved + bases + [
        ("iso_ts", re.compile(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
        ), "<ISO>"),
        ("epoch", re.compile(r"\b1[0-9]{9}\.\d+\b"), "<EPOCH>"),
        ("date", re.compile(r"\d{4}-\d{2}-\d{2}"), "<DATE>"),
        ("duration", re.compile(r"\b\d+\.\d+ ?m?s\b|\b\d+ ?ms\b"), "<DUR>"),
        ("age", re.compile(r"\bjust now\b|\b\d+[smhd] old\b"), "<AGE>"),
        ("id12", re.compile(r"\b[0-9a-f]{12}\b"), "<ID>"),
        ("id6", re.compile(r"(?<=[-_.])[0-9a-f]{6}\b"), "<ID6>"),
        ("float", re.compile(r"\b\d+\.\d{5,}\b"), _round_float),
    ]
    return rules


_LIST_LINE = re.compile(r"^(\s*[\w ]+:) \[(.*)\]\s*$")


def _mask_list_line(m: re.Match) -> str:
    body = m.group(2).strip()
    n = len(body.split("', '")) if body else 0
    return f"{m.group(1)} [{n} members]"


def normalize_text(text: str, rules: list[tuple], sort_lines: bool = False,
                   mask_list_lines: bool = False) -> str:
    for _name, pattern, repl in rules:
        text = pattern.sub(repl, text)
    if mask_list_lines:
        text = "\n".join(
            _LIST_LINE.sub(_mask_list_line, line) if _LIST_LINE.match(line) else line
            for line in text.splitlines()
        )
    if sort_lines:
        text = "\n".join(sorted(text.splitlines()))
    return text


def normalize_obj(obj, rules: list[tuple], sort_lines: bool = False,
                  mask_list_lines: bool = False):
    if isinstance(obj, str):
        return normalize_text(obj, rules, sort_lines, mask_list_lines)
    if isinstance(obj, list):
        return [normalize_obj(v, rules, sort_lines, mask_list_lines)
                for v in obj]
    if isinstance(obj, dict):
        return {k: normalize_obj(v, rules, sort_lines, mask_list_lines)
                for k, v in obj.items()}
    return obj


def normalize_response(response: dict, rules: list[tuple], sort_lines: bool,
                       mask_list_lines: bool = False) -> dict:
    content = []
    for item in response.get("content") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            item = {**item, "text": normalize_text(item["text"], rules,
                                                   sort_lines, mask_list_lines)}
        content.append(item)
    return {
        "isError": response.get("isError"),
        "content": content,
        "structuredContent": normalize_obj(
            response.get("structuredContent"), rules, sort_lines, mask_list_lines
        ),
    }


# --- fixture shape -------------------------------------------------------------


def replay_block(entry: dict, args: dict, rules: list, seq: int | None = None) -> dict:
    """The exact replay arguments for one tool, stored as the additive `replay` key."""
    block_args = {k: v for k, v in args.items() if k != "doc_id"}
    block: dict = {"args": normalize_obj(block_args, rules)}
    if "setup" in entry:
        block["setup"] = {
            "tool": entry["setup"]["tool"],
            "args": normalize_obj(entry["setup"]["args"], rules),
            "id_regex": entry["setup"]["id_regex"],
        }
    if seq is not None:
        block["seq"] = seq
    return block


def compare_scope(fixture: dict) -> dict:
    """The fixture fields the parity oracle compares (capture meta excluded)."""
    return {k: fixture[k] for k in ("schema", "tool", "arguments",
                                    "normalization", "response")}


def _diff_path(expected, actual, path="$") -> str | None:
    if type(expected) is not type(actual):
        return f"{path}: type {type(expected).__name__} != {type(actual).__name__}"
    if isinstance(expected, dict):
        for k in sorted(set(expected) | set(actual)):
            if k not in expected:
                return f"{path}.{k}: missing in golden"
            if k not in actual:
                return f"{path}.{k}: missing in capture"
            d = _diff_path(expected[k], actual[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}: len {len(expected)} != {len(actual)}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            d = _diff_path(e, a, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if expected == actual else f"{path}: {expected!r} != {actual!r}"
