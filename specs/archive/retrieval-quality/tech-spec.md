# Tech Spec: retrieval-quality

**Spec**: [spec.md](spec.md) | **Survey**: [survey.md](survey.md) | **Research**: [research.md](research.md) | **Created**: 2026-08-15
Every file/symbol citation below comes verbatim from survey.md (marked `survey FR-###`)
or from a cairn tool / grep run in this session (marked `[session: ...]`) — never from memory.

## Architecture

```mermaid
flowchart TD
    Q["query string"] --> ENR["query_enrich.enrich  [NEW · D-001]
    deterministic identifier/term extraction"]
    ENR -- "dense query (reformulated)" --> EQ["embed_query — embeddings.py:888
    ONE embed call, unchanged count"]
    ENR -- "sparse term query (unquoted)" --> BM["search_symbols limit=30 — semantic.py:477
    _pattern_to_fts UNTOUCHED"]
    EQ --> CAND["dense candidates (ANN semantic.py:410 / cosine_scan semantic.py:450)"]
    BM --> FUSE["rrf_fuse k, weights — fusion.py:13
    weights wired at semantic.py:494 call site [D-002]"]
    CAND --> FUSE
    FUSE --> GATE["_fused_confident margin 0.45 — semantic.py:546
    reads FUSED scores, pre-rerank"]
    GATE -- "confident_margin skip" --> OUT["results + SEMANTIC_BACKEND events"]
    GATE -- "rerank" --> RR["reranker.rerank — reranker.py:157
    pair format + explicit max_length=512 [D-005]"]
    RR --> OUT

    subgraph harness ["sweep harness  [NEW · FR-005/006]"
        SPLIT["seeded 50/50 tune/validate split of 58 L1 queries [D-06]
        + paired bootstrap guard"]
        SWEEP["lever combos → run_evaluation(tune ids, params)"]
        TABLE["sweep results table — own artifact shape [D-007]"]
        SPLIT --> SWEEP --> TABLE
    end
    SWEEP -- "winning chunk recipe" --> EMB["embed_all full re-embed via content_hash — D-004"]
    EMB --> CHUNK["chunk_for_symbol A/B/C + field-dropout variants — embeddings.py:100"]
    CHUNK -- "stored chunk text" --> EQ2["(corpus side, offline)"]
    SWEEP -- "shipped defaults" --> PARAMS["RetrievalParams [D-008]
    → threshold / weights / k / pair format / enrich on-off"]
    PARAMS --> CAND
    PARAMS --> FUSE
    PARAMS --> RR
```

The shipped system keeps the exact five-stage shape survey.md documents (retrieve → fuse →
gate → rerank → finish; `semantic_search` full call graph, survey "Supporting evidence") and
inserts **one new pure module before retrieval** (`query_enrich`), **wires parameters that
already exist** (`rrf_fuse`'s `weights`, `semantic_search`'s `threshold`), **re-formats the
reranker pair**, and adds an **offline sweep harness** that selects defaults on a seeded tune
split and regenerates the committed tables. Nothing in the query path becomes non-deterministic
or non-local: enrichment is regex/string operations, no LLM, no network, no model swap
(spec hard constraints; survey FR-001 status TODO — no enrichment exists today).

## Solution

### Chosen approach

Six decisions over research.md's option sets; each maps to an FR and lands as a lever the
harness ablates.

1. **D-001 Query enrichment (FR-001)** — new pure module `src/cairn/graph/query_enrich.py`:
   deterministic extraction of identifier-like tokens (camelCase/snake_case splits, backticked
   spans) plus stopword-trimmed term set. Output triple: `dense_query` (original sentence with
   extracted identifiers emphasized — reformulated string, still ONE `embed_query` call),
   `sparse_query` (whitespace term query, NOT a quoted phrase — kills the empty-BM25 failure),
   `identifiers` (for `_exact_name_hit` corroboration re-measurement). Called once at
   `semantic_search` entry; `search_symbols(conn, query, limit=30)` at semantic.py:477 receives
   `sparse_query` while `_pattern_to_fts` itself stays untouched.
