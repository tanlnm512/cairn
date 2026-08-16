# Test Cases: retrieval-quality

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-15
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Baseline under test (DS-v1 artifact, 58 graded code-target queries):
L1 recall@10 = 0.4174, MRR = 0.2862. Survey statuses: FR-001/005/006 TODO,
FR-002/003/004/007 PARTIAL — PARTIAL items carry regression-guard TCs that
cite the verify commands already proven in [survey.md](survey.md).

Standing "shall never" doctrines guarded by this suite:
no LLM in the query path (TC-004); the graded answer set is never edited by
tuning (TC-025); retrieval is deterministic — same query + same index → same
results (TC-011); a quality win bought by loosening what counts as a match is
a loss, not a win (TC-017).

## FR-001 — Query enrichment

## TC-001 — A sentence-style question returns its target
- **Story**: US1 · **Traces to**: FR-001, AC1, AC2
- **Given** the shipped retrieval defaults over the indexed reference codebase, and the graded query set's natural-language sentence questions (e.g. "where is the function that parses an unencoded URL string")
- **When** such a sentence question is asked of semantic search
- **Then** a ranked, non-empty result list returns — the sentence no longer matches nothing — and the graded primary target for that question appears in the top 10
- **Pass condition**: the shipped-defaults measurement reports a higher recall@10 over the sentence-shaped graded queries than the DS-v1 artifact's baseline; human spot-check: ask the example question and find the URL-parsing function among the top 10 results

## TC-002 — Identifier-style queries are not harmed by enrichment
- **Story**: US1 · **Traces to**: FR-001
- **Given** the shipped defaults and the graded query set's bare-identifier questions (exact-name lookups)
- **When** an identifier-style query runs under the enriched pipeline
- **Then** its hit rate and rank quality are no worse than the DS-v1 baseline for that query shape — enrichment emphasizes code-ish tokens, it must not bury them
- **Pass condition**: the harness report at shipped defaults shows recall@10 and MRR on the identifier-shaped graded subset ≥ the DS-v1 artifact numbers for that subset (or, if the artifact does not break queries out by shape, ≥ the overall baseline on a manually identified identifier subset)

## TC-003 — Enrichment is deterministic
- **Story**: US1 · **Traces to**: FR-001
- **Given** the same natural-language query against the same unchanged index, asked twice in separate processes with shipped defaults
- **When** both runs complete
- **Then** the two result lists (ordering and scores) are identical — enrichment adds no randomness, no time-dependence, no external calls that vary
- **Pass condition**: run the query twice via the eval surface, diff the outputs — the diff is empty

## TC-004 — No LLM and no network in the query path (standing guard)
- **Story**: US1 · **Traces to**: FR-001
- **Given** an environment with no LLM provider configured and outbound network access disabled
- **When** a natural-language query runs through semantic search with shipped defaults
- **Then** it succeeds and returns the same results as in a fully-networked environment — enrichment is purely local and hermetic
- **Pass condition**: repeat a fixed query offline vs online; both succeed and outputs match; no network/credential error appears in either run

## TC-005 — A no-match query still returns empty gracefully (boundary)
- **Story**: US1 · **Traces to**: FR-001
- **Given** the shipped defaults and a query describing something that does not exist in the indexed codebase
- **When** the query runs
- **Then** the result is an ordinary empty result with a clear no-match indication — enrichment must not manufacture plausible-looking matches out of nothing, and must not crash or hang
- **Pass condition**: run a nonsense query (e.g. "the function that teleports a giraffe to mars"); observe an empty result set with the standard empty reason, no error

## FR-002 — Corpus chunk recipe

## TC-006 — The chunk recipe is measured, with size accounted
- **Story**: US1 · **Traces to**: FR-002, AC1
- **Given** the sweep harness with corpus-recipe levers enabled
- **When** recipe variants are ablated against the graded tune split
- **Then** the results table carries a row per recipe with its recall@10 / MRR / latency and its storage cost (index size), and the chosen recipe's composed text stays within the stated size bounds (no silent truncation of the fields that bought the win)
- **Pass condition**: the committed ablation table contains recipe rows with all four columns populated; the shipped recipe row's size figure is within the bound the report states

