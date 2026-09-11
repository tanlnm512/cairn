# Spec: remove-scip-exact-rate

**Status**: done
                                (explicit user sign-off — a human gate, never
                                auto-satisfied) → active once the first task spawns
                                → done when all tasks are ticked and `check.py`
                                re-runs green -->
**Created**: 2026-09-11
**Branch**: `refactor/remove-scip-exact-rate`

## What
Remove the optional SCIP compiler-index ingestion path so cairn indexes exclusively via tree-sitter, and raise the tree-sitter edge resolver's exact rate — the share of intra-workspace reference edges resolved to exactly one definition (`resolution='exact'`) instead of being left `ambiguous` — so precise-by-default queries (`get_callers`, `impact_analysis`, `explore` call paths) stay useful without compiler-grade indexes.

## Why
SCIP bought compiler-grade bindings for typed languages at the cost of a whole subsystem: per-language external indexer binaries (`scip-java`, `scip-swift`, …), a vendored protobuf stub + `[scip]` extra, hybrid merge logic with known failure modes (opaque Swift USRs merging at ~0% and reverting to pure-SCIP skips), and a second path through builder/incremental/config/CLI. Tree-sitter is the always-present baseline for every language. Dropping SCIP removes the failure modes and the dependency surface; the exact-rate work compensates for the precision SCIP provided on the languages where it actually worked.

## Business value
- Simpler build: one indexing path, no external tool auto-generation, no protobuf runtime, smaller install matrix.
- Better default intelligence: more edges trusted by precise queries across all languages, not just SCIP-covered ones.
- Measured by: resolution mix (`exact/ambiguous/unresolved`) on a fixed corpus before/after, and the existing retrieval ground truth staying green.

## User stories
<!-- Ordered by priority; each independently demoable. -->
### US1 — Tree-sitter-only indexing (P1)
As a cairn user, I want `cairn build` to index every language via tree-sitter only, so that I never need external indexer binaries, SCIP index files, or the `[scip]` extra.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a workspace with no `scip` config, When `cairn build` runs, Then every file indexes via tree-sitter and the build succeeds with no SCIP code paths executed.
- AC2: Given a workspace whose `cairn.json` still carries a `scip` key, When `cairn build` runs, Then the build succeeds via tree-sitter only and the stale key is handled per FR-002.
- AC3: Given a fresh install without the `[scip]` extra, When the full test suite runs, Then no SCIP test path is collected or skipped-for-missing-extra.

### US2 — Higher exact rate (P1)
As an agent consumer of `get_callers`/`impact_analysis`/`explore`, I want more intra-workspace call edges resolved `exact` from tree-sitter data alone, so that precise queries return more trusted edges without `fuzzy=True` noise.

**Acceptance criteria** (each traces to an FR below):
- AC4: Given the measurement corpus, When built before and after the change, Then the exact share of intra-workspace edges strictly increases per FR-005's metric.
- AC5: Given the retrieval ground truth (`benchmarks/datasource/{t2,ds2}`), When re-verified after the change, Then no previously-passing expectation regresses (zero false-exact).
- AC6: Given previously-ambiguous edge shapes named in survey (e.g. import-alias targets, same-name overloads), When built, Then each now resolves exact with a regression test proving it.

## Requirements
<!-- EARS-shaped SHALL statements. One verb, one system, testable. -->
### Removal
- **FR-001**: The build pipeline shall index all languages exclusively via tree-sitter parsers; the SCIP import, merge, skip, and auto-generation code paths shall not exist.
- **FR-002**: WHEN `cairn.json` contains a `scip` key, the system shall treat it as any other unknown key — ignored, with no SCIP-specific handling of any kind (complete removal; no compat shim, no deprecation-warning machinery).
- **FR-003**: The CLI shall not expose `import-scip`; the packaged extras shall not define `[scip]`; the vendored protobuf stub, its regen script, and the `scip_indexers` orchestrator shall be deleted.
- **FR-004**: Documentation, diagrams, and status output shall describe the tree-sitter-only pipeline with no SCIP references.