2. **D-002 Fusion (FR-003)** — weighted RRF: pass `weights` through the existing call site
   (`rrf_fuse([bm25_ids, vec_ids], k=60)` at semantic.py:494 passes none today — survey FR-003),
   sweep a conservative grid of (k, w_dense, w_sparse) including k=60/equal as the incumbent
   row. Rank-based fusion retained (score-scale safe); defaults for `rrf_fuse`'s own signature
   unchanged so `search_memory` (see impact analysis) is untouched.
3. **D-003 Dense threshold (FR-003)** — calibrate the fixed threshold (today `threshold:
   float = 0.3` function default, semantic.py:262 — survey FR-003) on the tune split's labeled
   score distribution; sweep a grid; ship the measured value as the new default. Still a
   per-call parameter, not folklore.
4. **D-004 Chunk recipe (FR-002)** — ablate, don't build: variant B already embeds qualified
   name + file path + scope + signature + docstring (embeddings.py:124-158 — survey FR-002
   "machinery exists"). Sweep variants A/B/C plus **field-dropout variants of B** (RQ3 gap:
   no published symbol-level field ablation — first-principles axis, marked for measurement).
   Ship the measured winner as the `CAIRN_CHUNK_VARIANT` default; full re-embed via the
   existing content-hash staleness flow (embeddings.py:704-711) is by design an index operation.
5. **D-005 Rerank pair (FR-004)** — pair becomes (enriched query, structured candidate:
   kind, qualified name, path, signature, docstring — importance-ordered so tail truncation
   loses least), replacing `(query, c.get("chunk") or "")` (reranker.py:189 — survey FR-004).
   Construct `CrossEncoder` with explicit `max_length=512` and query-priority truncation
   (today constructed with NO max_length — reranker.py:153; effective length unknown, survey
   "Unknowns"). Report rerank's marginal recall/MRR at shipped config; re-measure (not
   re-derive) gate skip rates.
6. **D-006 Split + significance (FR-006)** — seeded 50/50 tune/validate split of the 58 L1
   queries using the seeded-`random.Random(seed)` pattern the repo already uses
   (bench/corpus.py:42, agent_suite.py:333 — survey FR-006); paired bootstrap significance
   guard on the validation split before a lever ships; final numbers on the full 58 with the
   split disclosed. Selection code structurally cannot load validation ids (harness asserts
   and fails otherwise).

Supporting decisions: **D-007** sweep output is a new artifact (shipped
under `benchmarks/quality/`; the illustrative benchmarks/baselines/DS-v1/sweep.json
never existed — path corrected at the 2026-08-18 doc audit, which also
confirmed PR #39's later unification into `ablation.json`, schema
`cairn-quality-ablation/2`) with its own schema, kept OUT of `quality.json`'s
role (gen_benchmark_tables.py's `render_quality` requires exact keys and raises — survey
FR-007); `quality.json` is regenerated only by a full-set `mint_quality` run at the shipped
config. **D-008** parameter injection is an explicit `RetrievalParams` object threaded
`run_evaluation → semantic_search` (and a `variant`/recipe param threaded into
`embed_all`/`embed_symbols`, which today call `chunk_for_symbol(r, signature=...)` with no
variant — variant comes from env only, embeddings.py:111 — survey FR-002; `[session: grep
chunk_for_symbol embeddings.py → 706, 823]`). No `os.environ` mutation per combo: the mint
path is in-process (survey FR-005) and env state would leak across combos.

**FR coverage**: FR-001→D-001; FR-002→D-004; FR-003→D-002+D-003; FR-004→D-005;
FR-005→harness+D-007+D-008; FR-006→D-006; FR-007→code guide area 6 (bench `--compare`
threshold 0.15 / exit 2 re-run + `gen_benchmark_tables.py` regen — survey FR-007).

**Order dependency for the planner**: FR-001 must land before the fusion sweep is meaningful —
today BM25 returns an empty list for sentence queries (survey FR-001: the quoted-FTS5-phrase
failure), so weighting a leg that contributes nothing measures noise.

### Alternatives rejected