## TC-007 — A recipe change is an index operation, not a dataset change
- **Story**: US1 · **Traces to**: FR-002
- **Given** the tuned recipe shipped as default and an already-embedded index built under the old recipe
- **When** the corpus is re-embedded through the standard embed pipeline
- **Then** re-embedding completes for the whole corpus and retrieval answers queries under the new recipe afterwards — the change flows through existing index maintenance, not through new authoring tooling
- **Pass condition**: run the standard re-embed over the reference corpus; it completes with a full embedded count and zero skipped-symbol errors; a fixed probe query returns results afterwards

## TC-008 — REGRESSION GUARD — composed context still carries identity fields
- **Story**: US1 · **Traces to**: FR-002 (PARTIAL)
- **Given** the existing chunk-composition machinery that already embeds qualified name, file path, signature, and docstring context
- **When** any retrieval-quality change to composition lands
- **Then** those identity fields are still present in composed chunks, and the existing variant/size controls still behave as before
- **Pass condition**: survey verify command still passes — `grep -n "CAIRN_CHUNK_VARIANT\|max_chars\|Body:" src/cairn/graph/embeddings.py` matches the variant selector, the size truncation, and the body-only-variant lines (survey.md, item FR-002)

## FR-003 — Fusion and thresholds

## TC-009 — Every tuned parameter records its swept range and chosen value
- **Story**: US1 · **Traces to**: FR-003, AC1
- **Given** the sweep harness and its machine-readable results output
- **When** the fusion/threshold levers (fusion weights, dense threshold, sparse threshold, candidate pool sizes) are tuned
- **Then** each parameter appears in the output with the range swept and the value chosen for shipping — no default remains folklore
- **Pass condition**: read the committed sweep output; every named fusion/threshold parameter has a non-empty swept-range and a chosen value; human review, one line per parameter

## TC-010 — REGRESSION GUARD — fusion stays on by default and the dense threshold stays applied
- **Story**: US1 · **Traces to**: FR-003 (PARTIAL)
- **Given** the existing behavior: hybrid fusion enabled by default, a dense similarity threshold applied, candidate pools bounded
- **When** the tuning change lands
- **Then** fusion is still active at shipped defaults (not silently reduced to dense-only), and the dense threshold still filters before fusion
- **Pass condition**: survey verify command still passes — `grep -n "rrf_fuse(\|threshold\|pool_size\|brute_force_limit\|CAIRN_FUSION" src/cairn/graph/semantic.py | head` matches the fusion call site, threshold, and pool lines (survey.md, item FR-003)

## TC-011 — Retrieval determinism (standing guard)
- **Story**: US1 · **Traces to**: FR-003
- **Given** an unchanged index and unchanged shipped defaults
- **When** the same query (sentence-style and identifier-style) is executed multiple times
- **Then** every run returns the same results in the same order with the same scores
- **Pass condition**: execute a fixed query set twice through the eval surface with defaults; diff the full outputs — empty diff

## FR-004 — Reranker pairs and confidence gate

## TC-012 — The rerank stage's marginal value is reported
- **Story**: US1 · **Traces to**: FR-004, AC1
- **Given** the shipped configuration and the ablation harness
- **When** the report is generated
- **Then** it states how much recall@10 and MRR the reranking stage itself contributes at the shipped config (rerank on vs off, all else equal) — the stage's value is a number, not an assumption
- **Pass condition**: the committed results contain a rerank on/off comparison row for the shipped config with both quality metrics populated

## TC-013 — Gate recalibration is evidenced when the pair format changes
- **Story**: US1 · **Traces to**: FR-004
- **Given** the chosen reranker input format differs from the format the confidence gate's calibration was based on
- **When** the pair format ships
- **Then** a calibration measurement accompanies it showing, at the shipped gate setting, how often confident queries skip reranking and how often skipping agrees with full reranking — the gate is re-validated against the new score distribution, not inherited
- **Pass condition**: the shipped-config report (or its notes) contains the gate skip-rate and skip-vs-rerank agreement figures measured under the new pair format; if the format is unchanged from today's, an explicit "unchanged, prior calibration stands" statement satisfies this

## TC-014 — REGRESSION GUARD — confident results still skip reranking
- **Story**: US1 · **Traces to**: FR-004 (PARTIAL)
- **Given** the existing behavior: a clear winner after fusion (strong margin, exact-name corroboration) skips the reranking stage with a stated reason
- **When** the tuned pipeline ships
- **Then** that skip behavior still exists and still reports its reason — tuning must not silently force always-rerank
- **Pass condition**: survey verify command still passes — `grep -n "pairs = \|_fused_confident(query\|rrk.rerank(query\|CrossEncoder(model_name)" src/cairn/graph/reranker.py src/cairn/graph/semantic.py` matches pair construction, the gate check before rerank, and the rerank call (survey.md, item FR-004); human: run an exact-name query and observe the skip reason in the result

