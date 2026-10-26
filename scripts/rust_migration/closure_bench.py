#!/usr/bin/env python3
"""Closure performance bench: rebuild wall time, peak WAL, insert bound.

Copies a pinned store, rebuilds the transitive closure via `cairn closure`,
samples the WAL file size during the rebuild, and compares the batched-commit
peak against a single-transaction replay of the same rows (the insert bound).
`--json` freezes the numbers; FR-009's re-ruled gate cites them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(Path(__file__).resolve().parent),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import parity  # reuse: pinned store location + live-store refusal guard

RUST_WORKSPACE = REPO_ROOT / "rust"
CAIRN_BIN = Path.home() / ".cargo" / "bin"
TIMINGS_RE = re.compile(r"timings: closure=([0-9.]+)ms")
WAL_POLL_S = 0.001


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the numbers channel."""
    print(f"[closure-bench] {msg}", file=sys.stderr, flush=True)


class WalSampler(threading.Thread):
    """Peak-size tracker for one `-wal` sidecar while the subject runs."""

    def __init__(self, db: Path):
        super().__init__(daemon=True)
        self.wal_path = Path(str(db) + "-wal")
        self.peak = 0
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                size = self.wal_path.stat().st_size
                if size > self.peak:
                    self.peak = size
            except OSError:
                pass
            time.sleep(WAL_POLL_S)

    def stop(self) -> int:
        self._halt.set()
        self.join(timeout=1.0)
        try:
            return max(self.peak, self.wal_path.stat().st_size)
        except OSError:
            return self.peak


