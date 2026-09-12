# Spec: indexing-exact-rate

**Status**: draft
**Created**: 2026-09-13
**Re-baselined**: 2026-09-13 against `ac09d4b` (post `remove-scip-exact-rate`)
**Branch**: `feat/indexing-exact-rate`

## What
Raise the code graph's exact-resolution share and keep the semantic embedding index complete and multivector-by-default, so precise queries (`get_callers`, `impact_analysis`, `explore`) and `semantic_search` return trustworthy results over the repo's real source corpus.

## Why
Two measurements frame the problem (2026-09-13, survey baseline): the workspace's live DB — built by the stale installed 0.20.0 binary — splits `calls`+`references` edges 39,140 exact / 26,588 ambiguous (59.6% of the candidate pool); a tree-built graph at `ac09d4b` measures **71.16%** exact (43,546 of 61,195), rising to **75.81%** (15,000 of 19,786) with the exclusion globs applied. Dragged down by:
- 13,232 noise symbols across 179 files (10,409 from vendored dashboard static chunks, 2,823 from third-party benchmark datasource corpora) — no `cairn.json` exclude exists; these add name-collision candidates that convert would-be-exact edges to ambiguous and dilute semantic search. The chunks escape the scanner's minified-file skip because `_is_minified` matches filename markers while `.min` sits in a directory component.
- The incremental embedding seam (`embeddings.embed_symbols`, documented as "per-upsert ANN sync") has zero callers — symbols added after the last manual `cairn embed` stay unembedded (10,880 of 20,296 embedded at measurement; the gap is dominated by the noise symbols above).
- `embeddings_mv` (multivector name/docstring vectors) is never built; the flag is opt-in.
- `cairn stats` reports edge totals but not resolution shares, so exact-rate regressions are invisible.

