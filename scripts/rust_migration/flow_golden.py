#!/usr/bin/env python3
"""Regenerate the flow-family parity fixtures (explore, trace_flow, taint, visualize_graph, cross_repo_deps).

Builds the checked-in deterministic corpus (rust/golden/flow/corpus) and store
(rust/golden/flow/flow-store.kg), captures the live Python tools, and writes
fixtures with the golden_record.py schema. Deterministic: run twice, bytes must
not change.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "src"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import golden_record as gr

FLOW_DIR = REPO_ROOT / "rust" / "golden" / "flow"
MCP_DIR = REPO_ROOT / "rust" / "golden" / "mcp"
CORPUS_DIR = FLOW_DIR / "corpus"
STORE_PATH = FLOW_DIR / "flow-store.kg"
FLOW_TOOLS = ("explore", "trace_flow", "cross_repo_deps", "visualize_graph")

# CAIRN_EMBED_BACKEND=hash with no embed run keeps explore lexical-only and the
# degradation footnote empty (explicit backend = informed choice).
ENV_PINS = {
    "CAIRN_EMBED_BACKEND": "hash",
    "CAIRN_RERANK": "0",
    "CAIRN_WATCH": "0",
    "CAIRN_FUSION": "1",
}

CORPUS_FILES = {
    "cairn.json": '{"repo_namespaces": {"flow.demo": "demo-sdk"}}\n',
    "app/main.py": '''"""Demo entry points."""
from app.cycle_a import a, recurse
from app.pipeline import run_pipeline
from app.service import fetch_user, save_user


def main():
    user = fetch_user(1)
    save_user(user)
    return user


def kickoff():
    a()
    run_pipeline()
    recurse(3)
''',
    "app/service.py": '''"""User service."""
import json


def fetch_user(uid):
    raw = load_payload(uid)
    return json.loads(raw)


def load_payload(uid):
    with open(f"u{uid}.json") as fh:
        return fh.read()


def save_user(user):
    conn = connect()
    conn.cursor().execute("INSERT", user)


def connect():
    from app.store import get_db
    return get_db("app.db")
''',
    "app/store.py": '''"""Storage helpers."""


def get_db(path):
    return Db(path)


class Db:
    def cursor(self):
        return Cur()

    def commit(self):
        pass


class Cur:
    def execute(self, sql, *params):
        return None
''',
    "app/pipeline.py": '''"""Pipeline: config read flows into query execution."""
from app.store import get_db


def read_config():
    with open("cfg.json") as fh:
        cfg = fh.read()
    dispatch(cfg)
    return cfg


def dispatch(cfg):
    run_query(cfg)


def run_query(cfg):
    db = get_db("pipeline.db")
    db.cursor().execute(cfg)


def run_pipeline():
    dispatch(read_config())
''',
    "app/page.py": '''"""Page assembly with a split renderer."""


def render_text(x):
    return str(x)


def render(x):
    return str(x)


def render(x):
    return repr(x)


def page(x):
    return render(x)
''',
    "app/cycle_a.py": '''"""Cycle and recursion demos."""
from app.cycle_b import b


def a():
    return b()


def recurse(n):
    if n <= 0:
        return 0
    return recurse(n - 1)
''',
    "app/cycle_b.py": '''"""Cycle half."""
from app.cycle_a import a


def b():
    return a()
''',
    "app/integrations.py": '''"""External namespace import for cross-repo mapping."""
import flow.demo.sdk as sdk


def use_sdk():
    return sdk.client()
''',
    "tests/test_service.py": '''"""Service tests."""
from app.service import fetch_user


def test_fetch_user():
    assert fetch_user(1) is not None
''',
}


def write_corpus() -> None:
    if CORPUS_DIR.exists():
        shutil.rmtree(CORPUS_DIR)
    for rel, body in sorted(CORPUS_FILES.items()):
        path = CORPUS_DIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (CORPUS_DIR / ".git").mkdir(exist_ok=True)


def build_store() -> dict:
    from cairn.graph.builder import build_graph
    from cairn.graph.dataflow import build_dataflow_index
    from cairn.graph.schema import get_db

    for suffix in ("", "-wal", "-shm"):
        p = Path(str(STORE_PATH) + suffix)
        if p.exists():
            p.unlink()
    summary = build_graph(workspace=str(CORPUS_DIR), db_path=str(STORE_PATH), verbose=False)
    conn = get_db(str(STORE_PATH))
    try:
        build_dataflow_index(conn)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("repos", "files", "symbols", "edges", "imports", "transitive_edges")
        }
    finally:
        conn.close()
    for suffix in ("-wal", "-shm", ".build.lock"):
        p = Path(str(STORE_PATH) + suffix)
        if p.exists():
            p.unlink()
    return {"build": {k: summary.get(k) for k in ("repos", "files", "symbols", "edges")},
            "tables": counts}


def tool_env(knowledge_dir: Path) -> dict:
    env = dict(os.environ)
    env.update(ENV_PINS)
    for key in gr._POP_KEYS:
        env.pop(key, None)
    env["CAIRN_DB"] = str(STORE_PATH)
    env["CAIRN_KNOWLEDGE"] = str(knowledge_dir)
    env["CAIRN_WORKSPACE"] = str(CORPUS_DIR)
    return env


def capture(env: dict) -> dict:
    """Capture the five tools from the live Python implementations."""
    from cairn.graph.taint import build_registry, find_paths, format_taint_warning
    from cairn.mcp_server.tools_compass import trace_flow as trace_flow_tool
    from cairn.mcp_server.tools_graph import (
        cross_repo_deps as cross_repo_deps_tool,
        explore as explore_tool,
        visualize_graph as visualize_graph_tool,
    )

    os.environ.clear()
    os.environ.update(env)
    Path(env["CAIRN_KNOWLEDGE"]).mkdir(parents=True, exist_ok=True)

    explore_cases = [
        {"arguments": {"query": "fetch_user"}, "text": explore_tool("fetch_user")},
        {"arguments": {"query": "read_config"}, "text": explore_tool("read_config")},
        {"arguments": {"query": "render"}, "text": explore_tool("render")},
    ]
    captures: dict[str, dict] = {}
    captures["explore"] = {
        "arguments": explore_cases[0]["arguments"],
        "text": explore_cases[0]["text"],
        "cases": explore_cases,
    }
    trace_cases = [
        {"arguments": {"entry": "kickoff"}, "text": trace_flow_tool("kickoff")},
        {"arguments": {"entry": "main", "max_depth": 1},
         "text": trace_flow_tool("main", max_depth=1)},
        {"arguments": {"entry": "recurse"}, "text": trace_flow_tool("recurse")},
    ]
    captures["trace_flow"] = {
        "arguments": trace_cases[0]["arguments"],
        "text": trace_cases[0]["text"],
        "cases": trace_cases,
    }
    xdep_cases = [
        {"arguments": {"repo": "corpus"}, "text": cross_repo_deps_tool("corpus")},
        {"arguments": {"repo": "be-sdk"}, "text": cross_repo_deps_tool("be-sdk")},
    ]
    captures["cross_repo_deps"] = {
        "arguments": xdep_cases[0]["arguments"],
        "text": xdep_cases[0]["text"],
        "cases": xdep_cases,
    }
    # Line order is not part of the mermaid/dot drawing contract, so those
    # captures sort lines; the machine-readable JSON format must not.
    viz_cases = [
        {"arguments": {"scope": "symbol", "symbol": "fetch_user", "depth": 2},
         "text": visualize_graph_tool(scope="symbol", symbol="fetch_user", depth=2),
         "sort_lines": True},
        {"arguments": {"scope": "symbol", "symbol": "fetch_user", "depth": 2, "format": "dot"},
         "text": visualize_graph_tool(scope="symbol", symbol="fetch_user", depth=2, format="dot"),
         "sort_lines": True},
        {"arguments": {"scope": "symbol", "symbol": "fetch_user", "depth": 2, "format": "json"},
         "text": visualize_graph_tool(scope="symbol", symbol="fetch_user", depth=2, format="json"),
         "sort_lines": False},
        {"arguments": {"scope": "impact", "symbol": "a", "depth": 3},
         "text": visualize_graph_tool(scope="impact", symbol="a", depth=3),
         "sort_lines": True},
        {"arguments": {"scope": "module", "module": "pipeline"},
         "text": visualize_graph_tool(scope="module", module="pipeline"),
         "sort_lines": True},
        {"arguments": {"scope": "repo", "repo": "corpus"},
         "text": visualize_graph_tool(scope="repo", repo="corpus"),
         "sort_lines": True},
        {"arguments": {"scope": "deps"},
         "text": visualize_graph_tool(scope="deps"),
         "sort_lines": True},
        {"arguments": {"scope": "bogus"},
         "text": visualize_graph_tool(scope="bogus"),
         "sort_lines": False},
    ]
    captures["visualize_graph"] = {
        "arguments": viz_cases[0]["arguments"],
        "text": viz_cases[0]["text"],
        "cases": viz_cases,
    }

    registry = build_registry()
    from cairn.graph.schema import get_db

    conn = get_db(str(STORE_PATH))
    try:
        taint_paths = find_paths(conn, registry, "file-read", "sql")
    finally:
        conn.close()
    assert taint_paths, "corpus lost its source->sink taint path"
    captures["taint"] = {
        "arguments": {"from": "file-read", "to": "sql"},
        "text": format_taint_warning(taint_paths),
        "cases": [],
    }
    return captures


def fixture_entry(tool: str, cap: dict, rules, sort_lines: bool) -> dict:
    meta = {
        "impl": "python",
        "harness": "scripts/rust_migration/flow_golden.py",
        "corpus": {
            "workspace": "rust/golden/flow/corpus",
            "identity_method": "checked-in-corpus",
        },
        "store": "rust/golden/flow/flow-store.kg",
        "env_pins": dict(ENV_PINS),
    }
    response = {
        "isError": False,
        "content": [{"type": "text", "text": cap["text"]}],
        "structuredContent": {"result": cap["text"]},
    }
    cases = [
        {
            "arguments": gr.normalize_obj(case["arguments"], rules),
            "sort_lines": case.get("sort_lines", sort_lines),
            "response": gr.normalize_response(
                {
                    "isError": False,
                    "content": [{"type": "text", "text": case["text"]}],
                    "structuredContent": {"result": case["text"]},
                },
                rules,
                case.get("sort_lines", sort_lines),
            ),
        }
        for case in cap["cases"]
    ]
    return {
        "schema": gr.SCHEMA,
        "tool": tool,
        "arguments": gr.normalize_obj(cap["arguments"], rules),
        "capture": {**meta, "captured_at": datetime.now(timezone.utc).isoformat()},
        "normalization": {
            "rules": [name for name, _p, _r in rules],
            "sort_lines": sort_lines,
            "mask_list_lines": False,
        },
        "response": gr.normalize_response(response, rules, sort_lines),
        "cases": cases,
    }


# sort_lines normalizes the mermaid/dot renderer output; the graph contents
# are order-stable, only the drawn line order is not part of the contract.
SORT_LINES_TOOLS = {"visualize_graph"}
TAINT_SORT_LINES = False


def compare_scope(entry: dict) -> dict:
    """Fixture fields the byte-parity check compares (capture meta excluded)."""
    return {k: entry[k] for k in ("schema", "tool", "arguments",
                                  "normalization", "response", "cases")}


def rules_for(env: dict):
    return gr.scrub_rules(Path(env["CAIRN_KNOWLEDGE"]).parent,
                          Path(env["CAIRN_KNOWLEDGE"]).parent, CORPUS_DIR)


def check_contracts(captures: dict) -> None:
    """Fail loudly when the corpus stops exercising the flow contracts."""
    explore0 = captures["explore"]["text"]
    assert "=== Source (" in explore0 and "=== Call paths ===" in explore0
    assert "=== Blast radius (depth 2) ===" in explore0
    assert "=== Tribal memory (0) ===" in explore0
    render_case = captures["explore"]["cases"][2]
    assert "=== Ambiguous dispatch ===" in render_case["text"], "no ambiguous dispatch hop captured"
    assert '"render" could dispatch to:' in render_case["text"]
    taint_case = captures["explore"]["cases"][1]
    assert "=== Taint paths ===" in taint_case["text"], "no taint section captured"
    trace_cases = captures["trace_flow"]["cases"]
    trace = trace_cases[0]["text"]
    assert "Branch points:" in trace and "Terminal calls:" in trace
    assert "Cyclic calls:" in trace, "no cycle captured in the kickoff trace"
    shallow = trace_cases[1]["text"]
    assert shallow.startswith("Traced") and "Cyclic calls" not in shallow, \
        "max_depth=1 did not bound the trace"
    xdep = captures["cross_repo_deps"]["text"]
    assert "demo-sdk" in xdep, "namespace mapping produced no dependency"
    viz = captures["visualize_graph"]
    assert viz["text"].startswith("graph LR"), viz["text"][:40]
    assert "digraph cairn {" in viz["cases"][1]["text"]
    assert json.loads(viz["cases"][2]["text"])["metadata"]["scope"] == "symbol"
    assert "Unknown scope 'bogus'" in viz["cases"][7]["text"]
    for cap in captures.values():
        for text in [cap["text"]] + [c["text"] for c in cap["cases"]]:
            assert not text.startswith("⚠"), f"staleness banner leaked: {text[:60]}"
            assert "Run `cairn embed`" not in text, "embed footnote leaked"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="Rebuild and verify byte-equality instead of writing.")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="cairn-flow-golden-") as tmp:
        env = tool_env(Path(tmp) / "knowledge")
        write_corpus()
        store_meta = build_store()

        captures = capture(env)
        check_contracts(captures)
        rules = rules_for(env)

        entries = {tool: fixture_entry(tool, captures[tool], rules,
                                       tool in SORT_LINES_TOOLS)
                   for tool in FLOW_TOOLS}
        entries["taint"] = fixture_entry("taint", captures["taint"], rules,
                                         TAINT_SORT_LINES)

        if args.check:
            for tool, path in _fixture_paths().items():
                got = compare_scope(json.loads(path.read_text(encoding="utf-8")))
                want = compare_scope(entries[tool])
                assert got == want, f"{path} drifts from the regenerated capture"
            print("flow fixtures byte-equal to the regenerated capture")
            return 0

        for tool, path in _fixture_paths().items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(entries[tool], indent=2) + "\n", encoding="utf-8")
    (FLOW_DIR / "README.md").write_text(FLOW_README, encoding="utf-8")
    print(json.dumps({"fixtures": sorted(p.name for p in _fixture_paths().values()),
                      "store": store_meta}, indent=2))
    return 0


def _fixture_paths() -> dict[str, Path]:
    paths = {f"{tool}": MCP_DIR / f"{tool}.json" for tool in FLOW_TOOLS}
    paths["taint"] = FLOW_DIR / "taint.json"
    return paths


FLOW_README = """# Flow-family parity fixtures

- `corpus/` — the deterministic two-repo-namespace demo corpus (11 files) the
  five flow tools are captured against; regenerated byte-identical by the
  harness.
- `flow-store.kg` — the store built from `corpus/` (closure index included,
  never embedded: explore stays lexical-only).
- `taint.json` — `cairn taint --from file-read --to sql` expectation (the taint
  surface is CLI-only, so it has no `rust/golden/mcp/` fixture).

The matching MCP fixtures live in `../mcp/{explore,trace_flow,cross_repo_deps,
visualize_graph}.json`; each carries its per-case captures in a `cases` array
(the recorder's five-key compare scope ignores that key).

Regenerate everything with `.venv/bin/python
scripts/rust_migration/flow_golden.py`; verify byte-equality with `--check`.
"""

if __name__ == "__main__":
    sys.exit(main())
