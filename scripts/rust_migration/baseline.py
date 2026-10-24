#!/usr/bin/env python3
"""Baseline harness measuring the python or rust engine for the rust-migration comparison (P0-2)."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path

# Allow running from a source checkout without installing (same pattern as
# scripts/mint_baselines.py). Guarded insert so repeated imports never grow
# sys.path.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

SCHEMA = "cairn-rust-baseline/1"
PROTOCOL_VERSION = "2025-03-26"
TOOL_COUNT = 26
WAL_POLL_S = 0.005
RSS_POLL_S = 0.2
HEALTHZ_TIMEOUT_S = 180.0
HTTP_TIMEOUT_S = 120.0

# Dep-free, deterministic pins (the same doctrine as cairn bench suites).
ENV_PINS = {"CAIRN_EMBED_BACKEND": "hash", "CAIRN_RERANK": "0"}

_BOOT = (
    "import sys; from cairn.mcp_server.server import run; "
    "run(port=int(sys.argv[1]), host='127.0.0.1', api_key=None, stateless=False)"
)

RUST_BIN = REPO_ROOT / "rust" / "target" / "release" / "cairn"


def ensure_rust_binary() -> Path:
    """The release cairn binary, built when missing."""
    if RUST_BIN.exists():
        return RUST_BIN
    cargo = shutil.which("cargo") or Path.home() / ".cargo" / "bin" / "cargo"
    _note("building rust cairn binary (release)")
    subprocess.run(
        [str(cargo), "build", "--release", "-p", "cairn-cli"],
        cwd=str(REPO_ROOT / "rust"),
        check=True,
    )
    return RUST_BIN


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the artifact channel."""
    print(f"[baseline] {msg}", file=sys.stderr, flush=True)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _git(*args: str, cwd: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


# --- corpus identity --------------------------------------------------------


def corpus_identity(workspace: Path) -> dict:
    """Path-identity block for the measured corpus: git pin when real, tree hash when not."""
    from cairn.bench.datasource import tree_hash

    ident: dict = {"workspace": str(workspace)}
    if (workspace / ".git").exists():
        ident["identity_method"] = "git_commit"
        ident["git_commit"] = _git("rev-parse", "HEAD", cwd=str(workspace))
        dirty = _git("status", "--porcelain", cwd=str(workspace))
        ident["git_dirty_entries"] = len(
            [ln for ln in (dirty or "").splitlines() if ln.strip()]
        )
    else:
        ident["identity_method"] = "tree_hash"
        ident["tree_hash"] = tree_hash(workspace)
    return ident


def stage_corpus(workspace: Path, scratch: Path) -> Path:
    """Corpora without a .git marker are copied to scratch and given the marker; real repos build in place."""
    if (workspace / ".git").exists():
        return workspace
    staged = scratch / "corpus"
    shutil.copytree(workspace, staged)
    (staged / ".git").mkdir(exist_ok=True)
    return staged


# --- build / derived indexes / embed ---------------------------------------


def run_build(corpus_ws: Path, db_path: Path) -> dict:
    """One full `cairn build` shape: build_graph wall time + dataflow index."""
    from cairn.graph.builder import build_graph
    from cairn.graph.dataflow import build_dataflow_index, _public_symbols
    from cairn.graph.schema import get_db

    t0 = time.perf_counter()
    summary = build_graph(workspace=str(corpus_ws), db_path=str(db_path), verbose=False)
    build_s = time.perf_counter() - t0

    conn = get_db(str(db_path))
    try:
        t0 = time.perf_counter()
        pub = len(_public_symbols(conn))
        df_count = build_dataflow_index(conn) if pub else 0
        df_s = time.perf_counter() - t0
    finally:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.close()

    return {
        "seconds": round(build_s, 3),
        "dataflow_seconds": round(df_s, 3),
        "dataflow_symbols": df_count,
        "repos": summary.get("repos"),
        "files": summary.get("files"),
        "symbols": summary.get("symbols"),
        "edges": summary.get("edges"),
        "imports": summary.get("imports"),
        "skipped": summary.get("skipped"),
        "resolution": summary.get("resolution"),
        "lsp_upgraded": (summary.get("lsp") or {}).get("upgraded"),
        "scip_edges": (summary.get("scip") or {}).get("edges"),
        "parse_errors": summary.get("parse_errors"),
    }


def run_closure(db_path: Path) -> dict:
    """Time build_transitive_closure while sampling the WAL sidecar for its peak."""
    from cairn.graph.dataflow import build_transitive_closure
    from cairn.graph.schema import get_db

    wal_path = Path(str(db_path) + "-wal")
    peak = {"bytes": 0}
    stop = threading.Event()

    def _sample() -> None:
        while not stop.is_set():
            try:
                size = wal_path.stat().st_size
            except OSError:
                size = 0
            if size > peak["bytes"]:
                peak["bytes"] = size
            stop.wait(WAL_POLL_S)

    conn = get_db(str(db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        sampler = threading.Thread(target=_sample, name="wal-sampler", daemon=True)
        sampler.start()
        t0 = time.perf_counter()
        rows = build_transitive_closure(conn)
        seconds = time.perf_counter() - t0
        stop.set()
        sampler.join()
        # A single transaction spills to the WAL in one burst at commit (the
        # 256 MB page cache holds it whole), so the post-commit sidecar size is
        # the floor for the true peak; the sampler catches multi-spill builds.
        wal_end = wal_path.stat().st_size if wal_path.exists() else 0
        sampled_peak = peak["bytes"]
        peak = max(sampled_peak, wal_end)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        db_bytes = Path(str(db_path)).stat().st_size
    finally:
        stop.set()
        conn.close()
    return {
        "seconds": round(seconds, 3),
        "rows": rows,
        "peak_wal_bytes": peak,
        "sampled_peak_wal_bytes": sampled_peak,
        "wal_end_bytes": wal_end,
        "db_bytes": db_bytes,
    }


def run_embed(db_path: Path) -> dict:
    """Embed the built graph with the pinned hash backend so semantic_search measures a real path."""
    from cairn.graph import embeddings as emb
    from cairn.graph.schema import get_db

    emb.reset_backend_cache()
    conn = get_db(str(db_path))
    try:
        t0 = time.perf_counter()
        result = emb.embed_all(conn, reap_orphans=False, multivector=False)
        seconds = time.perf_counter() - t0
    finally:
        conn.close()
    return {
        "seconds": round(seconds, 3),
        "backend_effective": emb._effective_backend(),
        "embedded": result.get("embedded"),
        "total": result.get("total"),
    }


# --- daemon + MCP client ----------------------------------------------------


def _child_env(scratch: Path, corpus_ws: Path, db_path: Path) -> dict:
    env = dict(os.environ)
    env.update(ENV_PINS)
    env["CAIRN_HOME"] = str(scratch / "home")
    env["CAIRN_DB"] = str(db_path)
    env["CAIRN_KNOWLEDGE"] = str(scratch / "home" / ".knowledge")
    env["CAIRN_WORKSPACE"] = str(corpus_ws)
    for key in ("CAIRN_READ_ONLY", "CAIRN_MCP_API_KEY", "PYTEST_CURRENT_TEST"):
        env.pop(key, None)
    return env


def seed_knowledge(scratch: Path, source_bundle: Path | None) -> int:
    """Copy the live knowledge bundle into the scratch home; returns seeded file count."""
    target = scratch / "home" / ".knowledge"
    target.mkdir(parents=True, exist_ok=True)
    if source_bundle is None or not source_bundle.is_dir():
        return 0
    count = 0
    for item in source_bundle.rglob("*"):
        if item.is_dir() or item.name == ".DS_Store":
            continue
        dst = target / item.relative_to(source_bundle)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dst)
        count += 1
    return count


class Daemon:
    """One daemon child bound to the scratch store: the python server or `cairn serve run`."""

    def __init__(
        self,
        scratch: Path,
        corpus_ws: Path,
        db_path: Path,
        port: int,
        impl: str = "python",
    ):
        self.port = port
        self.url = f"http://127.0.0.1:{port}/mcp"
        if impl == "rust":
            cmd = [
                str(ensure_rust_binary()),
                "serve",
                "run",
                "--db",
                str(db_path),
                "--port",
                str(port),
                "--host",
                "127.0.0.1",
            ]
        else:
            cmd = [sys.executable, "-c", _BOOT, str(port)]
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=_child_env(scratch, corpus_ws, db_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.boot_seconds: float | None = None

    def wait_ready(self) -> bool:
        healthz = f"http://127.0.0.1:{self.port}/healthz"
        deadline = time.monotonic() + HEALTHZ_TIMEOUT_S
        t0 = time.perf_counter()
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(healthz, timeout=2) as resp:
                    if resp.status == 200:
                        self.boot_seconds = round(time.perf_counter() - t0, 3)
                        return True
            except urllib.error.HTTPError:
                time.sleep(0.1)
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(0.25)
        return False

    def sample_rss_kb(self) -> int | None:
        try:
            out = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(self.proc.pid)],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None
        return int(out.split()[0]) if out else None

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


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


# --- latency measurement ----------------------------------------------------


def _stats(samples_ms: list[float]) -> dict:
    if not samples_ms:
        return {"calls": 0, "errors": 0}
    ordered = sorted(samples_ms)

    def pct(fraction: float) -> float:
        rank = max(1, -(-int(len(ordered) * fraction * 100) // 100))
        return round(ordered[min(rank, len(ordered)) - 1], 3)

    return {
        "calls": len(ordered),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "mean_ms": round(sum(ordered) / len(ordered), 3),
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
        "samples_ms": [round(s, 3) for s in samples_ms],
    }


def tool_script(targets: dict) -> list[dict]:
    """The fixed 26-tool query script; write tools carry untimed setup calls."""

    def _probe_doc(kind: str, i: object) -> tuple[str, dict]:
        return "knowledge_add", {
            "title": f"baseline probe {targets['label']} {kind} {i}",
            "body": "rust-migration baseline probe document",
            "doc_type": "decision",
        }

    def _extract_doc_id(text: str) -> str:
        match = re.search(r"Stored knowledge document: (\S+)", text)
        if not match:
            raise RuntimeError(f"knowledge_add setup returned no concept id: {text!r}")
        return match.group(1)

    return [
        {
            "tool": "find_definition",
            "args": lambda i: {"name": targets["sym"], "limit": 5},
        },
        {
            "tool": "get_callers",
            "args": lambda i: {"name": targets["sym"], "limit": 50},
        },
        {
            "tool": "get_callees",
            "args": lambda i: {"name": targets["sym"], "limit": 50},
        },
        {
            "tool": "impact_analysis",
            "args": lambda i: {"name": targets["hot"], "depth": 3, "limit": 100},
        },
        {
            "tool": "path",
            "args": lambda i: {
                "from_pattern": targets["path_from"],
                "to_pattern": targets["path_to"],
                "max_depth": 4,
            },
        },
        {"tool": "explore", "args": lambda i: {"query": targets["sym"]}},
        {
            "tool": "semantic_search",
            "args": lambda i: {"query": targets["query"], "limit": 10},
        },
        {
            "tool": "search_symbols",
            "args": lambda i: {"pattern": targets["sym"][:4] + "*"},
        },
        {"tool": "repo_map", "args": lambda i: {}},
        {
            "tool": "file_api",
            "args": lambda i: {"path": targets["file_path"], "repo": targets["repo"]},
        },
        {"tool": "cross_repo_deps", "args": lambda i: {"repo": targets["repo"]}},
        {
            "tool": "visualize_graph",
            "args": lambda i: {"scope": "symbol", "symbol": targets["hot"], "depth": 2},
        },
        {"tool": "get_compass", "args": lambda i: {"module": targets["module"]}},
        {
            "tool": "search_knowledge",
            "args": lambda i: {"query": targets["query"], "limit": 10},
        },
        {"tool": "ask_compass", "args": lambda i: {"query": targets["query"]}},
        {"tool": "trace_flow", "args": lambda i: {"entry": targets["hot"]}},
        {"tool": "generate_flow", "args": lambda i: {"entry": targets["hot"]}},
        {
            "tool": "federated_search",
            "args": lambda i: {"query": targets["query"], "limit": 20},
        },
        {
            "tool": "knowledge_search",
            "args": lambda i: {"query": targets["query"], "limit": 20},
        },
        {"tool": "trace_workflow", "args": lambda i: {"ref": targets["workflow_ref"]}},
        {"tool": "recall_memory", "args": lambda i: {"query": targets["hot"]}},
        {
            "tool": "record_memory",
            "args": lambda i: {
                "type": "pattern",
                "title": f"baseline probe {targets['label']} rec {i}",
                "body": "rust-migration baseline probe memory",
            },
        },
        {
            "tool": "wiki_generate",
            "args": lambda i: {"repo": targets["repo"], "pages": 5, "force": True},
        },
        {"tool": "knowledge_add", "args": lambda i: _probe_doc("add", i)[1]},
        {
            "tool": "knowledge_delete",
            "setup": lambda i: _probe_doc("del", i),
            "args": lambda i: {},
            "needs_setup_id": True,
            "extract_id": _extract_doc_id,
        },
        {
            "tool": "knowledge_status",
            "setup": lambda i: _probe_doc("stat", i),
            "args": lambda i: {"new_status": "superseded"},
            "needs_setup_id": True,
            "extract_id": _extract_doc_id,
        },
    ]


def measure_tools(client: McpClient, targets: dict, repeats: int) -> list[dict]:
    """Warmup + repeats per tool; write tools re-create their fixture untimed each iteration."""
    script = tool_script(targets)
    results = []

    def _invoke(entry: dict, i: int) -> tuple[bool, str, dict]:
        args = entry["args"](i)
        if entry.get("needs_setup_id"):
            setup_tool, setup_args = entry["setup"](i)
            ok, text = client.call(setup_tool, setup_args)
            if ok:
                raise RuntimeError(f"untimed setup {setup_tool} failed: {text}")
            args = {**args, "doc_id": entry["extract_id"](text)}
        is_error, text = client.call(entry["tool"], args)
        return is_error, text, args

    for entry in script:
        samples: list[float] = []
        errors: list[str] = []
        # Warmup (untimed) proves the tool answers before timing starts.
        ok, text, args_i = _invoke(entry, 0)
        if ok:
            errors.append(f"warmup: {text}")
        else:
            for i in range(1, repeats + 1):
                t0 = time.perf_counter()
                is_error, err_text, args_i = _invoke(entry, i)
                samples.append((time.perf_counter() - t0) * 1000)
                if is_error:
                    errors.append(err_text)
        stats = _stats(samples)
        results.append(
            {
                "tool": entry["tool"],
                "args": args_i
                if not entry.get("needs_setup_id")
                else {k: v for k, v in args_i.items() if k != "doc_id"},
                "errors": errors[:3],
                **stats,
            }
        )
        _note(
            f"  {entry['tool']}: p95 {stats.get('p95_ms')}ms"
            f" ({stats.get('calls', 0)}/{repeats} ok, {len(errors)} err)"
        )
    return results


def choose_targets(db_path: Path, corpus_ws: Path, knowledge_dir: Path) -> dict:
    """Probe the built store (and bundle) for real query targets."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        sym_row = conn.execute(
            "SELECT name FROM symbols WHERE name != '' ORDER BY id LIMIT 1"
        ).fetchone()
        hot_row = conn.execute(
            """
            SELECT e.target_name AS name, COUNT(*) AS fanin
            FROM edges e
            WHERE e.target_name IS NOT NULL AND e.target_name != ''
            GROUP BY e.target_name ORDER BY fanin DESC LIMIT 1
            """
        ).fetchone()
        edge_row = conn.execute(
            """
            SELECT s1.name AS src, s2.name AS dst
            FROM edges e
            JOIN symbols s1 ON e.source_id = s1.id
            JOIN symbols s2 ON e.target_id = s2.id
            LIMIT 1
            """
        ).fetchone()
        file_row = conn.execute(
            "SELECT path FROM files ORDER BY path LIMIT 1"
        ).fetchone()
        repo_row = conn.execute("SELECT id FROM repos LIMIT 1").fetchone()
    finally:
        conn.close()
    compass_modules = (
        sorted(p.stem for p in knowledge_dir.glob("compass/*.md"))
        if knowledge_dir.is_dir()
        else []
    )
    return {
        "sym": sym_row["name"] if sym_row else "main",
        "hot": (hot_row["name"] if hot_row else None)
        or (sym_row["name"] if sym_row else "main"),
        "path_from": edge_row["src"] if edge_row else "main",
        "path_to": edge_row["dst"] if edge_row else "main",
        "file_path": file_row["path"] if file_row else ".",
        "repo": repo_row["id"] if repo_row else (corpus_ws.name or "cairn"),
        "module": compass_modules[0] if compass_modules else "graph",
        "query": "how does the build resolve call edges",
        "workflow_ref": "nonexistent-baseline-probe",
        "label": corpus_ws.name or "corpus",
    }


# --- orchestration ----------------------------------------------------------


def run_baseline(
    workspace: Path, label: str, repeats: int, keep_scratch: bool, impl: str = "python"
) -> dict:
    """One corpus measurement: build -> dataflow -> closure(+WAL) -> embed -> daemon latency + RSS."""
    from cairn.bench.corpus import corpus_stats
    from cairn.bench.datasource import build_artifact_stamp
    from cairn import paths as cairn_paths

    live_store = cairn_paths.resolve_store()
    live_bundle = live_store.knowledge if live_store.knowledge.is_dir() else None

    scratch = Path(tempfile.mkdtemp(prefix=f"cairn-rust-baseline-{label}-"))
    (scratch / "home").mkdir(parents=True, exist_ok=True)
    _note(f"scratch {scratch}")
    try:
        corpus_ws = stage_corpus(workspace, scratch)
        db_path = scratch / "store.kg"
        seeded_files = seed_knowledge(
            scratch, live_bundle if corpus_ws == workspace else None
        )

        saved = {
            k: os.environ.get(k)
            for k in (
                *ENV_PINS,
                "CAIRN_HOME",
                "CAIRN_DB",
                "CAIRN_KNOWLEDGE",
                "CAIRN_WORKSPACE",
            )
        }
        os.environ.update(ENV_PINS)
        os.environ["CAIRN_HOME"] = str(scratch / "home")
        os.environ["CAIRN_DB"] = str(db_path)
        os.environ["CAIRN_KNOWLEDGE"] = str(scratch / "home" / ".knowledge")
        os.environ["CAIRN_WORKSPACE"] = str(corpus_ws)
        try:
            _note("building graph (build_graph + dataflow)...")
            build = run_build(corpus_ws, db_path)
            _note(f"build {build['seconds']}s, dataflow {build['dataflow_seconds']}s")
            _note("transitive closure (+ WAL sampling)...")
            closure = run_closure(db_path)
            _note(
                f"closure {closure['seconds']}s, peak WAL {closure['peak_wal_bytes']} B"
            )
            _note("embed (hash)...")
            embed = run_embed(db_path)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        targets = choose_targets(db_path, corpus_ws, scratch / "home" / ".knowledge")
        port = _free_port()
        daemon = Daemon(scratch, corpus_ws, db_path, port, impl=impl)
        rss = {"peak_kb": 0, "final_kb": None}
        stop = threading.Event()
        sampler: threading.Thread | None = None

        def _rss_loop() -> None:
            while not stop.is_set():
                kb = daemon.sample_rss_kb()
                if kb and kb > rss["peak_kb"]:
                    rss["peak_kb"] = kb
                stop.wait(RSS_POLL_S)

        try:
            if not daemon.wait_ready():
                out = daemon.proc.stdout.read() if daemon.proc.stdout else ""
                raise RuntimeError(
                    f"daemon never became healthy on :{port}: {out[-2000:]}"
                )
            sampler = threading.Thread(
                target=_rss_loop, name="rss-sampler", daemon=True
            )
            sampler.start()
            client = McpClient(daemon.url)
            catalog = client.start()
            if len(catalog) != TOOL_COUNT:
                raise RuntimeError(
                    f"tools/list returned {len(catalog)} tools, expected {TOOL_COUNT}"
                )
            _note(
                f"daemon up (boot {daemon.boot_seconds}s, {len(catalog)} tools); measuring..."
            )
            tools = measure_tools(client, targets, repeats)
        finally:
            stop.set()
            rss["final_kb"] = daemon.sample_rss_kb()
            daemon.stop()
        if sampler is not None:
            sampler.join()
        # The sampler polls at 200ms; a short run can end between ticks, so the
        # final reading floors the peak (never under-report the gate metric).
        rss["peak_kb"] = max(rss["peak_kb"], rss["final_kb"] or 0)
        identity = corpus_identity(workspace)
        stats = corpus_stats(corpus_ws)
        live_ref = (
            _live_store_reference(live_store.db) if live_store.db.exists() else None
        )
        return {
            "schema": SCHEMA,
            "suite": "rust-migration-baseline",
            "impl": impl,
            "label": label,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "harness": "scripts/rust_migration/baseline.py",
            "reproduce": (
                f".venv/bin/python scripts/rust_migration/baseline.py"
                f"{'' if impl == 'python' else f' --impl {impl}'}"
                f" --workspace {workspace} --label {label} --repeats {repeats}"
            ),
            **build_artifact_stamp(),
            "method": {
                "build": "build_graph(workspace, db) + build_dataflow_index (the `cairn build` shape)",
                "closure": (
                    f"build_transitive_closure(conn) timed; WAL sidecar polled every "
                    f"{WAL_POLL_S}s, with the post-commit sidecar size as the "
                    "single-burst floor for the peak"
                ),
                "latency": (
                    f"per-tool tools/call over streamable HTTP (keep-alive), "
                    f"{repeats} timed calls each after 1 untimed warmup; write tools "
                    "re-create their fixture untimed per call"
                ),
                "rss": f"ps -o rss= on the daemon pid every {RSS_POLL_S}s",
                "env_pins": dict(ENV_PINS),
                "daemon": {
                    "transport": "streamable-http",
                    "stateless": False,
                    "host": "127.0.0.1",
                },
                "knowledge_bundle_seeded_files": seeded_files,
                **(
                    {
                        "store": (
                            "python build_graph + dataflow + hash embed "
                            "(identical-stores doctrine); the rust daemon is "
                            "the measured engine"
                        )
                    }
                    if impl == "rust"
                    else {}
                ),
            },
            "corpus": {
                **identity,
                "py_files": stats["files"],
                "py_lines": stats["lines"],
                "py_bytes": stats["bytes"],
            },
            "build": build,
            "closure": closure,
            "embed": embed,
            "daemon": {"impl": impl, "boot_seconds": daemon.boot_seconds, "rss": rss},
            "tools": tools,
            "live_store_reference": live_ref,
        }
    finally:
        if keep_scratch:
            _note(f"scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


def _live_store_reference(db: Path) -> dict:
    """Row counts + size of the live `cairn config --db` store, for reference."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {
            "db_path": str(db),
            "db_bytes": db.stat().st_size,
            "symbols": conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
            "edges": conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
            "transitive_edges": conn.execute(
                "SELECT COUNT(*) FROM transitive_edges"
            ).fetchone()[0],
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        default=None,
        help="Corpus workspace (default: the registered cairn self-store workspace).",
    )
    parser.add_argument(
        "--label", default=None, help="Artifact label (default: workspace dir name)."
    )
    parser.add_argument(
        "--repeats", type=int, default=20, help="Timed calls per tool (default 20)."
    )
    parser.add_argument(
        "--out", default=None, help="Artifact JSON path (default: stdout)."
    )
    parser.add_argument(
        "--impl",
        choices=["python", "rust"],
        default="python",
        help="Engine measured by the daemon latency + RSS legs (default python).",
    )
    parser.add_argument(
        "--keep-scratch",
        action="store_true",
        help="Keep the scratch store for inspection.",
    )
    args = parser.parse_args(argv)

    from cairn.paths import resolve_store

    workspace = (
        Path(args.workspace).resolve() if args.workspace else resolve_store().workspace
    )
    label = args.label or (workspace.name or "cairn")
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")

    payload = run_baseline(
        workspace, label, args.repeats, args.keep_scratch, impl=args.impl
    )
    text = json.dumps(payload, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        _note(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
