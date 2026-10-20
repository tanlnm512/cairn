#!/usr/bin/env python3
"""Resolver differential harness (2b): python resolver vs the rust resolver.

Builds a corpus with the python implementation pinned to its resolution pass
(LSP/SCIP/import-materialization off), captures the exact per-edge signals the
python resolver consumed, replays them through the rust resolver on a store
copy, then compares per-repo resolution-label counts and byte-diffs both edges
tables via kg_dump.py.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "src"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reexec_repo_venv() -> None:
    """Re-exec under the repo venv python when this interpreter lacks the project deps."""
    try:
        from cairn.graph import resolver  # noqa: F401
    except ModuleNotFoundError:
        venv_python = REPO_ROOT / ".venv" / "bin" / "python"
        if (venv_python.exists()
                and os.path.realpath(sys.executable) != os.path.realpath(venv_python)):
            os.execv(str(venv_python),
                     [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_reexec_repo_venv()

import baseline  # reuse: env pins, corpus staging
from cairn.graph import builder as builder_mod
from cairn.graph import resolver as resolver_mod

KG_DUMP = Path(__file__).resolve().parent / "kg_dump.py"
CARGO_BIN = Path.home() / ".cargo" / "bin"
RUST_WORKSPACE = REPO_ROOT / "rust"
CORPORA = {
    "yarl": REPO_ROOT / "benchmarks" / "datasource" / "t2" / "yarl",
    "attrs": REPO_ROOT / "benchmarks" / "datasource" / "ds2" / "second-corpus" / "attrs-26.1.0",
}
LABELS = ("exact", "ambiguous", "unresolved")


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the verdict channel."""
    print(f"[resolver-parity] {msg}", file=sys.stderr, flush=True)


def build_python_store(workspace: Path, db: Path, captured: dict) -> dict:
    """Fresh python build pinned to the resolution pass; captures resolver signals."""
    saved = {
        k: os.environ.get(k)
        for k in (*baseline.ENV_PINS, "CAIRN_HOME", "CAIRN_KNOWLEDGE")
    }
    os.environ.update(baseline.ENV_PINS)
    os.environ["CAIRN_HOME"] = str(db.parent / "home")
    os.environ["CAIRN_KNOWLEDGE"] = str(db.parent / "home" / ".knowledge")
    Path(os.environ["CAIRN_KNOWLEDGE"]).mkdir(parents=True, exist_ok=True)

    real_resolve = resolver_mod.resolve_repo_edges

    def capture_resolve(conn, repo, edges_by_file):
        """Record the resolver's inputs, delegate, then record its stats."""
        files = [
            {
                "file_id": file_id,
                "edges": [
                    {
                        "edge_id": t[0],
                        "target_name": t[2],
                        "receiver_type": t[5] if len(t) > 5 else None,
                        "call_arity": t[6] if len(t) > 6 else None,
                        "generic_tier": bool(t[7]) if len(t) > 7 else False,
                    }
                    for t in edges
                ],
            }
            for file_id, edges in edges_by_file.items()
        ]
        stats = real_resolve(conn, repo, edges_by_file)
        captured["repos"].append({"repo": repo, "files": files})
        captured["python_stats"][repo] = {k: int(v) for k, v in stats.items()}
        _note(f"  python resolved repo={repo}: {dict(stats)}")
        return stats

    resolver_mod.resolve_repo_edges = capture_resolve
    # Post-resolution overlays are outside the 2b resolver contract; pin the
    # build to pure resolver output so the edges table diff isolates it.
    builder_mod.materialize_import_edges = lambda conn: 0
    builder_mod._apply_scip_overlay = lambda *a, **k: {"edges": 0}
    try:
        from cairn.graph.builder import build_graph

        summary = build_graph(workspace=str(workspace), db_path=str(db),
                              verbose=False, lsp=False)
    finally:
        resolver_mod.resolve_repo_edges = real_resolve
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return summary


