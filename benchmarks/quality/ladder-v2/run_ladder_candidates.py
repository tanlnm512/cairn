"""D-016 (T023 ladder-prep): the two enrich-direction confirmation-ladder
candidates on the DS-v1 k-fold rotation.

The v1 record's near-miss direction (benchmarks/quality/ablation.md Figure 2:
``enrich-on + rerank-off`` at Δ +0.1123, p = 0.118 on the 29-query validate
split) re-measured under D-009 pooling: the same 5-fold seeded rotation
(fold_seed 24301) T014's FR-003 calibration used, so the pooled paired
bootstrap runs at n = 58 (every query held out exactly once) instead of the
v1 campaign's n = 29 single split.

Candidates (exactly two, D-016):

* ``enrich-rerankoff``   -- RetrievalParams(enrich=True, rerank=False):
  the v1 record's own first candidate, unchanged.
* ``enrichidf-rerankoff`` -- RetrievalParams(enrich=True, enrich_idf=True,
  rerank=False): the FR-003 repaired successor.  The cutoff is the SHIPPED
  ``query_enrich.ENRICH_DF_MAX_FRACTION`` held at HEAD-plus-working-tree --
  recorded, never overridden (T014's calibration resolved the shipped value;
  if the tree moved it, whatever it holds is what runs).

Protocol (D-009, inherited verbatim from T014's
fr003-calibration/run_fr003_sweep.py): one process, one candidate, one
output document; torch threads pinned to 1, local bge-m3
(CAIRN_EMBED_BACKEND unset), brute-force cosine (CAIRN_ANN_BACKEND=off),
rerank marker CAIRN_RERANK=1 with flat pairs and gate margin 0.45 (the
incumbent all-levers-off row is rerank-active under the marker, exactly
today's retrieval; the candidates carry rerank=False, which wins over the
marker).  Models warmed with one untimed semantic_search before the
rotation.  The scratch DB is T014's build (snapshot into the ladder
workroot; facts verified at startup), never rebuilt.

``analyze`` derives the figures from the two emitted documents -- no
re-retrieval: pooled Δ/p/95% CI per candidate (the D-009 aggregate's own
bootstrap, n = 58), per-split tune/validate recall/MRR/p95 reconstructed
from the fold reports over the seed-24301 29/29 halves (T014's
analyze_fr003.py approach), and the 95% guard verdict.  The runs execute
SERIALLY on an otherwise-quiet reference machine per MEASURE.md (the
D-015 parallel-wave plan was replaced by that runbook before any run:
concurrent model processes time-share one GPU and only inflate p95), so
in-sweep p95 is a quiet-machine figure, not a contention caveat.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GROUND_TRUTH = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "ground_truth"
HERE = Path(__file__).resolve().parent

#: candidate key -> (combo label, RetrievalParams fields).  rerank=False is
#: the point of the direction (the v1 record: the reranker masks enrichment
#: gains at ~40x p95); everything else stays incumbent.
CANDIDATES: dict[str, tuple[str, dict]] = {
    "enrich-rerankoff": (
        "enrich+rerank-off",
        {"enrich": True, "rerank": False},
    ),
    "enrichidf-rerankoff": (
        "enrich_idf+rerank-off",
        {"enrich": True, "enrich_idf": True, "rerank": False},
    ),
}

#: The two output documents (D-016 mission contract).
SWEEP_DOCS = {
    "enrich-rerankoff": HERE / "sweep-ladder-enrich-rerankoff.json",
    "enrichidf-rerankoff": HERE / "sweep-ladder-enrichidf-rerankoff.json",
}

#: DS-v1's recorded build facts (T014's scratch_db.py, T008/T010 authoring):
#: a drifted snapshot must abort the measurement, never feed it.
EXPECTED_FACTS = {"embeddings_rows": 1066, "symbols": 1066}
#: Committed DS-v1 session anchors (ablation.md header/Figures 1-3) with
#: the documented session band -- the integrity check every run reports.
COMMITTED_FULL = {"recall_at_10": 0.4174, "mrr": 0.2862}
BAND = {"recall_at_10": 0.002, "mrr": 0.006}
ALL_LEVERS_OFF = "all-levers-off"


def cmd_run(args: argparse.Namespace) -> int:
    if args.candidate not in CANDIDATES:
        print(f"unknown candidate {args.candidate!r}", file=sys.stderr)
        return 2

    # D-009 protocol pins, set before any cairn model code reads them.
    import os

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

    label, fields = CANDIDATES[args.candidate]
    # Record (never override) the shipped cutoff the run inherits.
    shipped_cutoff = query_enrich.ENRICH_DF_MAX_FRACTION

    combos = [{"name": label, "params": RetrievalParams(**fields)}]

    gt = load_ground_truth(GROUND_TRUTH)
    queries = [q for q in gt if q.level == "L1"]
    if len(queries) != 58:
        print(f"expected 58 L1 queries, got {len(queries)}", file=sys.stderr)
        return 1

    conn = get_db(args.db)
    try:
        n_vec = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        n_sym = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        n_df = conn.execute("SELECT COUNT(*) FROM term_df").fetchone()[0]
        facts = {"embeddings_rows": n_vec, "symbols": n_sym}
        if facts != EXPECTED_FACTS or n_df < 1:
            print(
                f"SCRATCH DB DRIFT: {facts} term_df={n_df} != recorded "
                f"{EXPECTED_FACTS}",
                file=sys.stderr,
            )
            return 1
        # Warm both models outside the timed seam (bge-m3 embed + the
        # reranker under the marker; limit=1 still arms the 50-pair pool --
        # the incumbent row is rerank-active).
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

    print(
        f"wrote {out} (label={label} folds={doc['dataset']['k_folds']} "
        f"n={doc['dataset']['n_queries']} elapsed_s={elapsed:.1f} "
        f"enrich_df_max_fraction={shipped_cutoff})"
    )
    for cand, agg in doc["aggregate"]["combos"].items():
        pooled = agg["pooled"]
        b = pooled["baseline"]
        c = pooled["candidate"]
        boot = agg["bootstrap"]
        print(
            f"{cand}: pooled_candidate={sum(c) / len(c):.4f} "
            f"pooled_baseline={sum(b) / len(b):.4f} "
            f"delta={boot['delta']:.6f} p={boot.get('p_value')} "
            f"ci={boot.get('ci_low')}..{boot.get('ci_high')} "
            f"significant={boot.get('significant')}"
        )
    return 0


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _per_query(doc: dict, combo: str) -> dict[str, dict[str, float]]:
    """Per-query {qid: {recall_at_10, mrr}} read once per query.

    The harness's source-fold rule (analyze_fr003.py): fold 1's report for
    fold 0's held-out ids, else fold 0's (candidate and baseline share one
    embedding state).
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


