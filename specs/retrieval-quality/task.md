# Tasks: retrieval-quality

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Survey**: [survey.md](survey.md) | **Tech**: [tech-spec.md](tech-spec.md)
Status reflects code state per survey.md, not intent: FR-001/005/006 TODO,
FR-002/003/004/007 PARTIAL (gaps quoted in the tasks that build on them).
Nothing is DONE — every task opens unchecked.

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 6     | 0    |
| 2     | 6     | 0    |
| 3     | 3     | 0    |
| 4     | 4     | 0    |
| 5     | 5     | 0    |
| **Σ** | 24    | 0    |

## Phase 1: Measurement substrate (FR-005, FR-006)
<!-- Checkpoint (plan.md, After Phase 1): harness emits a machine-readable table whose baseline full-set row reproduces DS-v1's L1 recall@10 0.4174 / MRR 0.2862 (same DB, exact modulo runner noise); re-running produces an identical seeded split; a unit test proves selection touching the validation split fails loudly. -->
<!-- Serial phase: all tasks share `src/cairn/eval.py` until the harness core exists; the CLI (T005) is a thin consumer of the core per plan.md's strictly-ordered note. -->
- [x] T001 Add the seeded 50/50 tune/validate split of the 58 L1 ground-truth queries to the eval path, mirroring the repo's seeded `random.Random(seed)` pattern (`src/cairn/bench/corpus.py:42`, `agent_suite.py:333`) — split reproducible, disjoint, complete (TC-018); `src/cairn/eval.py` + tests. Survey basis: FR-006 TODO — `grep -rn "seed\|Random\|split" src/cairn/eval.py` matches only the TSV `line.split` at eval.py:197. (FR-006, D-006)
      [DONE 2026-08-16: 29/29 on the real 58-L1 set; sorted-then-shuffled (PYTHONHASHSEED-stable
      cross-process); 11 tests]
- [x] T002 (after T001) Enforce held-out discipline: selection-stage code fails loudly — non-zero exit, no results table emitted, error names the held-out violation — the moment it reads validation ids (TC-019); add the paired-bootstrap accept guard on validation before any lever ships (bootstrap/t, not Wilcoxon — 58 queries is the TREC 50-topic regime, research RQ5); `src/cairn/eval.py` + tests. (FR-006, D-006)
      [DONE 2026-08-16: HeldOutError (RuntimeError — survives ValueError handlers); validate
      purpose structurally requires the bootstrap verdict; 23 tests, 75 in test_eval]
- [x] T003 (after T002) Introduce `RetrievalParams` explicit injection: optional param object threaded `run_evaluation` (eval.py:483) → `semantic_search` (semantic.py:258), defaults preserving today's semantics exactly, never per-combo env mutation (in-process mint path would leak state); survey basis FR-005 TODO — `run_evaluation` accepts no overrides and calls `qmod.semantic_search(conn, query, limit=k)` with default args (eval.py:395). Verify-first: `grep -n "def run_evaluation\|def load_ground_truth\|corpus_filter" src/cairn/eval.py | head` → 121, 435, 452, 453, 483, 498. (FR-005, D-008)
      [DONE 2026-08-16: frozen dataclass, all None-means-default; equivalence proven both
      fusion paths; 5 knob-turn live proofs; 1580 full green; no env mutation]
- [x] T004 (after T003) Build the sweep harness core: enumerate lever combinations, run each against the tune split with injected `RetrievalParams`, emit the machine-readable multi-row results table — own schema per D-007, committed destination `benchmarks/quality/ablation.json` per AC1 — with recall@10 / MRR / p95 columns and query-subset selection (survey FR-005 gap: `corpus_filter` selects level only, `load_ground_truth` full-loads); ground-truth loader stays read-only, no write path anywhere in the harness (TC-025); `src/cairn/eval.py`. (FR-005, D-007)
      [DONE 2026-08-16: seam-inherited enforcement; implicit integrity row first; D-007
      schema with canonical bytes; baseline block feeds purpose=validate; 24 tests]
- [x] T005 (after T004) Add the `cairn eval --sweep` CLI surface — flags in `src/cairn/cli/system.py:493-508` (today `--db --knowledge --corpus --queries --json` only) as a thin consumer of the harness core; both `run_evaluation` callers (`eval_cmd`, `mint_quality`) change additively. (FR-005)
      [DONE 2026-08-16 (orchestrator inline — one-file task): --sweep/--out flags,
      thin run_sweep consumer, combos parsed to RetrievalParams; 3 CLI tests]