def copy_store(src: Path, dst: Path) -> None:
    """Consistent sqlite backup of a built store."""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        d = sqlite3.connect(str(dst))
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def reset_captured_edges(db: Path, captured: dict) -> int:
    """Restore every captured edge to its pre-resolution state in the rust copy."""
    rows = []
    for repo_entry in captured["repos"]:
        for file_entry in repo_entry["files"]:
            for edge in file_entry["edges"]:
                rows.append((None, edge["target_name"], None, edge["edge_id"]))
    conn = sqlite3.connect(str(db))
    try:
        conn.executemany(
            "UPDATE edges SET target_id = ?, target_name = ?, resolution = ? WHERE id = ?",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def ensure_parity_binary() -> Path:
    """Build (cargo, incremental) the resolve_parity example; PATH gets the cargo bin dir."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(CARGO_BIN), env.get("PATH", os.defpath)])
    subprocess.run(
        ["cargo", "build", "--release", "-p", "cairn-build",
         "--example", "resolve_parity"],
        cwd=str(RUST_WORKSPACE), check=True, env=env,
    )
    return RUST_WORKSPACE / "target" / "release" / "examples" / "resolve_parity"


def dump_edges(db: Path) -> str:
    """Canonical edges-table dump via kg_dump.py (the parity arbiter)."""
    proc = subprocess.run(
        [sys.executable, str(KG_DUMP), str(db), "--tables", "edges"],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout


def run_rust_resolver(exe: Path, db: Path, signals_path: Path) -> dict:
    """Replay the captured signals through the rust resolver; return its report."""
    proc = subprocess.run(
        [str(exe), str(db), str(signals_path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def compare(python_stats: dict, rust_report: dict) -> tuple[bool, str]:
    """Per-repo and total label-count comparison; returns (equal, detail)."""
    rust_by_repo = {r["repo"]: r["stats"] for r in rust_report["repos"]}
    detail = []
    equal = True
    for repo, stats in python_stats.items():
        rust = rust_by_repo.get(repo)
        if rust is None:
            equal = False
            detail.append(f"{repo}: missing from rust report")
            continue
        if {k: int(v) for k, v in stats.items()} != {k: int(v) for k, v in rust.items()}:
            equal = False
            detail.append(f"{repo}: python={stats} rust={rust}")
    extra = set(rust_by_repo) - set(python_stats)
    if extra:
        equal = False
        detail.append(f"rust-only repos: {sorted(extra)}")
    py_total = {k: sum(s[k] for s in python_stats.values()) for k in LABELS}
    rust_total = {k: int(v) for k, v in rust_report["total"].items()}
    if py_total != rust_total:
        equal = False
        detail.append(f"total: python={py_total} rust={rust_total}")
    return equal, "; ".join(detail)


def run(store: str, scratch: Path, keep_scratch: bool) -> int:
    db = scratch / "store.kg"
    workspace = REPO_ROOT if store == "self" else baseline.stage_corpus(
        CORPORA[store], scratch
    )
    _note(f"store={store} workspace={workspace} scratch={scratch}")

    captured: dict = {"repos": [], "python_stats": {}}
    summary = build_python_store(workspace, db, captured)
    py_total = {k: sum(s[k] for s in captured["python_stats"].values()) for k in LABELS}
    if summary.get("resolution") != py_total:
        _note(f"build summary resolution {summary.get('resolution')} != {py_total}")
    captured_edges = sum(len(f["edges"]) for r in captured["repos"] for f in r["files"])
    _note(f"python build done; {captured_edges} resolver edges, totals {py_total}")

    signals_path = scratch / "resolver_signals.json"
    signals_path.write_text(json.dumps(captured), encoding="utf-8")

    db_rust = scratch / "store.rust.kg"
    copy_store(db, db_rust)
    reset = reset_captured_edges(db_rust, captured)
    _note(f"reset {reset} captured edge(s) in the rust copy")

    exe = ensure_parity_binary()
    rust_report = run_rust_resolver(exe, db_rust, signals_path)
    rust_total = {k: int(v) for k, v in rust_report["total"].items()}
    _note(f"rust resolver totals {rust_total}")

    counts_equal, detail = compare(captured["python_stats"], rust_report)
    dump_python = dump_edges(db)
    dump_rust = dump_edges(db_rust)
    dumps_equal = dump_python == dump_rust

    if not counts_equal:
        _note(f"label-count mismatch: {detail}")
    if not dumps_equal:
        py_lines = dump_python.splitlines()
        rust_lines = dump_rust.splitlines()
        _note(f"edges dumps differ: {len(py_lines)} vs {len(rust_lines)} lines")
        diff = [ln for ln in py_lines if ln not in set(rust_lines)][:5]
        _note(f"python-only lines (first 5): {diff}")

    verdict = {
        "store": store,
        "edges": captured_edges,
        "python": py_total,
        "rust": rust_total,
        "label_counts_equal": counts_equal,
        "edges_dump_equal": dumps_equal,
        "verdict": "PASS" if counts_equal and dumps_equal else "FAIL",
    }
    print(json.dumps(verdict, indent=2))
    if keep_scratch:
        _note(f"scratch kept: {scratch}")
    else:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0 if verdict["verdict"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="self", choices=["self", *CORPORA],
                        help="self: the cairn workspace; yarl/attrs: benchmark corpora.")
    parser.add_argument("--keep-scratch", action="store_true",
                        help="Keep the scratch stores for inspection.")
    args = parser.parse_args(argv)
    scratch = Path(tempfile.mkdtemp(prefix=f"cairn-rust-resolver-parity-{args.store}-"))
    return run(args.store, scratch, args.keep_scratch)


if __name__ == "__main__":
    sys.exit(main())
