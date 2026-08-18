# Survey: retrieval-quality-v2

**Created**: 2026-08-17 | **Re-surveyed**: 2026-08-18 | **Baseline**: main @ 8dbf2ca (v0.12.0; prior survey baseline 5b84272 — the whole 24-task implementation, the PR #39 record unification, and the v0.12.0 release landed in between)
Re-survey (audit) persisted by the orchestrator from the surveyor's verbatim
delivery. Statuses below supersede the pre-implementation survey (which read
3 PARTIAL / 3 TODO against 5b84272).

## Items

```
item FR-001: "k-fold cross-validation in the eval harness"
  evidence:   src/cairn/eval.py:377 kfold_partitions(ids, *, k=MIN_KFOLD_K,
              seed=DEFAULT_SPLIT_SEED) — "Deduplicate and *sorted before* the
              seeded random.Random(seed).shuffle, then the shuffled list is
              cut into k contiguous slices"; "k < 5 raises ValueError naming
              the minimum". eval.py:920 KFOLD_SWEEP_SCHEMA =
              "cairn-quality-sweep-kfold/1". eval.py:1348 run_sweep_kfold —
              per fold i the UNCHANGED seam: evaluate_on(ids=selection,
              purpose="selection", held_out_ids=fold_i) with selection = all
              ids minus fold i; "a selection-stage read touching ANY fold's
              held-out ids raises HeldOutError at that fold's turn, before
              that fold's retrieval runs". Aggregate (T003, D-009): the
              ``aggregate`` block "pools each query exactly once across the
              rotation and runs the unchanged paired_bootstrap over the
              pooled per-query arrays"; ``descriptive`` (rotation-mean,
              per-fold figures, min/max delta spread) "is DESCRIPTIVE ONLY
              and never feeds the significance test — Bengio–Grandvalet".
              CLI: src/cairn/cli/system.py:506 --kfold (is_flag, default
              False), :511 --folds (default 5), :523 "--kfold requires
              --sweep (the rotation wraps the lever sweep)".
  status:     DONE
  verify:     PASS (ran 2026-08-18): `uv run pytest tests/test_eval_kfold.py
              -q` -> 29 passed; `uv run pytest tests/test_eval.py
              tests/test_eval_sweep.py -q` -> 129 passed (1020s, machine
              under CPU contention from a concurrent suite). CLI kfold tests
              live in tests/test_eval_sweep.py:1023-1123 ("cairn eval --sweep
              --kfold (T004, FR-001)").
  gap:        none.

item FR-002: "DS-v2 ground truth authoring surface"
  evidence:   benchmarks/datasource/ds2/ground_truth/ on disk, recounted this
              session through the loader shape: queries.jsonl 198 lines — L1
              154 (definition 46 / callers 42 / impact 34 / flow 32 — all
              four kinds) + L5 44 (knowledge); expectations.tsv 559 lines =
              header + 558 rows. VERIFICATION.md (schema
              cairn-ds2-verification/1): "Pass rate: 100% — 558/558
              expectations tier-1-exact, zero unresolved, zero aspirational".
              Second corpus VENDORED: ds2/second-corpus/DECISION.md "Decision:
              **VENDORED — attrs 26.1.0**" (MIT, "674.9 KB / 67 files",
              sha256-pinned sdist) with the 3-candidate table (attrs
              CHOSEN / markdown-it-py rejected "sdist ships no tests/" /
              cachetools rejected "too thin a symbol substrate"). Budget rule:
              scripts/verify_datasource.py:100 DS2_BUDGET_KB = 3072 beside
              T2_BUDGET_KB (:99) and DATASOURCE_BUDGET_KB (:101). CI:
              .github/workflows/ci.yml:237-255 job "ds2-seal ... run: python
              benchmarks/datasource/ds2/verify_dataset.py". Power analysis:
              ds2/power-analysis.{json,md} + recompute_power.py (T005).
  status:     DONE
  verify:     PASS (ran 2026-08-18): `uv run python scripts/verify_datasource.py
              --budget` -> "OK: 3/3 size budget(s) within limits" (t2 466.7 /
              3072 KB, ds2 865.8 / 3072 KB, total 1334.4 / 5120 KB — NOTE:
              drifted from task.md's claimed 705.0/1173.6 after the tree_hash
              build-noise fix ddb6531; HEAD values are these). `uv run pytest
              tests/test_verify_datasource.py -q` -> 34 passed.
  gap:        none.

item FR-003: "corpus-aware IDF term weighting in enrichment"
  evidence:   src/cairn/graph/schema.py:113 CREATE TABLE IF NOT EXISTS term_df;
              rebuild_term_df (schema.py:658) with _rebuild_term_df_vocab
              fts5vocab primary path (:601, the D-013 3-arg
              `fts5vocab(main, symbols_fts, row)` form) and
              _rebuild_term_df_scan aggregate-scan fallback (:635).
              src/cairn/graph/query_enrich.py:147 ENRICH_DF_MAX_FRACTION =
              0.90; enrich(query, df_lookup=None) — "With df_lookup=None (the
              default) the filtering is inert". Injection at the boundary:
              src/cairn/graph/semantic.py:740-743 — `if params.enrich_idf:
              _enriched = enrich_query(query, df_lookup=_term_df_lookup(conn))`
              else the single-argument form ("flag-off byte-equivalence");
              _term_df_lookup is memoized, one "SELECT symbol_df, n_symbols
              FROM term_df WHERE token = ?" per DISTINCT case-folded token —
              the documented O(#query tokens) bound. RetrievalParams
              field enrich_idf (semantic.py:454). Measured (ablation rows,
              recounted this session): enrich+enrich_idf@df_max={0.75, 0.80,
              0.85, 0.90} ALL 0.4123/0.2603 — the cutoff is INERT on DS-v1
              ("'url' prevalence 0.2711"); the honest AC4 finding is
              committed: L1-D03 recovers only below-cutoff ~0.27
              (fr003-calibration/d03-diagnostic.json) and L1-I03's dilution
              is a RARE identifier (DF 0.0047) — recorded in the ablation
              verdict's armed experiments as the next-knob evidence.
  status:     DONE
  verify:     PASS (ran 2026-08-18): `uv run pytest tests/test_term_df.py
              tests/test_query_enrich.py tests/test_semantic_enrichment.py -q`
              -> 88 passed.
  gap:        none (mechanism complete; the shipped 0.90 cutoff's measured
              effect on DS-v1 is null — honestly documented, threshold stays
              per AC3's "shipped value must be recorded").

item FR-004: "RM3-style PRF over the fused first pass"
  evidence:   src/cairn/graph/prf.py:185 def expand(query, feedback_docs, *,
              df_lookup=None, fb_terms=10, ...) — pure deterministic RM3
              (IDF-weighted selection over the fused top-k, (1−λ)·max_weight
              cap, no LLM, no randomness). Wiring: semantic.py:648 `_prf =
              params is not None and bool(params.prf)`; :649-650 `if _prf:
              rerank_on = False` ("PRF REPLACES the rerank stage ... never
              stacks on it", D-012); first pass extracted into _run_pass; the
              second _run_pass call is "the explicit, flag-gated exception to
              the one-embed_query-per-call doctrine". RetrievalParams fields
              prf/prf_docs/prf_terms/prf_lambda (semantic.py:457-460).
              Measured (ablation rows, recounted): prf@docs=3 0.3728/0.1752
              p95 99.4126 ms; prf@docs=10 0.3624/0.1568 p95 81.0575 ms — vs
              the 1142.0 ms rerank budget it replaces (AC5 latency half
              holds); honest negative (Δ−0.0447 p=0.30 / Δ−0.0550 p=0.19) —
              PRF stays flag-off. Artifacts: benchmarks/quality/fr004-prf/
              (rows-fr004.json, sweep-prf-docs{3,10}.json, FIGURES.md).
  status:     DONE
  verify:     PASS (ran 2026-08-18): `uv run pytest tests/test_prf.py
              tests/test_prf_wiring.py -q` -> passed (93 passed across
              test_prf + test_prf_wiring + test_embeddings_mv +
              test_multivector_query + test_ann_vecmv in one invocation).
  gap:        none (flag-off by default at HEAD — line 648 falsy resolution).

item FR-005: "multi-vector-per-symbol embeddings with max-score selection"
  evidence:   src/cairn/graph/schema.py:211 CREATE TABLE IF NOT EXISTS
              embeddings_mv (PK symbol_id, model, vector_kind; :222
              idx_embeddings_mv_model) — name/docstring kinds only, the
              base embeddings table untouched. Producer/CLI: cli/embed.py:35
              "--multivector" flag, wired at :134/:145-179 (rebuilds the
              vecmv_ index). Query path: semantic.py:627 `_mv = params is not
              None and bool(params.multivector)` ("None and False both keep
              every scan byte-identical"); brute leg UNION ALL over
              embeddings + embeddings_mv (:824) with max-over-vectors
              consolidation (:862); ANN leg _merge_ann_candidates (:309 —
              "exactly ONCE in the merged list, at its best (max) score")
              via ann_query(conn, model, q_blob, pool_size,
              source="embeddings_mv") (:786-790). RetrievalParams field
              multivector (semantic.py:456). Measured (ablation row,
              recounted): multivector 0.5588/0.3395 — BOTH SC-1 targets
              reached for the first time; db_mb 12.4531 vs incumbent 6.8789
              (growth 1.8103x, fr005-mv/SIZE.md); p95 703.0115 ms;
              guard-cleared on DS-v1 (Δ+0.1414 p=0.0035 CI[+0.0527,+0.2373])
              but REFUTED zero-shot on DS-v2 (attrs Δ−0.0432; macro MRR
              −0.0925) → NOT shipped; the lever stays flag-off.
  status:     DONE
  verify:     PASS (ran 2026-08-18): `uv run pytest tests/test_embeddings_mv.py
              tests/test_multivector_query.py tests/test_ann_vecmv.py -q` ->
              passed (same 93-passed batch as FR-004).
  gap:        none (lever wired + measured on both splits incl. db-size and
              p95; off by default).

item FR-006: "confirmation ladder re-run + ablation-record extension"
  evidence:   The record is now UNIFIED: benchmarks/quality/ablation.{json,md},
              schema cairn-quality-ablation/2 — ablation-v2.{json,md} (D-008's
              new document) was REMOVED by PR #39 (b57d0f7 "unify the v1/v2
              ablation records into one artifact"; `ls
              benchmarks/quality/ablation-v2.*` -> no matches). The v1 record
              is embedded verbatim under campaigns.retrieval-quality-v1 with
              original_blobs pins (ablation.json 3649dd1c..., ablation.md
              7112bb08...; "the /1 rows ... stay in this history block, never
              in this document's rows array (the never-cross-diff rule ...
              survives the file unification)"). Rows recounted: 22 total =
              10 ds-v1-kfold + 12 ds-v2 (per-corpus attrs-26.1.0 / yarl /
              macro-average rows for all-levers-off, multivector,
              enrich+rerank-off, enrich_idf+rerank-off). Verdict (recounted
              from ablation.json): status done, outcome
              "documented-shortfall-no-ship", statement "CLOSED (T024,
              document branch — exactly one branch taken): NO SHIP; defaults
              unchanged"; fold_count 5; per_fold_spread [0.0969, 0.1169,
              0.1491, 0.1624, 0.1832] "descriptive only"; ds2_counts 154/44
              vs floors 150/40 "floors met"; sc1_targets {"mrr": 0.33,
              "recall_at_10": 0.5} unchanged; best_candidate multivector with
              BOTH intervals (DS-v1 cleared Δ+0.1414 p=0.0035; DS-v2 attrs
              [−0.1017,+0.0157] p=0.1456, "refuted zero-shot — not shipped");
              next_binding_constraint lever generalization (not evidence
              power); shipped_defaults row null. Protected baselines NOT
              re-measured — correct: the document branch taken, and
              re-measurement "binds the ship branch only". docs/benchmarks.md
              (:21,:26,:57,:109-120) documents --kfold, DS-v1/DS-v2, and the
              unified record; docs/architecture.md (:233-235) covers
              embeddings_mv/vecmv + term_df.
  status:     DONE
  verify:     PASS (ran 2026-08-18): `uv run pytest tests/test_ablation_artifact.py
              -q` -> 8 passed (the guard was reshaped by the unification from
              the old 6-test v1 pin + 6-test v2 file into 8 unified tests;
              tests/test_ablation_v2_artifact.py no longer exists). The 8
              tests pin: canonical bytes, v1 blob identity (3649dd1c /
              7112bb0), verbatim DS-v1 family identity, family/dataset row
              labels, mv marker follows the lever, verdict evidence filled +
              targets unchanged, embedded v1 invariants, rendering carries
              the closed verdict.
  gap:        none (document-branch close; SC-1 0.50/0.33 and match rules
              untouched).
```

## Supporting evidence

**Flag-off-by-default triple (the "document, don't ship" verdict, verified at
HEAD)** — src/cairn/graph/semantic.py: `_mv = params is not None and
bool(params.multivector)` (:627), `_prf = params is not None and
bool(params.prf)` (:648), and enrich_idf activates only inside `if
params.enrich_idf:` (:740). RetrievalParams (:448-460) declares all 14 fields
`Optional[...] = None` — dense_threshold, rrf_k, rrf_weights, sparse_limit,
sparse_top_n, dense_pool, rerank_pool, rerank, enrich, enrich_idf,
gate_min_margin, multivector, prf, prf_docs, prf_terms, prf_lambda (None =
default-off per the additive-field doctrine). No code path turns the three
levers on without an explicit flag.

**eval.py k-fold surface (verified by read)** — kfold_partitions (:377, k>=5
floor via MIN_KFOLD_K, non-degenerate folds, pure function of (id set, k,
seed)); KFOLD_SWEEP_SCHEMA "cairn-quality-sweep-kfold/1" (:920);
run_sweep_kfold (:1348; per-fold held_out_ids through the unchanged
evaluate_on seam; aggregate = pooled paired_bootstrap
["significance_basis"] + descriptive rotation-mean/spread; per-combo
embedding state holds across the WHOLE rotation via _EmbeddingStateManager —
the d8c1025 audit-defect fix); HeldOutError (:470); split_queries /
evaluate_on / paired_bootstrap / run_sweep all still present (:314/:482/:656/
:1150).

**embeddings/DF machinery** — schema.py: term_df (:113), embeddings_mv (:211,
idx :222), _rebuild_term_df_vocab (:601, 3-arg fts5vocab), scan fallback
(:635), rebuild_term_df (:658). query_enrich.py: ENRICH_DF_MAX_FRACTION 0.90
(:147), enrich(query, df_lookup=None). ann_index.py: additive source=
parameter (vecmv_<safe-model>). cli/embed.py: --multivector (:35).

**Guard tests (recounted at HEAD)** — tests/test_ablation_artifact.py: 8 tests
(v1 blobs pinned 3649dd1c572652b1660d82f53d5d5bcdd1c8c76b /
7112bb0899aef22dfda8080596cc63bbbfb8314c); tests/test_eval_kfold.py 29;
tests/test_verify_datasource.py 34; tests/test_term_df.py +
test_query_enrich.py + test_semantic_enrichment.py 88 combined; tests/
test_prf.py + test_prf_wiring.py + test_embeddings_mv.py +
test_multivector_query.py + test_ann_vecmv.py 93 combined; tests/test_eval.py
+ test_eval_sweep.py 129 combined.

**DS-v1 immutability (recounted)** — benchmarks/datasource/t2/ground_truth/
queries.jsonl 82 lines, expectations.tsv 235 lines = header + 234 rows; the
unified record's embedded v1 block carries the same (queries 82 /
expectations 234 / l1 58 / l1_expectations 160).

**DS-v2 dataset facts (recounted this session)** — 198 queries (L1 154:
definition 46 / callers 42 / impact 34 / flow 32; L5 44: knowledge), 558
expectations, all tier-1-exact per VERIFICATION.{md,json}; corpora yarl (48
L1 / 14 L5) + attrs-26.1.0 (106 L1 / 30 L5) per task.md/T008-T009 authoring
claims, consistent with the 154/44 recount; budget at HEAD: t2 466.7/3072,
ds2 865.8/3072, total 1334.4/5120 KB (3/3 OK).

## Unknowns
- none (the prior survey's "~780ms p50" unknown is resolved: no committed
  artifact cites it; HEAD docs and the ablation rows cite p95 only — 1142.0
  vs 28.9 ms rerank budget, PRF rows 99.4/81.1 ms).