- [x] T006 (after T005) Prove harness trust (phase checkpoint): run the sweep entrypoint at the all-levers-off baseline config against the DS-v1 measurement DB — the full-set row reproduces L1 recall@10 0.4174 / MRR 0.2862 exactly (TC-017's integrity row, 4 decimals); re-run and diff the split (empty diff); T002's guard test green. (FR-005)
      [DONE 2026-08-16 — see D-009: the exact-4-decimals demand was too strong for a
      rerank-active pipeline. Bisect at the #35 merge commit proves the CODE never
      drifted; the ARTIFACT's 0.4174/0.2862 carries mint-time noise. The harness
      reproduces the deterministic present measurement EXACTLY (0.4195/0.2925,
      both entrypoints, twice, thread-pinned); split re-run diff empty; guard
      tests green. Baseline for SC-1 targets is the deterministic figure; the
      artifact band is documented as ±0.002/±0.006.]

## Phase 2: Query-path levers (FR-001, FR-003)
<!-- Checkpoint (plan.md, After Phase 2): results table holds enrichment on/off × fusion-param rows on the tune split; swept ranges and chosen values recorded; both splits' numbers reported; full-set row moved vs baseline. Verify: sentence-query MATCH expression no longer a single quoted phrase; `grep -n "rrf_fuse(" src/cairn/graph/semantic.py` shows weights/k from tunables. -->
<!-- Parallel map (plan.md): FR-001 stream (T007-T009, query-path hunks semantic.py:405/:477) ∥ FR-003 stream (T010, fusion hunks semantic.py:402/:429-450/:494) under a disjoint-hunk agreement — merge to one owner if conflicts appear. -->
- [x] T007 [P] Create `src/cairn/graph/query_enrich.py`: pure `enrich(query)` returning `(dense_query, sparse_query, identifiers)` — regex-only identifier extraction (camelCase/snake_case splits, backticked spans) plus stopword-trimmed terms; deterministic, hermetic, no LLM, no network (TC-003/TC-004 doctrine); unit tests incl. the no-match boundary (TC-005). Survey basis: FR-001 TODO — the raw query string reaches both paths unchanged (semantic.py:405, :477); BM25 is an empty list for every sentence query via the quoted-FTS5-phrase defect (lexical.py:76-80, :97-100). Verify-first: `uv run python -c "from cairn.graph.lexical import _pattern_to_fts; print(repr(_pattern_to_fts('where is the function that parses an unencoded URL string')))"` → the quoted phrase today. (FR-001, D-001)
      [DONE 2026-08-16: pure frozen EnrichedQuery; backtick/camel/snake/dotted/
      ALLCAPS/letter-digit extraction; tiny stopword list; original-query-preserved
      invariant; AST-enforced hermetic imports; 23 tests. KEY FACT for T008:
      space-joined terms STILL become one quoted FTS phrase through today's
      search_symbols — an explicit term-mode path is required]
- [x] T008 [P] (after T007) Wire the sparse leg: the `search_symbols` call at semantic.py:477 receives `sparse_query` as an unquoted term query — add a term-mode input path in `src/cairn/graph/lexical.py` WITHOUT changing `_pattern_to_fts` behavior for its 8 existing production callers (protected p95 6.25ms) — and thread the BM25 fetch limit (hard-coded 30 today) as a `RetrievalParams` tunable on that same line. Proof: the FTS MATCH expression for a sentence query is an OR-style term query, not one quoted phrase (TC-001's mechanism). (FR-001, D-001)
      [DONE 2026-08-16: search_symbols_terms with _terms_to_fts OR-prefix expressions
      (injection-safe: [A-Za-z0-9]-reduced + double-quoted); enrich flag gates the sparse
      leg; OFF = byte-identical; MATCH proof '"where...string"*' -> OR terms; BM25
      0.0->0.0164 fixture scores; sparse_limit threading already T010's (spy-proven);
      1656 full green]
- [x] T009 (after T007) Wire the dense leg: embed `dense_query` in the existing single `embed_query` call (semantic.py:405 — never a second call, latency doctrine); apply enrichment at the `semantic_search` boundary ONLY, never inside `embeddings.embed_query` (memory layer calls it: promotion.py:311, :581); keep the raw query string for `_exact_name_hit` corroboration (semantic.py:140-155) so gate inputs shift only where T018 measures them. (FR-001, D-001)
      [DONE 2026-08-16: single enrich() computed once at the boundary, feeds both legs;
      exactly ONE embed_query call in both modes (spy-proven); _exact_name_hit and
      rerank pairs keep the raw query; e2e: target found ONLY under the flag (dense
      cosine 0.0348 -> 0.2055); 10 tests, 1666 full green]
- [x] T010 [P] Wire fusion/threshold tunables from `RetrievalParams`: weights and k reach `rrf_fuse([bm25_ids, vec_ids], k=60)` at semantic.py:494 (no weights passed today); dense threshold (function default 0.3, semantic.py:262), `pool_size` (semantic.py:402), `brute_force_limit` (:429-450) sourced from params. Pitfalls: `rrf_fuse`'s own signature defaults unchanged — `search_memory` (promotion.py:270) shares it; `CAIRN_FUSION=0` keeps bypassing the whole leg (semantic.py:345); BM25-leg threshold is a NEW lever, sweep separately. Survey basis: FR-003 PARTIAL — "knobs exist as params/envs; values are folklore — no sweep has ever chosen them". Verify-first: `grep -n "rrf_fuse(\|threshold\|pool_size\|brute_force_limit\|CAIRN_FUSION" src/cairn/graph/semantic.py | head` → 262, 345, 402, 429, 450, 494. (FR-003, D-002, D-003)
      [DONE 2026-08-16: T003 coverage verified + knob-turn tests for pools/sparse_limit;
      NEW sparse_top_n lever (top-N over score-threshold — bm25() rank is negative-
      inverted and LIKE rows carry no rank; RRF consumes ranks); rrf_fuse signature
      untouched; 10 tests + live transcripts]
- [x] T011 (after T008, T009) Run the enrichment sweep on the tune split: on/off rows at incumbent fusion values; choose the enrichment default through T002's bootstrap accept guard; report tune/validate/full-set figures (TC-020 shape); identifier-shaped queries non-regressing vs baseline (TC-002). (FR-001, FR-003)
      [DONE 2026-08-16: DEFAULT OFF — bootstrap p=0.7198, negative point estimates on all
      three surfaces (tune 0.5828->0.5713, validate 0.2521->0.2371, full 0.4174->0.4042),
      TC-002 def-subset regressed (L1-D03 1.0->0.0: corpus-ubiquitous 'URL' token dilutes
      both legs), +30% p95. Wiring ships; default off until P3/P4 re-test. Finding: the
      D-009 deterministic baseline is session-state-dependent — today's twice-reproducible
      all-levers-off IS the artifact figure 0.4174/0.2862 (band flips sides across
      sessions); guard verdict unaffected (within-process pairing); T024 provenance records
      the session-measured row]
- [x] T012 (after T010, T011) Run the fusion/threshold sweep and ship defaults: conservative (k, w_dense, w_sparse) grid including the k=60/equal incumbent (Benham grid discipline) plus dense-threshold calibration on the tune split's labeled score distribution (0.3 is folklore — survey FR-003; no universal bge-m3 cutoff); record every parameter's swept range and chosen value (TC-009); full-set row moved vs baseline. Order is load-bearing: enrichment must land first — weighting a BM25 leg that contributes nothing measures noise (tech-spec order dependency). (FR-003, D-002, D-003)
      [DONE 2026-08-16: HONEST NO-SHIP — 18 combos/3 passes; every fusion lever INERT under the
      shipped pipeline (two structural causes proven: cross-encoder flattens order —
      byte-identical top-10s 29/29 across k{1,10,60}xweights{...}xtopN; sparse leg EMPTY
      29/29 with enrich OFF — RRF fuses [empty, dense], the task's own order warning
      realized). Threshold: no cutoff separates true/false (unmatched top-1s ABOVE
      matched median); 0.3 folklore-right, vacuously (flat plateau to ~0.45). Incumbent
      k60/w(1,1)/topN=none/thr0.3 stays; validate half untouched; full-set unmoved
      0.4174/0.2862. Quality upside now rests on P3 (chunks) + P4 (pairs/gate) + the
      P3/P4 enrichment re-test]

## Phase 3: Corpus recipe (FR-002)
<!-- Checkpoint (plan.md, After Phase 3): variant table (recall@10 / MRR / db_mb / p95) on the tune split under P2 defaults; chosen recipe + size bounds recorded; re-embed round-trip proven (hash change detected → full re-embed → vec0 rowids stable); `grep -n "CAIRN_CHUNK_VARIANT" src/cairn/graph/embeddings.py` default reflects the winner; embedding suite green after fixture churn. -->
- [x] T013 [P] Add field-dropout variants of variant B to `chunk_for_symbol` (embeddings.py:100-158) and thread an explicit recipe param through `embed_all` (embeddings.py:706) and `embed_symbols` (embeddings.py:823) — variant is env-only today (embeddings.py:111; tech-spec gap 3: call sites take no variant argument). Identity fields (qualified name, file path, signature, docstring) present in every variant (TC-008 — survey FR-002 PARTIAL: machinery exists, recipe unmeasured). Verify-first: `grep -n "CAIRN_CHUNK_VARIANT\|max_chars\|Body:" src/cairn/graph/embeddings.py` → 111 / 162-163 / 157-158. (FR-002, D-004, D-008)
      [DONE 2026-08-16: 4 additive variants (B_NO_SCOPE, B_NO_SIG — Parameters/Return
      dropped, Signature KEPT: the TC-008 floor beats the card's literal wording —
      B_IDENTITIES, C_TRIM); CHUNK_VARIANTS registry drives a floor test across all 7;
      A/B/C byte-pinned; variant param on embed_all/embed_symbols, param-beats-env,
      zero env mutation; 12 tests, 1678 full green]
- [x] T014 [P] Extend the sweep runner for recipes: per-variant re-embed orchestration through the content-hash staleness flow (embeddings.py:704-711 — any recipe change flips every `_chunk_hash` and forces a full re-embed; rowid-stable upsert :737-745) plus db_mb and size-bound accounting columns in the results table (survey FR-002 gap: "db_mb/size bounds not tracked per recipe"; 2048-char truncate bounds the chunk). Per-variant measurement runs are serial machine time, not agent time. (FR-002, FR-005)
      [DONE 2026-08-16: variant combos re-embed per-combo through the content-hash flow
      (runtime contract check against embed_all); db_mb + chunk_chars_{max,mean} per row;
      integrity row never re-embeds; schema bumped to cairn-quality-sweep/2 additively;
      11 tests, 1689 full green. Orchestrator amendment: CLI combo parser now passes
      variant through (was silently dropped — T015's shell entry needs it)]
- [ ] T015 (after T013, T014) (in-progress) Run the variant ablation and ship the recipe: sweep A/B/C + field-dropout variants under P2's shipped defaults on the tune split (measurement validity: variant choice under raw queries could flip once queries are enriched — plan dependency 2); ship the winner as the `CAIRN_CHUNK_VARIANT` default; prove the re-embed round-trip as an index operation (full embedded count, zero skipped-symbol errors, fixed probe query answers — TC-007); absorb fixture churn (`chunk_for_symbol` impact = 85 items incl. semantic/rerank-gating fixtures — plan risk budget). (FR-002, D-004)

## Phase 4: Rerank stage + gate re-calibration (FR-004)
<!-- Checkpoint (plan.md, After Phase 4): pair-format ablation + rerank marginal-value rows at the shipped config; gate margin re-calibrated (or explicit no-change justification) with the calibration note updated; confirmation sweep of leading combinations shows the shipped defaults are the joint winner on the tune split, held-out agrees; full-set re-run recorded. -->
<!-- Parallel map (plan.md): reranker.py (T016/T017) ∥ gate measurement in semantic.py (T018) — file-disjoint AND gate-safe (gate reads pre-rerank fused scores, semantic.py:546-552 before the rerank call at :567). -->
- [ ] T016 [P] Replace the rerank pair and pin truncation: `pairs = [(query, c.get("chunk") or "") for c in candidates]` (reranker.py:189) becomes (enriched query, importance-ordered structured candidate: kind, qualified name, path, signature, docstring — tail truncation loses least); construct `CrossEncoder` with explicit `max_length=512` (reranker.py:153 sets none today — effective length is a survey Unknown, probe it in this install first) with query-priority truncation; sigmoid before any score thresholding (bge-reranker raw scores are unbounded logits). Survey basis: FR-004 PARTIAL — "pair construction exists as one fixed format". Verify-first: `grep -n "pairs = \|_fused_confident(query\|rrk.rerank(query\|CrossEncoder(model_name)" src/cairn/graph/reranker.py src/cairn/graph/semantic.py` → reranker.py:153, 189 / semantic.py:552, 567. (FR-004, D-005)
- [ ] T017 (after T016) Measure the rerank stage's marginal value: on/off rows (all else equal) for the structured pair at the leading configs in the results table — the stage's recall@10/MRR contribution becomes a number, not an assumption (TC-012); confident results still skip reranking with the stated reason (TC-014). (FR-004)
- [ ] T018 [P] Re-calibrate the confidence gate: re-measure skip rate and skip-vs-rerank agreement under P2's enriched queries — the trigger is enrichment shifting `_exact_name_hit` corroboration (semantic.py:140-155), while the pair-format change itself is gate-safe (gate margin inputs are fused RRF scores set at semantic.py:520, gate at :546-564 runs before rerank at :567); recalibrate `_DEFAULT_RERANK_MIN_MARGIN` (semantic.py:97) or record the explicit no-change justification; update the calibration basis comment (semantic.py:81-94) (TC-013). Files: `src/cairn/graph/semantic.py` — disjoint from T016. (FR-004)
- [ ] T019 (after T016, T017, T018) Confirmation sweep and final rerank/gate defaults: leading combinations re-run on the tune split, held-out agreement via the bootstrap guard, joint winner ships; final marginal-value row captured at the shipped config; both splits + full set recorded; skip-reason behavior intact (TC-014). (FR-004, FR-006)

## Phase 5: Prove & publish (FR-007)
<!-- Checkpoint (plan.md, After Phase 5): `cairn bench --compare` exits 0 against DS-v1 perf.json + agent.json (threshold 0.15) or every regression is a documented trade with the quality gain quantified; regenerated tables show AFTER vs the untouched DS-v1 BEFORE; a warm-time artifact exists with a measured first-query number (322 ms advisory, decision recorded in its notes); `uv run python scripts/gen_benchmark_tables.py` succeeds; docs sentinel block updated; agent tokens within bounds vs 6848. -->
<!-- Parallel map (plan.md): perf re-run ∥ agent-effort re-run ∥ warm-time harness — three independent artifacts; table regeneration consumes them all and runs last. -->
- [ ] T020 [P] Re-run the perf suite and compare: `cairn bench --compare` against DS-v1 perf.json (threshold default 0.15 at cli/bench.py:198; regressions exit 2 at :370); protect semantic_search p95 201.67ms, explore p50 453.18ms / p95 513.73ms, search_symbols p95 6.25ms, impact_analysis p95 0.11ms; fix regressions or document each as a conscious trade with the quality gain quantified (TC-021). Verify-first: `grep -n "0.15\|exit(2)" src/cairn/cli/bench.py | head` → 198, 370. (FR-007)
- [ ] T021 [P] Re-run the agent-effort suite and compare: vs DS-v1 agent.json through `compare_agent_reports(..., threshold=0.15)` (agent_suite.py:521) — cairn est_tokens 6848 / tool_calls 9 / control reduction 99.0-99.5% within bounds (TC-022). (FR-007)
- [ ] T022 [P] Mint the warm-time measurement harness and artifact: first semantic query wall-time in a fresh process with warm-up active — `warm_models_in_background` (model_warmup.py:81+), `CAIRN_WARM_MODELS` kill switch (:136-137), boot wiring mcp_server/server.py:244-246 — committed artifact whose notes record that the 322 ms phase-doc figure (docs/phases/performance-gap/task.md:40) is advisory context, not a gate, since no committed baseline ever carried it (survey FR-007 PARTIAL gap: "the warm-time baseline has no artifact and no re-measurement path") (TC-023). Verify-first: `grep -rn "322" docs/phases/performance-gap/task.md` → task.md:40 only. (FR-007)
- [ ] T023 (after T020, T021, T022) Regenerate the reference quality tables: fresh full-set `mint_quality` run (scripts/mint_baselines.py:92) at the shipped config into a new baseline directory — DS-v1 stays byte-identical as the immutable BEFORE (TC-016/TC-025) — then `uv run python scripts/gen_benchmark_tables.py` under the exact-key contract (`L1`/`L5` blocks with recall_at_10 / mrr / n_queries, `machine_profile.runner_class` required by `_provenance_line`; sentinel `cairn-bench-tables:quality start/end` in docs/benchmarks.md); sweep artifacts stay beside, never inside, quality.json's role (D-007); improvement margins stated (TC-024). (FR-007, FR-005)
- [ ] T024 (after T023) Commit the final ablation table and verdict: the harness's closing invocation commits `benchmarks/quality/ablation.json` plus rendered `benchmarks/quality/ablation.md` (AC1) — every lever combination → recall@10 / MRR / p95 on both splits, exactly one row marked shipped-defaults, all-levers-off row reproducing 0.4174 / 0.2862 (TC-015, TC-017); three labeled figures (tune / validate / full set — TC-020); state the margins vs SC-1 (recall@10 ≥ 0.50, MRR ≥ 0.33) or document the shortfall per the honesty clause — the best evidenced configuration ships, the match rules are never loosened. (FR-005, FR-006, FR-007)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note on the
  line below, e.g. `done 2026-08-20 — uv run pytest tests/test_eval_sweep.py`
- Dropped: strike the task text and append `dropped <date> (D-###)` — never
  delete the line; dropped tasks stay visible with the decision that killed
  them; IDs are never renumbered
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)`; serial runs need a reason (shared file
  or output consumption per plan.md's parallelization map), parallel runs
  need none
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first. Verify-first commands are proof anchors: run them before
  implementing to confirm the survey's ground truth still holds.