| Alternative | Why rejected |
|-------------|--------------|
| RM3/Bo1 PRF over the fused first pass (RQ1 best-sourced lever) | Extra retrieval round threatens semantic p95 201.67ms; amplifies a 0.4174-recall first pass (Otero: PRF "remains vulnerable to topic drift when top-ranked documents are non-relevant" — research.md RQ1); deferred, not dismissed |
| API-doc thesaurus expansion (Lemos) | Needs a mined corpus cairn does not have — infrastructure, not tuning (research.md Choice 1d) |
| One-parameter convex combination (Bruch) instead of weighted RRF | Requires score normalization across BM25/cosine scales — new machinery + more overfit surface on 58 queries (research.md Choice 2d; Benham conservative-grids warning) |
| Plain RRF k=60 unchanged | Ignores the tuned-weights evidence; `weights` param already exists unwired (research.md Choice 2a; survey FR-003) |
| Committing to short-list k∈[1,10] a priori | Ecosystem-default k=60 is the documented incumbent; short-k is medium-confidence practitioner advice — sweep it instead (research.md RQ2 Milvus/serghei) |
| Keep threshold 0.3 | "No source sanctions a fixed 0.3 — calibrate on DS-v1 score distributions" (research.md RQ2 net; survey FR-003 "values are folklore") |
| Monotone calibration to probabilities | More machinery than a pre-fusion candidate filter needs (research.md Choice 3c) |
| Drop dense threshold, fused rank only | May admit weak results into the pool rerank/gate must then handle (research.md Choice 3d) |
| UniXcoder-style docstring⊕code vector concat | Two embeddings per symbol / pipeline reshaping vs text-level fields variant B already has (research.md RQ3; survey FR-002) |
| Body prefix (variant C) as unmeasured default | 2026 ablation: minimal/function-level chunks underperform declaration-style; and defaulting without measurement repeats the folklore pattern (research.md RQ3 arXiv:2605.04763; survey FR-002 gap) |
| k-fold CV instead of 50/50 split | Multiplies sweep compute per lever combo (chunk recipes each force a full re-embed) with no small-sample mandate for it (research.md Choice 6b; RQ5 gap) |
| Env-var mutation as the sweep override mechanism | In-process mint-style harness → env leaks across combos; and threshold/k/weights are call-site values, not envs, today (survey FR-003/FR-005) |
| Sweeping inside `search_symbols`/`_pattern_to_fts` | Eight other production callers pass identifier patterns and its p95 6.25ms is protected (survey FR-007; `[session: grep search_symbols( → 8 prod sites, below]`) |

## Impact analysis

Blast radius from cairn tools + greps this session; survey anchors noted.

- **`semantic_search`** (src/cairn/graph/semantic.py:258 — survey supporting evidence) is the
  hub. Precise `cairn impact` → 1 resolved caller: `src/cairn/mcp_server/tools_graph.py:597`
  (MCP tool wrapper) `[session: cairn impact semantic_search]`. The other two production
  consumers are survey-documented: `graph/explore.py:210` (`sem_rows = semantic_search(conn,
  query, limit=max_nodes)`) and `eval.py:395` (`results = list(qmod.semantic_search(conn,
  query, limit=k))` via the queries.py lazy re-export, queries.py:25-28). Fuzzy impact totals
  93 `[session: cairn impact semantic_search --fuzzy]` but is inflated by a name collision:
  `knowledge/search.py:225 _semantic_search` is a distinct function whose own docstring says
  "NO symbols/files JOIN (unlike queries.semantic_search)" `[session: grep]` — excluded
  per the no-name-collision-inflation doctrine. The fuzzy report also flags a cycle
  `['explore', 'semantic_search']` — a retrieve→explore feedback edge, informational.
  Direct test blast: ~30 test functions across test_fusion, test_rerank_gating,
  test_ann_*, test_semantic_*, test_audit_remediation `[session: same fuzzy run]`.
- **`rrf_fuse`** (fusion.py:13) — production callers: `semantic.py` fusion leg (survey FR-003:
  semantic.py:494) **and `search_memory` in src/cairn/memory/promotion.py:270**
  `[session: cairn callers rrf_fuse]` — a caller survey.md does not list (**gap reported**).
  Consequence: never change `rrf_fuse`'s signature defaults; wire weights only at the
  semantic call site.
