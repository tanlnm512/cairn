"""T014 (FR-003): one k-fold cutoff sweep over the DS-v1 L1 measurement set.

The D-009 protocol discipline, inherited verbatim from
benchmarks/quality/ablation.md's header: torch threads pinned to 1, local
bge-m3 (CAIRN_EMBED_BACKEND unset), brute-force cosine (no vec0 --
CAIRN_ANN_BACKEND=off), rerank under the CAIRN_RERANK=1 marker with flat
pairs and gate margin 0.45.

One process, one cutoff, one output document:

* ``--cutoff baseline`` (default) -- the integrity run: an EMPTY grid, so
  run_sweep_kfold's implicit all-levers-off row is the whole measurement
  (today's retrieval; every lever off).
* ``--cutoff <float>`` -- the calibration grid: one combo
  ``enrich+enrich_idf@df_max=<cutoff>`` (RetrievalParams(enrich=True,
  enrich_idf=True), everything else incumbent), with
  ``query_enrich.ENRICH_DF_MAX_FRACTION`` set to ``<cutoff>`` for this
  process only. The module global is read at call time inside
  ``enrich()``'s ubiquity predicate, so the override reaches the seam
  without touching any source file; the process boundary restores the
  shipped 0.90 (and 0.90 passed explicitly is byte-identical to the
  default -- the boundary is strictly-greater).

Each run evaluates the 58 L1 queries (the DS-v1 measurement set; L5 rows
never reach the semantic leg) over the 5-fold seeded rotation
(fold_seed 24301 = DEFAULT_SPLIT_SEED) through the unchanged evaluate_on
seam. The emitted cairn-quality-sweep-kfold/1 document carries per-fold
rows + reports and the pooled paired-bootstrap aggregate (D-009).

Models are warmed with one untimed semantic_search call before the
rotation so the first timed query does not pay the bge-m3 + reranker
load (the v1 session band's warm/cold caveat).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GROUND_TRUTH = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "ground_truth"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/tmp/fr003-calibration/graph.db")
    parser.add_argument(
        "--out", required=True, help="output path for the kfold sweep JSON"
    )
    parser.add_argument(
        "--cutoff",
        default="baseline",
        help="'baseline' for the all-levers-off integrity run, else a float "
        "in [0, 1] overriding query_enrich.ENRICH_DF_MAX_FRACTION",
    )
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args(argv)

    # D-009 protocol pins, set before any cairn model code reads them.
    os.environ["CAIRN_RERANK"] = "1"  # rerank marker: flat pairs, gate 0.45
    os.environ["CAIRN_ANN_BACKEND"] = "off"  # brute-force cosine, no vec0
    # CAIRN_EMBED_BACKEND intentionally untouched: unset = local bge-m3.

    import cairn.paths  # noqa: F401  (injects ~/.cairn/lib: torch importable)

    import torch

    torch.set_num_threads(1)

    from cairn.eval import format_sweep_json, load_ground_truth, run_sweep_kfold
    from cairn.graph import query_enrich
    from cairn.graph.queries import semantic_search
    from cairn.graph.schema import get_db
    from cairn.graph.semantic import RetrievalParams

    combos: list[dict] = []
    if args.cutoff == "baseline":
        label = "all-levers-off"
    else:
        cutoff = float(args.cutoff)
        if not 0.0 <= cutoff <= 1.0:
            print(f"--cutoff must be within [0, 1], got {cutoff!r}", file=sys.stderr)
            return 2
        query_enrich.ENRICH_DF_MAX_FRACTION = cutoff  # process-local override
        label = f"enrich+enrich_idf@df_max={cutoff:.2f}"
        combos = [
            {
                "name": label,
                "params": RetrievalParams(enrich=True, enrich_idf=True),
            }
        ]

    gt = load_ground_truth(GROUND_TRUTH)
    queries = [q for q in gt if q.level == "L1"]
    if len(queries) != 58:
        print(f"expected 58 L1 queries, got {len(queries)}", file=sys.stderr)
        return 1

    conn = get_db(args.db)
    try:
        # Warm both models outside the timed seam (bge-m3 embed + the
        # reranker under the marker; limit=1 still arms the 50-pair pool).
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
        f"wrote {out} (label={label} folds={doc['dataset']['k_folds']} "
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