def _fmt_block(block: dict) -> str:
    return (
        f"{block['recall_at_10']:.4f}/{block['mrr']:.4f}"
        f" (p95 {block['p95_ms']:.1f} ms*)"
    )


def cmd_analyze(_args: argparse.Namespace) -> int:
    sys.path.insert(0, str(REPO_ROOT / "src"))

    from cairn.eval import _percentile, load_ground_truth, split_queries

    gt = [q for q in load_ground_truth(GROUND_TRUTH) if q.level == "L1"]
    assert len(gt) == 58, f"expected 58 L1 queries, got {len(gt)}"
    tune_ids, validate_ids = split_queries(gt, seed=24301)
    assert len(tune_ids) == 29 and len(validate_ids) == 29
    all_ids = sorted(tune_ids + validate_ids)

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

    sections: list[str] = []
    verdicts: dict[str, dict] = {}
    per_query_by_cand: dict[str, dict] = {}

    for key, path in SWEEP_DOCS.items():
        doc = _load(path)
        label = CANDIDATES[key][0]
        cand_pq = _per_query(doc, label)
        base_pq = _per_query(doc, ALL_LEVERS_OFF)
        cand_dur = _durations(doc, label)
        base_dur = _durations(doc, ALL_LEVERS_OFF)
        assert sorted(cand_pq) == all_ids, f"{label} did not cover all 58 ids"
        assert sorted(base_pq) == all_ids, "baseline did not cover all 58 ids"
        per_query_by_cand[key] = cand_pq

        agg = doc["aggregate"]["combos"][label]
        boot = agg["bootstrap"]
        pooled_cand = {
            m: _mean([cand_pq[q][m] for q in all_ids])
            for m in ("recall_at_10", "mrr")
        }
        pooled_base = {
            m: _mean([base_pq[q][m] for q in all_ids])
            for m in ("recall_at_10", "mrr")
        }
        integrity_ok = {
            m: abs(pooled_base[m] - COMMITTED_FULL[m]) <= BAND[m]
            for m in pooled_base
        }
        clears = bool(
            boot["significant"] and boot["delta"] > 0 and boot["ci_low"] > 0
        )
        verdicts[key] = {
            "label": label,
            "delta": boot["delta"],
            "p_value": boot["p_value"],
            "ci_low": boot["ci_low"],
            "ci_high": boot["ci_high"],
            "significant": boot["significant"],
            "clears_95_guard": clears,
            "integrity_ok": all(integrity_ok.values()),
        }

        desc = agg["descriptive"]
        spread = desc["spread"]
        sections.append(
            f"### {label}\n"
            f"\n"
            f"| | pooled (n=58) | tune (n=29) | validate (n=29) |\n"
            f"|---|---|---|---|\n"
            f"| candidate | {pooled_cand['recall_at_10']:.4f}/"
            f"{pooled_cand['mrr']:.4f} | "
            f"{_fmt_block(_split_block(cand_pq, cand_dur, tune_ids))} | "
            f"{_fmt_block(_split_block(cand_pq, cand_dur, validate_ids))} |\n"
            f"| incumbent (all-levers-off) | {pooled_base['recall_at_10']:.4f}/"
            f"{pooled_base['mrr']:.4f} | "
            f"{_fmt_block(_split_block(base_pq, base_dur, tune_ids))} | "
            f"{_fmt_block(_split_block(base_pq, base_dur, validate_ids))} |\n"
            f"\n"
            f"- Pooled paired bootstrap vs incumbent (D-009 aggregate, "
            f"n={boot['n_queries']}): **Δ = {boot['delta']:+.4f}**, "
            f"p = {boot['p_value']:.4f}, 95% CI "
            f"[{boot['ci_low']:+.4f}, {boot['ci_high']:+.4f}] "
            f"(t-test cross-check p = {boot['p_value_t']:.4f}).\n"
            f"- 95% guard verdict: "
            f"{'**CLEARS**' if clears else '**does NOT clear**'} "
            f"(guard = significant AND Δ>0 AND CI excludes 0).\n"
            f"- Per-fold descriptive spread (never the significance basis): "
            f"rotation-mean Δ {desc['rotation_mean']['delta']:+.4f}, "
            f"per-fold Δ range [{spread['delta_min']:+.4f}, "
            f"{spread['delta_max']:+.4f}].\n"
            f"- Baseline integrity vs committed DS-v1 full-set "
            f"{COMMITTED_FULL['recall_at_10']}/{COMMITTED_FULL['mrr']}: "
            f"{pooled_base['recall_at_10']:.4f}/{pooled_base['mrr']:.4f}, "
            f"within band: {all(integrity_ok.values())} "
            f"(recall ±{BAND['recall_at_10']}, MRR ±{BAND['mrr']}).\n"
        )

    # (a)-vs-(b): identical per-query outcomes means the shipped 0.90 cutoff
    # dropped nothing on DS-v1 in the rerank-off direction too (T014's
    # term_df distribution finding, quantified from these runs).
    a_pq = per_query_by_cand["enrich-rerankoff"]
    b_pq = per_query_by_cand["enrichidf-rerankoff"]
    identical = a_pq == b_pq
    diff_ids = sorted(q for q in all_ids if a_pq[q] != b_pq[q])

    va, vb = verdicts["enrich-rerankoff"], verdicts["enrichidf-rerankoff"]
    headline = (
        "YES" if (va["clears_95_guard"] or vb["clears_95_guard"]) else "NO"
    )
    parts = [
        "# Ladder-prep figures (D-016) — enrich-direction candidates, "
        "DS-v1 5-fold\n",
        "Protocol (D-009, inherited from T014's fr003-calibration runner): "
        "torch threads 1, local bge-m3, brute-force cosine, rerank under "
        "the CAIRN_RERANK=1 marker (flat pairs, gate 0.45); 5-fold seeded "
        "rotation (fold_seed 24301) over the 58 L1 queries through the "
        "unchanged evaluate_on seam; candidates carry rerank=False (which "
        "wins over the marker), everything else incumbent. Candidate (b) "
        "ran at the SHIPPED query_enrich.ENRICH_DF_MAX_FRACTION — never "
        "overridden.\n",
        "Cells are recall@10/MRR. `*` in-sweep p95 — measured in the "
        "serial run on an otherwise-quiet reference machine (MEASURE.md; "
        "the D-015 parallel-wave plan was dropped before any run), so it "
        "is a quiet-machine figure rather than a contention caveat.\n",
        "## Headline — does either pooled Δ clear 95% at n=58?\n",
        f"**{headline}.** (a) enrich+rerank-off: Δ {va['delta']:+.4f}, "
        f"p = {va['p_value']:.4f}, CI [{va['ci_low']:+.4f}, "
        f"{va['ci_high']:+.4f}] — "
        f"{'clears' if va['clears_95_guard'] else 'does not clear'}; "
        f"(b) enrich_idf+rerank-off: Δ {vb['delta']:+.4f}, "
        f"p = {vb['p_value']:.4f}, CI [{vb['ci_low']:+.4f}, "
        f"{vb['ci_high']:+.4f}] — "
        f"{'clears' if vb['clears_95_guard'] else 'does not clear'}. "
        f"The v1 single-split near-miss was Δ+0.1123 at p=0.118 on n=29; "
        f"D-009 pooling doubles n to 58 via the rotation.\n",
    ]
    parts.extend(sections)
    parts.append(
        "## (a) vs (b): the FR-003 repair's effect in this direction\n"
        f"\n"
        f"Per-query outcomes {'BYTE-IDENTICAL' if identical else 'differ'} "
        f"between enrich+rerank-off and enrich_idf+rerank-off at the shipped "
        f"cutoff ({len(diff_ids)}/58 queries differ"
        + (
            f": {', '.join(diff_ids)}"
            if diff_ids
            else ")"
        )
        + ". Consistent with T014's calibration finding: DS-v1's highest "
        "term_df fraction is 'test' at 0.8583, so the 0.90 cutoff's "
        "drop-set is empty and enrich_idf is inert on this corpus.\n"
    )
    (HERE / "FIGURES.md").write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {HERE / 'FIGURES.md'}")
    for key, v in verdicts.items():
        print(
            f"{v['label']}: delta={v['delta']:+.6f} p={v['p_value']:.4f} "
            f"ci=[{v['ci_low']:+.6f},{v['ci_high']:+.6f}] "
            f"clears_95={v['clears_95_guard']} integrity_ok={v['integrity_ok']}"
        )
    print(f"(a)-vs-(b) identical: {identical} (differ: {len(diff_ids)})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="one candidate, one k-fold sweep")
    run_p.add_argument(
        "--candidate",
        required=True,
        choices=sorted(CANDIDATES),
    )
    run_p.add_argument("--db", default="/tmp/ladder-v2/graph.db")
    run_p.add_argument(
        "--out",
        required=True,
        help="output path for the kfold sweep JSON",
    )
    run_p.add_argument("--folds", type=int, default=5)
    run_p.set_defaults(func=cmd_run)

    an_p = sub.add_parser(
        "analyze", help="derive FIGURES.md from the emitted sweep docs"
    )
    an_p.set_defaults(func=cmd_analyze)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
