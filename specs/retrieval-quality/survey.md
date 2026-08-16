# Survey: retrieval-quality

**Created**: 2026-08-15 | **Baseline**: feat/retrieval-quality @ e8eee0e (off main; PR #35 DS-v1 quality baseline committed)
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item FR-001: "query enrichment — the exact query path today"
  evidence:   src/cairn/graph/semantic.py:405  `q_blob, q_dim = emb.embed_query(query)` — the raw
              query string goes to embed_query unchanged; no reformulation, no token extraction.
              src/cairn/graph/embeddings.py:888  `def embed_query(text: str) -> Tuple[bytes, int]:`
              body is `blobs, dim = _embed([text]); return blobs[0], dim` — zero preprocessing.
              src/cairn/graph/semantic.py:477  `bm25_raw = [dict(r) for r in search_symbols(conn, query, limit=30)]`
              — same raw query string to BM25 (fetch limit hard-coded 30).
              src/cairn/graph/lexical.py:76-80 (verified by running it):
              `_pattern_to_fts('where is the function that parses an unencoded URL string')`
              -> `'"where is the function that parses an unencoded URL string"*'`
              — a multi-word sentence becomes ONE QUOTED FTS5 PHRASE (all tokens, consecutive,
              in order) with a trailing phrase-prefix `*`. A phrase is stricter than implicit-AND:
              the symbol table has no row containing that token sequence, so MATCH returns nothing.
              src/cairn/graph/lexical.py:97-100 — the LIKE fallback then wraps the FULL sentence:
              `if "%" not in sql_pattern and "_" not in sql_pattern: sql_pattern = f"%{sql_pattern}%"`
              — `%where is the function...%` also matches nothing. Net: BM25 contributes an empty
              list for sentence queries; fusion degenerates to dense-only ranking.
              Only query normalization found anywhere in the path: src/cairn/graph/semantic.py:150
              `q = query.strip().lower()` inside `_exact_name_hit` — gate comparison only, not
              retrieval input.
  status:     TODO (no enrichment exists; raw string on both paths — verified)
  verify:     uv run python -c "from cairn.graph.lexical import _pattern_to_fts; print(repr(_pattern_to_fts('where is the function that parses an unencoded URL string')))"
              -> '"where is the function that parses an unencoded URL string"*'   [PASS]
              uv run python -c "from cairn.graph.embeddings import embed_query; import inspect; print(inspect.signature(embed_query))"
              -> (text: 'str') -> 'Tuple[bytes, int]'   [PASS]
  gap:        all of FR-001: identifier extraction, dense reformulation, sparse term weighting,
              measured default

item FR-002: "corpus chunk recipe — what gets embedded today"
  evidence:   src/cairn/graph/embeddings.py:100-105 `def chunk_for_symbol(row, signature=None,
              variant=None, max_tokens=512) -> str` — variants A/B/C, default from env:
              embeddings.py:111 `v = (variant or os.environ.get("CAIRN_CHUNK_VARIANT", "B")).upper()`.
              Variant B (the default) chunk composition, in order (embeddings.py:124-158):
              1. scope header — `File: {file_path}` + `Enclosing Scope: {parent_scope}` + `Imports: {imports_summary}`
                 (each only when the column is non-empty; embeddings.py:124-134)
              2. `{kind} {qualified_name-or-name}` header (embeddings.py:136-138)
              3. `Signature: {sig}` — the DECLARATION LINE read from disk by
                 `_signature_lines_for_rows` (embeddings.py:168-200, one source line per symbol)
              4. `Parameters: {params_raw}`, `Return Type: {ret_type}`, `Docstring: {doc}` (155)
              5. body ONLY in variant C: embeddings.py:157-158 `if v == "C" ... parts.append(f"Body:\n{row['body']}")`
              Max length: embeddings.py:161-164 `max_chars = max_tokens * 4` (512*4 = 2048 chars),
              hard truncation `res = res[:max_chars]`.
              content_hash semantics: embeddings.py:624-626 `_chunk_hash` = sha256 hex of the chunk
              text. Re-embed flow (embed_all, embeddings.py:704-711): a row is stale iff
              `r["existing_hash"] is None or r["existing_hash"] != new_hash` — so ANY recipe change
              that alters chunk text changes every hash and forces a full re-embed through the
              existing `ON CONFLICT(symbol_id, model) DO UPDATE` upsert (embeddings.py:737-745).
              Rowid-stable upsert preserves vec0 index keys. Model stamping: `current_model()`
              (embeddings.py:39-57) — rows are per-model; `purge_stale_models` cleans the rest.
  status:     PARTIAL (machinery exists and already carries qualified name + file path + signature
              + docstring; what is missing is the measured recipe/ablation — variant selection is
              an unmeasured env default, no size-bound accounting beyond the 2048-char truncate)
  verify:     grep -n "CAIRN_CHUNK_VARIANT\|max_chars\|Body:" src/cairn/graph/embeddings.py
              -> 111 / 162-163 / 157-158 lines matched   [PASS]
  gap:        no ablation ties variant choice to recall/MRR; db_mb/size bounds not tracked per recipe

item FR-003: "fusion/threshold knobs — exact tunables and current values"
  evidence:   src/cairn/graph/fusion.py:13-17 `def rrf_fuse(rankings, k=60, weights=None)` —
              weights param EXISTS but semantic.py:494 calls `fused_rank = rrf_fuse([bm25_ids, vec_ids], k=60)`
              — no weights passed -> fusion.py:28-29 `weights = [1.0] * len(rankings)`. k=60 hard-coded
              at the call site.
              threshold: semantic.py:262 `threshold: float = 0.3` (function default, NOT env). Applied
              DENSE-ONLY: brute path semantic.py:450 `scored = cosine_scan(q_blob, q_dim, triples, threshold)`;
              ANN path semantic.py:219 `ids = [sid for score ... if score >= threshold]`. BM25 candidates
              have NO threshold (semantic.py:477, all 30 rows enter fusion).
              Pool sizes: semantic.py:402 `pool_size = max(limit * 5, 50) if rerank_on else limit`
              (hard-coded); brute_force_limit semantic.py:429 `brute_force_limit = 50000` (hard-coded);
              BM25 fetch semantic.py:477 `limit=30` (hard-coded).
              Env-configurable vs hard-coded:
              - env: CAIRN_FUSION on/off semantic.py:345 `os.environ.get("CAIRN_FUSION", "1") != "0"`;
                CAIRN_RERANK on/off reranker.py:64-71; CAIRN_RERANK_MIN_MARGIN (default 0.45, clamped
                [0,1]) semantic.py:96-118; CAIRN_RERANK_MODEL reranker.py:77; CAIRN_CHUNK_VARIANT
                embeddings.py:111; CAIRN_EMBED_LOCAL_MODEL / CAIRN_EMBED_BACKEND / CAIRN_EMBED_MAX_SEQ_LEN
                (default "512") embeddings.py:57, 210, 472-474.
              - hard-coded: threshold 0.3 default (callers may override per-call), RRF k=60 + equal
                weights, pool multiplier 5 / floor 50, brute_force_limit 50000, bm25 fetch 30.
  status:     PARTIAL (knobs exist as params/envs; values are folklore — no sweep has ever chosen them;
              RRF weights not wired through the semantic_search call site)
  verify:     grep -n "rrf_fuse(\|threshold\|pool_size\|brute_force_limit\|CAIRN_FUSION" src/cairn/graph/semantic.py | head
              -> 262, 345, 402, 429, 450, 494 matched   [PASS]
  gap:        no swept ranges/choices recorded anywhere; weights/k/pools not env-tunable for a sweep

item FR-004: "reranker pairs + confidence gate calibration basis"
  evidence:   src/cairn/graph/reranker.py:189 `pairs = [(query, c.get("chunk") or "") for c in candidates]`
              — the pair is (raw query string, the STORED CHUNK — the same text that was embedded,
              i.e. variant-B chunk). No name+docstring-only format, no truncation control:
              reranker.py:153 `_RERANKER_CACHE[model_name] = CrossEncoder(model_name)` — constructed
              with NO max_length argument, so the model-config default applies (bge-reranker-base).
              Effective input length in this environment: unknown — verify.
              Gate reads FUSED scores, NOT rerank scores — VERIFIED: semantic.py:546-552 the gate
              `... and _fused_confident(query, candidates, limit)` runs on `candidates` which at that
              point are the fused candidates whose scores were overwritten at semantic.py:520
              `base["score"] = round(fused_score, 4)` (RRF rank-sums), and it runs BEFORE the rerank
              call at semantic.py:567 `final, reranked = rrk.rerank(query, candidates, limit)`.
              Consequence: a pair-format change shifts the rerank score distribution but does NOT
              shift the gate's margin inputs (RRF geometry). The gate input that FR-001 WOULD change
              is `_exact_name_hit(query, candidates[0])` (semantic.py:140-155: query.strip().lower()
              vs name/qualified_name) — an enriched/reformulated query string alters corroboration.
              Calibration basis (semantic.py:81-94 comment): "bge-m3 embeddings + BAAI/bge-reranker-base
              over a copy of this repo's src/ tree, 63 agent-style queries ... at threshold 0.45 the
              gated population keeps top-1 agreement 1.00 (limit=10) / 0.94 (limit=20) ... skipping
              ~17-25% of calls (~70% of exact-name traffic)". Margin default semantic.py:97
              `_DEFAULT_RERANK_MIN_MARGIN = 0.45`.
  status:     PARTIAL (pair construction exists as one fixed format; no measured-best format, no
              marginal-value report of the rerank stage, gate re-calibration measurement absent)
  verify:     grep -n "pairs = \[\|_fused_confident(query\|rrk.rerank(query\|CrossEncoder(model_name)" src/cairn/graph/reranker.py src/cairn/graph/semantic.py
              -> reranker.py:153, 189 / semantic.py:552, 567 matched   [PASS]
  gap:        rerank stage's recall/MRR marginal value never measured at any config; pair-format
              ablation absent; CrossEncoder effective max_seq_length unverified

item FR-005: "sweep harness surface — what exists"
  evidence:   src/cairn/eval.py:483-489 `def run_evaluation(conn, bundle_root=None, queries_path=None,
              corpus_filter="all", k=10) -> Dict[str, Any]` — NO parameter-override capability (no
              way to inject threshold/weights/pool/variant per run other than process env), and
              corpus_filter only selects level ("L1"/"L5"/"all") — it cannot select a query SUBSET
              (eval.py:452-453 `if corpus_filter != "all" and graded.level != corpus_filter: continue`).
              Ground-truth loader eval.py:121 `def load_ground_truth(ground_truth_dir: Path) -> List[GradedQuery]`
              — full load only, no subset/ids/split args. Retrieval entry used by the harness:
              eval.py:395 `results = list(qmod.semantic_search(conn, query, limit=k))` with DEFAULT
              threshold/rerank args (limit=k only).
              CLI: src/cairn/cli/system.py:493-508 `cairn eval` flags = --db --knowledge --corpus
              --queries --json ONLY. No --sweep, no overrides.
              quality.json artifact shape (benchmarks/baselines/DS-v1/quality.json, verbatim keys):
              schema "cairn-bench-baseline/1", suite "quality", timestamp, dataset{name,version,
              tree_hash,identity_size}, cairn_version, machine_profile{arch,cpu,cpu_count,os,
              runner_class}, embed{backend,model,embedded,skipped,total,reaped}, build{repos,files,
              symbols,edges,parse_errors}, ground_truth{path,queries,expectations,authoring_task},
              l5_surface (string), L1{count,recall_at_10,mrr,n_queries,n_expectations}, L5{same}.
              Mint path: scripts/mint_baselines.py:92 `def mint_quality(out_path)` — fresh t2 build +
              local-embed + run_evaluation over the graded pair, in-process (not a CLI suite).
  status:     TODO (single-config evaluation exists end-to-end; zero sweep/ablation surface — no
              parameter injection, no subset selection, no machine-readable multi-row results table)
  verify:    grep -n "def run_evaluation\|def load_ground_truth\|corpus_filter" src/cairn/eval.py | head
              -> 121, 435, 452, 483, 498 matched   [PASS]
  gap:        the whole harness: lever-combination enumeration, per-run overrides, results-table emit

item FR-006: "splits — existing split/seed machinery"
  evidence:   ABSENCE VERIFIED: `grep -rn "seed\|Random\|split" src/cairn/eval.py` matches only
              eval.py:197 `fields = line.split("\t")` (TSV parsing — unrelated). No seeded Random,
              no split, no held-out discipline anywhere in the eval path.
              Seeded Random patterns that exist to mirror (bench corpus side):
              src/cairn/bench/corpus.py:42 `rng = random.Random(seed)` (default seed fixed,
              corpus.py:17 "A seeded corpus is comparable across runs/machines");
              src/cairn/bench/agent_suite.py:333 `rng = random.Random(seed)` (target selection,
              seed 49374 stamped into agent.json).
  status:     TODO (nothing exists — verified absent)
  verify:     grep -rn "seed\|Random\|split" src/cairn/eval.py
              -> 197:fields = line.split("\t")  (only match)   [PASS]
  gap:        seeded tune/validate split of the 58 L1 queries; fail-if-selection-reads-validation

item FR-007: "protected baselines — exact numbers and artifacts"
  evidence:   benchmarks/baselines/DS-v1/perf.json ops (re-read this session):
              impact_analysis p95_ms 0.11 (median 0.1); impact_analysis_wide p95 0.89;
              semantic_search p95_ms 201.67 (median 196.12); explore p50_ms 453.18 (p95 513.73);
              search_symbols p95 6.25; find_definition p95 0.03. Corpus 301 files / 9000 symbols /
              db 37.2 MB.
              benchmarks/baselines/DS-v1/agent.json totals: cairn {tool_calls 9, chars 27397,
              est_tokens 6848, wall_ms 45.4}; control {tool_calls 919, est_tokens 1303120};
              reduction {calls_pct 99.0, tokens_pct 99.5}; seed 49374, runs 3, embed_backend "hash",
              chars_per_token 4.
              First-query warm time: the 322 ms figure exists ONLY as a phase-doc line —
              docs/phases/performance-gap/task.md:40 "P0-1 model warm-up at boot — first semantic
              query 9,428 -> 322 ms (29x); boot-thread, cache-verified only, HF-offline window,
              CAIRN_WARM_MODELS=0 kill switch. 23 tests." NO measured artifact in
              benchmarks/baselines/ carries it; re-measurement harness: unknown — verify. The
              machinery being protected: src/cairn/graph/model_warmup.py:81+
              `warm_models_in_background()` (boot daemon thread, CAIRN_WARM_MODELS kill switch
              model_warmup.py:136-137, PYTEST_CURRENT_TEST guard), wired at
              src/cairn/mcp_server/server.py:244-246.
              `cairn bench --compare`: src/cairn/cli/bench.py:197-200 `--threshold` default 0.15
              ("15%"), and bench.py:342-370 — regressions found -> `sys.exit(2)` (CI signal).
              Comparison basis: src/cairn/bench/report.py:148 `compare_reports(baseline, current,
              threshold=0.15)` compares per-op `median_ms`, `regressed = delta > threshold`;
              agent side agent_suite.py:521 `compare_agent_reports(..., threshold=0.15)`.
              Table regeneration contract: scripts/gen_benchmark_tables.py reads EXACTLY these
              quality.json keys — render_quality (gen_benchmark_tables.py:166-195) requires
              artifact["L1"] and artifact["L5"] blocks each with `recall_at_10`, `mrr`, `n_queries`
              (numbers; `_require` + `_num` raise GenerationError on missing/malformed -> exit 1),
              plus optional-shape notes from `n_expectations`, `ground_truth.path`, `l5_surface`,
              and `_provenance_line` (gen_benchmark_tables.py:257+) reading `dataset.version`,
              `machine_profile.runner_class` (REQUIRED — `_require`), `machine_profile.os/arch`,
              timestamp, cairn_version. A new sweep-results file that is NOT shaped like quality.json
              must stay OUT of benchmarks/baselines/<DS>/quality.json's role (or keep every required
              key) or table regeneration breaks.
  status:     PARTIAL (perf/agent baselines + compare thresholds + regeneration contract are exact
              and on disk; the warm-time baseline has no artifact and no re-measurement path)
  verify:     grep -n "0.15\|exit(2)" src/cairn/cli/bench.py | head
              -> 198 (default=0.15), 370 (sys.exit(2))   [PASS]
              grep -rn "322" docs/phases/performance-gap/task.md
              -> task.md:40 "9,428 -> 322 ms (29x)"   [PASS]
  gap:        warm-time re-measurement method (no harness, no artifact); how a sweep-results file
              coexists with gen_benchmark_tables.py's exact-key contract
