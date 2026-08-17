# Tasks: retrieval-quality-v2

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent — survey: 0 DONE /
3 PARTIAL (FR-002, FR-003, FR-006) / 3 TODO (FR-001, FR-004, FR-005). Nothing is
done, so every task opens `- [ ]`; PARTIAL items carry their survey gap quoted in
the phase note. The implementation wave started 2026-08-17 (one commit per
task, code + docs together; branches per plan.md Delivery: k-fold+spine, DS-v2,
lever families).

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 3    |
| 2     | 6     | 5    |
| 3     | 4     | 3    |
| 4     | 7     | 1    |
| 5     | 3     | 1    |
| **Σ** | 24    | 13   |

## Phase 1: Evidence core — k-fold harness (FR-001)
<!-- Checkpoint: a seeded >=5-fold sweep over DS-v1 emits per-fold spread + rotation-aggregated verdict; a negative test proves selection-stage reads of any fold's validate ids raise HeldOutError; fold count configurable. Survey FR-001 TODO: "no fold code anywhere" (grep confirms 0 hits in src/cairn/eval.py). -->
- [x] T001 Add `kfold_partitions` to `src/cairn/eval.py` — sorted-dedupe-seeded-shuffle then k contiguous rotation slices (the `split_queries` determinism recipe over `DEFAULT_SPLIT_SEED`); every id held out exactly once; refuse k < 5 with an error naming the floor; tests: regenerate-and-diff empty, union of held-out folds == full id set, no overlap (FR-001)
  done 2026-08-17 — `uv run pytest tests/test_eval.py tests/test_eval_sweep.py -q` → 122 passed (9 new: determinism, union/disjoint, k<5 floor); full suite 1742 passed
- [x] T002 Add `run_sweep_kfold` to `src/cairn/eval.py` — per fold i call the unchanged `run_sweep`/`evaluate_on` seam with selection ids = all minus fold i and `held_out_ids=fold_i` (flat iterable, no signature change); negative tests: selection-stage reads of any fold's validate ids raise `HeldOutError` before retrieval runs (FR-001)
  done 2026-08-17 — `uv run pytest tests/test_eval_kfold.py -q` → 18 passed (TC-003 negatives incl. consumed-fold breach + mid-rotation abort; cairn-quality-sweep-kfold/1 shape for T003)
- [x] T003 (after T002) Add the fold aggregate to `run_sweep_kfold` output — assemble the pooled per-query paired array (each query validate-side exactly once across the rotation) and run the unchanged `paired_bootstrap` over it for significance; report rotation-mean + per-fold spread as descriptive only, never the significance basis (D-009) (FR-001)
  done 2026-08-17 — `uv run pytest tests/test_eval_kfold.py -q` → 26 passed (+8: exactly-once cover, patched-bootstrap-receives-pooled-array proof, descriptive fields)
- [ ] T004 (after T003) Expose k-fold mode in `eval_cmd` (`src/cairn/cli/system.py`) — configurable fold count, fold-level rows in the emitted sweep doc; verify: `uv run cairn eval --sweep <spec> --queries benchmarks/datasource/t2/ground_truth --out /tmp/kfold-sweep.json` shows fold-level rows; targeted pytest for fold loop + guard green (FR-001)

## Phase 2: DS-v2 ground truth (FR-002)
<!-- Checkpoint: verifier passes with the new sibling-dir budget rule; loader counts confirm >=150 L1 across all four kinds and >=40 L5; every expectation empirically verified against a fresh graph build (zero aspirational, prior-campaign T011 bar); tree_hash pinned; second-corpus decision recorded with reasons. Authoring starts day 1 parallel to all code phases; its completion hard-gates Phase 5. -->
Survey FR-002 PARTIAL — gap: "no DS-v2 dataset (needs >=150 L1 / >=40 L5, four
kinds, empirically verified); no second-corpus candidate vendored or evaluated;
the budget checker covers only t2 + the total — a new sibling corpus dir needs
its own budget rule." T005–T007 close the decision/rule surface; T008–T010 do
the authoring long pole.

- [x] T005 [P] Run the Sakai topic-set-size power analysis on DS-v1's per-query matrices and record target n = max(150 L1, n_required) plus >=40 L5 as a decision with method + inputs (D-010; TC-005) (FR-002)
  done 2026-08-17 — `uv run python benchmarks/datasource/ds2/recompute_power.py` reproduces every figure; target n = 150 L1 (n_required 44–54 detectable / 90–109 at 80% power for Δ+0.11; half-effect +0.05 → 220–269 recorded out of reach) / ≥40 L5; σ_d from the five committed CI half-widths at n=29 (ablation.json carries no per-query matrices)
