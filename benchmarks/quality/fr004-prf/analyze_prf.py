"""T020 (FR-004): analyze the PRF sweeps and emit figures + row payloads.

Inputs: sweep-prf-docs3.json and sweep-prf-docs10.json (this directory's
run_prf_sweep.py output; fb_terms 10, lambda 0.5 -- the D-002 grid).
Everything is derived from the documents' own fold reports -- no
re-retrieval. Method mirrors T014's analyze_fr003.py verbatim:

* Pooled per-query values follow the harness's own source-fold rule
  (fold 1's report for fold 0's held-out ids, else fold 0's): a query is
  read exactly once per combo, exactly as the aggregate block does.
* The tune/validate halves come from ``split_queries(seed=24301)`` over
  the 58 L1 ids -- the SAME seeded split as the v1 campaign (Figures 1/2
  of benchmarks/quality/ablation.md), reconstructed from the rotation's
  per-query maps (the seam scores a query independently of the fold's
  selection set, so a split subset of the rotation equals a dedicated
  split run).
* The integrity gate is DETERMINISTIC: the implicit all-levers-off row's
  pooled recall@10/MRR must reproduce the committed DS-v1 session baseline
  within the documented band (+/-0.002 recall / +/-0.006 MRR). p95s are
  contention-noisy by design (D-015 parallel wave) and are marked as such;
  the orchestrator's consolidated quiet pass re-prices them.

Outputs (this directory): rows-fr004.json (machine-mergeable ablation-v2
row payloads) and FIGURES.md (the human record with the same payloads).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cairn.eval import (  # noqa: E402
    _percentile,
    load_ground_truth,
    paired_bootstrap,
    split_queries,
)

GT_DIR = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "ground_truth"

#: The committed DS-v1 session baseline (ablation.md header + Figure 3):
#: full-set recall@10 / MRR of the shipped all-levers-off config, with the
#: documented session band. The deterministic integrity target (TC-018).
COMMITTED_FULL = {"recall_at_10": 0.4174, "mrr": 0.2862}
BAND = {"recall_at_10": 0.002, "mrr": 0.006}
#: Figure 1/2 anchors (tune/validate halves of the same session).
COMMITTED_TUNE = {"recall_at_10": 0.5828, "mrr": 0.4444}
COMMITTED_VALIDATE = {"recall_at_10": 0.2521, "mrr": 0.1279}

#: The rerank budget PRF replaces (D-012): committed session p95 figures
#: (ablation.md) -- rerank-on 1142.0 ms vs rerank-off 28.9 ms p95. NEVER
#: the unretained ~780 ms p50 (flagged unsourced in survey.md's Unknowns).
RERANK_BUDGET = {
    "rerank_on_p95_ms": 1142.0,
    "rerank_off_p95_ms": 28.9,
    "note": "committed session figures; PRF's p95 is recorded against the "
    "1142.0 ms p95 budget it replaces, never the unretained ~780 ms p50",
}

CONFIGS = [
    (3, "prf@docs=3,terms=10,lambda=0.5", "sweep-prf-docs3.json"),
    (10, "prf@docs=10,terms=10,lambda=0.5", "sweep-prf-docs10.json"),
]
ALL_LEVERS_OFF = "all-levers-off"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _per_query(doc: dict, combo: str) -> dict[str, dict[str, float]]:
    """Per-query {qid: {recall_at_10, mrr}} read once per query.

    The harness's source-fold rule: fold 1's report for fold 0's held-out
    ids, else fold 0's (candidate and baseline share one embedding state).
    """
    first_held = set(doc["folds"][0]["held_out_ids"])
    out: dict[str, dict[str, float]] = {}
    for fold_index in (0, 1):
        report = doc["folds"][fold_index]["reports"][combo]
        for qid, values in report["per_query"].items():
            if qid in out:
                continue
            if fold_index == 0 and qid in first_held:
                continue  # fold 0's own members are read from fold 1
            out[qid] = values
    return out


def _durations(doc: dict, combo: str) -> dict[str, float]:
    first_held = set(doc["folds"][0]["held_out_ids"])
    out: dict[str, float] = {}
    for fold_index in (0, 1):
        report = doc["folds"][fold_index]["reports"][combo]
        for qid, ms in report["durations_ms"].items():
            if qid in out:
                continue
            if fold_index == 0 and qid in first_held:
                continue
            out[qid] = ms
    return out


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4)


def _split_block(per_query: dict, durations: dict, ids: list[str]) -> dict:
    recalls = [per_query[q]["recall_at_10"] for q in ids]
    mrrs = [per_query[q]["mrr"] for q in ids]
    times = sorted(durations[q] for q in ids)
    return {
        "n_queries": len(ids),
        "recall_at_10": _mean(recalls),
        "mrr": _mean(mrrs),
        "p95_ms": round(_percentile(times, 95.0), 4),
    }


def main() -> int:
    gt = [q for q in load_ground_truth(GT_DIR) if q.level == "L1"]
    assert len(gt) == 58, f"expected 58 L1 queries, got {len(gt)}"
    tune_ids, validate_ids = split_queries(gt, seed=24301)
    assert len(tune_ids) == 29 and len(validate_ids) == 29
    all_ids = sorted(tune_ids + validate_ids)

    # --- The deterministic integrity gate (docs3's implicit row). ---------
    base_doc = _load(HERE / "sweep-prf-docs3.json")
    base_pq = _per_query(base_doc, ALL_LEVERS_OFF)
    base_dur = _durations(base_doc, ALL_LEVERS_OFF)
    assert sorted(base_pq) == all_ids, "integrity run did not cover all 58 ids"
    pooled = {
        metric: _mean([base_pq[q][metric] for q in all_ids])
        for metric in ("recall_at_10", "mrr")
    }
    integrity = {
        "source": "sweep-prf-docs3.json implicit all-levers-off row",
        "pooled": {**pooled, "n_queries": 58},
        "committed_full_set": COMMITTED_FULL,
        "band": BAND,
        "within_band": {
            m: abs(pooled[m] - COMMITTED_FULL[m]) <= BAND[m] for m in pooled
        },
        "tune_anchor": {
            "measured": _split_block(base_pq, base_dur, tune_ids),
            "committed": COMMITTED_TUNE,
        },
        "validate_anchor": {
            "measured": _split_block(base_pq, base_dur, validate_ids),
            "committed": COMMITTED_VALIDATE,
        },
    }
    gate_ok = all(integrity["within_band"].values())

    # Cross-run determinism: docs10's implicit integrity row must be
    # byte-identical on recall/MRR (deterministic under the pins).
    base10_pq = _per_query(_load(HERE / "sweep-prf-docs10.json"), ALL_LEVERS_OFF)
    integrity["cross_run_all_levers_off_identical"] = base10_pq == base_pq

    # --- Per-config pooled figures + bootstrap verdicts. ------------------
    configs: dict[str, dict] = {}
    for docs, name, path in CONFIGS:
        doc = _load(HERE / path)
        pq = _per_query(doc, name)
        dur = _durations(doc, name)
        assert sorted(pq) == all_ids, f"{name} did not cover all 58 ids"
        agg = doc["aggregate"]["combos"][name]
        pooled_times = sorted(dur[q] for q in all_ids)
        configs[f"docs={docs}"] = {
            "docs": docs,
            "combo": name,
            "pooled": {
                "recall_at_10": _mean([pq[q]["recall_at_10"] for q in all_ids]),
                "mrr": _mean([pq[q]["mrr"] for q in all_ids]),
                "p95_ms": round(_percentile(pooled_times, 95.0), 4),
                "n_queries": 58,
            },
            "tune": _split_block(pq, dur, tune_ids),
            "validate": _split_block(pq, dur, validate_ids),
            "bootstrap_vs_all_levers_off": agg["bootstrap"],
            "descriptive": agg["descriptive"],
            "per_query": pq,
            "durations_ms": dur,
        }

    # Head-to-head PRF docs3 vs docs10 (both vs all-levers-off above).
    d3 = configs["docs=3"]["per_query"]
    d10 = configs["docs=10"]["per_query"]
    head_to_head = paired_bootstrap(
        [d10[q]["recall_at_10"] for q in all_ids],
        [d3[q]["recall_at_10"] for q in all_ids],
    )

    db_mb = base_doc["folds"][0]["rows"][0]["db_mb"]

    # --- The ablation-v2 row payloads (family ds-v1-kfold). ---------------
    levers_off = {
        "chunk_variant": "B",
        "dense_threshold": 0.3,
        "enrich": False,
        "enrich_idf": None,
        "pair_format": "flat",
        "rerank": "auto (on under CAIRN_RERANK=1 marker; confidence gate margin 0.45)",
        "rrf_k": 60,
        "rrf_weights": [1.0, 1.0],
        "sparse_top_n": None,
    }
    rows: list[dict] = []
    for docs, name, _path in CONFIGS:
        info = configs[f"docs={docs}"]
        boot = info["bootstrap_vs_all_levers_off"]
        rows.append(
            {
                "family": "ds-v1-kfold",
                "dataset": "DS-v1",
                "combo": f"prf@docs={docs},terms=10,lambda=0.5",
                "mv": False,
                "db_mb": db_mb,
                "recall_at_10": info["pooled"]["recall_at_10"],
                "mrr": info["pooled"]["mrr"],
                "p95_ms": info["pooled"]["p95_ms"],
                "n_queries": 58,
                "fold_count": 5,
                "split_basis": "pooled k-fold rotation (each query held out "
                "exactly once, D-009); tune/validate are the seed-24301 "
                "29/29 halves reconstructed from the same per-query maps",
                "tune": info["tune"],
                "validate": info["validate"],
                "bootstrap_vs_all_levers_off": {
                    k: boot[k]
                    for k in ("delta", "ci_low", "ci_high", "p_value", "significant", "n_queries")
                },
                "levers": {
                    **levers_off,
                    "rerank": "off (PRF replaces the rerank stage, D-012; "
                    "forced off before pool fetch regardless of "
                    "rerank/CAIRN_RERANK)",
                    "prf": True,
                    "prf_docs": docs,
                    "prf_terms": 10,
                    "prf_lambda": 0.5,
                },
                "p95_source": "kfold-sweep (contention-noisy, D-015 parallel "
                "wave; the consolidated quiet-p95 pass re-prices this row)",
                "p95_budget_comparison": RERANK_BUDGET,
                "notes": "FR-004 PRF grid point (T020, D-002 anchors: "
                "fb_terms 10, lambda 0.5; Anserini RM3 defaults). Single-"
                "lever combo over the all-levers-off base (enrich unset). "
                "p95 is recorded against the rerank budget it replaces "
                "(committed session figures: rerank-on 1142.0 ms p95 vs "
                "rerank-off 28.9 ms p95; never the unretained ~780 ms p50). "
                "In-sweep p95 is contention-noisy (D-015: concurrent FR-005 "
                "mv + ladder-prep measurement agents); recall/MRR are "
                "deterministic under the D-009 pins.",
            }
        )
    rows_doc = {
        "schema": "cairn-fr004-rows/1",
        "task": "T020 (FR-004): PRF rows to merge into benchmarks/quality/ablation-v2.json",
        "integrity": {
            "pooled": integrity["pooled"],
            "committed_full_set": COMMITTED_FULL,
            "within_band": integrity["within_band"],
            "cross_run_all_levers_off_identical": integrity[
                "cross_run_all_levers_off_identical"
            ],
        },
        "prf_docs3_vs_docs10_bootstrap": head_to_head,
        "rows": rows,
    }
    (HERE / "rows-fr004.json").write_text(
        json.dumps(rows_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # --- Console digest. -------------------------------------------------
    print(
        f"integrity pooled: {pooled} vs committed {COMMITTED_FULL} "
        f"within_band={integrity['within_band']}"
    )
    print(
        f"  tune anchor: {integrity['tune_anchor']['measured']} "
        f"vs committed {COMMITTED_TUNE}"
    )
    print(
        f"  validate anchor: {integrity['validate_anchor']['measured']} "
        f"vs committed {COMMITTED_VALIDATE}"
    )
    print(
        f"  cross-run (docs3 vs docs10 implicit row) identical: "
        f"{integrity['cross_run_all_levers_off_identical']}"
    )
    for key, info in configs.items():
        boot = info["bootstrap_vs_all_levers_off"]
        print(
            f"{key}: pooled {info['pooled']['recall_at_10']}/{info['pooled']['mrr']} "
            f"p95={info['pooled']['p95_ms']}ms "
            f"tune {info['tune']['recall_at_10']}/{info['tune']['mrr']} "
            f"validate {info['validate']['recall_at_10']}/{info['validate']['mrr']} "
            f"boot delta={boot['delta']} p={boot['p_value']} "
            f"ci={boot['ci_low']}..{boot['ci_high']} sig={boot['significant']}"
        )
    print(
        f"docs10 vs docs3 bootstrap: delta={head_to_head['delta']} "
        f"p={head_to_head['p_value']} sig={head_to_head['significant']}"
    )
    print("GATE:", "PASS" if gate_ok else "FAIL")
    print(f"wrote {HERE / 'rows-fr004.json'} ({len(rows)} rows)")
    return 0 if gate_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
