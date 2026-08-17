# Test Cases: retrieval-quality-v2

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-15
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Campaign context (from the spec, not the code): the first campaign's 58-query
ground truth could not certify its best candidate (Δ+0.1123 at p=0.118); SC-1
targets (L1 recall@10 ≥ 0.50, MRR ≥ 0.33) stand unreached; rerank costs
1142.0 ms p95 at the shipped config vs 28.9 ms rerank-off. Survey statuses:
0 DONE / 3 PARTIAL (FR-002, FR-003, FR-006) / 3 TODO — PARTIAL items carry
REGRESSION GUARD TCs citing the verify commands already proven in
[survey.md](survey.md).

Standing "shall never" doctrines guarded by this suite: no LLM and nothing
non-local in the query path (TC-014 FR-003, TC-017 FR-004); DS-v1
artifacts — including the ablation record's pinned dataset — stay immutable, v2
measurement families are new and never overwrite them (TC-007 FR-002,
TC-028 FR-006); retrieval is deterministic —
same query + same index → same results, for every new lever (TC-002, TC-010,
TC-014, TC-016, TC-017, TC-020, TC-021); match rules are never loosened
(TC-027); SC-1 targets stay 0.50/0.33 — no goalpost moves dressed as quality
wins (TC-026); nothing ships on point estimates — significance only via the
bootstrap guard (TC-024); k-fold per-fold held-out discipline fails loudly
(TC-003).

## FR-001 — k-fold cross-validation

## TC-001 — k-fold selection reports every fold and the aggregate verdict
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** the evaluation harness over the existing graded query set
- **When** lever selection runs in k-fold mode
- **Then** the run covers at least 5 folds, and the reported verdict states the aggregate result plus the per-fold spread (a reader sees both the central tendency and how much folds disagree)
- **Pass condition**: the selection output lists ≥5 folds each with its own quality figures, an aggregate verdict, and a spread measure (e.g. min/max or per-fold figures from which spread is readable); no fold's figure is silently missing

## TC-002 — Folds are seeded, reproducible, and rotate held-out duty exactly once
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** the same graded query set and the same fold seed
- **When** fold assignment is generated twice
- **Then** both generations produce identical fold membership; every query appears as held-out in exactly one fold and as tuning material in all others; the held-out folds together cover the whole set with no overlap
- **Pass condition**: regenerate the fold assignment and diff — empty; programmatically check that the union of held-out folds equals the full query-id set and no id appears in two held-out folds

## TC-003 — REGRESSION GUARD (doctrine extension) — selection reading any fold's held-out ids fails loudly
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** the harness in k-fold selection mode, partway through its folds
- **When** a selection-stage read is made to touch the validate ids of ANY fold — the fold currently being held out or one already consumed
- **Then** the run aborts with a non-zero exit and an error naming the held-out violation — the existing loud-failure contract holds per fold, not just per single split
- **Pass condition**: provoke the misuse (misconfiguration or tampered request) against the shipped harness; observe failing exit, an explicit held-out-access error, and no results table emitted for that run

