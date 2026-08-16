#!/usr/bin/env python3
"""Mint the committed bench baselines for one dataset version (T015, FR-004).

Run this ONLY on the reference machine (``runner_class=reference-local`` --
any non-GitHub-Actions host qualifies; the stamp records the profile so a
cross-machine use warns, D-005). It produces, under
``benchmarks/baselines/<version>/``:

    perf.json     from ``cairn bench --suite perf --json``
    scaling.json  from ``cairn bench --suite scaling --json``
                  (default sizes 100,500,1000,5000 -- minutes-scale; the
                  closure builds dominate. Do not shrink this.)
    agent.json    from ``cairn bench --suite agent --json``
    quality.json  from a FRESH t2 build + local-embed + ``run_evaluation``
                  over the T011 graded pair (see ``mint_quality``)

Every artifact is self-describing (D-001): the additive top-level
``"schema": "cairn-bench-baseline/1"`` tag beside the T013 stamp
(``dataset`` / ``cairn_version`` / ``machine_profile`` / ``timestamp``).
The three CLI-suite payloads are the CLI's own JSON verbatim -- the script
only adds the schema tag (additive; the CLI already carries the stamp).

D-010 (immutability): once a version directory is committed it is NEVER
edited -- corrections and re-measurements ship as a NEW version directory
(DS-v2, DS-v3, ...). The script refuses to overwrite an existing artifact
unless ``--force`` is passed, and ``--force`` is for pre-commit re-mints
on the minter's own machine only, never after the commit lands.

Usage:
    uv run python scripts/mint_baselines.py --version DS-v1
    uv run python scripts/mint_baselines.py --version DS-v1 --force
    uv run python scripts/mint_baselines.py --version DS-v1 --suites quality
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running from a source checkout without installing (same pattern as
# scripts/verify_ground_truth.py). Guarded insert so repeated imports never
# grow sys.path.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

# The self-describing tag every minted artifact carries (D-001). Version 1 =
# the T015 shape: schema tag + T013 stamp + the suite payload keys.
BASELINE_SCHEMA = "cairn-bench-baseline/1"

# CLI suites minted verbatim from `cairn bench --json` output. quality.json
# is minted in-process (see mint_quality) because it is not a CLI suite.
CLI_SUITES = ("perf", "scaling", "agent")
ALL_SUITES = CLI_SUITES + ("quality",)

T2_DIR = REPO_ROOT / "benchmarks" / "datasource" / "t2"
GROUND_TRUTH = T2_DIR / "ground_truth"


def _write_artifact(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _retrieval_state() -> dict:
    """Record the effective retrieval state (D-009 consequence: quality mints
    from DS-v1.1 on carry their rerank/threading state so the artifact is
    reproducible by construction).

    DS-v1's figures (0.4174/0.2862) were not bit-reproducible because the
    rerank-active pipeline flips near-tie rankings under mint-time state
    (reranker warm/cold, torch threading under a concurrent mint). Recording
    that state here turns "reproduce the session" into "reproduce the recorded
    configuration".
    """
    import os

    from cairn.graph import reranker

    torch_threads = None
    try:
        import torch

        torch_threads = torch.get_num_threads()
    except Exception:  # pragma: no cover - the local backend implies torch
        pass
    env_keys = (
        "CAIRN_EMBED_BACKEND",  # popped by mint_quality -> recorded as None
        "CAIRN_EMBED_LOCAL_MODEL",
        "CAIRN_RERANK",
        "CAIRN_RERANK_MODEL",
        "CAIRN_FUSION",
        "CAIRN_ANN_BACKEND",
        "CAIRN_WARM_MODELS",
    )
    return {
        "rerank": {
            # EFFECTIVE state (env OR the persistent auto-enable marker --
            # the T019 shipped config is rerank-auto), not just the env var.
            "enabled": reranker.rerank_enabled(),
            "model": reranker.current_rerank_model(),
            "available": reranker.reranker_available(),
        },
        "torch_num_threads": torch_threads,
        "env": {key: os.environ.get(key) for key in env_keys},
    }


def mint_cli_suite(suite: str, out_path: Path) -> dict:
    """Mint one CLI-suite baseline from `cairn bench --suite <s> --json`.

    The subprocess is the exact documented mint command, so the artifact and
    the README can never drift: whatever the CLI stamps (T013) is what lands
    in the file, plus the additive schema tag at the top.
    """
    cmd = ["uv", "run", "cairn", "bench", "--suite", suite, "--json"]
    print(f"[mint:{suite}] {' '.join(cmd)}", flush=True)
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"cairn bench --suite {suite} exited {proc.returncode}")
    payload = json.loads(proc.stdout)
    payload = {"schema": BASELINE_SCHEMA, **payload}
    _write_artifact(out_path, payload)
    print(f"[mint:{suite}] wrote {out_path.relative_to(REPO_ROOT)} ({elapsed:.0f}s)", flush=True)
    return payload


def mint_quality(out_path: Path) -> dict:
    """Mint quality.json: run_evaluation over the T011 graded ground truth.

    Pipeline (documented so the numbers are reproducible):

    1. Copy ``benchmarks/datasource/t2/yarl`` to a throwaway workspace and
       put the empty ``.git`` scanner marker on the COPY (same idiom as
       scripts/verify_ground_truth.py; the committed tree stays marker-free).
    2. ``build_graph`` a fresh graph over that copy.
    3. ``embed_all`` with the LOCAL embedding backend (the default
       ``CAIRN_EMBED_BACKEND`` -- unset here, and asserted below: quality
       numbers over hash vectors would be meaningless token-overlap, not
       semantics).
    4. ``run_evaluation(conn, bundle_root=None, queries_path=<t2
       ground_truth>)`` -- the graded loader + identity-first matcher path
       (D-008). ``bundle_root=None`` matches the committed dataset state:
       no OKF knowledge bundle exists for the t2 snapshot, so the L5
       retrieval surface is empty and L5 scores 0.0 by construction. The
       payload records that explicitly (``l5_surface``) so a future L5
       bundle baseline is an additive change, never a silent rewrite.
    5. Stamp the payload with ``build_artifact_stamp()`` (T013) + the same
       schema tag as the CLI suites, plus a ``retrieval`` block recording the
       effective rerank/threading state (D-009: mints from DS-v1.1 on are
       reproducible by construction).
    """
    import os

    if os.environ.get("GITHUB_ACTIONS"):
        raise SystemExit(
            "refusing to mint baselines under GitHub Actions: baselines are a "
            "reference-machine artifact (D-005 runner_class=reference-local)"
        )
    # The local backend must be the EFFECTIVE one, not just the configured
    # one: an unset CAIRN_EMBED_BACKEND silently degrades to hash when
    # sentence-transformers is missing (embeddings._effective_backend).
    saved_backend = os.environ.pop("CAIRN_EMBED_BACKEND", None)
    from cairn.graph import embeddings as emb

    emb.reset_backend_cache()
    effective = emb._effective_backend()
    if effective != "local":
        raise SystemExit(
            f"effective embed backend is {effective!r}, expected 'local' -- "
            "quality numbers over hash vectors are meaningless (token overlap, "
            "not semantics); install the [semantic] extra and re-run"
        )
    try:
        from cairn.bench.datasource import build_artifact_stamp
        from cairn.eval import load_ground_truth, run_evaluation
        from cairn.graph.builder import build_graph
        from cairn.graph.schema import get_db

        started = time.perf_counter()
        workroot = Path(tempfile.mkdtemp(prefix="cairn-mint-quality-"))
        try:
            workspace = workroot / "workspace"
            workspace.mkdir()
            repo_copy = workspace / "yarl"
            shutil.copytree(T2_DIR / "yarl", repo_copy)
            (repo_copy / ".git").mkdir()  # scanner marker, on the COPY only

            db_path = workroot / "graph.db"
            build = build_graph(workspace=str(workspace), db_path=str(db_path), verbose=False)
            if build.get("repos", 0) < 1 or build.get("parse_errors", 0):
                raise SystemExit(f"degraded t2 build: {build}")

            # get_db (not a bare sqlite3.connect): semantic_search reads scan
            # rows by column NAME (semantic.py `r["vec"]`), which needs the
            # Row factory get_db installs -- the same opener `cairn eval`
            # uses. A bare connection silently degrades every query to the
            # FTS5 fallback and mints recall 0.0.
            conn = get_db(str(db_path))
            try:
                embed_summary = emb.embed_all(conn)
            finally:
                conn.close()

            conn = get_db(str(db_path))
            try:
                report = run_evaluation(
                    conn, bundle_root=None, queries_path=GROUND_TRUTH, corpus_filter="all"
                )
            finally:
                conn.close()

            graded = load_ground_truth(GROUND_TRUTH)
            stamp = build_artifact_stamp()
            payload = {
                "schema": BASELINE_SCHEMA,
                "suite": "quality",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **stamp,
                "embed": {
                    "backend": effective,
                    "model": emb.current_model(),
                    **{k: embed_summary.get(k) for k in ("embedded", "skipped", "total", "reaped")},
                },
                "retrieval": _retrieval_state(),
                "build": {
                    key: build.get(key)
                    for key in ("repos", "files", "symbols", "edges", "parse_errors")
                },
                "ground_truth": {
                    "path": str(GROUND_TRUTH.relative_to(REPO_ROOT)),
                    "queries": len(graded),
                    "expectations": sum(len(g.expectations) for g in graded),
                    "authoring_task": "T011",
                },
                # No OKF bundle exists for the t2 snapshot (see
                # scripts/verify_ground_truth.py): L5 retrieval returns []
                # and scores 0.0 by construction. Recorded so the L5 column
                # reads as "surface absent", not "retrieval failed".
                "l5_surface": "none (no OKF knowledge bundle for the t2 snapshot)",
                "L1": report["L1"],
                "L5": report["L5"],
            }
            _write_artifact(out_path, payload)
            elapsed = time.perf_counter() - started
            print(
                f"[mint:quality] wrote {out_path.relative_to(REPO_ROOT)} ({elapsed:.0f}s) "
                f"L1={report['L1']} L5={report['L5']}",
                flush=True,
            )
            return payload
        finally:
            shutil.rmtree(workroot, ignore_errors=True)
    finally:
        if saved_backend is not None:
            os.environ["CAIRN_EMBED_BACKEND"] = saved_backend
        emb.reset_backend_cache()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--version", required=True, help="dataset version (e.g. DS-v1)")
    parser.add_argument(
        "--suites",
        default=",".join(ALL_SUITES),
        help=f"comma-separated subset of {ALL_SUITES} (default: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing artifacts (pre-commit re-mints only; D-010: a "
        "committed version directory is immutable -- re-measure as a new version)",
    )
    args = parser.parse_args(argv)

    suites = [s.strip() for s in args.suites.split(",") if s.strip()]
    unknown = [s for s in suites if s not in ALL_SUITES]
    if unknown:
        parser.error(f"unknown suite(s) {unknown}; choose from {ALL_SUITES}")

    out_dir = REPO_ROOT / "benchmarks" / "baselines" / args.version
    out_dir.mkdir(parents=True, exist_ok=True)
    if not args.force:
        existing = sorted(
            p.name for s in suites if (out_dir / f"{s}.json").exists() for p in [out_dir / f"{s}.json"]
        )
        if existing:
            raise SystemExit(
                f"{out_dir.relative_to(REPO_ROOT)} already holds {existing}; D-010 makes a "
                f"committed version immutable -- re-measure as a NEW version, or pass "
                f"--force for a pre-commit re-mint on the minter's machine"
            )

    total_started = time.perf_counter()
    for suite in suites:
        out_path = out_dir / f"{suite}.json"
        if suite == "quality":
            mint_quality(out_path)
        else:
            mint_cli_suite(suite, out_path)
    print(f"[mint] all done in {time.perf_counter() - total_started:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
