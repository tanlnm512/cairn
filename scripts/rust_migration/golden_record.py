#!/usr/bin/env python3
"""Golden recorder capturing the PYTHON MCP surface as rust-parity fixtures (P0-3)."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "src"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import baseline  # reuse: build/embed/targets/client helpers + env pins
from cairn.mcp_server import lifecycle  # reuse: mcp_url + healthz probes

SCHEMA = "cairn-rust-golden-mcp/1"
DEFAULT_OUT_DIR = REPO_ROOT / "rust" / "golden" / "mcp"
HEALTHZ_TIMEOUT_S = 180.0
# CAIRN_WATCH=0 keeps the live watcher from reindexing the workspace mid-capture.
ENV_PINS = {**baseline.ENV_PINS, "CAIRN_WATCH": "0"}
_POP_KEYS = (
    "CAIRN_READ_ONLY", "CAIRN_MCP_API_KEY", "PYTEST_CURRENT_TEST",
    "CAIRN_DB", "CAIRN_KNOWLEDGE",
)

_BOOT = (
    "import sys; from cairn.mcp_server.server import run; "
    "run(port=int(sys.argv[1]), host='127.0.0.1', api_key=sys.argv[2], stateless=False)"
)


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the artifact channel."""
    print(f"[golden] {msg}", file=sys.stderr, flush=True)


# --- daemon + client --------------------------------------------------------


class GoldenDaemon:
    """Dedicated keyed `cairn serve` child on a free loopback port."""

    def __init__(self, env: dict, port: int, api_key: str):
        self.port = port
        self.url = lifecycle.mcp_url(port)
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _BOOT, str(port), api_key],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def wait_ready(self) -> bool:
        deadline = time.monotonic() + HEALTHZ_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return False
            if lifecycle.healthz_responds(
                self.port, lifecycle.DEFAULT_HOST, timeout=2.0
            ):
                return True
            time.sleep(0.25)
        return False

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


class GoldenClient(baseline.McpClient):
    """McpClient with bearer auth and full-fidelity result capture."""

    def __init__(self, url: str, api_key: str):
        super().__init__(url)
        self.api_key = api_key

    def _post(self, payload: dict) -> tuple[list[dict], object]:
        if self.conn is None:
            self.conn = HTTPConnection(self.host, timeout=baseline.HTTP_TIMEOUT_S)
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


def seed_concepts(knowledge_dir: Path) -> None:
    """Fixture-native bundle docs so read tools capture found-paths deterministically."""
    from cairn.knowledge.store import add_document
    from cairn.okf.bundle import OKFBundle
    from cairn.okf.concept import OKFConcept

    bundle = OKFBundle(str(knowledge_dir))
    add_document(
        bundle,
        title="golden capture rollback rule",
        body="Rollbacks must replay the golden MCP fixtures before promotion.",
        doc_type="business-rule",
        affects_repos=["cairn"],
    )
    add_document(
        bundle,
        title="golden capture workflow",
        body="Capture the MCP goldens, then diff every tool response.",
        doc_type="workflow",
        steps=[
            {
                "name": "Record",
                "description": "Run the golden recorder against the Python daemon.",
                "symbol": "main",
                "file": "scripts/rust_migration/golden_record.py",
            },
            {
                "name": "Compare",
                "description": "Diff each response against the fixtures.",
                "symbol": "verify",
                "file": "scripts/rust_migration/golden_record.py",
            },
        ],
    )
    bundle.write_concept(OKFConcept(
        type="Compass-Guide",
        title="golden capture compass",
        description="Navigation guide for the golden capture corpus.",
        concept_id="compass/golden-capture",
        resource="scripts/rust_migration",
        tags=["golden"],
        body="Recorder entry: `main` in `scripts/rust_migration/golden_record.py`.\n",
    ))


