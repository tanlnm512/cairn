# Survey: retrieval-quality (re-survey at HEAD)

**Created**: 2026-08-15 | **Re-surveyed**: 2026-08-18 | **Baseline**: main @ 8dbf2ca
(v0.12.0 merge; 82 commits past the pre-implementation baseline e8eee0e — includes
this spec's implementation (PR #37, 24/24), the entire retrieval-quality-v2
campaign (PR #38/#39/#40/#41: k-fold harness, DS-v2, IDF weighting, RM3 PRF,
multi-vector), and the v0.12.0 release).
Phase-A output — the single source of truth for code state. Every citation
below is pasted verbatim from grep/read output in the session that wrote it.
The pre-implementation statuses (FR-001/005/006 TODO, FR-002/003/004/007
PARTIAL at e8eee0e) are superseded; this re-survey establishes HEAD truth.

## Items

```
item FR-001: "query enrichment — both-leg wiring behind a measured default"
  evidence:   src/cairn/graph/query_enrich.py:302 `def enrich(query: str,
              df_lookup=None) -> EnrichedQuery` — pure, deterministic, hermetic
              (docstring: "no randomness, time, environment, LLM, or network");
              class EnrichedQuery at :196; ubiquity predicate at :272; shipped
              DF cutoff `ENRICH_DF_MAX_FRACTION = 0.90` at :147 ("the shipped
              default (TC-011 pins it); T014's ablation sweeps 0.75-0.95").
              Wiring at the semantic_search boundary (semantic.py:741-743):
              `_enriched = enrich_query(query, df_lookup=_term_df_lookup(conn))`
              when `params.enrich_idf`, else `enrich_query(query)`.
              Dense leg — semantic.py:769 `q_blob, q_dim = emb.embed_query(dense_text)`
              (the ONE embed call embeds the enriched dense_query; T009).
              Sparse leg — semantic.py:926 `search_symbols_terms(` with the
              term list `_enriched.sparse_query.split()` (semantic.py:915);
              raw-query fallback `search_symbols(conn, query, limit=sparse_limit)`
              at :932 when enrichment is off/empty.
              Measured default: OFF — RetrievalParams.enrich field docstring
              (semantic.py:392-410): "``None``/``False`` keeps today's exact
              behavior (the flag is carried, not defaulted on)". The OFF
              decision is measurement-backed: benchmarks/quality/ablation.md:346
              "The shipped configuration is unchanged" (SC-1 not reached; 5/5
              bootstrap no-ships) and the unified ablation.json
              shipped_defaults block: "NO SHIP: the incumbent all-levers-off
              remains the shipped default configuration".
              Gate keeps the RAW query — semantic.py:104-107 comment "the gate
              deliberately still sees the RAW query, so flipping enrichment
              cannot shift the corroboration".
  status:     DONE (both-leg enrichment shipped behind a measured default of OFF;
              deterministic + hermetic; v2 added the IDF-aware enrich_idf variant
              with the 0.90 cutoff swept in benchmarks/quality/fr003-calibration/)
  verify:     uv run python -c "from cairn.graph.query_enrich import enrich; e = enrich('where is the function that parses an unencoded URL string'); print(repr(e.dense_query)); print(repr(e.sparse_query))"
              -> 'where is the function that parses an unencoded URL string URL' / 'parses unencoded URL string'   [PASS]
              uv run python -c "from cairn.graph.lexical import _terms_to_fts; print(repr(_terms_to_fts(['parse','url','string'])))"
              -> '"parse"* OR "url"* OR "string"*'   [PASS]
              uv run pytest tests/test_query_enrich.py tests/test_semantic_enrichment.py -q
              -> 79 passed in 4.51s   [PASS]
  gap:        none at the FR level — the measured default is honestly OFF; the
              SC-1 unlock path (enrich+rerank-off / multivector) is recorded in
              the v2 verdict, unshipped

item FR-002: "corpus chunk recipe — measured, re-embeddable, DS-v1 untouched"
  evidence:   src/cairn/graph/embeddings.py:105-108 `CHUNK_VARIANTS = ("A", "B",
              "C", "B_NO_SCOPE", "B_NO_SIG", "B_IDENTITIES", "C_TRIM")` — 7
              variants; env default still B at :139 `v = (variant or
              os.environ.get("CAIRN_CHUNK_VARIANT", "B")).upper()`; body only in
              variant C at :196 `parts.append(f"Body:\n{body}")`; truncate at
              :200-202 `max_chars = max_tokens * 4 ... res = res[:max_chars]`.
              Variant param threaded through the embed pipeline: embed_all
              signature at :867 (variant/multivector params at :874);
              `_reembed_for_variant` in eval.py:998 drives per-variant re-embed
              through the content-hash staleness flow; size accounting
              `db_mb` / chunk_chars_{max,mean} via `_size_accounting` (eval.py:1110).
              Measured recipe: B stays (C_TRIM won tune +9.3pp but validate
              p=0.579 at n=29 — recorded in the v1 ablation record embedded in
              benchmarks/quality/ablation.json, campaigns.retrieval-quality-v1);
              re-embed round-trip proven as an index operation (T015 note:
              1059 re-embedded + 7 content-identical no-ops, 0 failures).
              v2 extension (additive): MV_KINDS = ("name", "docstring") at
              embeddings.py:245 populates the parallel embeddings_mv table via
              `embed_all(multivector=True)` (:874); `cairn embed --multivector`
              CLI flag at src/cairn/cli/embed.py:35.
              DS-v1 ground truth untouched — re-counted this session via
              load_ground_truth: 82 queries = 58 L1 + 24 L5, 234 expectations
              (identical to the pre-implementation survey's count).
  status:     DONE (7-variant registry + threaded recipe param + measured no-ship
              of B + round-trip proof; v2's embeddings_mv is an additive FR-005
              surface, not a recipe change)
  verify:     grep -n "CAIRN_CHUNK_VARIANT\|max_chars\|Body:" src/cairn/graph/embeddings.py
              -> 135, 139, 196, 200, 201, 202, 885 matched (lines moved from
              the old survey's 111/162-163/157-158 — env-default/Body/truncate
              semantics unchanged)   [PASS]
              uv run pytest tests/test_chunk_variants.py tests/test_multivector_query.py -q
              -> 35 passed in 0.24s   [PASS]
  gap:        none — the winner-is-B outcome is measured and recorded

item FR-003: "fusion/threshold knobs — exposed, swept, chosen values recorded"
  evidence:   src/cairn/graph/semantic.py:343 `class RetrievalParams` with fields
              at :445-459: dense_threshold, rrf_k, rrf_weights, sparse_limit,
              sparse_top_n, dense_pool, rerank_pool, rerank, enrich, enrich_idf,
              gate_min_margin, multivector, prf, prf_docs, prf_terms, prf_lambda
              (None-means-default contract in the docstring :349-353). NOTE:
              the class MOVED from eval.py (pre-implementation home) to the
              graph layer.
              Weights+k now wired at the call site — semantic.py:972
              `fused_rank = rrf_fuse([bm25_ids, vec_ids], k=rrf_k, weights=rrf_weights)`
              (the old survey's finding "no weights passed" is fixed; rrf_fuse
              itself unchanged at src/cairn/graph/fusion.py:13).
              IDF infrastructure: term_df table at src/cairn/graph/schema.py:113
              `CREATE TABLE IF NOT EXISTS term_df`, FTS5-vocab builder
              `_rebuild_term_df_vocab` at schema.py:601, refreshed on every
              embed pass (embeddings.py:996 `rebuild_term_df(conn)`);
              `_term_df_lookup` boundary at semantic.py:474+.
              Swept ranges recorded on disk: benchmarks/quality/fr003-calibration/
              {sweep-baseline, sweep-df0.75, sweep-df0.80, sweep-df0.85,
              sweep-df0.90}.json (the df cutoff axis); the (k, weights, topN,
              threshold) grid was swept in v1 T012 — "18 combos/3 passes; every
              fusion lever INERT under the shipped pipeline" (task.md T012 note;
              rows in the embedded v1 ablation record). Chosen values: incumbent
              k=60 / weights (1,1) / threshold 0.3 / no topN — semantic.py:503
              `threshold: float = 0.3` default unchanged; the 0.90 DF cutoff is
              the one lever whose shipped value the sweeps set (query_enrich.py:147).
              Fusion bypass preserved: CAIRN_FUSION env path intact (grep
              matched the semantic.py fusion-gate lines).
  status:     DONE (every knob in the FR is a RetrievalParams field, wired,
              swept, and its chosen value recorded; the honest result is that
              the incumbents survived every sweep)
  verify:     grep -n "rrf_fuse(\|threshold\|pool_size\|brute_force_limit\|CAIRN_FUSION" src/cairn/graph/semantic.py | head
              -> matched at 88, 130, 250, 255, 263, 271, 354, 360, 361, 374, 445
              (call site now :972; threshold default :503 — both re-verified
              above)   [PASS]
              uv run pytest tests/test_retrieval_params.py tests/test_term_df.py -q
              -> 58 passed in 0.68s   [PASS]
  gap:        none — inert-lever verdicts (fusion grid, dense threshold) are
              documented findings, not gaps

item FR-004: "reranker pairs + gate recalibration + measured marginal value"
  evidence:   src/cairn/graph/reranker.py:370-374 `def rerank(query, candidates,
              limit, structured=False)` — TWO pair formats: flat (default) and
              structured (importance-ordered kind+qname/path/signature/docstring
              via `_structured_candidate_text` at :245). Pair construction at
              :438 `pairs = [(query, text) for text in pair_texts]`.
              Truncation pinned — :196-197 `_RERANKER_CACHE[model_name] =
              CrossEncoder(model_name, max_length=RERANK_MAX_LENGTH)` with the
              pin comment at :36-37 ("resolves max_length=512 ... a pin against
              future drift"). The old survey's Unknown (effective max length)
              is RESOLVED: probed 512 in this install and pinned.
              Sigmoid norm: rerank() docstring — "``rerank_score`` stays the RAW
              logit ... each result additionally carries ``rerank_score_norm``,
              the sigmoid-mapped [0, 1] value".
              Measured-best pair format: FLAT — docstring :393-396 "structured
              buys +0.7pp recall but costs -10.4pp MRR and ~10% stage latency
              vs the legacy flat format — the default is therefore FLAT
              (``structured=False``)". Marginal value of the STAGE measured
              (v1 T017 note: "+0.9..3.8pp recall / -6.5..-9.5pp MRR at ~40x p95").
              Gate recalibration: measured NO-CHANGE at 0.45 — semantic.py:100-110
              comment ("At margins {0.30, 0.45, 0.60, 0.75} the gate skips 0/29
              tune queries both at the shipped config and with query enrichment
              forced on ... A margin cannot be re-calibrated on a population
              with no skip traffic"); `_DEFAULT_RERANK_MIN_MARGIN = 0.45` at
              semantic.py:126. Call site semantic.py:1092
              `final, reranked = rrk.rerank(_dense_query, candidates, limit)`
              (enriched query when on, per D-005).
              v2 extension: RM3 pseudo-relevance feedback — src/cairn/graph/prf.py
              :185 `def expand(`; second pass at the post-fusion seam
              (semantic.py:1022 `if _prf and candidates and params is not None:`),
              REPLACING the rerank stage (D-012, semantic.py:642-649); PRF
              ablation rows committed (benchmarks/quality/fr004-prf/
              sweep-prf-docs{3,10}.json; ablation rows prf@docs=3 0.3728/0.1752,
              prf@docs=10 0.3624/0.1568 — both below incumbent; not shipped).
  status:     DONE (pair formats measured, flat shipped; truncation pinned;
              stage marginal value + gate recalibration both measured and
              recorded; PRF lever added and honestly refuted)
  verify:     grep -n "pairs = \|_fused_confident(query\|rrk.rerank(query\|CrossEncoder(model_name)" src/cairn/graph/reranker.py src/cairn/graph/semantic.py
              -> reranker.py:438 (pairs; construction shape changed from the old
              survey's :189) / semantic.py:1072 (_fused_confident with
              min_margin=_gate_margin_override) matched; rrk.rerank call now
              passes _dense_query (semantic.py:1092, re-verified above)   [PASS]
              uv run pytest tests/test_reranker.py tests/test_rerank_gating.py tests/test_prf.py tests/test_prf_wiring.py -q
              -> 97 passed in 0.39s   [PASS]
  gap:        none

item FR-005: "sweep harness — library + CLI + committed machine-readable table"
  evidence:   src/cairn/eval.py:1150 `def run_sweep(` (lever combos on the tune
              split, guarded seam `evaluate_on(ids=..., purpose="selection",
              held_out_ids=validate)`); :1348 `def run_sweep_kfold(` (seeded
              k-fold rotation, MIN_KFOLD_K floor 5); :1673 `def evaluate_full_set(`;
              :1736 `def format_sweep_json(` (canonical bytes).
              CLI surface — src/cairn/cli/system.py:498 `--sweep`, :503 `--out`,
              :506 `--kfold`, :511 `--folds` (default 5), :524 guard "--kfold
              requires --sweep".
              Committed results table: benchmarks/quality/ablation.json —
              schema "cairn-quality-ablation/2", 22 rows (re-counted this
              session: 10 ds-v1-kfold rows incl. the T014 integrity row
              0.4174/0.2862, 12 ds-v2 zero-shot rows), each row carrying
              recall_at_10/mrr/p95_ms/db_mb/tune/validate; the v1 campaign's
              /1 record is embedded VERBATIM under
              campaigns.retrieval-quality-v1 (22 embedded rows re-counted;
              its shipped_defaults row = "all-levers-off (shipped defaults)"
              with config {chunk_variant B, dense_threshold 0.3, enrich false,
              pair_format flat, rerank auto gate 0.45, rrf_k 60,
              rrf_weights [1,1], sparse_top_n null}). Per-lever-family sweep
              artifacts beside it: fr003-calibration/ (5 json + runner),
              fr004-prf/ (2 json + runner), fr005-mv/ (sweep-mv.json + runner),
              ladder-v2/ (3 ladder/zero-shot json). NOTE (audit): the D-007
              example path `benchmarks/baselines/DS-v1/sweep.json` in
              tech-spec.md:99 does NOT exist on disk and no sweep.json exists
              under benchmarks/baselines/ — the sweep artifacts live under
              benchmarks/quality/ (see Artifact inventory below).
              Reference quality table regenerated: benchmarks/baselines/DS-v1.1/
              quality.json (L1 {count 58, recall_at_10 0.4174, mrr 0.2862,
              n_expectations 160} — exact reproduction, re-read this session;
              plus the D-009 `retrieval` state block) and
              `uv run python scripts/gen_benchmark_tables.py` reports
              "already current" exit 0 at HEAD (run this session; no diff).
  status:     DONE (harness + CLI + k-fold + committed unified table + regenerated
              reference table; v2 renamed/unified the artifact: ablation-v2.*
              merged back into ablation.{json,md} at schema /2 with v1 embedded)
  verify:     grep -n "def run_evaluation\|def load_ground_truth\|corpus_filter" src/cairn/eval.py | head
              -> 175, 1191, 1961, 1980, 2011, 2015, 2056 matched (all three
              symbols exist; lines moved from the old survey's 121/435/452/483/498)   [PASS]
              uv run pytest tests/test_eval_sweep.py tests/test_eval_kfold.py -q
              -> 74 passed in 1.73s   [PASS]
              uv run python scripts/gen_benchmark_tables.py
              -> "docs/benchmarks.md already current (families: quality, perf, scaling)", exit 0   [PASS]
  gap:        none

item FR-006: "held-out validation — seeded splits + loud failure + both splits reported"
  evidence:   src/cairn/eval.py:314 `def split_queries(` (seeded 50/50 tune/validate;
              rng at :353 `rng = random.Random(seed)`); :377
              `def kfold_partitions(` (k>=5 floor, sorted-then-shuffled
              determinism; rng at :434); :470 `class HeldOutError(RuntimeError)`
              — docstring: "Subclasses ``RuntimeError`` — deliberately NOT
              ``ValueError`` ... An uncaught ``HeldOutError`` propagates to a
              non-zero exit ... fail loudly, no results table emitted";
              :656 `def paired_bootstrap(` (the accept guard).
              Sweep-time enforcement is seam-inherited (run_sweep docstring:
              "any requested id that intersects the validate half raises
              HeldOutError *before any retrieval runs*"); k-fold enforces per
              fold (run_sweep_kfold docstring: "a selection-stage read touching
              ANY fold's held-out ids raises HeldOutError at that fold's turn").
              Guard tests on disk: tests/test_eval_sweep.py,
              tests/test_eval_kfold.py (pytest.raises(HeldOutError) at :387,
              :399, :413, :429), tests/test_eval.py — all contain HeldOutError
              (rg -l verified this session).
              Both splits reported: every ablation.json row carries tune/
              validate sub-blocks; the integrity row's split_basis states
              "pooled k-fold rotation (each query held out exactly once, D-009);
              tune/validate are the seed-24301 29/29 halves reconstructed from
              the same per-query maps".
  status:     DONE (the old survey's verified ABSENCE — "only eval.py:197
              line.split matched" — is fully reversed; v2 upgraded 50/50 to a
              k-fold rotation with pooled paired bootstrap)
  verify:     grep -rn "seed\|Random\|split" src/cairn/eval.py
              -> 77 matching lines (was 1 pre-implementation); random.Random(
              at :353, :434, :714   [PASS]
              uv run pytest tests/test_eval_sweep.py tests/test_eval_kfold.py tests/test_eval.py -q
              -> 158 passed in 501.09s (HeldOutError guard tests included)   [PASS]
  gap:        none

item FR-007: "protected baselines re-measured + warm-time artifact + tables regenerated"
  evidence:   Warm-time artifact MINTED (the old survey's gap): benchmarks/
              quality/warm_time.json, schema "cairn-warm-time/1" — measurement
              re-read this session: cold first_query_ms 15497.2, warm
              first_query_ms 232.6, warmup join_ms 15070.3; notes record the
              322 ms phase figure as ADVISORY ("context, NOT a gate -- no
              committed baseline ever carried a warm-time number"). Harness:
              scripts/measure_warm_time.py + tests/test_warm_time_harness.py.
              Compare machinery unchanged: src/cairn/cli/bench.py:241
              `default=0.15` and :413 `sys.exit(2)  # CI signal: regressions
              found` (lines moved from the old survey's 198/370).
              Immutable BEFORE intact — DS-v1 perf.json re-read: semantic_search
              p95 201.67 (median 196.12), explore p50 453.18 / p95 513.73,
              search_symbols p95 6.25, impact_analysis p95 0.11,
              impact_analysis_wide p95 0.89, find_definition p95 0.03,
              db_size_mb 37.2 — byte-for-byte the old survey's numbers. DS-v1
              agent.json re-read: cairn {tool_calls 9, est_tokens 6848, wall_ms
              45.4}, reduction {calls_pct 99.0, tokens_pct 99.5} — unchanged.
              Quality table regenerated: DS-v1.1 quality.json (see FR-005) and
              docs/benchmarks.md sentinel block current (generator exit 0);
              docs/benchmarks.md:83 L1 row | 58 | 0.4174 | 0.2862 | with the
              provenance line "minted 2026-08-16, cairn 0.11.0".
              v0.12.0 note: DS-v1.1 quality.json and warm_time.json carry
              cairn_version 0.11.0 (minted before the release bump; HEAD
              __version__ = "0.12.0" at src/cairn/__init__.py:2). The tables
              are content-current at HEAD (generator: "already current") — no
              0.12.0-stamped re-mint exists.
              Warm-up machinery unchanged: model_warmup.py:81
              `def warm_models_in_background()`, :135 `_warm_disabled`, boot
              wiring mcp_server/server.py:244-246.
  status:     DONE (warm-time harness + committed artifact replace the manual
              322ms note; baselines re-verified intact; tables regenerated and
              current; no unresolved regression — the shipped config IS the
              baseline config, so nothing could regress)
  verify:     grep -n "0.15\|exit(2)" src/cairn/cli/bench.py | head
              -> 241 (default=0.15), 413 (sys.exit(2))   [PASS]
              grep -rn "322" docs/phases/performance-gap/task.md
              -> task.md:40 "9,428 -> 322 ms (29x)" (still the only warm-time
              figure in the phase doc; now advisory context in warm_time.json
              notes)   [PASS]
              uv run pytest tests/test_warm_time_harness.py tests/test_model_warmup.py -q
              -> 26 passed in 0.58s   [PASS]
  gap:        none (0.12.0 re-mint absence recorded above as a fact, not a gap —
              the shipped configuration did not change, so the 0.11.0-stamped
              tables remain the shipped numbers)
```

## Test evidence (this session's runs)

- `uv run pytest tests/test_query_enrich.py tests/test_semantic_enrichment.py
  tests/test_eval_sweep.py tests/test_eval_kfold.py tests/test_chunk_variants.py
  tests/test_reranker.py tests/test_prf.py tests/test_prf_wiring.py
  tests/test_multivector_query.py tests/test_term_df.py
  tests/test_retrieval_params.py -q` -> **317 passed in 5.36s**
- `uv run pytest tests/test_eval.py tests/test_rerank_gating.py
  tests/test_warm_time_harness.py tests/test_model_warmup.py -q`
  -> **136 passed in 999.29s** (warm-up suites load real models — long by nature)
- `uv run python scripts/gen_benchmark_tables.py` -> "already current", exit 0
- `uv run python ~/.agents/skills/spec-to-code/scripts/check.py specs/retrieval-quality/`
  -> "PASS (0 fail, 3 warn)": burndown Σ row 24/0 vs row sums 24/24 (task.md);
  citation `benchmarks/baselines/DS-v1/sweep.json` not found (tech-spec.md:99);
  survey-baseline-stale warning (resolved by this re-survey).

## Artifact inventory (re-counted this session)

benchmarks/baselines/:
- DS-v1/ — README.md, agent.json, perf.json, quality.json, scaling.json.
  NO sweep.json (never minted there; the tech-spec.md:99 D-007 path was an
  "e.g." example that the implementation placed elsewhere).
- DS-v1.1/ — README.md, quality.json (L1 0.4174/0.2862 exact reproduction,
  retrieval-state block, cairn_version 0.11.0).
- NO DS-v2 directory here — DS-v2 lives under benchmarks/datasource/ds2/
  (ground_truth/, second-corpus/ attrs-26.1.0, power-analysis.{json,md},
  verify_dataset.py, recompute_power.py).

benchmarks/quality/:
- ablation.json — schema cairn-quality-ablation/2; 22 rows = 10 ds-v1-kfold +
  12 ds-v2 (3 configs x {all-levers-off, multivector, enrich+rerank-off,
  enrich_idf+rerank-off}); v1 campaign record embedded verbatim (22 rows);
  verdict: multivector guard-CLEARED on DS-v1 k-fold (delta +0.1414,
  p=0.0035, 0.5588/0.3395) but REFUTED zero-shot on DS-v2 (macro MRR delta
  -0.0925; attrs delta -0.0432 p=0.1456) -> not shipped; shipped_defaults
  row: null with NO-SHIP statement.
- ablation.md — rendered record; line 374 "all-levers-off ▸ shipped defaults"
  0.5828/0.4444 tune; line 85 df_max=0.90 "(shipped)" = the shipped CUTOFF
  constant, not an enrich-on config.
- MEASURE.md — v2 runbook (reference-machine protocol, D-009 thread pinning).
- warm_time.json — see FR-007.
- fr003-calibration/ (sweep-baseline + df{0.75,0.80,0.85,0.90}.json +
  run_fr003_sweep.py + scratch_db.py), fr004-prf/ (2 json + runner),
  fr005-mv/ (sweep-mv.json + run_mv_sweep.py + scratch_db.py), ladder-v2/
  (3 json) — the per-lever-family sweep artifacts.

Ground truth re-counts (load_ground_truth, this session):
- DS-v1 (benchmarks/datasource/t2/ground_truth): 82 = 58 L1 + 24 L5,
  234 expectations — matches DS-v1/DS-v1.1 quality.json blocks exactly.
- DS-v2 (benchmarks/datasource/ds2/ground_truth): 198 = 154 L1 + 44 L5,
  558 expectations — matches ablation.json verdict.ds2_counts (154/44) and
  docs/benchmarks.md ("198 queries").

## Supporting evidence

Machinery inventory at HEAD (load-bearing symbols; line numbers this session):

- semantic_search — src/cairn/graph/semantic.py:499-507
  `def semantic_search(conn, query, limit=20, threshold=0.3,
  include_callers=False, rerank=None, params: Optional[RetrievalParams]=None)`.
  RetrievalParams NOW LIVES HERE (semantic.py:343; fields :445-459) — it moved
  out of eval.py. Retrieval stages inside: enrichment at the boundary (:716-744),
  dense embed (:769), sparse term-mode fetch (:926) with raw fallback (:932),
  RRF fusion with k+weights (:972), PRF second pass at the post-fusion seam
  (:1022), gate (:1072), rerank (:1092).
- query_enrich — src/cairn/graph/query_enrich.py: `enrich` (302, df_lookup
  param), `EnrichedQuery` (196), `_ubiquity_predicate` (272),
  `ENRICH_DF_MAX_FRACTION = 0.90` (147).
- prf — src/cairn/graph/prf.py: `expand` (185), `ExpansionResult` (167),
  `_idf` (150). RM3 module; consumed only under params.prf.
- embeddings — src/cairn/graph/embeddings.py: `CHUNK_VARIANTS` (105),
  `chunk_for_symbol` (111), `MV_KINDS = ("name","docstring")` (245),
  `embed_all` (867, variant+multivector params at 874), `embed_symbols`
  (1012), `rebuild_term_df` refresh on embed (996).
- fusion — src/cairn/graph/fusion.py:13 `rrf_fuse` (signature defaults
  unchanged — search_memory in memory/promotion.py still shares it).
- lexical — src/cairn/graph/lexical.py: `search_symbols` (237),
  `search_symbols_terms` (287), `_terms_to_fts` (86). `_pattern_to_fts`
  behavior unchanged (verify V1a) — its 8 production callers are protected.
- reranker — src/cairn/graph/reranker.py: `rerank` (370, structured=False
  default), `_structured_candidate_text` (245), pairs (438),
  CrossEncoder max_length pin (196-197), RERANK_MAX_LENGTH comment (36-37).
- eval — src/cairn/eval.py (2083 lines): `load_ground_truth` (175),
  `split_queries` (314), `kfold_partitions` (377), `HeldOutError` (470),
  `evaluate_on` (482), `paired_bootstrap` (656), `_normalize_combos` (923),
  `_reembed_for_variant` (998), `_EmbeddingStateMachine` (1027),
  `_size_accounting` (1110), `run_sweep` (1150), `run_sweep_kfold` (1348),
  `evaluate_full_set` (1673), `format_sweep_json` (1736), `run_evaluation`
  (2011, corpus_filter param at 2015).
- eval CLI — src/cairn/cli/system.py:498-524 --sweep/--out/--kfold/--folds.
- embed CLI — src/cairn/cli/embed.py:35 --multivector.
- term_df — src/cairn/graph/schema.py:113 (CREATE TABLE), :601
  (_rebuild_term_df_vocab, FTS5-vocab primary path).
- ANN dual-index — src/cairn/graph/ann_index.py:146
  `_SOURCE_PREFIX = {"embeddings": "vec_", "embeddings_mv": "vecmv_"}` — the
  FR-005 mv table gets its own vec0 index.
- bench compare — src/cairn/cli/bench.py:241 (threshold default 0.15), :413
  (sys.exit(2)).
- warm-up — src/cairn/graph/model_warmup.py:81 `warm_models_in_background`,
  :135 `_warm_disabled`; boot wiring src/cairn/mcp_server/server.py:244-246;
  harness scripts/measure_warm_time.py.
- explore consumer — src/cairn/graph/explore.py:210
  `sem_rows = semantic_search(conn, query, limit=max_nodes)` (unchanged).
- queries.py lazy re-export — src/cairn/graph/queries.py:25
  `if name == "semantic_search":` (unchanged mechanism).
- MCP tool wrapper — src/cairn/mcp_server/tools_graph.py:524
  `def semantic_search(query, limit=20, include_callers=False,
  structured=False, rerank=None)`.
- mint/regen — scripts/mint_baselines.py:136 `mint_quality`;
  scripts/gen_benchmark_tables.py:153 `render_quality`, :341 `main`, sentinel
  contract `<!-- cairn-bench-tables:{quality|perf|scaling} start/end -->`.

## Audit findings (status + evidence only)

- check.py WARN "citation path not found: benchmarks/baselines/DS-v1/sweep.json"
  — reproduced this session. The citing line is tech-spec.md:99 (D-007's
  "e.g." example path). No sweep.json exists under benchmarks/baselines/;
  the real sweep artifacts live under benchmarks/quality/ (inventory above).
  Not edited here (tech-spec.md is outside this survey's file ownership).
- check.py WARN "burndown: Σ row 24/0 disagrees with row sums 24/24" — task.md's
  burndown Σ says 0 done while phase rows and all 24 checkboxes say done; the
  task.md header ("Nothing is DONE — every task opens unchecked") is likewise
  stale vs the 24 [x] boxes. task.md is not this survey's to edit.
- v2 displaced several symbols this spec's docs cite by line number:
  RetrievalParams eval.py -> semantic.py:343; reranker pairs :189 -> :438;
  bench.py compare 198/370 -> 241/413; embeddings env-default :111 -> :139;
  run_evaluation :483 -> :2011. All re-pinned above.
- The v1 ablation record survives verbatim inside the unified /2 artifact
  (campaigns.retrieval-quality-v1.original_blobs) — no v1 row was retyped or
  migrated across measurement protocols.

## Unknowns (explicit)

- None open. The two pre-implementation unknowns are resolved at HEAD:
  warm-time re-measurement (warm_time.json + measure_warm_time.py) and
  CrossEncoder effective max length (probed 512, pinned RERANK_MAX_LENGTH).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
