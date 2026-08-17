# Survey: retrieval-quality-v2

**Created**: 2026-08-17 | **Baseline**: main @ 5b84272 (branch docs/retrieval-quality-v2-spec — code identical to main)
Persisted by the orchestrator from the surveyor's verbatim delivery (read-only harness this wave).

## Items

```
item FR-001: "k-fold cross-validation in the eval harness"
  evidence:   src/cairn/eval.py — the harness today is SINGLE-seeded, single-split:
              DEFAULT_SPLIT_SEED = 0x5EED; split_queries(queries, *, seed=..., ratio=0.5)
              -> (tune_ids, validate_ids) — "Dedupe + sort BEFORE shuffling... pure function
              of (id set, seed, ratio)"; run_sweep hardwires the one split and threads
              held_out_ids=validate_ids into every evaluate_on(purpose="selection") call —
              THE fold-rotation insertion point. The guard extends per-fold AS-IS:
              evaluate_on(held_out_ids=...) intersects id sets and raises HeldOutError
              "before any retrieval runs" — a flat id iterable, so per-fold validate lists
              need no signature change; only run_sweep's single split call and the aggregate
              reporting (dataset.split: "tune") are single-fold.
  status:     TODO
  verify:     PASS (ran): split on the real 58-L1 set -> 29 29, reproducible, disjoint;
              5 seeded splits each give (29,29) — rotation material exists; no fold code
              anywhere (grep "fold" src/cairn/eval.py -> 0 hits).
  gap:        no fold loop, no rotation, no per-fold spread / aggregate verdict reporting;
              the bootstrap guard pairs ONE baseline array per run (single-split consumer).

item FR-002: "DS-v2 ground truth authoring surface"
  evidence:   benchmarks/datasource/t2/ground_truth/queries.jsonl (82 lines) + expectations.tsv
              (234 rows): 58 L1 / 24 L5; kinds callers 20, definition 18, flow 10, impact 10,
              knowledge 24. load_ground_truth validates {query_id, level, kind, text, rationale}
              + expectations header query_id\tsymbol_id\tgrade, grade {1,2}, file#symbol ids.
              Versioning: manifest dataset_version "DS-v1"; baselines/DS-v1 (immutable) +
              DS-v1.1 (quality refresh). Budget: t2 <= 3MB, datasource total <= 5MB.
  status:     PARTIAL
  verify:     PASS (ran): verify_datasource.py --budget -> t2 OK 469.6/3072 KB, total OK
              471.5/5120 KB — headroom ~2.6MB under t2, ~4.65MB under the total. Loader
              counts confirmed.
  gap:        no DS-v2 dataset (needs >=150 L1 / >=40 L5, four kinds, empirically verified);
              no second-corpus candidate vendored or evaluated; the budget checker covers
              only t2 + the total — a new sibling corpus dir needs its own budget rule.

item FR-003: "corpus-aware IDF term weighting in enrichment"
  evidence:   query_enrich.py — pure pipeline, NO graph access: enrich(query) ->
              EnrichedQuery(dense_query, sparse_query, identifiers); extraction backticks +
              identifier-shape; tiny hand-curated _STOPWORDS; NO document-frequency signal
              anywhere. Call seam: semantic.py computes enrichment ONCE at the boundary
              (params.enrich), feeds BOTH legs. DF signal = one FTS count query per term
              (same MATCH machinery lexical.py runs). Failure evidence reproduced live:
              enrich(L1-D03 text).identifiers == ('URL',) — the ubiquitous token is appended
              to dense_query and kept in sparse_query.
  status:     PARTIAL
  verify:     PASS (ran): the enrich() output on L1-D03's text — identifiers ('URL',),
              sparse 'parses already encoded URL string without re quoting', dense
              '<original> URL'.
  gap:        no DF computation, no threshold, no down-weight/drop; enrich's purity doctrine
              (no env reads) means the DF signal must be INJECTED as a parameter/table.

item FR-004: "RM3-style PRF over the fused first pass"
  evidence:   Insertion point: fused candidates exist at `candidates = fused_candidates`
              (immediately before the confidence gate) — PRF's feedback-doc source. Budget:
              ablation.json rows — all-levers-off tune p95 1142.0 vs rerank-off 28.9
              (~1113ms p95 gap at the shipped config); T017: "+0.9..3.8pp recall /
              -6.5..-9.5pp MRR at ~40x p95". One-call doctrine (the tension):
              query_enrich's dense_query contract explicitly forbids a second embedding
              call; semantic_search docstring pins "the one embed_query call". PRF needs a
              SECOND embed of the expanded query — a principled exception lives at the same
              boundary (after fusion, before gate/rerank), flag-gated, replacing the rerank
              budget rather than stacking. RetrievalParams: additive-field doctrine
              documented ("flags the function does not know are ignored, never errors").
  status:     TODO
  verify:     PASS (ran): stage order greps confirm the seams; no PRF code anywhere
              (grep prf|rm3|feedback src/cairn/ -> 0 hits).
  gap:        no PRF; NOTE the "~780ms p50" figure in v2 spec.md's Why paragraph is a spec
              claim not in any committed artifact — on-disk evidence is p95-based (1142 vs
              28.9; "~40x p95" in T017 DONE). T017's results.json was /tmp, not retained.

item FR-005: "multi-vector-per-symbol embeddings with max-score selection"
  evidence:   Schema is single-vector: embeddings PK (symbol_id, model) — a second vector per
              symbol SILENTLY OVERWRITES (ON CONFLICT DO UPDATE). Staleness compares ONE
              content_hash. chunk_for_symbol builds exactly ONE chunk. Scan path: brute leg
              yields DUPLICATE candidates per symbol under multi-row; ANN path's
              _candidates_from_ann_hits dedups by last-wins, not max. vec0: per-model table,
              rowid-keyed, rebuild indexes every row (multi-row-capable). Max-over-vectors
              seam: the candidate-dict construction loops (brute post-scan; ANN hits).
              Db-size tracked per row by run_sweep's _size_accounting.
  status:     TODO
  verify:     PASS (ran): PRAGMA on fresh init_db -> PK cols ['symbol_id','model']; the
              single-row upsert greps in embed_all/embed_symbols.
  gap:        schema PK, staleness flow, embed CLI, ANN index, and both candidate paths
              assume one vector per (symbol, model); no name-only/docstring-only producers;
              no max-score selection.

item FR-006: "confirmation ladder re-run + ablation-record extension"
  evidence:   Ladder machinery ALL exists: run_sweep + evaluate_on(purpose="validate",
              baseline_metrics=...) + paired_bootstrap. Ablation record:
              cairn-quality-ablation/1, 22 rows, verdict block with sc1 targets/actuals/
              margins/outcome. EXTENSION CONSTRAINT: tests/test_ablation_artifact.py (6
              tests) PINS doc[dataset] to (benchmark-datasource, DS-v1) with split 29+29
              ==58 — DS-v2/k-fold rows CANNOT live in this document's dataset block
              without breaking test 1; v2 families need a NEW document or a schema bump +
              guard-test change; row shape otherwise additive (>= comparisons).
              Protected baselines on disk: perf (search_symbols p95 6.25, semantic_search
              p95 201.67, explore p50 453.18/p95 513.73, impact p95 0.11); agent totals
              cairn est_tokens 6848; warm_time cold 15497.2 / warm 232.6 / 66.6x.
  status:     PARTIAL
  verify:     PASS (ran): pytest tests/test_ablation_artifact.py -> 6 passed; baseline
              figures re-read from the JSON files directly.
  gap:        no k-fold/DS-v2 measurement family; extension path undecided (new doc vs
              schema v2); ladder needs only fold aggregation + DS-v2.
```