def refine_targets(db_path: Path, targets: dict) -> dict:
    """Replace rowid-dependent probes with name-ordered, tie-broken picks (the
    parse pool makes symbol rowids nondeterministic across builds)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        hot = conn.execute(
            """
            SELECT s.name AS name, COUNT(*) AS fanin
            FROM edges e
            JOIN symbols s ON e.target_id = s.id
            WHERE e.target_id IS NOT NULL
              AND s.kind IN ('function', 'method')
              AND s.id IN (
                  SELECT DISTINCT source_id FROM edges WHERE source_id IS NOT NULL
              )
            GROUP BY s.id ORDER BY fanin DESC, s.name ASC LIMIT 1
            """
        ).fetchone()
        sym = conn.execute(
            "SELECT name FROM symbols WHERE name != '' ORDER BY name LIMIT 1"
        ).fetchone()
        edge = conn.execute(
            """
            SELECT s1.name AS src, s2.name AS dst
            FROM edges e
            JOIN symbols s1 ON e.source_id = s1.id
            JOIN symbols s2 ON e.target_id = s2.id
            ORDER BY src, dst LIMIT 1
            """
        ).fetchone()
        viz = conn.execute(
            """
            SELECT s.name AS name FROM symbols s
            WHERE s.kind IN ('function', 'method')
              AND (SELECT COUNT(*) FROM symbols s2 WHERE s2.name = s.name) = 1
              AND (SELECT COUNT(*) FROM edges e WHERE e.target_id = s.id) BETWEEN 1 AND 30
              AND (SELECT COUNT(*) FROM edges e WHERE e.source_id = s.id) BETWEEN 1 AND 30
            ORDER BY s.name LIMIT 1
            """
        ).fetchone()
    finally:
        conn.close()
    if hot and hot["name"]:
        targets["hot"] = hot["name"]
    if sym:
        targets["sym"] = sym["name"]
    if edge:
        targets["path_from"] = edge["src"]
        targets["path_to"] = edge["dst"]
    if viz and viz["name"]:
        targets["viz_sym"] = viz["name"]
    targets.setdefault("doc_query", "golden capture rollback")
    return targets


# --- fixed query script -----------------------------------------------------


def workflow_ref(knowledge_dir: Path) -> str:
    """First seeded workflow concept id, else a not-found probe ref."""
    wf = sorted((knowledge_dir / "knowledge" / "workflow").glob("*.md"))
    if wf:
        return f"knowledge/workflow/{wf[0].stem}"
    return "nonexistent-golden-probe"


def query_script(t: dict, wf_ref: str) -> list[dict]:
    """The fixed 26-tool capture script over store-probed targets."""

    def _probe_setup(kind: str) -> dict:
        return {
            "tool": "knowledge_add",
            "args": {
                "title": f"golden mcp capture {kind} probe",
                "body": "golden capture probe document",
                "doc_type": "decision",
            },
            "id_regex": r"Stored knowledge document: (\S+)",
        }

    return [
        {"tool": "find_definition", "args": {"name": t["sym"], "limit": 5}},
        {"tool": "get_callers", "args": {"name": t["hot"], "limit": 20}},
        {"tool": "get_callees", "args": {"name": t["path_from"], "limit": 20}},
        {"tool": "impact_analysis",
         "args": {"name": t["hot"], "depth": 3, "limit": 100}},
        {"tool": "path",
         "args": {"from_pattern": t["path_from"], "to_pattern": t["path_to"],
                  "max_depth": 4}},
        {"tool": "explore", "args": {"query": t["sym"]}},
        {"tool": "semantic_search", "args": {"query": t["query"], "limit": 10}},
        {"tool": "search_symbols", "args": {"pattern": t["sym"][:4] + "*"}},
        {"tool": "repo_map", "args": {}},
        {"tool": "file_api", "args": {"path": t["file_path"], "repo": t["repo"]}},
        {"tool": "cross_repo_deps", "args": {"repo": t["repo"]}},
        {"tool": "visualize_graph",
         "args": {"scope": "symbol", "symbol": t["viz_sym"], "depth": 2},
         "sort_lines": True},
        {"tool": "get_compass", "args": {"module": t["module"]}},
        {"tool": "search_knowledge", "args": {"query": t["doc_query"], "limit": 10}},
        {"tool": "ask_compass", "args": {"query": t["query"]},
         "sort_lines": True, "mask_list_lines": True},
        {"tool": "trace_flow", "args": {"entry": t["hot"]}},
        {"tool": "generate_flow", "args": {"entry": t["hot"]}},
        {"tool": "federated_search", "args": {"query": t["query"], "limit": 20}},
        {"tool": "knowledge_search", "args": {"query": t["doc_query"], "limit": 20}},
        {"tool": "trace_workflow", "args": {"ref": wf_ref}},
        {"tool": "recall_memory", "args": {"query": t["hot"]}},
        {"tool": "record_memory",
         "args": {"type": "pattern",
                  "title": "golden mcp capture parity probe",
                  "body": "Why: exercise the record path during golden capture.\n"
                          "How to apply: regenerated by the golden recorder."}},
        {"tool": "wiki_generate",
         "args": {"repo": t["repo"], "pages": 5, "force": True}},
        {"tool": "knowledge_add",
         "args": {"title": "golden mcp capture add probe",
                  "body": "golden capture probe document",
                  "doc_type": "decision"}},
        {"tool": "knowledge_delete", "setup": _probe_setup("delete"), "args": {}},
        {"tool": "knowledge_status",
         "setup": _probe_setup("status"),
         "args": {"new_status": "superseded"}},
    ]


def drive(client: GoldenClient, entries: list[dict]) -> dict:
    """Run the script against the daemon; one captured call per tool."""
    catalog = client.start()
    if len(catalog) != baseline.TOOL_COUNT:
        raise RuntimeError(
            f"tools/list returned {len(catalog)} tools, expected {baseline.TOOL_COUNT}"
        )
    names = {e["tool"] for e in entries}
    if names != set(catalog):
        raise RuntimeError(
            f"script/catalog drift: script-only={sorted(names - set(catalog))} "
            f"catalog-only={sorted(set(catalog) - names)}"
        )
    out = {}
    for entry in entries:
        args = dict(entry["args"])
        if "setup" in entry:
            setup = entry["setup"]
            ok, text = client.call(setup["tool"], setup["args"])
            if ok:
                raise RuntimeError(f"setup {setup['tool']} failed: {text}")
            m = re.search(setup["id_regex"], text)
            if not m:
                raise RuntimeError(
                    f"setup {setup['tool']} returned no concept id: {text!r}"
                )
            args["doc_id"] = m.group(1)
        result = client.capture(entry["tool"], args)
        out[entry["tool"]] = {"args": args, "response": result}
        n_chars = sum(len(c.get("text", "")) for c in result["content"])
        _note(f"  {entry['tool']}: isError={result['isError']} chars={n_chars}")
    return out


# --- capture orchestration --------------------------------------------------


def seed_bundle(source: Path | None, target: Path) -> int:
    """Copy the live knowledge bundle into the scratch store; returns file count."""
    target.mkdir(parents=True, exist_ok=True)
    if source is None or not source.is_dir():
        return 0
    count = 0
    for item in source.rglob("*"):
        if item.is_dir() or item.name == ".DS_Store":
            continue
        dst = target / item.relative_to(source)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dst)
        count += 1
    return count


def store_counts(db_path: Path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("repos", "files", "symbols", "edges")
        }
    finally:
        conn.close()


def daemon_env(scratch: Path, corpus_ws: Path) -> dict:
    env = dict(os.environ)
    env.update(ENV_PINS)
    env["CAIRN_HOME"] = str(scratch / "home")
    env["CAIRN_WORKSPACE"] = str(corpus_ws)
    for key in _POP_KEYS:
        env.pop(key, None)
    return env


def run_capture(workspace: Path, keep_scratch: bool) -> tuple[dict, dict]:
    """Build the self-store in a scratch home, boot a keyed daemon, capture all 26 tools.

    Returns (per-tool captures with normalization meta, capture meta).
    """
    import cairn.paths as cairn_paths
    from cairn.paths import register_workspace, resolve_store

    live_store = resolve_store(workspace)
    scratch = Path(tempfile.mkdtemp(prefix="cairn-rust-golden-"))
    home = scratch / "home"
    home.mkdir(parents=True, exist_ok=True)
    _note(f"scratch {scratch}")
    try:
        corpus_ws = baseline.stage_corpus(workspace, scratch)
        saved = {k: os.environ.get(k) for k in (*ENV_PINS, "CAIRN_HOME",
                                                 "CAIRN_WORKSPACE", *_POP_KEYS)}
        os.environ.update(ENV_PINS)
        os.environ["CAIRN_HOME"] = str(home)
        os.environ["CAIRN_WORKSPACE"] = str(corpus_ws)
        # paths.py binds CAIRN_HOME/REGISTRY_FILE/CONFIG_FILE at import: patch
        # the attributes the same way the test suite does.
        cairn_paths.CAIRN_HOME = home
        cairn_paths.REGISTRY_FILE = home / "workspaces.json"
        cairn_paths.CONFIG_FILE = home / "config.json"
        try:
            store = register_workspace(corpus_ws)
            seeded = seed_bundle(live_store.knowledge, store.knowledge)
            seed_concepts(store.knowledge)
            _note(f"building graph into {store.db} ...")
            build = baseline.run_build(corpus_ws, store.db)
            _note(f"build {build['seconds']}s; embedding (hash) ...")
            baseline.run_embed(store.db)
            targets = refine_targets(
                store.db, baseline.choose_targets(store.db, corpus_ws, store.knowledge)
            )
            targets.setdefault("doc_query", "golden capture rollback")
        finally:
            cairn_paths.CAIRN_HOME = Path(saved["CAIRN_HOME"] or str(Path.home() / ".cairn"))
            cairn_paths.REGISTRY_FILE = cairn_paths.CAIRN_HOME / "workspaces.json"
            cairn_paths.CONFIG_FILE = cairn_paths.CAIRN_HOME / "config.json"
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        api_key = secrets.token_urlsafe(24)
        port = baseline._free_port()
        daemon = GoldenDaemon(daemon_env(scratch, corpus_ws), port, api_key)
        try:
            if not daemon.wait_ready():
                out = daemon.proc.stdout.read() if daemon.proc.stdout else ""
                raise RuntimeError(
                    f"daemon never became healthy on :{port}: {out[-2000:]}"
                )
            _note(f"daemon up on :{port} (keyed); capturing ...")
            client = GoldenClient(daemon.url, api_key)
            entries = query_script(targets, workflow_ref(store.knowledge))
            captures = drive(client, entries)
        finally:
            daemon.stop()

        rules = scrub_rules(store.home, home, corpus_ws)
        meta = {
            "impl": "python",
            "harness": "scripts/rust_migration/golden_record.py",
            "corpus": normalize_obj(baseline.corpus_identity(corpus_ws), rules),
            "store": store_counts(store.db),
            "env_pins": dict(ENV_PINS),
            "knowledge_bundle_seeded_files": seeded,
        }
        for tool, cap in captures.items():
            entry = next(e for e in entries if e["tool"] == tool)
            sort_lines = bool(entry.get("sort_lines"))
            mask_lists = bool(entry.get("mask_list_lines"))
            cap["fixture"] = {
                "schema": SCHEMA,
                "tool": tool,
                "arguments": normalize_obj(cap["args"], rules),
                "capture": {**meta, "captured_at": datetime.now(timezone.utc).isoformat()},
                "normalization": {
                    "rules": [name for name, _p, _r in rules],
                    "sort_lines": sort_lines,
                    "mask_list_lines": mask_lists,
                },
                "response": normalize_response(cap["response"], rules,
                                               sort_lines, mask_lists),
            }
        return captures, meta
    finally:
        if keep_scratch:
            _note(f"scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


# --- fixture IO + verify ----------------------------------------------------


def compare_scope(fixture: dict) -> dict:
    """The fixture fields the parity oracle compares (capture meta excluded)."""
    return {k: fixture[k] for k in ("schema", "tool", "arguments",
                                    "normalization", "response")}


def write_fixtures(out_dir: Path, captures: dict) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for tool, cap in sorted(captures.items()):
        path = out_dir / f"{tool}.json"
        path.write_text(
            json.dumps(cap["fixture"], indent=2) + "\n", encoding="utf-8"
        )
        written.append(path)
    return written


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


def verify(out_dir: Path, captures: dict) -> list[str]:
    failures = []
    for tool in sorted(captures):
        path = out_dir / f"{tool}.json"
        if not path.exists():
            failures.append(f"{tool}: missing fixture {path}")
            continue
        golden = json.loads(path.read_text(encoding="utf-8"))
        d = _diff_path(compare_scope(golden), compare_scope(captures[tool]["fixture"]))
        if d:
            failures.append(f"{tool}: {d}")
        else:
            _note(f"  {tool}: OK")
    for path in sorted(out_dir.glob("*.json")):
        if path.stem not in captures:
            failures.append(f"{path.name}: fixture without a script entry")
    return failures


# --- entry point ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true",
                        help="Capture and (re)write the golden fixtures.")
    parser.add_argument("--workspace", default=None,
                        help="Corpus workspace (default: the cairn self-store workspace).")
    parser.add_argument("--out-dir", default=None,
                        help=f"Fixture directory (default: {DEFAULT_OUT_DIR}).")
    parser.add_argument("--keep-scratch", action="store_true",
                        help="Keep the scratch store for inspection.")
    args = parser.parse_args(argv)

    from cairn.paths import resolve_store

    workspace = (Path(args.workspace).resolve() if args.workspace
                 else resolve_store().workspace)
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR

    captures, meta = run_capture(workspace, args.keep_scratch)
    if args.record:
        written = write_fixtures(out_dir, captures)
        _note(f"wrote {len(written)} fixture(s) under {out_dir}")
        print(json.dumps({"recorded": len(written), "out_dir": str(out_dir),
                          "store": meta["store"]}, indent=2))
        return 0

    failures = verify(out_dir, captures)
    if failures:
        for f in failures:
            print(f"DIFF {f}")
        print(f"{len(captures) - len(failures)}/{len(captures)} tools golden-equal")
        return 1
    print(f"{len(captures)}/{len(captures)} tools golden-equal against {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