- [x] T006 [P] Add the `DS2_BUDGET_KB` sibling rule to `scripts/verify_datasource.py` (beside `T2_BUDGET_KB`/`DATASOURCE_BUDGET_KB`, lines 97–101) so the new ds2 dir is covered by a rule, not exempt by omission; `uv run python scripts/verify_datasource.py --budget` stays green (TC-009) (FR-002)
  done 2026-08-17 — `uv run pytest tests/test_verify_datasource.py -q` → 30 passed (4 new incl. over-budget breach); `verify_datasource.py --budget` → OK 3/3 with ds2 engaged (705.0/3072 KB), total 1173.6/5120 KB
- [x] T007 [P] Evaluate second-corpus candidate(s) against the datasource constraints (per-corpus <= 3 MB, datasource total <= 5 MB, permissive license, full provenance + NOTICE) and commit the vendored-or-deferred decision artifact naming concrete size/license findings, never a vague "later" (D-011; TC-008) (FR-002)
  done 2026-08-17 — vendored attrs 26.1.0 (MIT, 674.9 KB / 67 files, sha256-pinned sdist): DECISION.md carries the 3-candidate table (attrs / markdown-it-py 4.2.0 / cachetools 7.1.7) with measured sizes + licenses; budget total 1173.6/5120 KB OK
- [x] T008 (after T005, T007) Author DS-v2 L1 queries under `benchmarks/datasource/ds2/ground_truth/` — >=150 with all four kinds represented, staged batches that each load through `load_ground_truth` (`queries.jsonl` + `expectations.tsv`, the loader shape from survey FR-002 evidence), sized to T005's target (FR-002)
  done 2026-08-17 — loader: 154 L1 queries (definition 46 / callers 42 / impact 34 / flow 32; attrs-26.1.0 106 / yarl 48) in 5 loadable batches; ALL 392 expectations resolved tier-1-exact against fresh scratch builds (AUTHORING.md carries method + per-batch counts); ids DS2-L1-*, no DS-v1 collision
- [x] T009 (after T008) Author DS-v2 L5 queries — >=40, same directory + loader shapes, staged batches landing verifiable (FR-002)
  done 2026-08-17 — loader: 198 queries (L1 154 unchanged + L5 44: attrs 30 / yarl 14, kind=knowledge, DS-v1 L5 semantics mirrored); ALL 558/558 expectations tier-1-exact (L5 166/166 fresh); L1 rows byte-identical
- [ ] T010 (after T009) Verify and seal DS-v2 — empirically verify every expectation against a fresh graph build (zero aspirational entries) and commit the provenance artifact beside the dataset (per-kind/level counts, pass rate 100%, the build facts verified against); pin tree_hash and the manifest dataset_version; DS-v1 artifacts byte-identical throughout (TC-006, TC-007) (FR-002)

## Phase 3: Enrichment repaired (FR-003)
<!-- Checkpoint: enrich() remains pure (no env/graph reads) — the DF signal demonstrably arrives as an injected parameter/table; the reproduced L1-D03 failure (ubiquitous 'URL' token) is gone; threshold documented; ablation rows on both splits; no previously-passing DS-v1 tune-split query regresses to zero (AC4). -->
Survey FR-003 PARTIAL — gap: "no DF computation, no threshold, no
down-weight/drop; enrich's purity doctrine (no env reads) means the DF signal
must be INJECTED as a parameter/table." The verified L1-D03 repro
(`enrich(...).identifiers == ('URL',)`) is T012's unit-test anchor.

- [x] T011 [P] Add the persisted `term_df(token TEXT PRIMARY KEY, symbol_df INTEGER, n_symbols INTEGER)` table to `src/cairn/graph/schema.py` (following the `EMBEDDINGS_CONTENT_HASH_MIGRATION` migration pattern) plus a builder from the `symbols_fts` FTS5 vocabulary (fts5vocab row mode; fallback: one aggregate scan at embed time), refreshed on the embed pass (D-005) (FR-003)
  done 2026-08-17 — `uv run pytest tests/test_term_df.py -q` → 9 passed (migration, determinism, fallback parity, embed-pass wiring); fts5vocab read needed the 3-arg `fts5vocab(main, symbols_fts, row)` form → D-013
- [x] T012 (after T011) Extend `enrich` in `src/cairn/graph/query_enrich.py` to `enrich(query, df_lookup=None)` — hard cutoff 0.90 (D-004, the scikit-learn `max_df` convention): terms with symbol_df/n_symbols > 0.90 dropped from the appended identifier tail and the sparse term list; the original dense-query prefix untouched; lookup keys case-folded (FTS5 unicode61 vs enrich casing); unit test pins the L1-D03 'URL' repro fixed deterministically (TC-010, TC-012, TC-015) (FR-003)
  done 2026-08-17 — `uv run pytest tests/test_query_enrich.py -q` → 57 passed (34 new: L1-D03 repro, 0.89/0.90/0.91 boundary, rare-term survival, None-lookup equivalence); ENRICH_DF_MAX_FRACTION = 0.90 documented; df_lookup contract published for T013