## Supporting evidence

**eval.py public surface (verified by import/read)**
- load_ground_truth(dir) -> List[GradedQuery{id, level, kind, text, rationale, expectations[Expectation{symbol_id, grade}]}]; parse_symbol_id rpartitions file#symbol.
- split_queries(queries, *, seed=0x5EED, ratio=0.5) -> (tune, validate) — sorted-dedupe-shuffle; ceil to tune; ValueError outside [0,1].
- evaluate_on(conn, queries, *, ids, purpose="selection", held_out_ids=None, baseline_metrics=None, metric="recall_at_10", bundle_root=None, k=10, n_resamples=10000, seed=0xB0057, params=None, timer) -> report{purpose, n_queries, recall_at_10, mrr, per_query, durations_ms[, metric, baseline_mean, bootstrap]}; HeldOutError(RuntimeError).
- paired_bootstrap(a, b, n_resamples=10000, seed=..., confidence=0.95) — percentile CI + recentered-null p + paired-t cross-check.
- run_sweep(conn, queries, *, combos, split_seed=..., ids=None, baseline=None, metric=..., dataset_name/version, bundle_root, k, timer) -> {schema: cairn-quality-sweep/2, dataset, rows[{combo, recall_at_10, mrr, p95_ms, n_queries, db_mb, chunk_chars_max/mean, variant?}], baseline{combo, metric, per_query}}; variant combos re-embed via embed_all(variant=...); implicit ALL_LEVERS_OFF integrity row.
- evaluate_full_set(conn, queries, *, params, bundle_root, k, timer); format_sweep_json (canonical bytes).
- Matching: match_rank two-tier (identity file-suffix+exact-name, then substring); score_graded_query (recall=matched/expectations; MRR first-grade-2 rank).

