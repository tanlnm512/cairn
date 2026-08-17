# retrieval-quality-v2 — remaining measurement runs (T020, T021, T023, T024)

Runbook for the campaign's deferred measurements. Everything before this
(T001–T019, T022; 19/24 tasks) is landed on `feat/retrieval-quality-v2`,
including T014's FR-003 calibration and its rows in `ablation.{json,md}` (the unified record; `ablation-v2.{json,md}` before the 2026-08-17 unification).
What remains is machine-time: the PRF and multi-vector ablation rows, and the
D-016 confirmation ladder. Run this on the **reference machine, quiet** (the
D-009 protocol: no concurrent load — on MPS hardware, concurrent model
processes time-slice one GPU and only inflate p95; see D-015's diagnosis).

Branch: `feat/retrieval-quality-v2` · Prereqs: `uv sync`; the bge-m3 and
bge-reranker-base models (auto-cached on first use, or pre-download);
`benchmarks/datasource/` (committed; budgets checked by
`uv run python scripts/verify_datasource.py --budget`).

## Step 0 — scratch DBs (once; ~1 + ~14 min)

```bash
# DS-v1 graph + real bge-m3 embeddings (base rows; shared by ladder + PRF)
uv run python benchmarks/quality/fr003-calibration/scratch_db.py build
#   -> /tmp/fr003-calibration/graph.db  (1066 symbols, 852 term_df rows)

# DS-v1 graph + base AND mv rows (name + docstring kinds, 3x embed time)
uv run python benchmarks/quality/fr005-mv/scratch_db.py build
CAIRN_ANN_BACKEND=off OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  uv run cairn embed --db /tmp/fr005-mv/graph.db --multivector
#   -> 1066 base + ~1240 mv rows; also note db sizes for the growth factor:
#       ls -l /tmp/fr003-calibration/graph.db /tmp/fr005-mv/graph.db
```

If `/tmp` was wiped between steps, rerun the builders — they are idempotent
and self-verifying (they gate on the recorded DS-v1 build facts).

## Step 1 — the sweep chain (serial; ~15–25 min per run)

```bash
uv run python benchmarks/quality/ladder-v2/run_ladder_candidates.py run \
  --candidate enrich-rerankoff  --out benchmarks/quality/ladder-v2/sweep-ladder-enrich-rerankoff.json
uv run python benchmarks/quality/ladder-v2/run_ladder_candidates.py run \
  --candidate enrichidf-rerankoff --out benchmarks/quality/ladder-v2/sweep-ladder-enrichidf-rerankoff.json
uv run python benchmarks/quality/ladder-v2/run_ladder_candidates.py analyze   # -> FIGURES.md
uv run python benchmarks/quality/fr004-prf/run_prf_sweep.py --docs 3  --out benchmarks/quality/fr004-prf/sweep-prf-docs3.json
uv run python benchmarks/quality/fr004-prf/run_prf_sweep.py --docs 10 --out benchmarks/quality/fr004-prf/sweep-prf-docs10.json
uv run python benchmarks/quality/fr004-prf/analyze_prf.py                    # -> FIGURES.md
uv run python benchmarks/quality/fr005-mv/run_mv_sweep.py --out benchmarks/quality/fr005-mv/sweep-mv.json
```

Every run pins the D-009 protocol itself (threads 1, local bge-m3, brute
cosine, rerank marker, warm-up untimed). Each output JSON is a
`cairn-quality-sweep-kfold/1` doc: per-fold rows + the pooled
paired-bootstrap aggregate (the significance basis, D-009).

## Step 2 — integrity check (hard gate)

Each sweep's implicit all-levers-off row must reproduce the committed DS-v1
figures — recall@10 0.4174 / MRR 0.2862 within ±0.002 / ±0.006. If any run
misses, STOP: the numbers were bought by something other than the lever.
(T014's runs verified 58/58 per-query equality; expect the same here.)

## Step 3 — write the rows (single write; D-015)

Append rows to `benchmarks/quality/ablation.{json,md}` in ONE pass from
the measured JSONs (canonical serialization: `json.dumps(..., indent=2,
sort_keys=True)`), mirroring T014's rows' shape: family `ds-v1-kfold`,
dataset `DS-v1`, combo, recall_at_10, mrr, p95_ms (+ `db_mb` and `mv: true`
for the multi-vector row). PRF rows' notes must cite the rerank budget they
replace (committed figures 1142.0 vs 28.9 ms p95). Then:
`uv run pytest tests/test_ablation_v2_artifact.py tests/test_ablation_artifact.py -q`
→ 12 passed.

## Step 4 — T023 remainder: DS-v2 zero-shot + verdict (D-016)

The ladder's DS-v1 leg is Step 1's two runs. Still to do:

1. Build a DS-v2 measurement DB (both corpora: `benchmarks/datasource/t2/yarl`
   + `benchmarks/datasource/ds2/second-corpus/attrs-26.1.0`, same scratch-DB
   recipe) and evaluate the three D-016 candidates zero-shot over
   `benchmarks/datasource/ds2/ground_truth` (198 queries) — per-corpus rows
   (derivable from the expectations' corpus-prefixed file paths) + macro
   average, never an aggregate alone (D-011), never diffed against DS-v1 rows.
2. Write the verdict block into ablation.json citing its evidence: fold count
   ≥5 with per-fold spread, DS-v2 counts ≥150 L1 / ≥40 L5 (actuals: 154/44).
   SC-1 targets stay 0.50/0.33; the all-levers-off row must reproduce the
   committed DS-v1 baseline at 4 dp.

## Step 5 — T024: ship or document (exactly one branch)

If any candidate clears the pooled bootstrap guard at 95%: ship it as
defaults (shipped_defaults row + re-measure every protected baseline —
search_symbols p95 6.25 / semantic_search 201.67 / explore 453.18/513.73 /
impact 0.11; agent est_tokens 6848; warm_time 15497.2/232.6/66.6x).
Otherwise: record the shortfall (best candidate's interval + p) and name
the next binding constraint. T014's diagnostic already arms the
document-branch: the enrichment cutoff's real threshold on DS-v1 is ~0.27
('url' prevalence 0.2711), not 0.90 — cite it if it binds.

## Status of the runners (what is and isn't written)

| Piece | State |
|---|---|
| ladder runner + analyzer (D-016 candidates a/b) | written, unmeasured |
| PRF runner + analyzer (docs {3,10}, terms 10, λ 0.5) | written, unmeasured |
| mv runner (multivector=True sweep) + scratch builder | written, unmeasured |
| mv analyzer (db sizes → growth factor, FIGURES) | derive by hand in Step 3 from `ls -l` + the sweep JSON |
| DS-v2 zero-shot runner (Step 4.1) | NOT yet written — needs a small runner mirroring `run_ladder_candidates.py` over the ds2 ground truth |
