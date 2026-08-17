"""T014 (FR-003): the L1-D03 corpus-level diagnostic (AC4 evidence).

Three facts the calibration record needs, measured live on the scratch
DS-v1 graph (same protocol pins as the sweeps):

1. **The DF distribution around the sweep band** -- the top term_df
   fractions and, per swept cutoff, which of the enrichment vocabulary's
   tokens a cutoff would drop (the [0.75, 0.95] band's actual leverage).
2. **The regression reproduces under DF-blind enrichment** -- L1-D03's
   target (yarl/_url.py#pre_encoded_url, its single grade-2 expectation)
   at recall 0.0 with ``enrich=True`` alone.
3. **The repair mechanism works where the token IS ubiquitous** -- with
   ``enrich_idf`` on and the cutoff lowered below 'url's real DS-v1 DF
   fraction, 'URL' drops from BOTH legs and L1-D03 returns to its
   all-levers-off state (recall 1.0 at the incumbent's rank). This is
   DIAGNOSTIC evidence about the mechanism, not a shipped configuration:
   the sweep band is fixed at [0.75, 0.95] by the spec and the shipped
   cutoff by D-004 (0.90, ties resolve there).

Also records the incumbent (all-levers-off) state for the same query --
its "historical passing state" in the committed record is recall 1.0 (the
1.0 -> 0.0 fall was recall); the incumbent rank is 6, not 1.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
GROUND_TRUTH = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "ground_truth"
OUT = HERE / "d03-diagnostic.json"

#: The swept grid (spec: 0.75-0.95) plus the two below-band probes that
#: bracket 'url's real DS-v1 DF fraction (~0.2711).
SWEEP_CUTOFFS = [0.75, 0.80, 0.85, 0.90, 0.95]
PROBE_CUTOFFS = [0.2712, 0.25]


def main() -> int:
    os.environ["CAIRN_RERANK"] = "1"
    os.environ["CAIRN_ANN_BACKEND"] = "off"

    import cairn.paths  # noqa: F401  (injects ~/.cairn/lib: torch importable)

    import torch

    torch.set_num_threads(1)

    from cairn.eval import evaluate_graded_query, load_ground_truth
    from cairn.graph import query_enrich
    from cairn.graph.queries import semantic_search
    from cairn.graph.schema import get_db
    from cairn.graph.semantic import RetrievalParams, _term_df_lookup

    gt = {q.query_id: q for q in load_ground_truth(GROUND_TRUTH)}
    d03 = gt["L1-D03"]
    conn = get_db("/tmp/fr003-calibration/graph.db")
    semantic_search(conn, "warm up the models", limit=1)  # untimed load

    # --- Fact 1: the DF distribution and the band's leverage. -----------
    top = conn.execute(
        "SELECT token, symbol_df, n_symbols FROM term_df "
        "ORDER BY symbol_df DESC LIMIT 10"
    ).fetchall()
    distribution = [
        {"token": t, "symbol_df": d, "n_symbols": n, "fraction": round(d / n, 4)}
        for t, d, n in top
    ]
    lookup = _term_df_lookup(conn)
    eq_naive = query_enrich.enrich(d03.text)
    vocab = sorted(
        {tok.lower() for tok in (*eq_naive.identifiers, *eq_naive.sparse_query.split())}
    )
    band_drops = {
        f"{c:.2f}": [
            {"token": t, "fraction": round(lookup(t)[0] / lookup(t)[1], 4)}
            for t in vocab
            if lookup(t) is not None and lookup(t)[0] / lookup(t)[1] > c
        ]
        for c in SWEEP_CUTOFFS
    }

    # --- Facts 2+3: the query outcome under each configuration. ---------
    outcomes: dict[str, dict] = {}
    for label, params in [
        ("all_levers_off", None),
        ("enrich_df_blind", RetrievalParams(enrich=True)),
    ]:
        rec, mrr = evaluate_graded_query(conn, None, d03, k=10, params=params)
        outcomes[label] = {
            "recall_at_10": round(rec, 4),
            "mrr": round(mrr, 4),
            "rank_of_target": (round(1 / mrr) if mrr > 0 else None),
        }
    query_enrich.ENRICH_DF_MAX_FRACTION = 0.90
    rec, mrr = evaluate_graded_query(
        conn, None, d03, k=10, params=RetrievalParams(enrich=True, enrich_idf=True)
    )
    outcomes["enrich_idf_at_0.90"] = {
        "recall_at_10": round(rec, 4),
        "mrr": round(mrr, 4),
        "rank_of_target": (round(1 / mrr) if mrr > 0 else None),
    }
    probes: dict[str, dict] = {}
    for cutoff in PROBE_CUTOFFS:
        query_enrich.ENRICH_DF_MAX_FRACTION = cutoff
        eq = query_enrich.enrich(d03.text, df_lookup=lookup)
        rec, mrr = evaluate_graded_query(
            conn,
            None,
            d03,
            k=10,
            params=RetrievalParams(enrich=True, enrich_idf=True),
        )
        probes[f"{cutoff:.4f}"] = {
            "identifiers": list(eq.identifiers),
            "sparse_query": eq.sparse_query,
            "recall_at_10": round(rec, 4),
            "mrr": round(mrr, 4),
            "rank_of_target": (round(1 / mrr) if mrr > 0 else None),
        }
    query_enrich.ENRICH_DF_MAX_FRACTION = 0.90  # restore the shipped value

    # --- The one tune-split casualty under in-band cutoffs: L1-I03. -----
    # Its enrichment appends identifiers ('split', 'url') -- the same
    # 0.2711-DF 'url' token, i.e. the same root cause as L1-D03.
    i03 = gt["L1-I03"]
    query_enrich.ENRICH_DF_MAX_FRACTION = 0.90
    eq90 = query_enrich.enrich(i03.text, df_lookup=lookup)
    rec90, mrr90 = evaluate_graded_query(
        conn, None, i03, k=10, params=RetrievalParams(enrich=True, enrich_idf=True)
    )
    query_enrich.ENRICH_DF_MAX_FRACTION = 0.25
    eq25 = query_enrich.enrich(i03.text, df_lookup=lookup)
    rec25, mrr25 = evaluate_graded_query(
        conn, None, i03, k=10, params=RetrievalParams(enrich=True, enrich_idf=True)
    )
    query_enrich.ENRICH_DF_MAX_FRACTION = 0.90  # restore the shipped value
    i03_block = {
        "identifiers_at_0.90": list(eq90.identifiers),
        "at_0.90": {"recall_at_10": round(rec90, 4), "mrr": round(mrr90, 4)},
        "identifiers_at_0.25": list(eq25.identifiers),
        "at_0.25": {"recall_at_10": round(rec25, 4), "mrr": round(mrr25, 4)},
        "note": "the tune split's single regression under in-band cutoffs "
        "does NOT recover below the band either (measured at 0.25): its "
        "enrichment appends two identifiers ('split', 'url'); 'url' "
        "(DF 0.2711) drops below 0.2711 but 'split' is rare (DF ~0.03) "
        "and no max_df-style cutoff in any plausible band drops a RARE "
        "token -- the dilution survives. This regression is an "
        "(DF 0.2711) drops below 0.2711 but 'split' is rare (DF 5/1066 = "
        "0.0047) and no max_df-style cutoff in any plausible band drops a "
        "RARE token -- the dilution survives (the sparse leg also keeps "
        "the compound 'split_url'). This regression is an "
        "identifier-append effect the FR-003 DF lever cannot repair by "
        "construction (it suppresses ubiquity, not specificity; TC-012 "
        "keeps rare identifiers at full weight ON PURPOSE).",
    }

    doc = {
        "schema": "cairn-fr003-d03-diagnostic/1",
        "query": {
            "id": "L1-D03",
            "text": d03.text,
            "expectations": [
                {"symbol_id": e.symbol_id, "grade": e.grade}
                for e in d03.expectations
            ],
        },
        "term_df_top10": distribution,
        "d03_enrichment_vocabulary": vocab,
        "band_token_drops": band_drops,
        "outcomes": outcomes,
        "below_band_probes": probes,
        "L1_I03_tune_casualty": i03_block,
        "reading": "The [0.75,0.95] sweep band cannot drop 'url' on DS-v1 "
        "(its real DF fraction is ~0.271): the regression L1-D03 records "
        "is not a >90% ubiquity effect on this corpus, and no in-band "
        "cutoff recovers it. Lowering the cutoff below 0.2711 (diagnostic "
        "only, outside the spec'd band) drops 'URL' from both legs and "
        "restores L1-D03 to its all-levers-off state -- the mechanism "
        "itself is proven, on this corpus and in T012/T013's boundary "
        "unit tests. The tune split's single casualty (L1-I03) does NOT "
        "recover even at 0.25: its rare 'split' identifier (DF 0.0047) "
        "keeps the dilution -- an identifier-append effect outside the DF "
        "lever's reach by construction. NOTE: the 'identifiers' record is "
        "the extraction report and stays untouched by design; the drop "
        "applies to the appended dense tail and the sparse term list.",
    }
    OUT.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(doc, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
