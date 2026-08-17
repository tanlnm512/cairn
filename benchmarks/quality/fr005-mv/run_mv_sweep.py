"""T021 (FR-005): the multi-vector ablation k-fold sweep over DS-v1.

The D-009 protocol discipline, inherited verbatim from
fr003-calibration/run_fr003_sweep.py (which inherited it from
benchmarks/quality/ablation.md's header): torch threads pinned to 1, local
bge-m3 (CAIRN_EMBED_BACKEND unset), brute-force cosine (no vec0 --
CAIRN_ANN_BACKEND=off), rerank under the CAIRN_RERANK=1 marker with flat
pairs and gate margin 0.45.

One process, one sweep, one output document. The grid carries a single
combo — ``multivector`` (RetrievalParams(multivector=True), everything
else incumbent) — so run_sweep_kfold prepends its implicit
all-levers-off integrity row: with mv rows PRESENT in the scratch DB but
the lever OFF, that row must reproduce the committed session baseline
(TC-022; the brute flag-off SQL never reads embeddings_mv -- T018's
byte-equivalence battery).

The DB is /tmp/fr005-mv/graph.db: the t2 corpus, real bge-m3, base
``embeddings`` rows plus ``embeddings_mv`` name/docstring rows from the
ONE ``cairn embed --multivector`` pass. db_mb on every row is measured by
the harness's ``_size_accounting`` on this same file -- for the
all-levers-off row that includes the (unread) mv bytes, which is exactly
the honest on-disk figure for a DB built the FR-005 way; SIZE.md carries
the single-vector reference and the growth factor.

Post-processing (this file only -- eval.py is never touched): every row
of the mv combo gains the additive ``mv: true`` marker (the row-level
schema slot ``variant`` occupies for recipe combos), and the document is
written through the unchanged ``format_sweep_json``.

Models are warmed with one untimed semantic_search call before the
rotation (flag ON, so both the bge-m3 + reranker load AND one mv-shaped
query run outside the timed seam).

Integrity gate (deterministic, printed): the all-levers-off row's
full-set figures — reconstructed by held-out attribution (each query
read from the earliest fold whose selection material includes it, the
same fold for both arms) — must match the committed T014 baseline
(fr003-calibration/sweep-baseline.json, same reconstruction) within
±0.002 recall / ±0.006 MRR.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GROUND_TRUTH = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "ground_truth"
COMMITTED_BASELINE = (
    REPO_ROOT / "benchmarks" / "quality" / "fr003-calibration" / "sweep-baseline.json"
)

#: TC-022 tolerances: recall ±0.002, MRR ±0.006 vs the committed baseline.
TOL_RECALL = 0.002
TOL_MRR = 0.006

MV_COMBO = "multivector"


def full_set_figures(doc: dict) -> dict[str, float]:
    """Reconstruct full-set per-query figures by held-out attribution.

    The seam scores each query independently of the fold's selection set,
    so a query's per-query values are identical in every fold whose
    selection material includes it. Reading each query from the fold
    AFTER its own (any non-own fold works; +1 mod k is deterministic)
    pools every query exactly once -> n = all queries.
    """
    folds = doc["folds"]
    k = len(folds)
    held = [set(f["held_out_ids"]) for f in folds]
    reports = [f["reports"]["all-levers-off"] for f in folds]
    recalls: dict[str, float] = {}
    mrrs: dict[str, float] = {}
    for fold_index, held_out in enumerate(held):
        source = reports[(fold_index + 1) % k]["per_query"]
        for qid in held_out:
            recalls[qid] = source[qid]["recall_at_10"]
            mrrs[qid] = source[qid]["mrr"]
    n = len(recalls)
    return {
        "n": n,
        "recall_at_10": sum(recalls.values()) / n,
        "mrr": sum(mrrs.values()) / n,
        "per_query_recall": recalls,
        "per_query_mrr": mrrs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/tmp/fr005-mv/graph.db")
    parser.add_argument(
        "--out", required=True, help="output path for the kfold sweep JSON"
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
    from cairn.graph import embeddings as emb
    from cairn.graph.queries import semantic_search
    from cairn.graph.schema import get_db
    from cairn.graph.semantic import RetrievalParams

    gt = load_ground_truth(GROUND_TRUTH)
    queries = [q for q in gt if q.level == "L1"]
    if len(queries) != 58:
        print(f"expected 58 L1 queries, got {len(queries)}", file=sys.stderr)
        return 1

    conn = get_db(args.db)
    try:
        # Pre-flight: the DB must actually carry mv rows for the current
        # model, else the mv combo would silently measure flag-off shapes.
        model = emb.current_model()
        n_vec = conn.execute(
            "SELECT COUNT(*) FROM embeddings WHERE model = ?", (model,)
        ).fetchone()[0]
        n_mv = conn.execute(
            "SELECT COUNT(*) FROM embeddings_mv WHERE model = ?", (model,)
        ).fetchone()[0]
        mv_models = [
            r[0] for r in conn.execute("SELECT DISTINCT model FROM embeddings_mv")
        ]
        print(
            f"pre-flight: model={model!r} embeddings={n_vec} "
            f"embeddings_mv={n_mv} (mv models: {mv_models})"
        )
        if n_vec != 1066:
            print(f"EMBED COUNT DRIFT: {n_vec} != 1066", file=sys.stderr)
            return 1
        if n_mv < 1000:  # name kind alone should contribute ~1066
            print(
                f"MV ROWS MISSING: embeddings_mv={n_mv} for {model!r} -- "
                "rerun the --multivector embed pass",
                file=sys.stderr,
            )
            return 1

        # Warm both models outside the timed seam (bge-m3 embed + the
        # reranker under the marker), flag ON so one mv-shaped query also
        # runs untimed.
        semantic_search(
            conn,
            "warm up the models",
            limit=1,
            params=RetrievalParams(multivector=True),
        )
        started = time.perf_counter()
        doc = run_sweep_kfold(
            conn,
            queries,
            combos=[{"name": MV_COMBO, "params": RetrievalParams(multivector=True)}],
            k_folds=args.folds,
            dataset_name="benchmark-datasource",
            dataset_version="DS-v1",
        )
        elapsed = time.perf_counter() - started
    finally:
        conn.close()

    # Additive mv row marker (the slot `variant` occupies for recipe
    # combos) -- post-processing only, the harness document is otherwise
    # untouched.
    marked = 0
    for fold in doc["folds"]:
        for row in fold["rows"]:
            if row["combo"] == MV_COMBO:
                row["mv"] = True
                marked += 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(format_sweep_json(doc) + "\n", encoding="utf-8")

    # --- Deterministic integrity gate (TC-022). -------------------------
    committed = json.loads(COMMITTED_BASELINE.read_text(encoding="utf-8"))
    mine = full_set_figures(doc)
    theirs = full_set_figures(committed)
    d_recall = abs(mine["recall_at_10"] - theirs["recall_at_10"])
    d_mrr = abs(mine["mrr"] - theirs["mrr"])
    identical = sum(
        1
        for qid in mine["per_query_recall"]
        if qid in theirs["per_query_recall"]
        and mine["per_query_recall"][qid] == theirs["per_query_recall"][qid]
        and mine["per_query_mrr"][qid] == theirs["per_query_mrr"][qid]
    )
    integrity_ok = d_recall <= TOL_RECALL and d_mrr <= TOL_MRR

    print(
        f"wrote {out} (folds={doc['dataset']['k_folds']} "
        f"n={doc['dataset']['n_queries']} rows_marked_mv={marked} "
        f"elapsed_s={elapsed:.1f})"
    )
    for fold in doc["folds"]:
        for row in fold["rows"]:
            print(
                f"fold {fold['fold']} {row['combo']}: "
                f"recall@10={row['recall_at_10']:.4f} mrr={row['mrr']:.4f} "
                f"p95_ms={row['p95_ms']} db_mb={row['db_mb']} "
                f"n={row['n_queries']}"
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
    print(
        f"INTEGRITY all-levers-off full-set: mine recall={mine['recall_at_10']:.6f} "
        f"mrr={mine['mrr']:.6f} (n={mine['n']}) vs committed "
        f"recall={theirs['recall_at_10']:.6f} mrr={theirs['mrr']:.6f} "
        f"(n={theirs['n']}) -> |d_recall|={d_recall:.6f} "
        f"(tol {TOL_RECALL}) |d_mrr|={d_mrr:.6f} (tol {TOL_MRR}) "
        f"per-query identical={identical}/{mine['n']} "
        f"{'PASS' if integrity_ok else 'FAIL'}"
    )
    if not integrity_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
