"""T014 (FR-003): quiet-machine p95 re-measurement for the committed rows.

The k-fold sweeps ran while a full test suite was executing on the same
machine (orchestrator-confirmed), so their ``durations_ms`` (and the rows'
p95 derived from them) are CPU-contention-inflated. recall/MRR are
deterministic under the protocol pins (threads 1, brute cosine, cached
weights) and stand.

This pass re-measures LATENCY ONLY, one config at a time on the quiet
machine: a single ``evaluate_on`` selection pass over all 58 L1 ids per
config (models pre-warmed outside the timed seam), recording
``durations_ms`` and the per-query recall/MRR. The per-query figures double
as a determinism cross-check -- they must equal the sweep-derived pooled
values (contention may not move a ranking).

Writes ``p95-remeasure.json`` incrementally (one entry per config) so
partial results survive an interruption; the emitter uses these durations
for every committed p95 figure.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
GROUND_TRUTH = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "ground_truth"
OUT = HERE / "p95-remeasure.json"

CONFIGS = ["baseline", "0.75", "0.80", "0.85", "0.90", "0.95"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/tmp/fr003-calibration/graph.db")
    parser.add_argument(
        "--configs", nargs="*", default=CONFIGS, help="subset of CONFIGS to run"
    )
    args = parser.parse_args(argv)

    os.environ["CAIRN_RERANK"] = "1"
    os.environ["CAIRN_ANN_BACKEND"] = "off"

    import cairn.paths  # noqa: F401  (injects ~/.cairn/lib: torch importable)

    import torch

    torch.set_num_threads(1)

    from cairn.eval import evaluate_on, load_ground_truth
    from cairn.graph import query_enrich
    from cairn.graph.queries import semantic_search
    from cairn.graph.schema import get_db
    from cairn.graph.semantic import RetrievalParams

    doc_path = OUT
    doc: dict = {"schema": "cairn-fr003-p95-remeasure/1", "configs": {}}
    if doc_path.exists():  # incremental resume
        doc = json.loads(doc_path.read_text())

    queries = [q for q in load_ground_truth(GROUND_TRUTH) if q.level == "L1"]
    all_ids = sorted(q.query_id for q in queries)
    conn = get_db(args.db)
    try:
        semantic_search(conn, "warm up the models", limit=1)
        for spec in args.configs:
            if spec in doc["configs"]:
                print(f"skip {spec} (already measured)")
                continue
            if spec == "baseline":
                query_enrich.ENRICH_DF_MAX_FRACTION = 0.90  # shipped value
                params = None
                label = "all-levers-off"
            else:
                query_enrich.ENRICH_DF_MAX_FRACTION = float(spec)
                params = RetrievalParams(enrich=True, enrich_idf=True)
                label = f"enrich+enrich_idf@df_max={float(spec):.2f}"
            started = time.perf_counter()
            report = evaluate_on(
                conn,
                queries,
                ids=all_ids,
                purpose="selection",
                held_out_ids=[],  # nothing held out: a pure latency/figures pass
                params=params,
            )
            elapsed = time.perf_counter() - started
            doc["configs"][spec] = {
                "label": label,
                "elapsed_s": round(elapsed, 1),
                "recall_at_10": report["recall_at_10"],
                "mrr": report["mrr"],
                "durations_ms": report["durations_ms"],
                "per_query": report["per_query"],
            }
            doc_path.write_text(
                json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(
                f"{spec}: recall={report['recall_at_10']} mrr={report['mrr']} "
                f"elapsed={elapsed:.1f}s (written)"
            )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
