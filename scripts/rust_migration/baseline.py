#!/usr/bin/env python3
"""Per-tool p95 latency harness for the Rust daemon over the pinned stores.

Serves each checked-in pinned store (self/yarl/attrs) to the `serve_search`
daemon, replays every MCP fixture tool N rounds, and reports per-tool
p50/p95/max wall time. `--json` freezes a baseline; `--compare` gates a run
against a frozen baseline within `--tolerance` (FR-008's recorded tolerance).
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(Path(__file__).resolve().parent),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import parity  # reuse: pinned-store wiring, daemon, keyed client


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the numbers channel."""
    print(f"[baseline] {msg}", file=sys.stderr, flush=True)


# Timed tools whose string args are perturbed per round so repeat inserts stay
# on the success path instead of degrading into duplicate-key errors.
PERTURB_TITLE_TOOLS = {"knowledge_add", "record_memory"}


def percentile(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile of a non-empty sample list."""
    ordered = sorted(samples)
    rank = max(1, min(len(ordered), int(-(-len(ordered) * pct // 100))))
    return ordered[rank - 1]


def timed_call(client: parity.RustClient, tool: str, args: dict) -> tuple[float, bool]:
    """One tools/call round trip; returns (elapsed_s, is_error)."""
    started = time.perf_counter()
    is_error, _text = client.call(tool, args)
    return time.perf_counter() - started, is_error


def resolve_setup_args(client: parity.RustClient, entry: dict, round_idx: int) -> dict | None:
    """Run the entry's setup probe and return timed args with the fresh id, or None on setup failure."""
    args = dict(entry["args"])
    if "setup" not in entry:
        return args
    setup = entry["setup"]
    setup_args = dict(setup["args"])
    if setup["tool"] in PERTURB_TITLE_TOOLS:
        setup_args["title"] = f"{setup_args.get('title', '')} p95bench{round_idx}"
    is_error, text = client.call(setup["tool"], setup_args)
    if is_error:
        return None
    match = re.search(setup["id_regex"], text)
    if not match:
        return None
    args["doc_id"] = match.group(1)
    return args


def bench_round(client: parity.RustClient, entries: list[dict], round_idx: int,
                samples: dict[str, list[float]], errors: dict[str, int],
                timed: bool) -> None:
    """One interleaved round over every tool; setup probes run untimed."""
    for entry in entries:
        tool = entry["tool"]
        args = resolve_setup_args(client, entry, round_idx)
        if args is None:
            errors[tool] = errors.get(tool, 0) + 1
            samples.setdefault(tool, [])
            _note(f"  round {round_idx}: {tool}: setup failed; round skipped")
            continue
        if tool in PERTURB_TITLE_TOOLS:
            args["title"] = f"{args.get('title', '')} p95bench{round_idx}"
        elapsed, is_error = timed_call(client, tool, args)
        if timed:
            samples.setdefault(tool, []).append(elapsed)
        if is_error:
            errors[tool] = errors.get(tool, 0) + 1


def measure(store: str, scratch: Path, rounds: int, warmup: int,
            serve_exe: Path | None) -> tuple[dict, int]:
    """Serve the pinned store, replay every fixture tool; returns (results, catalog size)."""
    parity.load_pinned_store(store, scratch)
    workspace = scratch / "corpus"
    db = scratch / parity.DB_NAME
    parity.refuse_live_store(db, scratch / ".knowledge")
    out_dir = (parity.DEFAULT_OUT_DIR if store == "self"
               else parity.DEFAULT_OUT_DIR / store)
    entries = parity.entries_from_fixtures(out_dir, scratch, workspace)
    exe = serve_exe or parity.ensure_serve_binary()
    api_key = secrets.token_urlsafe(24)
    daemon = parity.RustDaemon(exe, db, workspace,
                               parity.daemon_env(scratch, workspace, api_key,
                                                 cargo_path=True))
    try:
        if not daemon.wait_ready():
            raise RuntimeError(f"daemon never became healthy: {daemon.diagnostics()}")
        client = parity.RustClient(daemon.url, api_key)
        catalog = client.start()
        missing = sorted({e["tool"] for e in entries} - set(catalog))
        if missing:
            raise RuntimeError(f"daemon catalog missing tools: {missing}")
        samples: dict[str, list[float]] = {}
        errors: dict[str, int] = {}
        for round_idx in range(-warmup, rounds):
            bench_round(client, entries, round_idx, samples, errors,
                        timed=round_idx >= 0)
            if round_idx >= 0:
                _note(f"  {store}: round {round_idx + 1}/{rounds} done")
    finally:
        daemon.stop()
    results = {}
    for entry in entries:
        tool = entry["tool"]
        tool_samples = samples.get(tool, [])
        if not tool_samples:
            results[tool] = {"error": "no timed samples (setup always failed)"}
            continue
        results[tool] = {
            "rounds": len(tool_samples),
            "errors": errors.get(tool, 0),
            "min_ms": round(min(tool_samples) * 1000, 3),
            "p50_ms": round(percentile(tool_samples, 50) * 1000, 3),
            "p95_ms": round(percentile(tool_samples, 95) * 1000, 3),
            "max_ms": round(max(tool_samples) * 1000, 3),
        }
    return results, len(catalog)


def run(store: str, rounds: int, warmup: int, serve_exe: Path | None) -> dict:
    """One store measurement; returns the JSON-serializable result block."""
    scratch = Path(tempfile.mkdtemp(prefix=f"cairn-baseline-{store}-"))
    _note(f"{store}: scratch {scratch}")
    try:
        results, catalog_size = measure(store, scratch, rounds, warmup, serve_exe)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {
        "store": store,
        "rounds": rounds,
        "warmup": warmup,
        "catalog_tools": catalog_size,
        "serve_exe": str(serve_exe) if serve_exe else "built-from-tree",
        "tools": results,
    }


def print_report(block: dict) -> None:
    """stdout table: one line per tool."""
    print(f"store={block['store']} rounds={block['rounds']} "
          f"catalog_tools={block['catalog_tools']}")
    print(f"{'tool':<20} {'p50_ms':>9} {'p95_ms':>9} {'max_ms':>9} {'err':>4}")
    for tool in sorted(block["tools"]):
        row = block["tools"][tool]
        if "error" in row:
            print(f"{tool:<20} {'-':>9} {'-':>9} {'-':>9} {-1:>4}  {row['error']}")
            continue
        print(f"{tool:<20} {row['p50_ms']:>9.3f} {row['p95_ms']:>9.3f} "
              f"{row['max_ms']:>9.3f} {row['errors']:>4}")


def compare(block: dict, baseline_path: Path, tolerance: float) -> int:
    """Gate: every baseline tool's p95 must stay within tolerance; 0 pass, 1 fail."""
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    base_block = next((b for b in ([baseline] if baseline.get("store")
                                    else baseline.get("stores", []))
                       if b["store"] == block["store"]), None)
    if base_block is None:
        print(f"compare: baseline has no block for store {block['store']}")
        return 1
    failures = []
    for tool, base in sorted(base_block["tools"].items()):
        if "p95_ms" not in base:
            continue
        if tool not in block["tools"] or "p95_ms" not in block["tools"][tool]:
            failures.append(f"{tool}: missing from this run")
            continue
        now = block["tools"][tool]["p95_ms"]
        limit = base["p95_ms"] * tolerance
        status = "ok" if now <= limit else "REGRESSED"
        print(f"compare {tool:<20} baseline_p95={base['p95_ms']:>9.3f}ms "
              f"now={now:>9.3f}ms limit={limit:>9.3f}ms {status}")
        if now > limit:
            failures.append(f"{tool}: p95 {now}ms > {limit:.3f}ms "
                            f"({base['p95_ms']}ms x {tolerance})")
    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print(f"compare: all {len(base_block['tools'])} baseline tools within "
          f"{tolerance}x on {block['store']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, choices=["self", "yarl", "attrs"])
    parser.add_argument("--rounds", type=int, default=25,
                        help="Timed rounds per tool (default 25).")
    parser.add_argument("--warmup", type=int, default=2,
                        help="Untimed warmup rounds (default 2).")
    parser.add_argument("--serve-exe", default=None,
                        help="Serve this example binary instead of building from the tree.")
    parser.add_argument("--json", default=None,
                        help="Write the result block to this path.")
    parser.add_argument("--compare", default=None,
                        help="Gate against a frozen baseline JSON (with --tolerance).")
    parser.add_argument("--tolerance", type=float, default=1.5,
                        help="Max p95 ratio vs baseline per tool (default 1.5).")
    args = parser.parse_args(argv)
    if args.rounds < 1 or args.warmup < 0:
        parser.error("--rounds must be >= 1 and --warmup >= 0")

    block = run(args.store, args.rounds, args.warmup,
                Path(args.serve_exe) if args.serve_exe else None)
    print_report(block)
    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(block, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        _note(f"wrote {path}")
    if args.compare:
        return compare(block, Path(args.compare), args.tolerance)
    return 0


if __name__ == "__main__":
    sys.exit(main())