## FR-005 — Sweep harness

## TC-015 — A committed, machine-readable ablation table with the shipped row
- **Story**: US1 · **Traces to**: FR-005, AC1
- **Given** the sweep harness (`cairn eval --sweep` or its scripts equivalent)
- **When** the ablation runs against the DS-v1 graded set on the tune split
- **Then** it emits a results table mapping each lever combination to recall@10, MRR, and p95 latency, the table is machine-readable (parses without human massage), it is committed to the repository, and it includes the shipped-defaults configuration as one row
- **Pass condition**: locate the committed table; parse it programmatically; every row has non-null values for all three columns; exactly one row is marked as the shipped defaults

## TC-016 — The reference quality table is regenerated from the shipped config
- **Story**: US1 · **Traces to**: FR-005, FR-007, AC2
- **Given** the winning configuration shipped as defaults
- **When** the reference quality table is regenerated and committed
- **Then** the quality table in `docs/benchmarks.md` shows the new full-set measurement, and the DS-v1 baseline artifact still carries the old numbers (0.4174 / 0.2862) untouched for attribution
- **Pass condition**: the regenerated table's L1 numbers differ from (improve on) the artifact's; the DS-v1 artifact file still contains recall_at_10 0.4174 and mrr 0.2862

## TC-017 — The all-levers-off row reproduces the published baseline (standing guard)
- **Story**: US2 · **Traces to**: FR-005, AC2
- **Given** the ablation table's baseline-equivalent row (every new lever disabled) measured over the full DS-v1 set
- **When** compared to the committed DS-v1 baseline artifact
- **Then** it reproduces the artifact's L1 recall@10 = 0.4174 and MRR = 0.2862 (within rounding) — proving the tuned numbers were bought by better retrieval, not by looser matching rules, name-collision inflation, or a drifted judge
- **Pass condition**: the all-off row's two quality numbers equal the artifact's to 4 decimal places

## FR-006 — Held-out discipline

## TC-018 — The seeded split is reproducible, disjoint, and complete
- **Story**: US2 · **Traces to**: FR-006, AC3
- **Given** the ground-truth split with its fixed seed (tune half / validate half)
- **When** the split is generated twice
- **Then** both generations produce identical membership; the two halves share no query; together they cover every graded query exactly once
- **Pass condition**: generate the split twice and diff — empty; verify no query id appears in both halves and every query id appears in one

## TC-019 — Lever selection that reads the validation split fails loudly
- **Story**: US2 · **Traces to**: FR-006
- **Given** the harness in lever-selection mode
- **When** a selection run is made to consult the validation split (misconfigured or tampered attempt)
- **Then** the harness aborts with a non-zero exit and an error naming the violation — it never silently scores selection decisions on held-out data
- **Pass condition**: provoke the misuse against the shipped harness; observe a failing exit code and an explicit held-out-access error; no results table is emitted for that run

## TC-020 — Final numbers are reported for both splits
- **Story**: US2 · **Traces to**: FR-006, AC3
- **Given** the shipped configuration's final measurement
- **When** results are reported
- **Then** the tune-split number, the validation-split number, and the full-set number all appear, with the split disclosed — a reader can see the generalization gap
- **Pass condition**: the final report contains three labeled figures (tune, validate, full set) for recall@10 and MRR

## FR-007 — No-give-back shipping

## TC-021 — Latency stays within the committed regression bounds
- **Story**: US3 · **Traces to**: FR-007, AC4
- **Given** the shipped configuration and the committed performance baseline
- **When** the performance suite re-runs and is compared against the baseline
- **Then** no measured operation (impact, semantic search, explore, lexical search, definition lookup) regresses beyond the committed compare threshold; the comparison passes
- **Pass condition**: the baseline-comparison command exits 0 (no regression found); a just-over-threshold regression, if injected in a dry run, is flagged and fails the comparison

## TC-022 — Agent-effort totals stay within bounds
- **Story**: US3 · **Traces to**: FR-007, AC4
- **Given** the shipped configuration and the committed agent-effort baseline
- **When** the agent-effort suite re-runs
- **Then** the serving-side tool-call count and estimated token total stay within the committed compare bounds relative to baseline
- **Pass condition**: the agent-effort comparison reports no metric beyond threshold; exit 0

