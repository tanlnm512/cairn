#!/usr/bin/env python3
"""Closure differential + bench (2e): python vs rust transitive-closure rebuilds.

Builds one store with the rust builder, copies it, then times the python
`build_transitive_closure` on one copy and the rust streaming closure on the
other, both with WAL-sidecar sampling. The transitive_edges content must be
identical (that gates the exit code); the timing ratio and the >=5x flagship
target are reported, not gated.
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
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "src"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _reexec_repo_venv() -> None:
    """Re-exec under the repo venv python when this interpreter lacks the project deps."""
    try:
        from cairn.graph import dataflow  # noqa: F401
    except ModuleNotFoundError:
        venv_python = REPO_ROOT / ".venv" / "bin" / "python"
        if (venv_python.exists()
                and os.path.realpath(sys.executable) != os.path.realpath(venv_python)):
            os.execv(str(venv_python),
                     [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


_reexec_repo_venv()

import baseline  # reuse: corpus staging, python closure + WAL-sampling harness
from cairn.graph.schema import get_db

RUST_BIN = REPO_ROOT / "rust" / "target" / "release" / "cairn"


def _note(msg: str) -> None:
    """Progress goes to stderr: stdout is the verdict channel."""
    print(f"[closure-bench] {msg}", file=sys.stderr, flush=True)


def ensure_rust_binary() -> Path:
    """Build the release cairn binary when missing."""
    if RUST_BIN.exists():
        return RUST_BIN
    import shutil

    cargo = shutil.which("cargo") or Path.home() / ".cargo" / "bin" / "cargo"
    _note("building rust cairn binary (release)")
    subprocess.run(
        [str(cargo), "build", "--release", "-p", "cairn-cli"],
        cwd=REPO_ROOT / "rust",
        check=True,
    )
    return RUST_BIN


def copy_store(src: Path, dst: Path) -> None:
    """Consistent copy of a WAL-mode store via the sqlite backup API."""
    if dst.exists():
        dst.unlink()
    conn = sqlite3.connect(str(src))
    try:
        backup = sqlite3.connect(str(dst))
        try:
            conn.backup(backup)
        finally:
            backup.close()
    finally:
        conn.close()


def run_rust_closure(binary: Path, db: Path) -> dict:
    """Time the rust closure subprocess while sampling its WAL sidecar peak.

    The reported seconds come from the binary's internal `timings:` line
    (build_transitive_closure only), matching the python harness which times
    the call on an already-open connection; the WAL sampler covers the whole
    subprocess lifetime either way.
    """
    wal_path = Path(str(db) + "-wal")
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
            stop.wait(baseline.WAL_POLL_S)

    sampler = threading.Thread(target=_sample, name="wal-sampler-rs", daemon=True)
    sampler.start()
    proc = subprocess.run(
        [str(binary), "closure", "--db", str(db)],
        capture_output=True,
        text=True,
    )
    stop.set()
    sampler.join()
    if proc.returncode != 0:
        raise RuntimeError(f"rust closure failed: {proc.stderr.strip()}")
    outcome = json.loads(proc.stdout.splitlines()[0])
    timings = {
        part.split("=")[0]: float(part.split("=")[1].rstrip("ms"))
        for part in proc.stdout.splitlines()[1].split()
        if "=" in part
    }
    wal_end = wal_path.stat().st_size if wal_path.exists() else 0
    return {
        "seconds": round(timings["closure"] / 1000, 3),
        "rows": outcome["rows"],
        "levels": outcome.get("levels"),
        "peak_frontier": outcome.get("peak_frontier"),
        "peak_wal_bytes": max(peak["bytes"], wal_end),
        "sampled_peak_wal_bytes": peak["bytes"],
        "wal_end_bytes": wal_end,
        "db_bytes": db.stat().st_size,
    }


def read_closure_rows(db: Path) -> list[tuple]:
    """Sorted transitive_edges content; NULL-safe tuple projection."""
    conn = get_db(str(db))
    try:
        rows = [
            tuple(r)
            for r in conn.execute(
                "SELECT source_id, target_name, target_id, distance FROM transitive_edges"
            )
        ]
    finally:
        conn.close()
    # NULL-safe sort key; row equality itself stays exact.
    return sorted(rows, key=lambda r: (r[0], r[1] or "", r[2] or "", r[3]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=REPO_ROOT,
        help="corpus workspace (default: this repo, the largest available corpus)",
    )
    parser.add_argument("--keep", action="store_true", help="keep the scratch directory")
    args = parser.parse_args(argv)

    binary = ensure_rust_binary()
    scratch = Path(tempfile.mkdtemp(prefix="cairn-closure-bench."))
    try:
        corpus_ws = baseline.stage_corpus(args.workspace, scratch)
        base = scratch / "base.kg"
        _note(f"building rust store for {corpus_ws}")
        subprocess.run(
            [str(binary), "build-graph", "--workspace", str(corpus_ws), "--db", str(base)],
            check=True,
            capture_output=True,
            text=True,
        )
        py_db = scratch / "py.kg"
        rs_db = scratch / "rs.kg"
        copy_store(base, py_db)
        copy_store(base, rs_db)
        for stale in (Path(str(base) + "-wal"), Path(str(base) + "-shm")):
            stale.unlink(missing_ok=True)

        shape = sqlite3.connect(str(base))
        try:
            symbols, edges = shape.execute(
                "SELECT (SELECT COUNT(*) FROM symbols), (SELECT COUNT(*) FROM edges)"
            ).fetchone()
        finally:
            shape.close()
        _note(f"store: {symbols} symbols / {edges} edges")

        _note("python closure")
        py = baseline.run_closure(py_db)
        _note("rust closure")
        rs = run_rust_closure(binary, rs_db)

        py_rows = read_closure_rows(py_db)
        rs_rows = read_closure_rows(rs_db)
        parity = py_rows == rs_rows
        ratio = py["seconds"] / rs["seconds"] if rs["seconds"] > 0 else float("inf")

        print(json.dumps({
            "suite": "rust-migration-closure-bench/1",
            "corpus": str(args.workspace),
            "symbols": symbols,
            "edges": edges,
            "python": py,
            "rust": rs,
            "ratio_python_over_rust": round(ratio, 2),
            "content_parity": parity,
        }, indent=2))
        print(f"python closure: {py['seconds']}s  rows={py['rows']}  "
              f"peak_wal={py['peak_wal_bytes']:,}B")
        print(f"rust   closure: {rs['seconds']}s  rows={rs['rows']}  "
              f"peak_wal={rs['peak_wal_bytes']:,}B")
        print(f"ratio (python/rust): {ratio:.2f}x")
        print("flagship target >=5x: "
              + ("MET" if ratio >= 5 else "NOT MET")
              + " (reported, not gated)")
        if not parity:
            print(f"PARITY: FAIL — {len(py_rows)} python rows vs {len(rs_rows)} rust rows")
            return 1
        print("PARITY: PASS — transitive_edges content identical")
        return 0
    finally:
        if args.keep:
            print(f"[closure-bench] scratch kept: {scratch}", file=sys.stderr)
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