### Exact rate
- **FR-005**: On the fixed measurement corpus (cairn's own repo plus the `ds2` datasource corpus), the post-change build shall show a strictly higher share of intra-workspace edges resolved `exact` than the pre-change baseline, subject to zero false-exact conversions. The share is `exact/(exact+ambiguous)` computed on the built edges table over resolver-resolved reference kinds (`calls`, `references` — the literals parsers emit, tech-spec §measurement) — `imports` (exact by construction via `materialize_import_edges`), `contains`, and `decorates` edges are excluded from numerator and denominator, as is `unresolved` (external/stdlib). Baseline at `dc9882b`: self-repo share recorded by T001 under this definition; T001 also records the ds2 equivalent.
- **FR-006**: The exact-rate gains shall come from resolver-core heuristics (tier refinement in `graph/resolver.py`) and parser-side signal enrichment (what tree-sitter parsers emit: import aliases, receiver types, qualified names), with no new runtime dependency.
- **FR-007**: WHERE parser-side signal enrichment lands, it shall cover all fourteen golden-fixture languages (c, cpp, csharp, dart, go, java, javascript, kotlin, objc, php, python, ruby, swift, typescript) — each language's parser module or shared query set carries the signals its grammar exposes; a language whose grammar lacks a signal degrades to current behavior, never worse.
- **FR-008**: The resolver shall preserve the existing tier contract (type-aware → same-file → import-aware → same-repo → global → ambiguous) unless a tech-spec D-### records a changed ordering with evidence.

### Non-regression
- **FR-009**: IF any improvement would mark an edge `exact` whose true target differs from the resolved candidate (false-exact), the resolver shall abstain (leave `ambiguous`) — precision outranks recall.
- **FR-010**: The build's resolution phase wall-time on the scaling corpus shall not regress materially vs the pre-change baseline (scaling-suite gate; materiality pinned by the surveyor's baseline numbers).

## Scope
**In**: complete removal of the SCIP subsystem (modules, builder/incremental/config/CLI paths, `[scip]` extra, vendored stub + regen script, tests exercising them, docs/diagrams/status lines) with no compat shims; resolver-core and parser-signal work raising intra-workspace exact rate across all fourteen golden-fixture languages; before/after resolution-mix measurement on the fixed corpus; regression tests for converted edge shapes.
**Out (deferred)**: resolving `unresolved` (external/stdlib) edges — would need external symbol knowledge; any new runtime dependency for name binding (stack-graphs-grade) unless a D-### justifies it per CONSTITUTION C-03; per-language type inference beyond what tree-sitter nodes expose; LSP-based indexing.

## Assumptions & risks
- Assumption: no DB migration is required — cairn DBs are rebuild artifacts; post-change builds produce `source='tree_sitter'` rows only, legacy DBs simply age out.
- Assumption: a resolution-mix measurement exists as runnable SQL over the built edges table (survey S-11 records the recipe and both corpora's baseline numbers); no resolution-specific ground-truth dataset exists at baseline — AC6's per-shape regression tests are the false-exact vehicle.
- Ruling (survey S-08): CHANGELOG historical entries are append-only history and stay untouched; this change adds one new entry. FR-004's "no SCIP references" covers living docs (docs/, README, diagrams incl. regenerated PNG twins, shipped `agent_integration` skill docs), not the historical record.
- Assumption (clarify-round ruling, Q1): "completely remove SCIP" — the `scip` config key gets no dedicated deprecation warning; unknown-key behavior already ignores it silently.
- Risk: removing SCIP degrades exact rate on languages where SCIP genuinely worked (Java/Kotlin/TS via scip-java/scip-typescript) — mitigation: the exact-rate FRs must show the net effect on the measurement corpus, and the surveyor records the pre-change per-language mix.
- Risk: heuristic exact-rate gains smuggle in false positives — mitigation: FR-009 + ground-truth gate (AC5).