def ensure_cairn_binary() -> Path:
    """Build (cargo, incremental) and return the release `cairn` binary."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(CAIRN_BIN), env.get("PATH", os.defpath)])
    subprocess.run(
        ["cargo", "build", "--release", "-p", "cairn-cli"],
        cwd=str(RUST_WORKSPACE), check=True, env=env,
    )
    return RUST_WORKSPACE / "target" / "release" / "cairn"


def child_env() -> dict:
    """Env without store overrides so `--db` is the only input."""
    env = dict(os.environ)
    for key in ("CAIRN_DB", "CAIRN_KNOWLEDGE", "CAIRN_WORKSPACE", "CAIRN_HOME"):
        env.pop(key, None)
    env["PATH"] = os.pathsep.join([str(CAIRN_BIN), env.get("PATH", os.defpath)])
    return env


def run_closure(cairn: Path, db: Path) -> dict:
    """One `cairn closure` rebuild on a fresh copy; returns the measured row."""
    sampler = WalSampler(db)
    sampler.start()
    started = time.perf_counter()
    proc = subprocess.run([str(cairn), "closure", "--db", str(db)],
                          capture_output=True, text=True, env=child_env(),
                          timeout=600)
    wall_s = time.perf_counter() - started
    peak_wal = sampler.stop()
    if proc.returncode != 0:
        raise RuntimeError(f"cairn closure failed: {proc.stderr.strip()}")
    lines = [line for line in proc.stdout.strip().splitlines() if line]
    if len(lines) < 2:
        raise RuntimeError(f"unexpected closure output: {proc.stdout!r}")
    outcome = json.loads(lines[0])
    match = TIMINGS_RE.search(lines[1])
    if not match:
        raise RuntimeError(f"no timings line in {proc.stdout!r}")
    return {
        "rows": outcome["rows"],
        "levels": outcome["levels"],
        "peak_frontier": outcome["peak_frontier"],
        "closure_ms": float(match.group(1)),
        "wall_ms": round(wall_s * 1000, 1),
        "peak_wal_bytes": peak_wal,
    }


def unbatched_wal_peak(used_db: Path) -> dict:
    """Replay the same rows in ONE transaction with autocheckpoint off."""
    probe = used_db.with_suffix(".probe.kg")
    shutil.copy2(used_db, probe)
    for suffix in ("-wal", "-shm"):
        Path(str(probe) + suffix).unlink(missing_ok=True)
    conn = sqlite3.connect(probe, isolation_level=None)
    try:
        conn.execute("PRAGMA wal_autocheckpoint=0")
        rows = conn.execute("SELECT COUNT(*) FROM transitive_edges").fetchone()[0]
        conn.execute("DROP TABLE IF EXISTS closure_shadow")
        conn.execute(
            "CREATE TABLE closure_shadow AS "
            "SELECT source_id, target_name, target_id, distance "
            "FROM transitive_edges")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        sampler = WalSampler(probe)
        sampler.start()
        started = time.perf_counter()
        conn.execute("BEGIN")
        conn.execute("DELETE FROM transitive_edges")
        conn.execute(
            "INSERT INTO transitive_edges "
            "(source_id, target_name, target_id, distance) "
            "SELECT source_id, target_name, target_id, distance "
            "FROM closure_shadow")
        conn.execute("COMMIT")
        wall_s = time.perf_counter() - started
        peak_wal = sampler.stop()
        conn.execute("DROP TABLE closure_shadow")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
        for suffix in ("-wal", "-shm"):
            Path(str(probe) + suffix).unlink(missing_ok=True)
        probe.unlink(missing_ok=True)
    return {"rows": rows, "wall_ms": round(wall_s * 1000, 1),
            "peak_wal_bytes": peak_wal}


def median_throughput(per_iteration: list[dict]) -> float:
    """Median closure rows/s across iterations."""
    rates = [row["rows"] / (row["closure_ms"] / 1000)
             for row in per_iteration if row["closure_ms"] > 0]
    return round(statistics.median(rates)) if rates else 0.0


def compare(block: dict, baseline_path: Path, tolerance: float) -> int:
    """Gate: median wall time and peak WAL within tolerance; 0 pass, 1 fail."""
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline["store"] != block["store"]:
        print(f"compare: baseline store {baseline['store']} != {block['store']}")
        return 1
    failures = []
    for key, label in (("median_closure_ms", "median_closure_ms"),
                       ("max_peak_wal_bytes", "max_peak_wal_bytes")):
        base, now = baseline[key], block[key]
        limit = base * tolerance
        status = "ok" if now <= limit else "REGRESSED"
        print(f"compare {label:<20} baseline={base} now={now} limit={limit:.1f} {status}")
        if now > limit:
            failures.append(f"{label}: {now} > {limit:.1f} ({base} x {tolerance})")
    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print(f"compare: closure gate within {tolerance}x on {block['store']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="self", choices=["self", "yarl", "attrs"])
    parser.add_argument("--iterations", type=int, default=5,
                        help="Measured rebuilds on fresh store copies (default 5).")
    parser.add_argument("--cairn-exe", default=None,
                        help="Drive this cairn binary instead of building from the tree.")
    parser.add_argument("--skip-unbatched", action="store_true",
                        help="Skip the single-transaction insert-bound probe.")
    parser.add_argument("--json", default=None, help="Write the result block to this path.")
    parser.add_argument("--compare", default=None,
                        help="Gate against a frozen baseline JSON (with --tolerance).")
    parser.add_argument("--tolerance", type=float, default=3.0,
                        help="Max median-ms/peak-WAL ratio vs baseline (default 3).")
    parser.add_argument("--keep-scratch", action="store_true")
    args = parser.parse_args(argv)
    if args.iterations < 1:
        parser.error("--iterations must be >= 1")

    pinned = parity.PINNED_STORES / args.store / parity.DB_NAME
    if not pinned.is_file():
        raise SystemExit(f"missing pinned store {pinned}")
    scratch = Path(tempfile.mkdtemp(prefix=f"cairn-closure-bench-{args.store}-"))
    _note(f"{args.store}: scratch {scratch}")
    try:
        db = scratch / parity.DB_NAME
        parity.refuse_live_store(db, scratch / ".knowledge")
        cairn = Path(args.cairn_exe) if args.cairn_exe else ensure_cairn_binary()
        per_iteration = []
        for idx in range(args.iterations):
            for suffix in ("-wal", "-shm"):
                Path(str(db) + suffix).unlink(missing_ok=True)
            shutil.copy2(pinned, db)
            row = run_closure(cairn, db)
            per_iteration.append(row)
            _note(f"  iter {idx + 1}/{args.iterations}: {row['rows']} rows, "
                  f"closure={row['closure_ms']:.1f}ms, "
                  f"peak_wal={row['peak_wal_bytes'] / 1_048_576:.1f}MiB")
        block = {
            "store": args.store,
            "cairn_exe": str(cairn),
            "iterations": args.iterations,
            "per_iteration": per_iteration,
            "median_closure_ms": round(statistics.median(
                r["closure_ms"] for r in per_iteration), 1),
            "max_peak_wal_bytes": max(r["peak_wal_bytes"] for r in per_iteration),
            "median_rows_per_s": median_throughput(per_iteration),
        }
        if not args.skip_unbatched:
            unbatched = unbatched_wal_peak(db)
            batched_peak = block["max_peak_wal_bytes"]
            unbatched["gain_x"] = (round(unbatched["peak_wal_bytes"] / batched_peak, 2)
                                   if batched_peak else None)
            block["unbatched_single_tx"] = unbatched
    finally:
        if args.keep_scratch:
            _note(f"scratch kept: {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)

    print(f"store={block['store']} iterations={block['iterations']} "
          f"rows={per_iteration[0]['rows']}")
    for row in per_iteration:
        print(f"  closure={row['closure_ms']:>9.1f}ms wall={row['wall_ms']:>9.1f}ms "
              f"peak_wal={row['peak_wal_bytes'] / 1_048_576:>7.1f}MiB "
              f"levels={row['levels']} peak_frontier={row['peak_frontier']}")
    print(f"median_closure_ms={block['median_closure_ms']} "
          f"max_peak_wal_MiB={block['max_peak_wal_bytes'] / 1_048_576:.1f} "
          f"median_rows_per_s={block['median_rows_per_s']}")
    if "unbatched_single_tx" in block:
        probe = block["unbatched_single_tx"]
        print(f"unbatched_single_tx: rows={probe['rows']} "
              f"peak_wal={probe['peak_wal_bytes'] / 1_048_576:.1f}MiB "
              f"gain_x={probe['gain_x']}")
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
