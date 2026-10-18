#!/usr/bin/env python3
"""P1b nav harness: verify the nav-tool goldens reproduce, emit the slim nav store fixture."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "src"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import golden_record  # reuse: normalization rules + diff oracle
import spike_golden  # reuse: scratch home + slim-store writer
from cairn.mcp_server import tools_graph

FIXTURES = ("find_definition", "get_callers", "get_callees", "impact_analysis")
MCP_DIR = golden_record.DEFAULT_OUT_DIR
NAV_DIR = REPO_ROOT / "rust" / "golden" / "nav"
NAV_STORE = NAV_DIR / "nav-store.kg"
# Every table the four nav tools (find_definition/get_callers/get_callees/
# impact_analysis + the impact cross-repo xref) read. No FTS, no embeddings,
# no closure: the captured impact path is the DFS (seed cycle present).
_KEEP_TABLES = frozenset({"repos", "files", "symbols", "edges", "imports"})

TOOL_FNS = {
    "find_definition": tools_graph.find_definition,
    "get_callers": tools_graph.get_callers,
    "get_callees": tools_graph.get_callees,
    "impact_analysis": tools_graph.impact_analysis,
}


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the artifact channel."""
    print(f"[p1b-nav] {msg}", file=sys.stderr, flush=True)


def capture_response(tool: str, args: dict) -> dict:
    """The nav-tool MCP response envelope for one argument set."""
    text = TOOL_FNS[tool](**args)
    return {
        "isError": False,
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"result": text},
    }


def capture_all(db: Path, args_by_tool: dict[str, dict], rules: list,
                read_only: bool) -> dict[str, dict]:
    os.environ["CAIRN_DB"] = str(db)
    os.environ.update({"CAIRN_READ_ONLY": "1"} if read_only else {})
    try:
        return {
            tool: golden_record.normalize_response(
                capture_response(tool, args), rules, False, False
            )
            for tool, args in args_by_tool.items()
        }
    finally:
        os.environ.pop("CAIRN_READ_ONLY", None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=None,
                        help="Corpus workspace (default: the registered cairn workspace).")
    parser.add_argument("--keep-scratch", action="store_true",
                        help="Keep the scratch store for inspection.")
    args = parser.parse_args(argv)

    from cairn.paths import resolve_store

    workspace = (Path(args.workspace).resolve() if args.workspace
                 else resolve_store().workspace)
    fixtures = {
        tool: json.loads((MCP_DIR / f"{tool}.json").read_text(encoding="utf-8"))
        for tool in FIXTURES
    }
    args_by_tool = {tool: fixtures[tool]["arguments"] for tool in FIXTURES}

    scratch = spike_golden._Scratch(workspace)
    try:
        spike_golden.build_store(scratch)
        rules = golden_record.scrub_rules(scratch.store_home, scratch.home,
                                          scratch.corpus_ws)

        full = capture_all(scratch.db, args_by_tool, rules, read_only=False)
        for tool in FIXTURES:
            diff = golden_record._diff_path(fixtures[tool]["response"], full[tool])
            if diff:
                print(f"GOLDEN DIFF {tool}: {diff}")
                return 1
            _note(f"{tool}: golden-equal")

        slim_bytes = spike_golden.write_slim(scratch.db, NAV_STORE, _KEEP_TABLES)
        _note(f"slim store {NAV_STORE} ({slim_bytes} B)")
        slim = capture_all(NAV_STORE, args_by_tool, rules, read_only=True)
        for tool in FIXTURES:
            diff = golden_record._diff_path(full[tool], slim[tool])
            if diff:
                print(f"SLIM DIFF {tool}: {diff}")
                return 1
            _note(f"{tool}: slim response-equal")

        NAV_DIR.mkdir(parents=True, exist_ok=True)
        (NAV_DIR / ".gitignore").write_text(
            "# SQLite sidecars from opening the store; the checked-in artifact is the .kg only.\n"
            "*.kg-wal\n*.kg-shm\n*.kg-journal\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "golden_equal": True,
            "slim_store": str(NAV_STORE.relative_to(REPO_ROOT)),
            "slim_bytes": slim_bytes,
        }, indent=2))
        return 0
    finally:
        scratch.close()
        scratch.cleanup(args.keep_scratch)


if __name__ == "__main__":
    sys.exit(main())
