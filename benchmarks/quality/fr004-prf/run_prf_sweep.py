"""T020 (FR-004): PRF ablation measurements on the DS-v1 L1 k-fold.

One process, one grid point (D-002: fb_terms 10, lambda 0.5, fb_docs either
3 or 10), one output document -- mirroring T014's
fr003-calibration/run_fr003_sweep.py exactly (the established D-009
pattern): torch threads pinned to 1, local bge-m3 (CAIRN_EMBED_BACKEND
unset), brute-force cosine (CAIRN_ANN_BACKEND=off), rerank armed under the
CAIRN_RERANK=1 marker (flat pairs, gate margin 0.45), warm-up untimed
semantic_search call before the rotation, 5-fold seeded rotation
(fold_seed default = DEFAULT_SPLIT_SEED = 24301) over the 58 L1 queries
through the unchanged run_sweep_kfold/evaluate_on seam.

The grid carries ONE explicit combo -- ``prf@docs=<n>,terms=10,lambda=0.5``
(RetrievalParams(prf=True, prf_docs=<n>, prf_terms=10, prf_lambda=0.5);
``enrich`` stays None, so the paired-bootstrap delta vs the implicit row
isolates the PRF lever). Because no params-None variant-less combo is in
the grid, run_sweep_kfold prepends the implicit all-levers-off integrity
row per fold -- the TC-018/TC-019 integrity row that must reproduce the
committed DS-v1 session baseline (0.4174/0.2862 within the documented
band). That integrity row runs with rerank under the CAIRN_RERANK=1 marker
(the committed session protocol); the PRF combo forces the rerank stage
off BEFORE the pool fetch (D-012: PRF replaces-not-stacks the rerank
budget), so its latency is PRF-only by construction.

D-015: the original plan ran this sweep concurrently with the FR-005 mv
and ladder-prep measurements; MEASURE.md replaced that with the SERIAL
runbook before any run (concurrent model processes time-share one GPU
and only inflate p95 -- D-015's diagnosis).  In-sweep p95 is therefore a
quiet-machine figure; recall/MRR are deterministic under the pins.

Reuses T014's scratch DB read-only (/tmp/fr003-calibration/graph.db:
1066 real bge-m3 embeddings + 852 term_df rows; the PRF df_lookup reads
term_df, so the embed pass must have minted it).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GROUND_TRUTH = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "ground_truth"

FB_TERMS = 10  # D-002 anchors
FB_LAMBDA = 0.5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/tmp/fr003-calibration/graph.db")
    parser.add_argument(
        "--out", required=True, help="output path for the kfold sweep JSON"
    )
    parser.add_argument(
        "--docs", type=int, required=True, help="fb_docs grid point (3 or 10, D-002)"
    )
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args(argv)

    if args.docs not in (3, 10):
        print(f"--docs must be 3 or 10 (D-002 grid), got {args.docs!r}", file=sys.stderr)
        return 2

    # D-009 protocol pins, set before any cairn model code reads them.
    os.environ["CAIRN_RERANK"] = "1"  # marker: integrity row's session protocol
    os.environ["CAIRN_ANN_BACKEND"] = "off"  # brute-force cosine, no vec0
    # CAIRN_EMBED_BACKEND intentionally untouched: unset = local bge-m3.

    import cairn.paths  # noqa: F401  (injects ~/.cairn/lib: torch importable)

    import torch

    torch.set_num_threads(1)

    from cairn.eval import format_sweep_json, load_ground_truth, run_sweep_kfold
    from cairn.graph.queries import semantic_search
    from cairn.graph.schema import get_db
    from cairn.graph.semantic import RetrievalParams

    name = f"prf@docs={args.docs},terms={FB_TERMS},lambda={FB_LAMBDA}"
    combos: list[dict] = [
        {
            "name": name,
            "params": RetrievalParams(
                prf=True,
                prf_docs=args.docs,
                prf_terms=FB_TERMS,
                prf_lambda=FB_LAMBDA,
                # enrich/rerank/multivector stay None: single-lever isolation
                # over the implicit all-levers-off row; rerank is forced off
                # by params.prf itself (D-012).
            ),
        }
    ]

    gt = load_ground_truth(GROUND_TRUTH)
    queries = [q for q in gt if q.level == "L1"]
    if len(queries) != 58:
        print(f"expected 58 L1 queries, got {len(queries)}", file=sys.stderr)
        return 1

    conn = get_db(args.db)
    try:
        # Warm the models outside the timed seam (bge-m3 embed + the
        # reranker under the marker; the PRF second pass reuses the same
        # embed model, so one warm-up covers both passes).
        semantic_search(conn, "warm up the models", limit=1)
        started = time.perf_counter()
        doc = run_sweep_kfold(
            conn,
            queries,
            combos=combos,
            k_folds=args.folds,
            dataset_name="benchmark-datasource",
            dataset_version="DS-v1",
        )
        elapsed = time.perf_counter() - started
    finally:
        conn.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(format_sweep_json(doc) + "\n", encoding="utf-8")

    base = doc["folds"][0]["reports"]["all-levers-off"]["per_query"]
    print(
        f"wrote {out} (label={name} folds={doc['dataset']['k_folds']} "
        f"n={doc['dataset']['n_queries']} elapsed_s={elapsed:.1f})"
    )
    for cand, agg in doc["aggregate"]["combos"].items():
        pooled = agg["pooled"]
        b = pooled["baseline"]
        c = pooled["candidate"]
        boot = agg["bootstrap"]
        print(
            f"{cand}: pooled_candidate={sum(c) / len(c):.4f} "
            f"pooled_baseline={sum(b) / len(b):.4f} "
            f"delta={boot['delta']} p={boot.get('p_value')} ci={boot.get('ci_low')}..{boot.get('ci_high')}"
        )
    # The integrity summary the hard gate reads (pooled = full-set, n=58).
    print(
        f"all-levers-off pooled: n={len(base)} "
        f"(per-fold reports carry the full per-query maps)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