- [x] T013 (after T012) Inject the DF lookup at the boundary seam in `src/cairn/graph/semantic.py` (`_enriched = enrich_query(query)`, ~L581) — per-term indexed SELECTs bounded by the query's distinct token count (documented O(#query tokens) bound); add flag-off `RetrievalParams.enrich_idf` (additive-field doctrine); equivalence tests prove default behavior byte-identical (FR-003)
  done 2026-08-17 — `uv run pytest tests/test_semantic_enrichment.py -q` → 22 passed (19 new: flag-off byte-equivalence battery, ubiquitous-token drop through full path, EXPLAIN-proved index probes, memoization); RetrievalParams.enrich_idf additive
- [ ] T014 (after T013, T004) Calibrate and measure FR-003 — sweep the cutoff within 0.75–0.95 on the DS-v1 k-fold, record the shipped value (default 0.90) in code + the ablation record, emit ablation rows on both splits, and prove no previously-passing DS-v1 tune-split query regresses to zero with L1-D03 recovered (AC4; TC-011, TC-013, TC-014) (FR-003)

## Phase 4: New lever families — PRF + multi-vector (FR-004, FR-005)
<!-- Checkpoint: both levers flag-off by default; default config's all-levers-off row still reproduces the session baseline (integrity doctrine); PRF's p95 recorded and under the rerank budget it replaces (~1113 ms p95 gap, ablation.json 1142.0 vs 28.9 — cite p95, not the unretained ~780 ms p50); multi-vector row carries db-size (<=3x growth) + p95; PRF's second-embed exception documented at the boundary (one-call doctrine amended, not silently broken). Verify: sweep with each flag flipped emits its rows; full pytest green. -->
Soft order after Phase 3's boundary edit (T013) on `src/cairn/graph/semantic.py`;
PRF (T015–T016, T020) and multi-vector (T017–T019, T021) are otherwise disjoint
seams — coordinate only on the brute-leg candidate loops. Survey FR-004 and
FR-005 both TODO (grep-confirmed: no prf/rm3/feedback, no embeddings_mv/vecmv).

- [x] T015 [P] (after T011) Create `src/cairn/graph/prf.py` — pure deterministic RM3-style expansion from the fused top-k: extract candidate expansion terms, score by summed corpus-aware IDF over the feedback docs (reads `term_df`), drop terms already in the query, keep the top `fb_terms` filtered by the `(1−λ)·max_weight` cap (D-001, D-003); no LLM, no randomness; unit tests (FR-004)
  done 2026-08-17 — `uv run pytest tests/test_prf.py -q` → 25 passed (IDF ordering, exact λ-cap boundary, determinism incl. PYTHONHASHSEED, hermeticity AST guard); expand() contract published for T016
- [ ] T016 (after T013, T015) Wire PRF at the post-fusion seam in `src/cairn/graph/semantic.py` (`candidates = fused_candidates`, ~L726–752, immediately before the confidence gate): one flag-gated second full pass (both legs + fusion) with at most one extra `embed_query` — the explicit doctrine exception documented at the boundary (D-012), REPLACING the rerank stage (`rerank=False` enforced on PRF combos, never stacking); add `RetrievalParams` fields `prf`/`prf_docs`/`prf_terms`/`prf_lambda` (additive, None-means-default); flag-off equivalence + offline determinism tests (TC-016, TC-017, TC-019) (FR-004)
- [x] T017 [P] Add the parallel `embeddings_mv` table (PRIMARY KEY (symbol_id, model, vector_kind); `name` + `docstring` kinds only) to `src/cairn/graph/schema.py`, with new producer functions building kind-specific texts and their own `_chunk_hash` staleness in `src/cairn/graph/embeddings.py`, wired into the embed CLI (`src/cairn/cli/embed.py`) — producers NOT added to `CHUNK_VARIANTS` (identity-floor tests iterate it); the `embeddings` table, its upserts/staleness/reaping, and flag-off behavior byte-identical (D-006; TC-020). Front-load first in the phase: deepest blast radius (80 impacted symbols via `embed_all`) (FR-005)
  done 2026-08-17 — `uv run pytest tests/test_embeddings_mv.py -q` → 14 passed (flag-off zero-mv-writes + base-table byte-equivalence, per-kind staleness, reaping); `--multivector` CLI flag; CHUNK_VARIANTS unchanged
