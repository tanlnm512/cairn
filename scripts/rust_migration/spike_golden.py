#!/usr/bin/env python3
"""P0-5 spike harness: capture search_symbols spike goldens + the slim read-only store fixture."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "src"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import baseline  # reuse: env pins, stage_corpus, run_build
import golden_record  # reuse: normalization rules + diff oracle
from cairn import paths as cairn_paths
from cairn.mcp_server.tools_graph import _render_search_symbols, search_symbols_data

FIXTURE = golden_record.DEFAULT_OUT_DIR / "search_symbols.json"
SPIKE_DIR = REPO_ROOT / "rust" / "golden" / "spike"
SLIM_STORE = SPIKE_DIR / "search-store.kg"
CONTROL_QUERY = "zzz_no_match_spike*"
_KEEP_TABLES = {"repos", "files", "symbols"}


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the artifact channel."""
    print(f"[p05-spike] {msg}", file=sys.stderr, flush=True)


def spike_queries(fixture: dict) -> list[str]:
    """Queries taken from the fixture: its arguments pattern + patterns derived
    from its response lines (first name, underscored name, capitalized name,
    longest name), deduplicated in order."""
    queries = [fixture["arguments"]["pattern"]]
    names: list[str] = []
    for line in fixture["response"]["content"][0]["text"].splitlines():
        parts = line.strip().split("  ")
        if len(parts) >= 2 and " " in parts[0]:
            name = parts[0].split(" ", 1)[1]
            if name and name not in names:
                names.append(name)

    def add(q: str) -> None:
        if q not in queries:
            queries.append(q)

    add(names[0] + "*")
    underscored = next((n for n in names if "_" in n), None)
    if underscored:
        add(underscored)
    capped = next((n for n in names if n[:1].isupper()), None)
    if capped:
        add(capped + "*")
    add(max(names, key=len))
    if len(queries) < 5:
        raise RuntimeError(f"only {len(queries)} fixture-derived queries")
    queries.append(CONTROL_QUERY)
    return queries


class _Scratch:
    """Scratch home + registered store; env/paths patched, restored on exit."""

    def __init__(self, workspace: Path):
        from cairn.paths import register_workspace

        self.dir = Path(tempfile.mkdtemp(prefix="cairn-rust-p05-"))
        self.home = self.dir / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.corpus_ws = baseline.stage_corpus(workspace, self.dir)
        self._saved = {
            k: os.environ.get(k)
            for k in (*golden_record.ENV_PINS, "CAIRN_HOME", "CAIRN_WORKSPACE",
                      "CAIRN_DB", "CAIRN_KNOWLEDGE", *golden_record._POP_KEYS)
        }
        os.environ.update(golden_record.ENV_PINS)
        os.environ["CAIRN_HOME"] = str(self.home)
        os.environ["CAIRN_WORKSPACE"] = str(self.corpus_ws)
        os.environ["CAIRN_KNOWLEDGE"] = str(self.home / ".knowledge")
        for key in golden_record._POP_KEYS:
            os.environ.pop(key, None)
        cairn_paths.CAIRN_HOME = self.home
        cairn_paths.REGISTRY_FILE = self.home / "workspaces.json"
        cairn_paths.CONFIG_FILE = self.home / "config.json"
        store = register_workspace(self.corpus_ws)
        self.db = Path(store.db)
        self.store_home = Path(store.home)
        _note(f"scratch {self.dir}")

    def env(self, db: Path | None = None, **extra: str) -> None:
        os.environ["CAIRN_DB"] = str(db or self.db)
        os.environ.update(extra)

    def close(self) -> None:
        cairn_paths.CAIRN_HOME = Path(
            self._saved.get("CAIRN_HOME") or str(Path.home() / ".cairn")
        )
        cairn_paths.REGISTRY_FILE = cairn_paths.CAIRN_HOME / "workspaces.json"
        cairn_paths.CONFIG_FILE = cairn_paths.CAIRN_HOME / "config.json"
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def cleanup(self, keep: bool) -> None:
        import shutil

        if keep:
            _note(f"scratch kept: {self.dir}")
        else:
            shutil.rmtree(self.dir, ignore_errors=True)


