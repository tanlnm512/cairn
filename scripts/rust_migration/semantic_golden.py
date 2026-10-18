#!/usr/bin/env python3
"""P1b search harness: verify the semantic_search golden reproduces, emit the slim embedded store fixture."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "src"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import baseline  # reuse: env pins, run_embed
import golden_record  # reuse: normalization rules + diff oracle
import spike_golden  # reuse: scratch home, build, slim-store writer
from cairn.mcp_server._server_core import _append_embed_degradation_footnote
from cairn.mcp_server.tools_graph import _render_semantic_search

FIXTURE = golden_record.DEFAULT_OUT_DIR / "semantic_search.json"
SEARCH_DIR = REPO_ROOT / "rust" / "golden" / "search"
SEMANTIC_STORE = SEARCH_DIR / "semantic-store.kg"
# The fusion probe hits the sparse (bm25) and dense legs at once, so the
# captured response carries fused/semantic/bm25 provenance in one artifact.
PROBE_QUERY = "search_symbols"
CONTROL_QUERY = "zzz_no_match_probe_queried"


def semantic_queries(fixture: dict) -> list[dict]:
    """The committed capture first, then the fusion and empty-result probes."""
    return [
        {"query": fixture["arguments"]["query"],
         "limit": fixture["arguments"]["limit"]},
        {"query": PROBE_QUERY, "limit": 10},
        {"query": CONTROL_QUERY, "limit": 10},
    ]


def capture_response(query: str, limit: int) -> dict:
    """The semantic_search MCP response envelope for one query."""
    from cairn.graph import queries

    conn = sqlite3.connect(f"file:{os.environ['CAIRN_DB']}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = queries.semantic_search(
            conn, query, limit=limit, include_callers=False, rerank=None
        )
    finally:
        conn.close()
    data = {
        "query": query,
        "count": len(rows),
        "stale_banner": "",
        "matches": [
            {
                "kind": r["kind"],
                "name": r["name"],
                "qualified_name": r.get("qualified_name"),
                "file_path": r.get("file_path") or "",
                "repo": r["repo"],
                "score": r["score"],
                "provenance": r.get("provenance", "semantic"),
                "reranked": bool(r.get("reranked")),
                "rerank_score": r.get("rerank_score"),
                "chunk": r.get("chunk") or "",
                "callers": [],
                "callees": [],
            }
            for r in rows
        ],
    }
    text = _append_embed_degradation_footnote(
        _render_semantic_search(data, include_callers=False)
    )
    return {
        "isError": False,
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"result": text},
    }


def capture_all(queries: list[dict], rules: list[tuple]) -> list[dict]:
    return [
        golden_record.normalize_response(
            capture_response(q["query"], q["limit"]), rules, False, False
        )
        for q in queries
    ]


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
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    queries = semantic_queries(fixture)

    scratch = spike_golden._Scratch(workspace)
    try:
        spike_golden.build_store(scratch)
        spike_golden._note("embedding (hash) ...")
        embedded = baseline.run_embed(scratch.db)
        spike_golden._note(f"embed backend={embedded['backend_effective']} "
                           f"embedded={embedded['embedded']}")

        scratch.env()
        rules = golden_record.scrub_rules(scratch.store_home, scratch.home,
                                          scratch.corpus_ws)
        captures = capture_all(queries, rules)

        diff = golden_record._diff_path(fixture["response"], captures[0])
        if diff:
            print(f"GOLDEN DIFF {diff}")
            return 1
        spike_golden._note("query 1 is golden-equal to the committed fixture")

        names = [r[0] for r in sqlite3.connect(
            f"file:{scratch.db}?mode=ro", uri=True).execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
        keep = {"repos", "files", "symbols", "embeddings"}
        keep |= {n for n in names if n.startswith("symbols_fts")}
        slim_bytes = spike_golden.write_slim(scratch.db, SEMANTIC_STORE,
                                             keep_tables=frozenset(keep))
        spike_golden._note(f"slim store {SEMANTIC_STORE} ({slim_bytes} B)")

        scratch.env(SEMANTIC_STORE)
        slim_captures = capture_all(queries, rules)
        for q, full, slim in zip(queries, captures, slim_captures):
            d = golden_record._diff_path(full, slim)
            if d:
                print(f"SLIM DIFF {q['query']!r}: {d}")
                return 1
        spike_golden._note("slim store is response-equal for every query")

        fixture["spike_queries"] = [
            {**q, "response": r} for q, r in zip(queries, captures)
        ]
        FIXTURE.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        SEARCH_DIR.mkdir(parents=True, exist_ok=True)
        (SEARCH_DIR / "README.md").write_text(
            "# P1b search fixtures\n"
            "\n"
            "- `semantic-store.kg` -- the cairn self-store corpus built and\n"
            "  hash-embedded by this harness, pruned to the tables\n"
            "  `semantic_search` reads (`repos`, `files`, `symbols`,\n"
            "  `symbols_fts*`, `embeddings`) and response-verified\n"
            "  query-for-query against the full store before check-in.\n"
            "- `../mcp/semantic_search.json` -- `spike_queries` holds the captured\n"
            "  query / response pairs this store must serve golden-equal; entry 0\n"
            "  duplicates the committed golden capture.\n"
            "\n"
            "Regenerate with `.venv/bin/python scripts/rust_migration/semantic_golden.py`\n"
            "(a corpus change can move the nearest neighbors; see the golden diff\n"
            "message). The Rust integration test reads only these checked-in bytes.\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "queries": [q["query"] for q in queries],
            "golden_equal": True,
            "slim_store": str(SEMANTIC_STORE.relative_to(REPO_ROOT)),
            "slim_bytes": slim_bytes,
        }, indent=2))
        return 0
    finally:
        scratch.close()
        scratch.cleanup(args.keep_scratch)


if __name__ == "__main__":
    sys.exit(main())