- **`search_symbols`** (lexical.py:121) — the enrichment must NOT move into it. Production
  call sites `[session: grep search_symbols(`]: eval.py:255/260/400 (lexical fallback leg),
  bench/perf_suite.py:206 (protected perf op, p95 6.25), semantic.py:477 (fusion leg),
  explore.py:167 (seed rows), cli/query.py:83, compass/router.py:186,
  mcp_server/tools_graph.py:710. Survey FR-001 documents only the semantic.py:477 site —
  the other seven are this session's addition (**gap reported**: survey's FR-001 caller
  inventory is partial).
- **`chunk_for_symbol`** (embeddings.py:100) — callers `embed_all` (embeddings.py:706) and
  `embed_symbols` (embeddings.py:823) + chunk tests `[session: cairn callers
  chunk_for_symbol]`, matching survey supporting evidence. Any recipe change flips every
  `_chunk_hash` → full re-embed through the rowid-stable upsert (survey FR-002). Perf
  exposure: `embed_all` timing is a bench op (bench/perf_suite.py:159-166
  `[session: grep]`) but is NOT among the protected DS-v1 perf.json ops (impact,
  impact_wide, semantic_search, explore, search_symbols, find_definition — survey FR-007);
  db_mb (37.2 MB today) must be tracked per recipe (spec risk).
- **`rerank` / reranker** — `rrk.rerank(query, candidates, limit)` at semantic.py:567 and
  `pairs = [(query, c.get("chunk") or "") ...]` at reranker.py:189 (survey FR-004). Precise
  `cairn callers rerank` → none (attribute-dispatch; survey is the anchor). Gate
  interplay: the gate reads FUSED scores overwritten at semantic.py:520 and runs BEFORE
  rerank at semantic.py:567 — so a pair-format change does NOT shift gate margin inputs;
  what FR-001's enriched query changes is `_exact_name_hit` corroboration (semantic.py:140-155)
  (all survey FR-004). CrossEncoder is built with no `max_length` (reranker.py:153); effective
  bge-reranker-base input length unverified (survey "Unknowns" — verify).