## TC-004 — Boundary — the fold-count floor is enforced
- **Story**: US1 · **Traces to**: FR-001
- **Given** the harness's fold-count setting (configurable, per the spec's machine-time mitigation)
- **When** a selection run requests fewer than 5 folds
- **Then** the harness refuses the request with a clear error rather than silently running a weaker evaluation — "≥5 folds" is a floor, not a suggestion
- **Pass condition**: request fold counts 2 and 4; both are rejected with an error naming the minimum; the default configuration runs at ≥5 folds (observable in TC-001's output)

## FR-002 — DS-v2 ground truth

## TC-005 — DS-v2 meets its count and kind floors
- **Story**: US1 · **Traces to**: FR-002, AC2
- **Given** the authored DS-v2 dataset
- **When** its queries are counted by level and by kind
- **Then** it contains at least 150 L1 queries with all four query kinds represented, and at least 40 L5 queries — roughly 3–5× the first campaign's evidence base
- **Pass condition**: count the dataset through its loader (which validates the record shape); L1 ≥ 150 with each of the four kinds > 0, L5 ≥ 40; the counts are re-derived, not taken from a README claim

## TC-006 — Zero aspirational entries — every expectation verified against a fresh build
- **Story**: US1 · **Traces to**: FR-002, AC2
- **Given** DS-v2's expectations as authored
- **When** each expectation is checked against a freshly built index of the corpus
- **Then** every expected target resolves to a real symbol at its stated grade — nothing in the dataset is wishful; the dataset carries proof of this verification, not just the claim
- **Pass condition**: a verification pass over a fresh graph build marks every expectation confirmed (zero unverified/aspirational entries in the dataset's verification record); human spot-check of a sample (≥10 expectations across levels and kinds) against the fresh build agrees with the recorded grades

## TC-007 — DS-v2 is a new immutable dataset; DS-v1 stays untouched (standing guard)
- **Story**: US1 · **Traces to**: FR-002, AC2
- **Given** the DS-v1 dataset snapshotted before DS-v2 authoring begins
- **When** DS-v2 lands as a versioned dataset
- **Then** DS-v2 lives under its own new version label/directory, and every DS-v1 artifact (queries, expectations, baseline measurements) is byte-identical to the snapshot afterwards
- **Pass condition**: content-compare all DS-v1 dataset and baseline files before/after — identical; DS-v2's version label is distinct and declared; no DS-v2 content is written into any DS-v1 location

## TC-008 — The second-corpus decision is made and recorded, not skipped
- **Story**: US1 · **Traces to**: FR-002
- **Given** the candidate second corpus (or candidates) for cross-corpus ground truth
- **When** the evaluation against the datasource constraints (size, license) completes
- **Then** the outcome is explicit: either the corpus is vendored within the documented size budget and a compatible license, or the deferral is recorded with the specific size and/or license findings that caused it
- **Pass condition**: a committed decision artifact states included-or-deferred with reasons; if included — the datasource budget check passes with the new corpus present and the license is stated compatible; if deferred — the reasons name concrete constraint findings, not a vague "later"

## TC-009 — REGRESSION GUARD — the datasource budget check still passes
- **Story**: US1 · **Traces to**: FR-002 (PARTIAL)
- **Given** the existing datasource size-budget verification, proven passing at 469.6/3072 KB (per-corpus) and 471.5/5120 KB (total) in the survey
- **When** any DS-v2 or second-corpus change lands
- **Then** the budget check still passes — DS-v2's 3–5× growth and any vendored corpus stay within the committed budgets, and the checker itself is not weakened to make that true
- **Pass condition**: survey verify command still exits clean — `verify_datasource.py --budget` (survey.md, item FR-002) — with every corpus and the total within budget; if a new sibling corpus directory is added, it is covered by a budget rule, not exempt by omission

## FR-003 — IDF-aware enrichment

## TC-010 — A corpus-ubiquitous term stops diluting both query legs, deterministically
- **Story**: US2 · **Traces to**: FR-003, AC3
- **Given** a term that appears in more than the documented fraction of the corpus's symbols (the known case: "URL" in the query about parsing an already-encoded URL string)
- **When** enrichment builds the sparse and dense query texts
- **Then** that term is down-weighted or dropped from BOTH legs — not merely moved from one to the other — and the outcome is identical on every repeat
- **Pass condition**: run the known regression query's text through enrichment twice; both runs produce identical output; the ubiquitous token no longer appears at full weight in either leg (observe the ranked results: the URL-parsing target is no longer buried by symbol after symbol that merely mentions the token)

## TC-011 — Boundary — behavior matches the documented threshold on both sides
- **Story**: US2 · **Traces to**: FR-003, AC3
- **Given** the documented prevalence threshold, and a controlled miniature corpus where a probe term's prevalence is set just below and just above it
- **When** enrichment runs with each prevalence
- **Then** below the threshold the term keeps full weight; above it the term is down-weighted or dropped — the behavior switches exactly where the documentation says, with no off-by-one or fuzzy zone
- **Pass condition**: the threshold value appears in the shipped documentation; the two controlled-corpus runs show full weight below and reduced/absent above, at the documented cut point

## TC-012 — Discriminative terms keep full weight
- **Story**: US2 · **Traces to**: FR-003, AC3
- **Given** a rare, discriminative term (e.g. a distinctive identifier appearing in a handful of symbols)
- **When** enrichment builds the query
- **Then** the term survives at full weight in both legs — the repair suppresses ubiquity, not specificity; enrichment still emphasizes code-ish tokens
- **Pass condition**: probe with a query carrying a rare identifier; the identifier remains present at full weight in the enriched query (and the identifier's target still ranks at the top, as before the change)

## TC-013 — Non-regression — no previously-passing query falls to zero
- **Story**: US2 · **Traces to**: FR-003, AC4
- **Given** the first campaign's graded query set and its recorded per-query outcomes, including the enrichment regression query (L1-D03, which fell 1.0 → 0.0 on exactly this failure mode)
- **When** the repaired enrichment runs over the tuning split
- **Then** every query that previously passed still passes (its graded target still appears in the top 10) — none regresses to zero; L1-D03 in particular recovers its target
- **Pass condition**: the tune-split measurement under the repaired enrichment shows zero previously-passing queries at zero; L1-D03's graded target appears in its top 10 (at rank 1, its historical passing state, for the known case)

## TC-014 — The weighting signal is hermetic, deterministic, and within its cost bound
- **Story**: US2 · **Traces to**: FR-003
- **Given** an environment with outbound network access disabled and no LLM provider configured, and the documented per-query cost bound for computing the term-weighting signal
- **When** enriched queries run, offline and online, repeatedly
- **Then** results are identical in both environments and across repeats — the signal comes from the local index alone, with no environment, network, or time dependence — and the measured per-query overhead of the weighting stays within the documented bound
- **Pass condition**: run a fixed query set offline vs online and diff — empty; run twice and diff — empty; the documented bound is stated with the measured figure beside it, and the measurement does not exceed it

## TC-015 — REGRESSION GUARD — the enrichment stage stays a pure, reproducible transform
- **Story**: US2 · **Traces to**: FR-003 (PARTIAL)
- **Given** the existing enrichment pipeline, verified in the survey as a hermetic one-command repro on the L1-D03 text (deterministic, no environment reads)
- **When** the IDF-aware repair lands
- **Then** enrichment remains a local pure function of its inputs — the prevalence signal is supplied to it, not fetched by it from the environment — and the survey's repro remains a deterministic one-command check whose output changes only in the intended direction (ubiquitous token suppressed)
- **Pass condition**: survey verify command still behaves deterministically — the one-command L1-D03 enrichment repro (survey.md, item FR-003), run twice, gives identical output; no environment variable or network access influences it

## FR-004 — Pseudo-relevance feedback (PRF)

## TC-016 — PRF ships flag-off; defaults are unchanged
- **Story**: US3 · **Traces to**: FR-004, AC5
- **Given** the shipped retrieval defaults, snapshotted before PRF lands
- **When** queries run with shipped defaults (PRF flag off)
- **Then** results are identical to the pre-PRF snapshot — a wired-but-off lever costs nothing and changes nothing for existing users
- **Pass condition**: run a fixed probe query set before and after the change under defaults; diff the full outputs — empty; the shipped-configuration row in the ablation record is unchanged by PRF's mere presence

## TC-017 — PRF is deterministic and LLM-free (standing guard)
- **Story**: US3 · **Traces to**: FR-004, AC5
- **Given** PRF enabled, an environment with no LLM provider and outbound network disabled
- **When** the same query runs twice, offline
- **Then** both runs return identical results in identical order — PRF's query expansion is a fixed computation over the first-pass results, with no LLM, no network, no randomness beyond a pinned seed
- **Pass condition**: offline, run the query twice and diff — empty; the offline results equal the online results; no provider/network error or credential lookup appears

## TC-018 — PRF's ablation row exists on both splits with its latency priced against the rerank budget
- **Story**: US3 · **Traces to**: FR-004, AC5
- **Given** the ablation harness and PRF enabled as a lever
- **When** the ablation table is generated
- **Then** PRF has a row with recall@10, MRR, and p95 latency on the tuning split AND the validation split, and the report states the comparison against the rerank budget it may replace (1142.0 ms p95 at the shipped config vs 28.9 ms rerank-off in the committed record)
- **Pass condition**: the committed ablation table contains a PRF row per split with all columns populated (no nulls); the p95-vs-rerank-budget comparison figure is stated next to the row or in its notes

## TC-019 — Boundary — PRF replaces rerank's budget, it does not stack on it
- **Story**: US3 · **Traces to**: FR-004, AC5
- **Given** a rerank-off configuration (the freed-budget scenario PRF targets)
- **When** PRF is enabled in that configuration
- **Then** the added query latency stays within the budget freed by removing reranking — the p95 of PRF-on + rerank-off does not exceed the p95 the rerank stage was costing — PRF is never billed on top of rerank
- **Pass condition**: compare measured p95: PRF-on + rerank-off ≤ the rerank-on configuration's p95 (or the documented freed-budget bound); a configuration with both PRF and rerank at full strength, if offered, is labeled with its combined p95 rather than hidden

## FR-005 — Multi-vector retrieval

## TC-020 — Multi-vector ships flag-off; behavior AND storage are unchanged
- **Story**: US3 · **Traces to**: FR-005, AC6
- **Given** the shipped defaults and a default index build, snapshotted before multi-vector lands
- **When** the corpus is indexed and queried with the multi-vector flag off
- **Then** query results are identical to the snapshot and the index storage size matches the single-vector build — the up-to-3× storage risk is paid only by opted-in users
- **Pass condition**: build a fresh index with defaults and compare its size to the pre-change single-vector build — equal within noise; run the fixed probe set and diff against the snapshot — empty

## TC-021 — Max-over-vectors surfaces a symbol via its best-matching representation
- **Story**: US3 · **Traces to**: FR-005, AC6
- **Given** multi-vector enabled (name-only, docstring-only, and chunk vectors per symbol)
- **When** a telegraphic name-style query and a prose sentence-style query — the two poles of the sentence-vs-telegraphic mismatch — each run
- **Then** a symbol whose NAME alone matches the telegraphic query is found, and a symbol whose DOCSTRING alone matches the prose query is found: scoring takes the best match across the symbol's vectors, and repeats return identical results
- **Pass condition**: probe with a bare-function-name query and a describes-the-behavior prose query over known targets; each target appears in its top 10 (the pole that previously missed now hits); run each probe twice and diff — empty

## TC-022 — Multi-vector's ablation row carries quality AND cost on both splits
- **Story**: US3 · **Traces to**: FR-005, AC6
- **Given** the ablation harness and multi-vector enabled as a lever
- **When** the ablation table is generated
- **Then** multi-vector has a row with recall@10, MRR, index size (db), and p95 latency on the tuning split AND the validation split — the storage multiplication is a stated number, not a surprise
- **Pass condition**: the committed ablation table contains a multi-vector row per split with all five figures populated; the row (or its notes) states the storage growth factor versus the single-vector build

## TC-023 — Boundary — one symbol, one entry in a result list
- **Story**: US3 · **Traces to**: FR-005, AC6
- **Given** multi-vector enabled, so each symbol has several scored representations
- **When** any query returns its ranked results
- **Then** no symbol appears more than once in a single result list — multiple vectors consolidate to the symbol's best score, never to duplicate rows that crowd out other candidates
- **Pass condition**: scan the result lists of a fixed query set; zero lists contain a repeated symbol; list lengths remain within the requested top-k

## FR-006 — Confirmation ladder and the honest record

## TC-024 — Ship path — a guard-cleared combination ships with every protected baseline re-measured
- **Story**: US4 · **Traces to**: FR-006, AC7
- **Given** the confirmation ladder run on the upgraded evidence base, and a candidate combination whose improvement clears the bootstrap significance guard
- **When** the campaign concludes
- **Then** that combination ships as the retrieval defaults, the clearance evidence (confidence interval and p-value, not a point estimate) is recorded, and all three protected baselines — performance, agent effort, warm time — are re-measured and stand within their committed bounds
- **Pass condition**: the shipped defaults equal the cleared combination; the record shows the guard's verdict for it; the three baseline comparisons each pass (exit clean) against their committed thresholds. NOTE: exactly one of TC-024/TC-025 is exercised per campaign outcome — both are written; an outcome where nothing ships must not be reported through this TC

## TC-025 — Document path — nothing clears, and the record says why and what next
- **Story**: US4 · **Traces to**: FR-006, AC7
- **Given** the ladder outcome in which no combination clears the bootstrap guard on the upgraded evidence base
- **When** the campaign concludes
- **Then** the extended ablation record states the shortfall (how close the best candidate came, with its interval and p-value) and names the next binding constraint — a reader knows what to attack next, not just that it failed
- **Pass condition**: the record contains an explicit no-ship outcome with the best candidate's figures, the guard's verdict for it, and a named binding constraint (e.g. evidence power, lever strength, latency); defaults are unchanged from their pre-campaign state

## TC-026 — Standing guard — SC-1 targets stay 0.50 / 0.33, untouchable by this campaign
- **Story**: US4 · **Traces to**: FR-006, AC7
- **Given** the campaign's verdict reporting, whatever the outcome
- **When** targets and actuals are stated
- **Then** the SC-1 targets read recall@10 ≥ 0.50 and MRR ≥ 0.33 — the same bar as the first campaign; no re-scoped target, no metric swap, no "new improved" threshold appears anywhere in the campaign's artifacts
- **Pass condition**: read the verdict blocks of the extended record and any shipped summary; every stated target pair is 0.50/0.33; a grep-scale review finds no alternative target figures presented as the bar

## TC-027 — Standing guard — match rules are never loosened; the baseline still reproduces
- **Story**: US4 · **Traces to**: FR-006, AC7
- **Given** the every-new-lever-off measurement over the legacy dataset, and the committed first-campaign baseline artifact (L1 recall@10 0.4174, MRR 0.2862)
- **When** the two are compared after the campaign's changes land
- **Then** the all-off measurement reproduces the baseline within rounding — proving the campaign's numbers were bought by better retrieval, not by looser matching, grade inflation, or a drifted judge; probe queries grade identically before and after
- **Pass condition**: the all-levers-off row's two quality figures equal the committed artifact's to 4 decimal places; a fixed probe set graded before the campaign grades identically after it

## TC-028 — REGRESSION GUARD — the existing ablation record and its guard tests stay intact; v2 rows are a new family
- **Story**: US4 · **Traces to**: FR-006 (PARTIAL), AC7
- **Given** the first campaign's committed ablation record, pinned by its guard suite to the legacy dataset (58 queries, 29/29 split, verified 6-passing in the survey)
- **When** the record is extended with k-fold and DS-v2 measurements
- **Then** the existing document's pinned content is untouched — v2 measurements live in a NEW measurement family/document, never diffed against legacy rows directly and never overwriting them — and the guard suite still passes unmodified
- **Pass condition**: survey verify command still passes — `pytest tests/test_ablation_artifact.py` → 6 passed (survey.md, item FR-006); the legacy record file is byte-identical to its pre-campaign snapshot; v2 rows carry their own dataset/family labels, and no v2 row is presented as a delta against a legacy row

## TC-029 — The verdict cites the upgraded evidence base, with its size disclosed
- **Story**: US4 · **Traces to**: FR-006, AC7
- **Given** the ladder's verdict on the upgraded evidence
- **When** the verdict is read
- **Then** it names its evidence: the k-fold aggregate (fold count, per-fold spread) over the legacy set and the DS-v2 measurement (query counts) — not the legacy single split — so a reader can see the evidence power actually bought
- **Pass condition**: the verdict block states the evidence base with fold count ≥5 and DS-v2 counts ≥150 L1 / ≥40 L5; no verdict in the campaign cites the legacy single-split numbers as its basis

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003, TC-004 | TC-001/002 auto (output parse, diff), TC-003 auto (exit code), TC-004 auto (refusal exit) |
| FR-002      | TC-005, TC-006, TC-007, TC-008, TC-009 | TC-005/007/009 auto (counts, byte compare, verify cmd), TC-006 auto + manual spot-check, TC-008 manual (decision review) + auto (budget) if vendored |
| FR-003      | TC-010, TC-011, TC-012, TC-013, TC-014, TC-015 | TC-010/011/012/013/014 auto (repro/diff/measurement), TC-015 auto (survey repro determinism) |
| FR-004      | TC-016, TC-017, TC-018, TC-019 | TC-016/018/019 auto (diff, row parse, number compare), TC-017 auto + manual (offline env) |
| FR-005      | TC-020, TC-021, TC-022, TC-023 | TC-020/022/023 auto (diff+size, row parse, list scan), TC-021 auto (probes, diff) + manual spot-check |
| FR-006      | TC-024, TC-025, TC-026, TC-027, TC-028, TC-029 | TC-024/027/028 auto (baselines exit 0, 4dp compare, guard tests), TC-025/026/029 auto checks + manual (record review) |

### Acceptance-criteria trace
| AC | Test cases |
|----|------------|
| AC1 — fold discipline + aggregate with spread | TC-001, TC-002, TC-003, TC-004 |
| AC2 — DS-v2 counts + empirical verification | TC-005, TC-006, TC-007, TC-008, TC-009 |
| AC3 — DF threshold behavior | TC-010, TC-011, TC-012, TC-014, TC-015 |
| AC4 — L1-D03-style non-regression | TC-013 |
| AC5 — PRF budget + rows | TC-016, TC-017, TC-018, TC-019 |
| AC6 — multi-vector rows with costs | TC-020, TC-021, TC-022, TC-023 |
| AC7 — ship-or-document | TC-024, TC-025, TC-026, TC-027, TC-028, TC-029 |

### User-story boundary coverage
- US1 boundaries: fold-count floor (TC-004); rotation exactness — each query held out exactly once (TC-002); dataset size/kind floors at 3–5× scale (TC-005); budget headroom at scale (TC-009); fresh-build pole vs aspirational pole (TC-006).
- US2 boundaries: threshold edge just-below/just-above (TC-011); ubiquitous pole vs rare-discriminative pole (TC-010 / TC-012); the known zero-regression query (TC-013); offline/hermetic pole (TC-014).
- US3 boundaries: flag-off pole for both levers (TC-016, TC-020); PRF within the freed budget, never stacked (TC-019); name-only vs docstring-only query poles (TC-021); duplicate-candidate edge under multi-row scoring (TC-023).
- US4 boundaries: ship branch vs document branch — both specified, exactly one exercised (TC-024 / TC-025); target-unchanged edge (TC-026); matcher-unchanged edge (TC-027).

## Spec smells (reported, not papered over)
1. **AC3's threshold value is nowhere pinned.** Spec says ">X% of corpus symbols (documented threshold)" — the number is deferred to documentation the spec does not name. Black-box pass used here: threshold documented + boundary behavior matches it (TC-011). The planner should pin the default and where it is documented.
2. **AC5's budget metric is ambiguous and partly unsourced.** The spec's Why paragraph cites "~780ms p50" for rerank, but the survey flags that figure as absent from committed artifacts — on-disk evidence is p95-based (1142.0 vs 28.9). TC-018/TC-019 use the committed p95 figures; the planner should pin which metric (p50 vs p95) constitutes "the rerank budget" so the pass condition is exact.
3. **FR-001's "rotation-aggregated" method is unspecified.** Whether the aggregate pools held-out predictions across folds or averages per-fold figures changes what the bootstrap guard tests. TC-001 accepts either with spread disclosed; the planner must pin the aggregation before the guard is meaningful.
4. **AC2's verification provenance has no named artifact.** "Empirically verified" needs a recorded verification pass a tester can read (TC-006 assumes one exists alongside the dataset). The planner should pin where the verification record lives and its shape.
5. **FR-002's second-corpus constraint is a cross-spec dependency.** "Size/license per the datasource spec's constraints" resolves by reference; TC-008/TC-009 use that spec's budget checker. The plan should cite the exact constraint section so a deferred-vs-included verdict is auditable against it.