## TC-023 — First-query warm time stays within bounds
- **Story**: US3 · **Traces to**: FR-007, AC4
- **Given** a fresh process start with shipped defaults and model warm-up active
- **When** the first semantic query after startup is timed
- **Then** its latency stays within the documented bound against the committed warm-time figure — the quality work did not reintroduce cold-start cost
- **Pass condition**: the re-measured first-query time is recorded and compared to the committed bound with a pass/fail statement. NOTE: no warm-time measurement harness or artifact exists today (survey Unknowns) — this TC is executable only once FR-007's re-measurement method ships; flagged below as a spec smell, not dropped

## TC-024 — The quality gain is real and stated
- **Story**: US1 · **Traces to**: FR-007, AC2
- **Given** the regenerated reference table and the untouched DS-v1 baseline artifact
- **When** the two are compared
- **Then** L1 recall@10 and MRR both strictly improve over 0.4174 / 0.2862, and the improvement margin for each metric is explicitly stated in the shipped report
- **Pass condition**: both new numbers exceed the artifact's; a stated margin accompanies each; the ablation table attributes the gain to named levers

## TC-025 — The graded answer set is never edited by tuning (standing guard)
- **Story**: US2 · **Traces to**: FR-002, FR-005, FR-006, AC3
- **Given** the DS-v1 graded answer set (queries, expected targets, judging rules) snapshotted before the tuning cycle begins
- **When** the entire cycle completes — sweep, selection, shipping, table regeneration
- **Then** the graded answer set is byte-identical to the snapshot; any measurement that required touching it would have failed the harness instead
- **Pass condition**: content-compare the ground-truth files before and after the cycle — identical; spot-check that the harness nowhere offers a write path to it

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003, TC-004, TC-005 | TC-001/002/003 auto (harness/diff), TC-004/005 manual (offline env, observation) |
| FR-002      | TC-006, TC-007, TC-008 | TC-006/007 auto (table/re-embed run), TC-008 auto (verify command) |
| FR-003      | TC-009, TC-010, TC-011 | TC-009 manual (review), TC-010 auto (verify command), TC-011 auto (diff) |
| FR-004      | TC-012, TC-013, TC-014 | TC-012 auto (table), TC-013 manual (report review), TC-014 auto (verify command) + manual observation |
| FR-005      | TC-015, TC-016, TC-017 | auto (parse table, artifact compare, row compare) |
| FR-006      | TC-018, TC-019, TC-020 | TC-018/019 auto (diff, exit code), TC-020 manual (report review) |
| FR-007      | TC-021, TC-022, TC-023, TC-024 | TC-021/022 auto (exit codes), TC-023 auto once harness ships, TC-024 auto (number compare) |

### Acceptance-criteria trace
| AC | Test cases |
|----|------------|
| AC1 — committed ablation table | TC-001, TC-006, TC-009, TC-012, TC-015 |
| AC2 — regenerated quality tables, old numbers preserved | TC-016, TC-017, TC-024 |
| AC3 — both-splits reporting | TC-018, TC-019, TC-020, TC-025 |
| AC4 — protected baselines within bounds | TC-021, TC-022, TC-023 |

### User-story boundary coverage
- US1 boundaries: sentence pole (TC-001) vs identifier pole (TC-002) vs no-match pole (TC-005); size bound on composed chunks (TC-006).
- US2 boundaries: split disjointness/completeness/reproducibility (TC-018), misuse of held-out data (TC-019), dataset immutability (TC-025).
- US3 boundaries: threshold-edge regression detection (TC-021 dry-run provocation), cold-start edge (TC-023).

## Spec smells (reported, not papered over)
1. **"Stated margin" is never stated.** SC-1 and TC-024's stronger form depend on a target margin that spec.md nowhere numerically pins. Minimal black-box pass used here: strict improvement on both metrics with the margin disclosed in the report. The planner should pin targets (e.g. recall@10 ≥ 0.50) or the criterion stays subjective.
2. **FR-007's first-query warm time has no measurement method today.** The 322 ms figure lives only in a phase document; no artifact, no harness (survey, Unknowns). TC-023 is written and executable only once that harness lands as part of FR-007 — the FR implicitly includes building it.
3. **AC1 does not pin the committed table's location or schema** beyond "machine-readable". TC-015 uses "wherever the harness documents, committed to the repo"; plan.md should pin the path and column contract so CI can check it.