- [ ] T018 (after T013, T017) Multi-vector query path in `src/cairn/graph/semantic.py` — brute scan UNIONs `embeddings` + `embeddings_mv` rows and the candidate-dict construction dedups per symbol by MAX score; add flag-off `RetrievalParams.multivector`; result lists contain each symbol at most once (TC-021, TC-023) (FR-005)
- [ ] T019 [P] (after T017) Dedicated `vecmv_<safe-model>` vec0 index — additive source parameter on `rebuild_index`/`ann_query` (`src/cairn/graph/ann_index.py`) with the same DELETE+INSERT rowid-keyed contract; rebuild call site `src/cairn/cli/embed.py` (line 130); `_candidates_from_ann_hits` (`src/cairn/graph/semantic.py`, line 246) dedup changes last-wins → max (correctness even single-vector) (D-007) (FR-005)
- [ ] T020 (after T016) Emit PRF ablation rows on the DS-v1 k-fold — grid fb_docs {3, 10}, fb_terms 10, λ 0.5 (D-002); recall@10/MRR/p95 on both splits, all columns populated; p95 recorded against the rerank budget it replaces (committed figures 1142.0 vs 28.9 ms p95 — never the unretained ~780 ms p50); the sweep's implicit all-levers-off integrity row still reproduces the session baseline (TC-018, TC-019) (FR-004)
- [ ] T021 (after T018, T019) Emit multi-vector ablation rows on the DS-v1 k-fold — recall@10/MRR/db_mb (via `_size_accounting`, same DB file) /p95 on both splits with the storage growth factor (<= 3x) stated and an additive `mv` row marker like `variant`; the sweep's implicit all-levers-off integrity row still reproduces the session baseline (TC-022) (FR-005)

## Phase 5: Confirmation ladder + extended record (FR-006)
<!-- Checkpoint: ladder re-run on k-fold aggregate + DS-v2; v2 rows live in their new family and are never diffed against DS-v1 rows; the six existing guard tests still pass plus new v2-family guard tests; SC-1 targets unchanged at 0.50/0.33 and match rules untouched; if anything ships: shipped_defaults row + perf/agent-effort/warm-time baselines re-measured; if nothing ships: verdict states the shortfall and the next binding constraint. Hard-gated by Phase 2 completion and Phase 1's aggregate machinery. -->
Survey FR-006 PARTIAL — gap: "no k-fold/DS-v2 measurement family; extension
path undecided (new doc vs schema v2); ladder needs only fold aggregation +
DS-v2." The extension path is now decided: D-008 (new `ablation-v2` document,
schema `cairn-quality-ablation/2`).

- [x] T022 [P] Create `benchmarks/quality/ablation-v2.{json,md}` (schema `cairn-quality-ablation/2`) plus its own guard test file — v1 record byte-identical and `uv run pytest tests/test_ablation_artifact.py` → 6 passed unmodified; v2 rows carry their own dataset/family labels and no v2 row diffs against a DS-v1 row (D-008; TC-028) (FR-006)
  done 2026-08-17 — `uv run pytest tests/test_ablation_v2_artifact.py -q` → 6 passed (v1 suite 6 passed unmodified, blob-hash-pinned); families ds-v1-kfold + ds-v2 (t2, attrs-26.1.0); verdict pending with SC-1 0.50/0.33 and TC-029 slots
- [ ] T023 (after T004, T010, T014, T020, T021, T022) Re-run the confirmation ladder on the upgraded evidence base — k-fold pooled aggregate over DS-v1 + zero-shot DS-v2 validation reported as per-corpus rows plus macro-average (D-011, never an aggregate alone, never cross-corpus row diffs); verdict block cites its evidence: fold count >= 5 with per-fold spread and DS-v2 counts >= 150 L1 / >= 40 L5; SC-1 targets stay 0.50/0.33, match rules never loosened, all-levers-off row reproduces the committed DS-v1 baseline at 4 dp (TC-026, TC-027, TC-029) (FR-006)
- [ ] T024 (after T023) Close ship-or-document — if a combination clears the pooled bootstrap guard: ship it as defaults with a shipped_defaults row and re-measure every protected baseline (perf search_symbols p95 6.25 / semantic_search 201.67 / explore 453.18/513.73 / impact 0.11; agent est_tokens 6848; warm_time cold 15497.2 / warm 232.6 / 66.6x); if nothing clears: record the shortfall (best candidate's interval + p) and name the next binding constraint in the ablation-v2 verdict — exactly one of the two branches (TC-024, TC-025) (FR-006)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      done <date> — <test/command that proves it>
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)`; serial runs need a reason, parallel
  runs need none
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