**query_enrich** — enrich(query) -> EnrichedQuery; pure regex, stdlib re only; idempotence caveat documented (applied exactly once at the boundary).

**semantic_search stage order** — params resolution -> (enrich once if flagged) -> embed_query(_dense_query) [THE one call] -> ANN else brute -> RRF fusion (term mode when enriched; sparse_top_n clamp) -> confidence gate (raw query, margin 0.45) -> rerank(_dense_query) -> slice. RetrievalParams (frozen): dense_threshold, rrf_k, rrf_weights, sparse_limit, sparse_top_n, dense_pool, rerank_pool, rerank, enrich, gate_min_margin — None-means-default.

**embeddings** — embed_all(conn, batch_size=64, limit, progress, reap_orphans=True, variant=None); embed_symbols(conn, symbol_ids, sync_ann=True, variant=None); chunk_for_symbol(row, signature, variant, max_tokens=512) + CHUNK_VARIANTS (A, B, C, B_NO_SCOPE, B_NO_SIG, B_IDENTITIES, C_TRIM); _chunk_hash sha256; embed_query(text) -> (blob, dim); vec0: no replace semantics (DELETE+INSERT), rowid-keyed, per-model table.

**Ablation guard tests (6)** — 1: schema + canonical bytes + dataset pinned (benchmark-datasource, DS-v1), split 29+29; 2: exactly one shipped_defaults row, tune triples non-null, source "T0xx"; 3: validate/full_set measured-or-reason, never null; 4: shipped full_set == DS-v1 quality L1 at 4dp; 5: verdict exact block + near-miss rows p 0.118; 6: rendering three figures + findings.

**DS-v1 dataset facts (re-counted)** — 82 queries (58 L1 / 24 L5), 234 expectations (160 L1), kinds def 18 / callers 20 / flow 10 / impact 10 / knowledge 24; split seed 24301; tree_hash 65e3df39...f87bb.

## Unknowns
- The "~780ms p50" rerank figure in the v2 spec's Why paragraph — not in any committed artifact (on-disk evidence is p95-based: 1142 vs 28.9, "~40x p95"); T017's /tmp results not retained. Tech should cite p95 or re-measure.