```

## Supporting evidence

Machinery inventory (load-bearing symbols for tech-spec.md's code guide):

- semantic_search full call graph — src/cairn/graph/semantic.py:258 `def semantic_search(conn,
  query, limit=20, threshold=0.3, include_callers=False, rerank=None)`:
  1. retrieve — embed_query (semantic.py:405) -> ANN (semantic.py:410 ann.ann_query, pool_size)
     or brute force (semantic.py:429-450: SELECT ... LIMIT 50000, cosine_scan with threshold);
     candidates sliced to `scored[:pool_size]` (semantic.py:452).
  2. fuse — semantic.py:469-531: search_symbols(conn, query, limit=30) -> bm25_ids;
     `rrf_fuse([bm25_ids, vec_ids], k=60)` (semantic.py:494); bm25-only rows get chunk "" and
     score 0.0 then the fused score (semantic.py:507-520).
  3. gate — semantic.py:546-564: `_fused_confident(query, candidates, limit)` (margin >= 0.45 via
     `_rerank_min_margin` env + `_exact_name_hit`) -> skip rerank, emit RERANK_SKIPPED
     reason="confident_margin" (semantic.py:561).
  4. rerank — semantic.py:566-577 `rrk.rerank(query, candidates, limit)`; rerank_score rounded.
  5. finish — `_finish` closure (semantic.py:363-398): emits SEMANTIC_BACKEND (backend hash>ann>
     brute, fusion/rerank execution + degraded flags, ms/n buckets) and EMPTY_RESULT.
- embeddings pipeline — src/cairn/graph/embeddings.py: `embed_all` (663, batch 64, content_hash
  skip, rowid-stable upsert 737-745), `embed_symbols` (770, targeted + per-row vec0 sync),
  `chunk_for_symbol` (100), `_chunk_hash` (624, sha256), `_signature_lines_for_rows` (168, disk
  read of declaration line), `current_model` (39), `embed_query` (888), `purge_stale_models` (482),
  `reap_orphaned_embeddings` (629).
- fusion — src/cairn/graph/fusion.py:13 `rrf_fuse(rankings, k=60, weights=None)`; formula
  score(d) = sum_i w_i / (k + rank_i(d)); tie-break by doc_id (fusion.py:43).
- lexical — src/cairn/graph/lexical.py:121 `search_symbols(conn, pattern, kind=None, limit=100)`;
  `_pattern_to_fts` (38), `_is_fts_prefix_pattern` (18), `_search_like` (83, substring union at
  lexical.py:191-198).
- reranker — src/cairn/graph/reranker.py:157 `rerank(query, candidates, limit) -> (results,
  reranked)`; pairs at 189; `rerank_enabled` (53, env + marker file CAIRN_HOME/rerank_enabled),
  `current_rerank_model` (76, default BAAI/bge-reranker-base at reranker.py:21).
- eval — src/cairn/eval.py: `run_evaluation` (483), `load_ground_truth` (121), `GradedQuery`/
  `Expectation` (83-103), `match_rank` (317, tier-1 file-suffix + exact name, tier-2 substring),
  `score_graded_query` (357, recall = matched grade>=1 fraction; MRR = first grade-2 rank else 0),
  `_retrieve_l1` (390, semantic first / lexical fallback).
- eval CLI — src/cairn/cli/system.py:492-530 `cairn eval` (--db --knowledge --corpus --queries --json).
- warm-up — src/cairn/graph/model_warmup.py: `warm_models_in_background` (81), `warm_models` (109),
  kill switch `_warm_disabled` (136); boot wiring src/cairn/mcp_server/server.py:244-246.
- explore consumer — src/cairn/graph/explore.py:210 `sem_rows = semantic_search(conn, query,
  limit=max_nodes)` (any retrieval-quality win propagates into `cairn explore`).
- queries.py lazy re-export — src/cairn/graph/queries.py:25-28 `__getattr__` returns
  semantic.semantic_search (what eval.py's `qmod.semantic_search` resolves to).
- mint/regen — scripts/mint_baselines.py:92 `mint_quality`; scripts/gen_benchmark_tables.py:
  `load_baseline` (101), `render_quality` (166), `_require` (122), `_num` (129), sentinel
  contract `<!-- cairn-bench-tables:quality start/end -->` (module docstring).
- bench corpus seeds — src/cairn/bench/corpus.py:42 `rng = random.Random(seed)`;
  src/cairn/bench/agent_suite.py:333 (same pattern; seed stamped in agent.json).

Re-counted numbers (this session, from benchmarks/datasource/t2/ground_truth via load_ground_truth):
- total 82 queries = 58 L1 + 24 L5; L1 expectations 160, L5 74, total 234.
- L1 kinds: callers 20, definition 18, impact 10, flow 10.
- Matches quality.json: L1 {count 58, recall_at_10 0.4174, mrr 0.2862, n_expectations 160} —
  the spec's baseline numbers are reproduced by the artifact on disk.

DS-v1 artifact key shapes (what any new sweep output must interoperate with):
- quality.json: {schema, suite, timestamp, dataset{name,version,tree_hash,identity_size},
  cairn_version, machine_profile{...runner_class}, embed{...}, build{...},
  ground_truth{path,queries,expectations,authoring_task}, l5_surface, L1{count,recall_at_10,mrr,
  n_queries,n_expectations}, L5{same}}
- perf.json: {schema, corpus, db_path, db_size_mb, symbols, edges, ops[{name,median_ms,p50_ms,
  p95_ms,p99_ms,ops_per_sec}], timestamp, dataset, cairn_version, machine_profile}
- agent.json: {schema, corpus, seed, runs, embed_backend, chars_per_token, tasks[{label,
  question, cairn{...}, control{...}, reduction{...}}], totals{cairn,control,reduction}}

## Unknowns (explicit)

- First-query warm time re-measurement: no harness, no artifact; only the manual 322 ms in
  docs/phases/performance-gap/task.md:40 — unknown — verify.
- CrossEncoder effective max input length for bge-reranker-base in this install (code sets none;
  model-config default applies) — unknown — verify.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