- **`run_evaluation`** (eval.py:483) — callers: `eval_cmd` (cli/system.py:508)
  `[session: cairn callers run_evaluation]` and `mint_quality` (scripts/mint_baselines.py:92,
  survey FR-005). It currently accepts no overrides and no query subset (survey FR-005);
  threading `RetrievalParams` + tune-split ids through it changes both callers' behavior
  only additively (defaults preserve today's semantics).
- **Protected baselines** (FR-007): enrichment adds regex work and ZERO extra embed calls
  (dense reformulation replaces the embedded string); fusion weights add O(candidates)
  arithmetic; impact p95 0.11ms is graph-layer and untouched; explore p50 453.18ms inherits
  whatever semantic_search does; agent tokens 6848 depend on result-row shape, which this
  spec does not change. First-query warm time has NO artifact and NO re-measurement harness
  (survey FR-007 gap + "Unknowns") — flagged below.

**Gaps (not in survey.md — reported, not silently cited)**
1. `rrf_fuse`'s second production caller `search_memory` (memory/promotion.py:270) — found by
   `cairn callers` this session; survey's FR-003 evidence names only the semantic.py call site.
2. `search_symbols` caller inventory (7 additional production sites, listed above).
3. `embed_all`/`embed_symbols` call `chunk_for_symbol` without a `variant` argument — recipe
   injection needs a new threaded parameter (survey documents the env-only default at
   embeddings.py:111 but not the call-site shape).
4. CrossEncoder effective max input length in this install (survey "Unknowns" — verify).
5. First-query warm-time re-measurement method (survey FR-007 gap: 322 ms exists only as a
   phase-doc line, docs/phases/performance-gap/task.md:40).

## Code guide

### Area 1 — Query enrichment (FR-001)
- Touches: NEW `src/cairn/graph/query_enrich.py`; call sites in `semantic_search` —
  `embed_query` at semantic.py:405 and the BM25 leg at semantic.py:477 (survey FR-001);
  gate corroboration `_exact_name_hit` at semantic.py:140-155 (survey FR-004).
- Approach: pure functions (`enrich(query) -> (dense_query, sparse_query, identifiers)`),
  regex-only, no I/O. Dense path embeds `dense_query` in the existing single call; sparse leg
  passes `sparse_query` to `search_symbols` (unquoted term set, so `_pattern_to_fts` gets
  input it can handle). Keep the raw query for `_exact_name_hit` or re-measure the gate.
- Verify before implementing: `uv run python -c "from cairn.graph.lexical import
  _pattern_to_fts; print(repr(_pattern_to_fts('where is the function that parses an
  unencoded URL string')))"` (survey FR-001 verify — expect the quoted-phrase output today)
  and `grep -rn "seed\|Random\|split" src/cairn/eval.py` (survey FR-006 verify — expect only
  the TSV `line.split` match, proving no enrichment-adjacent machinery exists).
- Pitfalls: do NOT edit `_pattern_to_fts` (8 other callers, protected p95 6.25 — impact
  analysis); do NOT add a second `embed_query` call (latency); an enriched query string
  changes `_exact_name_hit` corroboration → gate skip-rate re-measurement is mandatory
  (survey FR-004 consequence note).

### Area 2 — Fusion/threshold params (FR-003)
- Touches: the `rrf_fuse([bm25_ids, vec_ids], k=60)` call at semantic.py:494 and
  `threshold: float = 0.3` default at semantic.py:262 (survey FR-003); `rrf_fuse` itself
  (fusion.py:13-17 — weights param already exists).
- Approach: thread `RetrievalParams` (weights, k, threshold, pool sizes) from
  `semantic_search`'s signature (optional, defaults = shipped config) down to the call site;
  sweep selects values on the tune split.
- Verify before implementing: `grep -n "rrf_fuse(\|threshold\|pool_size\|brute_force_limit\|
  CAIRN_FUSION" src/cairn/graph/semantic.py | head` (survey FR-003 verify → 262, 345, 402,
  429, 450, 494).
- Pitfalls: never change `rrf_fuse`'s own defaults — `search_memory`
  (memory/promotion.py:270, session finding) shares it; `CAIRN_FUSION=0` must keep bypassing
  the whole leg (semantic.py:345, survey FR-003); BM25 leg has no threshold today — adding
  one is a new lever, sweep it separately.

### Area 3 — Chunk recipe (FR-002)
- Touches: `chunk_for_symbol` (embeddings.py:100-105, variants at 124-158), re-embed flow
  `embed_all` (embeddings.py:704-711 hash-staleness) and `embed_symbols` (770) (survey
  FR-002 + supporting evidence); call sites at embeddings.py:706/823 take no `variant`
  (session grep — gap 3).
- Approach: add field-dropout variants (subsets of variant B's fields), thread an explicit
  recipe param through `embed_all`/`embed_symbols`, sweep recall@10/MRR + db_mb per recipe;
  ship the winner as the `CAIRN_CHUNK_VARIANT` default (today an unmeasured env default,
  embeddings.py:111).
- Verify before implementing: `grep -n "CAIRN_CHUNK_VARIANT\|max_chars\|Body:"
  src/cairn/graph/embeddings.py` (survey FR-002 verify → 111 / 162-163 / 157-158).
- Pitfalls: every recipe change forces a full re-embed (content_hash — survey FR-002), so
  recipe is the most expensive sweep axis — run it as its own stage, then sweep other levers
  on the winning recipe; 2048-char truncate (max_tokens 512 × 4) bounds the enriched chunk
  but db_mb must be in the results table (spec risk).

### Area 4 — Reranker pairs (FR-004)
- Touches: `pairs = [(query, c.get("chunk") or "") for c in candidates]` (reranker.py:189)
  and `CrossEncoder(model_name)` with no max_length (reranker.py:153); stage entry
  `rrk.rerank(query, candidates, limit)` at semantic.py:567 (survey FR-004).
- Approach: structured candidate text (kind, qname, path, signature, docstring,
  importance-ordered), explicit `max_length=512`, query-priority truncation; report the
  stage's marginal recall/MRR on/off at the shipped config.
- Verify before implementing: `grep -n "pairs = \[\|_fused_confident(query\|rrk.rerank(query\|
  CrossEncoder(model_name)" src/cairn/graph/reranker.py src/cairn/graph/semantic.py`
  (survey FR-004 verify → reranker.py:153, 189 / semantic.py:552, 567); empirically probe the
  effective max length in this install (survey "Unknowns").
- Pitfalls: bge-reranker-base raw scores are unbounded (research.md RQ4 model card) — never
  threshold them without a sigmoid; the gate's margin inputs are FUSED scores (survey FR-004),
  so pair-format changes do NOT excuse gate re-measurement — the enriched query does.

### Area 5 — Sweep harness + splits (FR-005/FR-006)
- Touches: `run_evaluation` (eval.py:483-489, no override/subset capability today),
  `load_ground_truth` (eval.py:121, full load only), `corpus_filter` (eval.py:452-453, level
  select only — cannot select a query subset), CLI `cairn eval` flags (cli/system.py:493-508,
  `--db --knowledge --corpus --queries --json` ONLY) (survey FR-005); split machinery is a
  verified ABSENCE (survey FR-006: only eval.py:197 `line.split` matches).
- Approach: `--sweep` subcommand (or scripts/ equivalent per FR-005) that enumerates lever
  combos, threads `RetrievalParams` + tune-split query ids into `run_evaluation`, emits a
  machine-readable multi-row table (own schema — D-007). Seeded split mirrors
  `random.Random(seed)` (bench/corpus.py:42, agent_suite.py:333 — survey FR-006); harness
  raises if selection-stage code touches validation ids (FR-006's fail condition); paired
  bootstrap on validation before accept.
- Verify before implementing: `grep -n "def run_evaluation\|def load_ground_truth\|
  corpus_filter" src/cairn/eval.py | head` (survey FR-005 verify → 121, 435, 452, 453, 483,
  498).
- Pitfalls: `run_evaluation` calls `qmod.semantic_search` with DEFAULT args (eval.py:395) —
  overrides must be explicit params, not env; 58 queries ≈ TREC's 50-topic regime where
  bootstrap/t are interchangeable but Wilcoxon/sign are not (research.md RQ5 Smucker);
  keep parameter grids conservative (Benham, research.md RQ5).

### Area 6 — Baseline protection + table regeneration (FR-007)
- Touches: `scripts/gen_benchmark_tables.py` `render_quality` (166-195, `_require`/`_num`
  raise on missing keys), sentinel contract `<!-- cairn-bench-tables:quality start/end -->`;
  `scripts/mint_baselines.py:92 mint_quality` (in-process fresh build + local-embed +
  run_evaluation); `cairn bench --compare` (cli/bench.py:197-200 threshold default 0.15;
  342-370 regressions → `sys.exit(2)`); agent compare `compare_agent_reports(...,
  threshold=0.15)` (agent_suite.py:521) (all survey FR-007 + supporting evidence).
- Approach: after the winning config ships — re-run perf + agent suites under `--compare`
  against DS-v1; regenerate quality tables via `mint_quality` full-set run; sweep table lives
  beside (not inside) quality.json; warm-time: define a minimal re-measurement (first
  semantic query wall-time in a fresh process, `CAIRN_WARM_MODELS` path per
  model_warmup.py:81+/136-137, survey FR-007) or document the trade — advisory CI posture
  means regressions are fixed-or-documented, not auto-blocked beyond bench's exit 2.
- Verify before implementing: `grep -n "0.15\|exit(2)" src/cairn/cli/bench.py | head`
  (survey FR-007 verify → 198, 370) and `grep -rn "322" docs/phases/performance-gap/task.md`
  (→ task.md:40, the only warm-time figure).
- Pitfalls: any new sweep file shaped differently from quality.json must stay out of
  quality.json's path or keep every required key — `render_quality` hard-fails otherwise
  (survey FR-007 gap names exactly this); `machine_profile.runner_class` is REQUIRED by
  `_provenance_line` (survey FR-007).

## References

From research.md (why each matters here):
- [Hui et al., PRF comparative study](https://khui.github.io/files/publications/Hui2011_Chapter_AComparativeStudyOfPseudoRelev.pdf) — the deferred-but-best-sourced enrichment lever (D-001 rejection rationale).
- [Otero 2026, LLM-Assisted PRF](https://arxiv.org/abs/2601.11238) — PRF topic-drift risk on weak first passes; why PRF waits for a better baseline.
- [Bruch et al., Fusion Functions for Hybrid Retrieval](https://arxiv.org/abs/2210.11934) — licenses tuned fusion weights on a small tune split (D-002).
- [Cormack et al., SIGIR 2009 RRF](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf) — k=60 incumbent provenance.
- [BGE-M3 paper](https://arxiv.org/html/2402.03216v3) + [threshold discussion](https://www.emergentmind.com/topics/bge-m3-embedding-model-3ae9be85-46f0-4bec-be49-80d85554d4a6) — no universal cosine cutoff; calibrate on own pairs (D-003).
- [UniXcoder](https://aclanthology.org/2022.acl-long.499.pdf) — docstring⊕code concat evidence (rejected as pipeline reshaping).
- [Chunking ablation, arXiv:2605.04763](https://arxiv.org/html/2605.04763v1) — declaration/context chunks beat minimal chunks (D-004 prior).
- [BAAI/bge-reranker-base model card](https://huggingface.co/BAAI/bge-reranker-base) — unbounded raw scores, 512 max (D-005).
- [SentenceTransformers CrossEncoder docs](https://sbert.net/docs/package_reference/cross_encoder/model.html) — `max_length` is caller-controlled truncation.
- [Smucker et al., CIKM 2007](https://dl.acm.org/doi/10.1145/1321440.1321528) + [Urbano et al.](https://julian-urbani.info/files/publications/076-statistical-significance-testing-information-retrieval-empirical-analysis-type-i-type-ii-type-iii-errors.pdf) — 58-query significance regime: bootstrap/t, not Wilcoxon (D-006).
- [Benham et al., ADCS 2017](https://rodgerbenham.github.io/bc17-adcs.pdf) — conservative fusion grids on small tuning sets (D-002/D-006 grid discipline).
- [Overtuning, arXiv:2506.19540](https://arxiv.org/html/2506.19540v1) — adaptive overfitting on a fixed test set; the FR-006 threat model.
- [BEIR](https://arxiv.org/abs/2104.08663) — never tune on the eval set; BM25 robustness argues for keeping the sparse leg alive (D-001's sparse fix).

## Decisions

### D-001: Deterministic query enrichment module (FR-001)
- **Context**: raw sentence queries reach both paths unchanged (survey FR-001 TODO); quoted-FTS5-phrase failure makes BM25 always empty for NL queries; LLM rewriting is doctrinally out.
- **Decision**: new pure `query_enrich` module producing (dense_query, sparse_query, identifiers); applied once in `semantic_search`; `_pattern_to_fts` untouched.
- **Consequences**: sparse leg becomes non-empty for sentence queries (changes fused geometry → fusion sweep must re-run after); `_exact_name_hit` corroboration shifts → gate skip-rate re-measured; PRF remains a future lever on top of a fixed sparse leg.

### D-002: Weighted RRF, conservative (k, weights) grid (FR-003)
- **Context**: `weights` exists but is never passed; k=60 hard-coded (survey FR-003); Bruch shows tuned one-parameter fusion beats plain RRF; Benham warns small tuning sets overfit fine grids.
- **Decision**: wire weights at the semantic.py call site; sweep a small grid including the k=60/equal incumbent; `rrf_fuse` signature defaults unchanged.
- **Consequences**: `search_memory` (second caller, session finding) unaffected; shipped weights are DS-v1-tuned and must be re-tuned if the corpus model or chunk recipe changes materially.

### D-003: Calibrated fixed dense threshold (FR-003)
- **Context**: 0.3 is folklore (survey FR-003); no universal bge-m3 cutoff exists (research.md RQ2).
- **Decision**: sweep the threshold on the tune split's labeled score distribution; ship the measured value as the `semantic_search` default.
- **Consequences**: default changes for all callers (MCP, explore, eval) at once — full-set quality regen and perf re-run gate the change; monotone calibration remains available if interpretability is later needed.

### D-004: Ablate-then-ship chunk recipe, field-dropout axis (FR-002)
- **Context**: variant B already carries qname+path+scope+signature+docstring (survey FR-002); no published symbol-level field ablation (research.md RQ3 gap).
- **Decision**: sweep A/B/C + field-dropout variants of B with recall/MRR and db_mb; ship the winner as the variant default; accept the content-hash full re-embed as an index operation.
- **Consequences**: one-time re-embed cost per shipped recipe change; db_mb tracked; DS-v1 ground truth untouched (measurement only).

### D-005: Structured rerank pair, explicit truncation (FR-004)
- **Context**: pair is (raw query, stored chunk) with no truncation control (survey FR-004); rerank marginal value never measured; pair-context evidence absent (research.md RQ4 gap).
- **Decision**: (enriched query, importance-ordered structured candidate), explicit `max_length=512`, query-priority truncation; report on/off marginal value at shipped config.
- **Consequences**: rerank score distribution shifts → any score-thresholding on rerank output needs a sigmoid (raw scores unbounded); gate margin inputs (fused scores) unaffected by the pair change itself.

### D-006: Seeded 50/50 split + paired bootstrap guard (FR-006)
- **Context**: no split machinery exists (survey FR-006 verified absence); 58 queries ≈ the 50-topic regime (research.md RQ5); adaptive-overfitting risk on a fixed set.
- **Decision**: seeded `random.Random(seed)` 50/50 tune/validate over the 58 L1 queries; selection reads tune only (harness fails otherwise); accept a lever only if the validation delta passes paired bootstrap; final numbers on the full set with the split disclosed.
- **Consequences**: 29-query tune split is noisy — conservative grids and the incumbent-included sweep are the compensating controls; k-fold remains available if a lever's delta is within noise.

### D-007: Sweep results live in their own artifact shape (FR-005/FR-007)
- **Context**: `gen_benchmark_tables.py` requires quality.json's exact keys and raises otherwise (survey FR-007 gap names this contract).
- **Decision**: new sweep file (own schema) beside, never inside, `quality.json`'s role; the reference quality table is regenerated only from a full-set `mint_quality` run at the shipped config.
- **Consequences**: two committed artifacts to keep coherent; the sweep table is provenance for "which lever bought what", the quality table is the shipped number.

### D-008: Explicit RetrievalParams injection, no per-combo env mutation (FR-005)
- **Context**: the mint path is in-process (survey FR-005); env state would leak across combos; threshold/k/weights are call-site values today (survey FR-003); `embed_all`/`embed_symbols` don't thread a variant (session finding, gap 3).
- **Decision**: an optional `RetrievalParams` object threaded `run_evaluation → semantic_search`, plus an explicit recipe param threaded into the embed pipeline; defaults preserve current behavior exactly.
- **Consequences**: `run_evaluation` and both its callers' signatures grow an optional param (additive); the sweep harness never mutates process environment, keeping runs hermetic and order-independent.


### D-009: DS-v1 quality figures carry mint-time measurement noise (orchestrator, 2026-08-16)

**Context**: T006's integrity checkpoint demanded exact 4-decimal reproduction of
DS-v1's L1 recall@10 0.4174 / MRR 0.2862. A fresh deterministic measurement on
the reference machine gives **0.4195 / 0.2925** — stable across two thread-pinned
runs, identical through both the original `run_evaluation` entrypoint and the new
sweep harness, and — the decisive bisect — **identical at the #35 merge commit
itself**. The committed artifact's numbers are not bit-reproducible because the
rerank-active pipeline flips near-tie rankings under mint-time environment state
(reranker warm/cold, torch threading under the 752s concurrent mint).

**Decision**: (1) DS-v1 stays immutable (D-010 of the datasource spec) — the
artifact records what was measured then. (2) The integrity contract is
henceforth *deterministic self-consistency*: the harness must reproduce the
current deterministic measurement exactly (it does: 0.4195/0.2925, both
entrypoints, twice), with the artifact band documented as ±0.002 recall /
±0.006 MRR measurement noise. (3) Improvement targets (SC-1: ≥0.50 / ≥0.33) are
unchanged — measured against the deterministic baseline, which is slightly
higher, making the bar honestly harder. (4) Sweep measurements pin
torch.set_num_threads(1) for reproducibility (protocol documented in the
harness; a P5 publish task notes it in the ablation provenance).

**Consequences**: the all-levers-off row of every sweep table reads 0.4195 /
0.2925 (not the artifact's figures), with a provenance note explaining the band;
future quality mints (DS-v2+) record their rerank/threading state so their
artifacts are reproducible by construction.
