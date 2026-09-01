# FR-003 cutoff calibration (T014)

Artifacts, method, and caveats for the `ds-v1-kfold` FR-003 rows committed
in [`../ablation.json`](../ablation.json) (the unified record; `../ablation-v2.json` before the 2026-08-17 unification). Measured 2026-08-17.

## Method (D-009 protocol, inherited from the v1 record)

Scratch graph over `benchmarks/datasource/t2/yarl` (copy + empty `.git`
scanner marker; build facts verified against the recorded DS-v1 identity:
1 repo / 24 files / 1066 symbols / 0 parse errors), one real-embedding pass
with the local `BAAI/bge-m3` (torch threads pinned to 1, no vec0 index —
brute-force cosine via `CAIRN_ANN_BACKEND=off`, rerank under the
`CAIRN_RERANK=1` marker with flat pairs and gate margin 0.45). Each cutoff
is its own process: `run_fr003_sweep.py` sets
`query_enrich.ENRICH_DF_MAX_FRACTION` process-locally (the module global
is read at call time inside `enrich()`'s ubiquity predicate — no source
file is touched; the shipped code value stays 0.90) and runs
`run_sweep_kfold` over the 58 L1 queries, 5 seeded rotation folds
(fold_seed 24301), through the unchanged `evaluate_on` seam.

## Files

| file | what |
|---|---|
| ~~scratch_db.py~~ (retired) | build + embed the scratch measurement DB |
| ~~run_fr003_sweep.py~~ (retired) | one k-fold sweep per cutoff (also `--cutoff baseline` = the integrity run) |
| ~~remeasure_p95.py~~ (retired) | quiet-machine p95 re-measurement (baseline + shipped config) |
| ~~diagnose_d03.py~~ (retired) | the L1-D03 / L1-I03 corpus-level diagnostic (AC4 evidence) |
| ~~analyze_fr003.py~~ (retired) | analysis record + decision + `rows-fr003.json` |
| `sweep-baseline.json`, `sweep-df0.{75,80,85,90}.json` | raw k-fold sweep documents |
| `p95-remeasure.json` | quiet p95 pass output (+ per-query determinism cross-check) |
| `d03-diagnostic.json` | measured diagnostic output |
| `analysis.json` | integrity gate, grid figures, bootstrap verdicts, AC4, decision |
| `rows-fr003.json` | the row fragment merged into `ablation.json` |

## Results in one paragraph

The integrity run reproduced the committed DS-v1 session baseline exactly
(pooled 0.4174/0.2862, drift 0.0000; tune/validate anchors to 4 decimals).
All four grid cutoffs {0.75, 0.80, 0.85, 0.90} produced byte-identical
per-query outcomes (pooled 0.4123/0.2603; bootstrap vs all-levers-off
Δ −0.0052, p = 0.82) — on DS-v1 the [0.75, 0.95] band is inert: the
highest `term_df` fraction is `test` at 0.8583 and `url` sits at 0.2711,
so no in-band cutoff drops any token the 58 queries' enrichments append.
The shipped cutoff therefore stays 0.90 (D-004 default; ties resolve
there). AC4's L1-D03 clause is not reachable in-band (diagnostic: below
0.2711 the `URL` token drops from both legs and the query recovers to its
incumbent state — recall 1.0 at rank 6); the tune split's single
regression (L1-I03) does not recover even below the band (its rare
`split` identifier, DF 0.0047, keeps the dilution). Full detail in
`analysis.json` and `../ablation.md`.

## Caveats

- **Grid truncation (D-014).** The upper-bound point 0.95 was descoped on
  wall-clock grounds after the first three cutoffs proved byte-identical;
  0.95 measures the same drop-set as 0.90 on this corpus (nothing above
  0.8583), and AC3's wording ("may calibrate within 0.75–0.95") permits
  the truncated grid. The shipped default sits mid-grid.
- **p95 contention.** The k-fold sweeps ran while a full test suite
  executed on the same machine (orchestrator-confirmed), so in-sweep
  `durations_ms` are inflated. Committed rows: the integrity and shipped
  rows carry quiet-machine re-measured p95 (`p95-remeasure.json`); the
  non-shipped grid rows carry in-sweep p95 under that stated caveat.
  recall/MRR are deterministic under the protocol pins and were
  cross-checked per-query between the sweeps and the quiet pass (58/58
  equal, both configs).
- The scratch DB lives under `/tmp/fr003-calibration/` — rebuild with
  `scratch_db.py` (build then embed; the embed pass is the one long
  command).