Already landed upstream in the tree (out of this spec's scope, context only): Python receiver-type inference beyond `self`/`cls` — `ScopeTypeTracker` with annotated-parameter and constructor-assignment binding, call-arity matching, abstention on unknown/shadowed receivers (`0a956fa`); the SCIP subsystem is removed entirely and tree-sitter is the only indexing path (`remove-scip-exact-rate`, done 2026-09-11). The installed CLI (`~/.local/bin/cairn`, 0.20.0) predates both and still exposes `import-scip`; the measuring binary for the live-DB numbers above is therefore the stale one, and all baselines in this spec come from the surveyor's tree-built graphs.

## Business value
Agents and humans querying cairn get precise blast radii and non-empty semantic results without remembering to re-embed. Success is measurable: resolution shares (FR-005) and embedding coverage against the targets in FR-006.

## User stories
### US1 — Quiet corpus (P1)
As an agent querying the graph, I want vendored/generated assets excluded from the index, so that semantic and symbol results reflect the repo's own code.

**Acceptance criteria**:
- AC1: Given a fresh `cairn build` on this repo, When it completes, Then zero symbols exist under the excluded paths (dashboard static chunks, benchmark datasource corpora).
- AC2: Given the exclusions, When resolution runs, Then the ambiguous share of the candidate pool decreases (measured via FR-005's surface, before/after on the same tree).

### US2 — Coverage that stays complete (P1)
As an agent relying on `semantic_search`, I want newly indexed symbols embedded without a manual `cairn embed`, so that results never silently shrink.

**Acceptance criteria**:
- AC3: Given symbols added by `cairn update`, When the update completes (and the semantic backend is available), Then those symbols have embeddings under the current model.
- AC4: Given the semantic backend is unavailable, When `cairn update` runs, Then the update succeeds and the pending embeds are observable (deferred, not dropped silently).

### US3 — Multivector by default (P2)
As an agent searching by symbol name or docstring meaning, I want name-only and docstring-only vectors built by default, so that both query shapes retrieve well without extra flags.

**Acceptance criteria**:
- AC5: Given `cairn embed` runs with no flags on a built graph, Then `embeddings_mv` rows exist for the symbol corpus (name and docstring kinds), with an opt-out flag documented.

### US4 — Resolution observability (P2)
As a maintainer, I want `cairn stats` to report exact/ambiguous/unresolved shares, so that regressions in resolution quality are visible.

**Acceptance criteria**:
- AC6: Given any built graph, When `cairn stats` runs, Then it prints the three resolution shares alongside edge totals.

## Requirements
- **FR-001**: The repo's committed `cairn.json` shall exclude vendored dashboard static assets and third-party benchmark datasource corpora from indexing.
- **FR-002**: WHEN `cairn update` creates or changes symbols and a semantic backend is available, the system shall embed those symbols under the current model before the update reports completion.
- **FR-003**: IF no semantic backend is available, `cairn update` shall succeed and surface the deferred embeds (log/doctor-observable), never fail the update.
- **FR-004**: `cairn embed` shall build the multivector kinds (`embeddings_mv`: name, docstring) by default; `--no-multivector` shall restore single-vector builds (clarify ruling: default flipped on). Tests asserting the off-by-default byte-identical build are superseded by this FR and follow C-02's failing-test-first treatment; the survey enumerates 8 build-side pins (test_embeddings_mv.py, test_ann_vecmv.py, test_multivector_query.py) — query-side pins stay.
- **FR-005**: `cairn stats` shall report edge resolution shares (`exact` / `ambiguous` / `unresolved`).
- **FR-006**: Acceptance targets — after implementation, of `calls`/`references` edges with at least one same-name indexed candidate (the exact+ambiguous pool, per `remove-scip-exact-rate` FR-005's measurement definition), at least 75% shall be `exact` and at most 25% `ambiguous`; embedding coverage shall be 100% of indexed symbols after an embed pass. Target provenance: user's clarify ruling was 95%; the survey measured the machinery ceiling at 75.81% exact with exclusions applied (exclusion is the only in-scope resolution lever), so D-001 in tech-spec.md re-derived the target to ≥75% pre-approval rather than discovering the shortfall at the closing audit — the approve gate confirms or overrides this.

## Scope
**In**: cairn.json exclusion config; incremental embedding wiring (update path); multivector default flip + `--no-multivector` opt-out; `cairn stats` resolution shares; acceptance-target measurement.
**Out (deferred)**: any SCIP re-introduction — the subsystem was deliberately removed (`remove-scip-exact-rate`); re-adding contradicts that landed direction and needs its own overriding spec; receiver-type inference depth increases (landed inference already exceeds this spec's original `self`/`cls` ruling; further depth rides the landed abstention guard); cross-repo dependency indexing (site-packages sources as extra repos); reranker and chunk-variant changes (eval-gated follow-up); non-Python parser signal work.

## Assumptions & risks
- Assumption: FR-006's pool definition (calls/references edges with ≥1 same-name candidate) is computable with one SQL query over the edges/symbols tables — it is the denominator for both targets (survey-verified).
- Assumption: the multivector default flip migrates lazily — existing workspaces populate `embeddings_mv` on their next embed pass (per-kind content-hash staleness), with no forced migration step.
- Assumption: excluding benchmark corpora does not break `cairn bench`/`cairn eval` corpus discovery — survey-verified: bench/eval/verify_ground_truth read corpora from disk or build fresh graphs over copies.
- Risk: FR-006's original 95% target exceeded the measured ceiling — resolved pre-approval by D-001's re-derivation to ≥75% exact (measured 75.81% post-exclusion; `references`-ambiguous → 0, residual `calls` ambiguity is machinery-bounded); a post-implementation shortfall below 75% still adjudicates as a fresh D-###, never a silent closing-audit failure.
- Risk: embedding on the update path slows incremental updates — mitigation: bounded to changed symbols; backend availability gate; degrade-not-fail (FR-003).
- Risk: multivector-by-default triples vector storage and lengthens first embed — mitigation: `--no-multivector`; lazy migration (assumption above).