def build_store(scratch: _Scratch) -> None:
    """Build the graph (parse + dataflow, no embed) into the scratch store."""
    _note(f"building graph into {scratch.db} ...")
    build = baseline.run_build(scratch.corpus_ws, scratch.db)
    _note(f"build {build['seconds']}s; symbols={build['symbols']} edges={build['edges']}")


def capture_response(pattern: str) -> dict:
    """The search_symbols MCP response envelope for one pattern."""
    data = search_symbols_data(pattern, kind="")
    text = _render_search_symbols(data)
    return {
        "isError": False,
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"result": text},
    }


def capture_all(db: Path, queries: list[str], rules: list[tuple],
                read_only: bool) -> list[dict]:
    os.environ["CAIRN_DB"] = str(db)
    os.environ.update({"CAIRN_READ_ONLY": "1"} if read_only else {})
    try:
        return [
            golden_record.normalize_response(capture_response(q), rules, False, False)
            for q in queries
        ]
    finally:
        os.environ.pop("CAIRN_READ_ONLY", None)


def write_slim(store_db: Path, out: Path) -> int:
    """Backup the store, drop every table search_symbols never reads, VACUUM."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / (out.name + ".tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{store_db}?mode=ro", uri=True)
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
        names = [r[0] for r in dst.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        keep = _KEEP_TABLES | {n for n in names if n.startswith("symbols_fts")}
        for name in names:
            if not name.startswith("sqlite_") and name not in keep:
                dst.execute(f'DROP TABLE "{name}"')
        dst.commit()
        # DELETE journal mode: a checked-in store opens read-only with no
        # -wal/-shm sidecars.
        dst.execute("PRAGMA journal_mode = DELETE")
        dst.execute("VACUUM")
        dst.commit()
    finally:
        dst.close()
        src.close()
    size = tmp.stat().st_size
    tmp.replace(out)
    return size


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
    queries = spike_queries(fixture)
    _note(f"queries: {queries}")

    scratch = _Scratch(workspace)
    try:
        build_store(scratch)
        rules = golden_record.scrub_rules(scratch.store_home, scratch.home,
                                          scratch.corpus_ws)
        captures = capture_all(scratch.db, queries, rules, read_only=False)

        golden_scope = golden_record.compare_scope(fixture)
        actual_scope = golden_record.compare_scope({
            "schema": fixture["schema"], "tool": fixture["tool"],
            "arguments": {"pattern": queries[0]},
            "normalization": fixture["normalization"],
            "response": captures[0],
        })
        diff = golden_record._diff_path(golden_scope, actual_scope)
        if diff:
            print(f"GOLDEN DIFF {diff}")
            return 1
        _note("query 1 is golden-equal to the committed fixture")

        slim_bytes = write_slim(scratch.db, SLIM_STORE)
        _note(f"slim store {SLIM_STORE} ({slim_bytes} B)")
        slim_captures = capture_all(SLIM_STORE, queries, rules, read_only=True)
        for q, full, slim in zip(queries, captures, slim_captures):
            d = golden_record._diff_path(full, slim)
            if d:
                print(f"SLIM DIFF {q}: {d}")
                return 1
        _note("slim store is response-equal for every query")

        fixture["spike_queries"] = [
            {"pattern": q, "response": r} for q, r in zip(queries, captures)
        ]
        FIXTURE.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "queries": queries,
            "golden_equal": True,
            "slim_store": str(SLIM_STORE.relative_to(REPO_ROOT)),
            "slim_bytes": slim_bytes,
        }, indent=2))
        return 0
    finally:
        scratch.close()
        scratch.cleanup(args.keep_scratch)


if __name__ == "__main__":
    sys.exit(main())
