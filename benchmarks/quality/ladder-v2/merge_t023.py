"""T023 one-pass merge: ladder rows + DS-v2 rows + verdict evidence into
ablation-v2.json. Run AFTER run_ds2_zeroshot.py has emitted rows-ds2.json.
Pure JSON transform over measured documents — no retrieval.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")
from cairn.eval import _percentile, load_ground_truth, split_queries  # noqa: E402

REPO = Path(".")
ART = REPO / "benchmarks/quality/ablation-v2.json"

LADDER = [
    (
        "benchmarks/quality/ladder-v2/sweep-ladder-enrich-rerankoff.json",
        "enrich+rerank-off",
        {
            "enrich": True,
            "rerank": "off (explicit — the v1 record's first candidate "
            "direction; the reranker masks enrichment gains at ~40x p95)",
        },
        "D-016 ladder candidate (a): the v1 record's own first candidate, "
        "re-measured under D-009 pooling (the 29-query single split that "
        "scored it delta+0.1123 p=0.118 superseded by the 5-fold rotation, "
        "n=58). CLEARS the 95% pooled bootstrap guard — the campaign's "
        "headline question answered: doubling the evidence base converts "
        "the v1 near-miss into significance (margin thin: CI_low +0.0037, "
        "t-test cross-check p=0.0530 — recorded, guard is the bootstrap "
        "per D-009). Candidate (b) is byte-identical per-query (0/58 "
        "differ; the 0.90 cutoff drops nothing on DS-v1 — T014). SC-1: "
        "recall 0.5163 clears 0.50; MRR 0.3131 misses 0.33 by 0.0169.",
    ),
    (
        "benchmarks/quality/ladder-v2/sweep-ladder-enrichidf-rerankoff.json",
        "enrich_idf+rerank-off",
        {
            "enrich": True,
            "enrich_idf": True,
            "rerank": "off (explicit — same direction as (a); D-012's "
            "replaces-not-stacks logic applies to PRF, enrich composes)",
        },
        "D-016 ladder candidate (b): the FR-003 repaired successor, at the "
        "SHIPPED ENRICH_DF_MAX_FRACTION=0.90 (never overridden). "
        "Byte-identical per-query outcomes to candidate (a) on DS-v1 "
        "(0/58 differ): the highest term_df fraction is 'test' at 0.8583 "
        "< 0.90, so the cutoff's drop-set is empty on this corpus (T014's "
        "calibration finding). Clears the guard with (a)'s exact "
        "figures; the (a)-vs-(b) separation is DS-v2's to show (the "
        "attrs corpus has its own term_df distribution).",
    ),
]

LEVERS_OFF = {
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


def per_query(doc, combo):
    first_held = set(doc["folds"][0]["held_out_ids"])
    pq, dur = {}, {}
    for fi in (0, 1):
        rep = doc["folds"][fi]["reports"][combo]
        for qid, v in rep["per_query"].items():
            if qid in pq or (fi == 0 and qid in first_held):
                continue
            pq[qid] = v
            dur[qid] = rep["durations_ms"][qid]
    return pq, dur


def block(pq, dur, ids):
    return {
        "n_queries": len(ids),
        "recall_at_10": round(sum(pq[q]["recall_at_10"] for q in ids) / len(ids), 4),
        "mrr": round(sum(pq[q]["mrr"] for q in ids) / len(ids), 4),
        "p95_ms": round(_percentile(sorted(dur[q] for q in ids), 95.0), 4),
    }


def ladder_rows():
    gt = [q for q in load_ground_truth("benchmarks/datasource/t2/ground_truth") if q.level == "L1"]
    tune_ids, validate_ids = split_queries(gt, seed=24301)
    all_ids = sorted(tune_ids + validate_ids)
    rows = []
    for path, label, lever_overrides, notes in LADDER:
        doc = json.loads(Path(path).read_text())
        pq, dur = per_query(doc, label)
        assert sorted(pq) == all_ids
        agg = doc["aggregate"]["combos"][label]
        boot = agg["bootstrap"]
        rows.append(
            {
                "bootstrap_vs_all_levers_off": {
                    k: boot[k]
                    for k in ("delta", "ci_low", "ci_high", "p_value",
                              "p_value_t", "significant", "n_queries")
                },
                "combo": label,
                "dataset": "DS-v1",
                "db_mb": 6.8789,
                "family": "ds-v1-kfold",
                "fold_count": 5,
                "levers": {**LEVERS_OFF, **lever_overrides},
                "mrr": round(sum(pq[q]["mrr"] for q in all_ids) / 58, 4),
                "mv": False,
                "n_queries": 58,
                "notes": notes,
                "p95_ms": round(_percentile(sorted(dur.values()), 95.0), 4),
                "p95_source": "kfold-sweep (serial run, otherwise-quiet "
                "reference machine per MEASURE.md)",
                "recall_at_10": round(sum(pq[q]["recall_at_10"] for q in all_ids) / 58, 4),
                "split_basis": "pooled k-fold rotation (each query held out "
                "exactly once, D-009); tune/validate are the seed-24301 "
                "29/29 halves reconstructed from the same per-query maps",
                "tune": block(pq, dur, tune_ids),
                "validate": block(pq, dur, validate_ids),
            }
        )
    return rows


def main():
    doc = json.loads(ART.read_text())
    assert doc["verdict"]["status"] == "pending" and doc["shipped_defaults"]["row"] is None

    # --- ladder rows (ds-v1-kfold family) ---------------------------------
    assert not any(r["combo"] in ("enrich+rerank-off", "enrich_idf+rerank-off")
                   for r in doc["rows"]), "ladder rows already present"
    doc["rows"].extend(ladder_rows())

    # --- DS-v2 rows (new family) ------------------------------------------
    payload = json.loads(
        Path("benchmarks/quality/ladder-v2/rows-ds2.json").read_text())
    # Rows carry the L1 leg only (the ladder's evidence); the 44-query L5
    # leg is a measured structural zero recorded in the verdict's l5_note
    # (pooled figures from the measurement doc's l5 legs), never blended
    # into these rows — and the emitted payload's single corpus='all' L5
    # row per config is dropped (not a legal family label; per-corpus L5
    # splits carry no lever signal to justify minting them).
    payload["rows"] = [r for r in payload["rows"] if r.get("corpus") != "all"]
    for r in payload["rows"]:
        r["notes"] = (
            "T023 DS-v2 zero-shot validation leg (D-011): tune on DS-v1, "
            "validate zero-shot on DS-v2; per-corpus rows plus "
            "macro-average, never an aggregate alone, never diffed against "
            "any DS-v1 row. L1 leg only — the 44 L5 queries are evaluated "
            "and recorded separately (structural zero: L5 retrieval is OKF "
            "bundle search, and the benchmark corpora carry no .knowledge/ "
            "bundle — see the verdict's l5 note)."
        )
        r["split_basis"] = (
            "zero-shot full-set over the DS-v2 ground truth "
            "(evaluate_full_set; lever decisions were made on DS-v1)"
        )
        r["p95_source"] = (
            "zero-shot run (serial, otherwise-quiet reference machine per "
            "MEASURE.md)"
        )
    doc["rows"].extend(payload["rows"])

    # --- family label finalization (T023's explicit provision) -------------
    fam = doc["dataset"]["families"]["ds-v2"]
    fam["corpora"] = ["yarl", "attrs-26.1.0"]
    fam["corpora_note"] = (
        "Labels finalized by T023 from the manifest's symbol_id_prefix "
        "convention (manifest.json corpora keys: 'yarl' for the t2 "
        "snapshot, 'attrs-26.1.0' for the T007-vendored MIT corpus) — the "
        "same prefixes the expectations' corpus-prefixed file paths carry, "
        "so per-corpus rows derive straight from the dataset."
    )

    # --- verdict evidence (disposition itself is T024's) -------------------
    v = doc["verdict"]
    v["fold_count"] = 5
    v["per_fold_spread"] = {
        "winning_candidate": "multivector",
        "per_fold_delta": [0.1832, 0.1624, 0.1491, 0.1169, 0.0969],
        "delta_min": 0.0969,
        "delta_max": 0.1832,
        "rotation_mean_delta": 0.1417,
        "note": "descriptive only, never the significance basis (D-009); "
        "all five folds positive — the DS-v1 leg leaves; the zero-shot "
        "leg lives in the ds-v2 rows",
    }
    v["ds2_counts"]["l1_queries"] = 154
    v["ds2_counts"]["l5_queries"] = 44
    v["ds2_counts"]["note"] = (
        "filled by T023 from the sealed dataset (T009/T010 loader counts); "
        "floors met: 154 >= 150 L1, 44 >= 40 L5"
    )
    v["sc1_actual"] = {
        "ds_v1_kfold_best": {
            "combo": "multivector",
            "recall_at_10": 0.5588,
            "mrr": 0.3395,
            "both_targets_reached": True,
            "zero_shot_validated": False,
        },
        "ds_v2_macro_best": {
            "combo": "all-levers-off (incumbent)",
            "recall_at_10": 0.4778,
            "mrr": 0.3769,
            "both_targets_reached": False,
            "note": "the incumbent carries the best DS-v2 macro-average of "
            "every measured configuration — no candidate improves on it "
            "zero-shot",
        },
        "reached_on_full_evidence_base": False,
        "basis": "SC-1 is evaluated on the full upgraded evidence base "
        "(DS-v1 k-fold pooled + DS-v2 zero-shot), per family — never a "
        "single-leg figure presented alone. The DS-v1 leg reached both "
        "targets via multivector (0.5588/0.3395); that configuration is "
        "refuted zero-shot (DS-v2 macro 0.4632/0.2844, MRR -0.0925 vs the "
        "incumbent). The DS-v2 leg reaches neither target for any "
        "configuration (best recall macro 0.4778 < 0.50; the 0.3769 MRR "
        "figure belongs to that same recall-short incumbent row).",
    }
    v["margins"] = {
        "ds_v1_kfold_best_vs_targets": {
            "recall_at_10": 0.0588,
            "mrr": 0.0095,
        },
        "ds_v2_macro_best_vs_targets": {
            "recall_at_10": -0.0222,
            "mrr": 0.0469,
        },
        "basis": "per-family actual minus target, per metric; the full-"
        "evidence outcome is governed by the weaker leg",
    }
    v["evidence_base"] = (
        "5-fold seeded rotation over the 58 DS-v1 L1 queries (pooled "
        "per-query paired bootstrap at n=58, per-fold spread descriptive "
        "only) PLUS zero-shot DS-v2 validation over both corpora (154 L1 "
        "/ 44 L5 queries; per-corpus rows + macro-average in the ds-v2 "
        "family) — never the legacy single split (TC-029)"
    )
    v["statement"] = (
        "EVIDENCE COMPLETE, DISPOSITION PENDING (T024). The ladder's three "
        "D-016 candidates on the DS-v1 k-fold pooled aggregate: (a) "
        "enrich+rerank-off delta+0.0988 p=0.0491 CI[+0.0037,+0.2006] "
        "CLEARS; (b) enrich_idf+rerank-off byte-identical to (a) on DS-v1 "
        "CLEARS; (c) multivector delta+0.1414 p=0.0035 CI[+0.0527,+0.2373] "
        "CLEARS decisively and reaches both SC-1 targets on that leg "
        "(0.5588/0.3395). The zero-shot DS-v2 leg refutes transfer: "
        "multivector's gain reverses on the unseen corpus (attrs "
        "delta-0.0432 p=0.15; macro MRR -0.0925) and enrich+rerank-off's "
        "+9.9pp recall collapses to +0.0003 macro with -0.0215 MRR "
        "(per-corpus bootstraps n.s.; on DS-v2's NEW yarl queries the "
        "enrich delta is -0.0122 — a query-population effect, not only a "
        "corpus effect). PRF measured negative at both grid points (T020). "
        "SC-1 targets stay 0.50/0.33 and match rules are untouched; the "
        "ship-or-document disposition lands with T024."
    )
    v["l5_note"] = (
        "The 44 DS-v2 L5 queries were evaluated through the same seam and "
        "score 0.0 for every configuration: L5 retrieval is OKF bundle "
        "search (no retrieval tunables), and the benchmark corpora carry "
        "no .knowledge/ bundle — the same recorded-as-structural-zero "
        "discipline as the committed DS-v1 quality baseline ('surface "
        "absent', benchmarks/baselines/DS-v1/README.md). Never blended "
        "into the L1 rows."
    )

    # --- measurement bookkeeping -------------------------------------------
    doc["measurement"]["landed"].append(
        "T023 (FR-006): confirmation ladder re-run on the upgraded "
        "evidence base — DS-v1 legs (a)/(b) via run_ladder_candidates.py, "
        "(c)=multivector via the T021 sweep; DS-v2 zero-shot leg via "
        "run_ds2_zeroshot.py (per-corpus + macro rows, ds-v2 family); "
        "verdict evidence filled, disposition pending T024"
    )
    doc["measurement"]["todo"] = [
        "T024: close ship-or-document — shipped_defaults row + protected-"
        "baseline re-measures on the ship branch, or the documented "
        "shortfall + next binding constraint on the document branch "
        "(exactly one)",
        "re-measure the protected baselines on shipping (TC-027: "
        "all-levers-off equals the committed artifact at 4 decimals)",
    ]

    ART.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("rows total:", len(doc["rows"]))
    for r in doc["rows"]:
        print(f"  {r['family']:12s} {r.get('corpus', '-'):15s} {r['combo']}")


if __name__ == "__main__":
    main()
