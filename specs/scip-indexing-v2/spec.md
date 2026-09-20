# Spec: scip-indexing-v2

**Status**: approved      <!-- approved at the approve gate 2026-09-20 (explicit
                                user sign-off) → active once the first task spawns
                                → done when all tasks are ticked and `check.py`
                                re-runs green -->
**Created**: 2026-09-20
**Branch**: `feat/scip-indexing-v2`

## What
Reintroduce SCIP compiler-index support as a redesigned, opt-in precision layer: a configured (or once, auto-generated) `.scip` index contributes exact call and reference edges on top of the unchanged tree-sitter symbol graph — per-file authoritative, joined by position — for seven languages, with the `[scip]` protobuf extra restored and the transitive-closure matrix redesigned to absorb the resulting edge-volume multiplication without stalling.

## Why
Tree-sitter-only resolution plateaus: the self-repo measures ~76% exact on intra-workspace candidate edges, and the residue (dynamic dispatch, cross-file overloads, re-exports) is structurally out of reach of name-based inference. A SCIP index carries compiler-grade bindings — every reference occurrence names the definition it points to — so covered languages get exact edges exactly where tree-sitter abstains to `ambiguous`.

The first SCIP subsystem (`remove-scip-exact-rate`, removed 2026-09-11 at `0a956fa`) is the cautionary design: name-keyed coexistence merge produced two disconnected symbol populations for opaque-USR indexers (scip-swift ~0% merge), the all-or-nothing per-language fallback was coarse, a whole-workspace index imported under one repo id, and the closure matrix blew up (>30 min, 4.6 GB WAL) at SCIP's ~5× edge volume. This spec reintroduces the capability with each failure mode addressed structurally: no symbol merging at all (edges-only overlay, position join), per-file authority, per-document repo attribution, and closure scaling designed in scope rather than gated around.

## Business value
Agents running precise queries (`get_callers`, `impact_analysis`, `explore`, taint paths) on typed-language repos get exact edges without `fuzzy=True` noise, opt-in via one config line per language. Measurable by: per-language exact-share uplift on the resolution-share stats surface (same tree, index on vs off), zero false-exact conversions against the retrieval ground truth, and closure wall-time within its recorded budget at scaling-corpus edge volume.

## User stories
<!-- Ordered by priority; each independently demoable. -->
### US1 — Exact edges from a SCIP index (P1)
As an agent consuming `get_callers`/`impact_analysis`, I want a configured `.scip` index to contribute exact call/reference edges for its covered files, so that precise queries return trusted edges where tree-sitter goes ambiguous.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a workspace with a valid `.scip` index configured in `cairn.json`, When `cairn build` runs, Then every covered file's `calls`/`references` edges come from the index (`source='scip'`, `resolution='exact'`) with no duplicate tree-sitter edges for those files.
- AC2: Given an opaque-USR index shape (swift-like fixture), When imported, Then every edge attaches to the single tree-sitter symbol population via the position join — no duplicate or disconnected symbols are introduced.
- AC3: Given the resolution-share stats surface, When builds with and without the index are compared on the same tree, Then covered languages' exact share rises and the retrieval ground truth shows zero false-exact conversions.

### US2 — Closure at 5× edges (P1)
As a user of multi-hop queries on SCIP-enriched graphs, I want the transitive-closure matrix redesigned to fit a recorded budget at ~5× edge volume, so that enabling SCIP never trades precision for a stalled build.

**Acceptance criteria**:
- AC4: Given the scaling corpus at SCIP-heavy edge volume (~5× tree-sitter), When the closure builds, Then it completes within the wall-time budget recorded in tech-spec.
- AC5: Given any non-SCIP build, When the redesigned closure runs, Then multi-hop query results are byte-identical to the current implementation (shared machinery regression gate).

### US3 — Bounded auto-generation (P1)
As a user on a language with a known indexer, I want the missing index generated once when configured-but-absent and the binary is present, so that setup is one config line, not a pipeline.

**Acceptance criteria**:
- AC6: Given a registry language, index configured but absent, indexer binary on PATH, When `cairn build` runs, Then the indexer runs exactly once under its bounded timeout and the produced index imports.
- AC7: Given the binary missing, exiting nonzero, or timing out, When `cairn build` runs, Then the build succeeds via tree-sitter with an observable fallback record and (under `-v`) the tool's install hint.

### US4 — Incremental honesty (P2)
As a user editing covered files, I want `cairn update` to keep those files' edges current via tree-sitter, so that updates never stall on a compiler and provenance always reflects reality.

**Acceptance criteria**:
- AC8: Given a changed file in a covered language, When `cairn update` runs, Then that file's edges are tree-sitter-resolved with the provenance flip observable, and the next full build restores SCIP edges for it.

### US5 — Manual import and config surface (P2)
As a power user with CI-generated indexes, I want `cairn import-scip` and the `scip` config key back, so that out-of-band pipelines can feed cairn.

**Acceptance criteria**:
- AC9: Given an already-built DB, When `cairn import-scip <file>` runs, Then edges land under the same overlay rules with per-document repo attribution (AC1/AC2 hold).

## Requirements
<!-- EARS-shaped SHALL statements. One verb, one system, testable. -->
### Overlay ingestion
- **FR-001**: The build pipeline shall import configured SCIP indexes (protobuf, via the `[scip]` extra) as an edges-only overlay — tree-sitter remains the sole source of symbols and structure; SCIP contributes `calls`/`references` edges only.
- **FR-002**: WHEN a SCIP document covers a file, the system shall replace that file's tree-sitter `calls`/`references` edges with the index's exact edges (per-file authority); files outside every index shall keep their tree-sitter edges unchanged.
- **FR-003**: SCIP edges shall join the graph by position — each occurrence range maps to the tree-sitter symbol containing that range in the same file — never by symbol-name or USR string equality; out-of-workspace targets shall be tagged `unresolved` per the existing resolution vocabulary.
- **FR-004**: WHEN the workspace holds multiple repos, the importer shall attribute each document's data per-document via path normalization (repo-relative identity shared with the scanner/incremental paths), never to a single repo id.
- **FR-005**: IF a document's position-join rate is anomalous (below the threshold recorded in tech-spec), the system shall retain that file's tree-sitter edges and record the anomaly observably — never silently degrade a covered file.
- **FR-006**: IF the protobuf runtime is absent or version-mismatched, the build shall succeed via tree-sitter and report the `[scip]` install hint with an observable fallback record; no crash.

### Auto-generation
- **FR-007**: WHEN a language's index is configured but absent and a known indexer binary is on PATH, the build shall attempt generation exactly once under a bounded timeout; an existing index file shall never be rebuilt.
- **FR-008**: IF generation fails (missing binary, nonzero exit, timeout, OS error), the build shall succeed via tree-sitter with an observable record on the skip/fallback surface; the tool's install hint shall surface under `-v`.
- **FR-009**: The auto-generation registry shall ship entries for swift, java, kotlin (via scip-java), typescript, python, go, and rust (via rust-analyzer's `scip` subcommand), each degrading independently of the others.

### Incremental
- **FR-010**: WHEN `cairn update` encounters changed files in covered languages, it shall (re)index those files via tree-sitter — provenance flips to tree-sitter, observably — and shall not invoke any indexer; the next full build shall restore SCIP edges.

### Closure redesign
- **FR-011**: The transitive-closure build shall be redesigned to complete within the wall-time/memory budget recorded in tech-spec at ~5× tree-sitter edge volume on the scaling corpus, while producing byte-identical multi-hop results for unchanged graphs.
- **FR-012**: The closure budget shall be enforced as a scaling-suite gate (budget number and measurement recorded as a tech-spec D-###).

### Measurement and reporting
- **FR-013**: `cairn stats` shall report SCIP provenance within its resolution-share surface such that per-language exact-share uplift is measurable, index on vs off, on the same tree.
- **FR-014**: The exact-rate gains shall introduce zero false-exact edges — where the index's binding and the resolver's would disagree, the edge follows the index (its binding is compiler-grade) and the disagreement is counted, not silently resolved either way.

### CLI, config, docs
- **FR-015**: The CLI shall expose `cairn import-scip` (manual import against a built DB under the same overlay rules) and `cairn config` shall echo the `scip` key.
- **FR-016**: Documentation shall include an end-to-end SCIP toolchain guide (`docs/scip.md`): per-indexer install (including the npm-not-pip scip-python and macOS-only scip-swift cases), config, generation semantics, fallback behavior, and the overlay model.

### Dependency
- **FR-017**: The packaged extras shall define `[scip]` (protobuf runtime + vendored stub + regen script) as a recorded C-03 tech-spec decision.

## Scope
**In**: the `scip` config key and importer (edges-only overlay, position join, per-file authority, per-document repo attribution, anomalous-join guard); the auto-generation registry (seven languages, run-once bounded, observable fallbacks); `[scip]` extra + vendored stub + regen script; incremental per-file tree-sitter fallback with observable provenance; the transitive-closure redesign (shared machinery) with its scaling gate; `import-scip` CLI + config echo; stats provenance on the resolution-share surface; `docs/scip.md`; fixture indexes for an opaque-USR shape and a readable-descriptor shape driving the regression tests; before/after exact-share and closure measurements.
**Out (deferred)**: producing SCIP indexes from inside cairn (cairn stays a consumer, never a producer); resolving `unresolved` (external/stdlib) edges; any SCIP-side symbol or structure contribution (edges-only by design); semantic/embedding pipeline changes; LSP-based indexing; registry rows beyond the seven; federation/graft interplay beyond provenance surviving the merge.

## Assumptions & risks
- Assumption: the overlay touches only `calls`/`references` edge kinds; `imports`/`contains`/`decorates` stay tree-sitter-only.
- Assumption: CI verifies via committed fixture indexes, not by running real indexers (the compiler/toolchain matrix is too heavy for CI); real-indexer runs happen as measurement harness work.
- Assumption: the closure budget number is pinned at tech-spec time from a scaling-suite baseline; the default proposal is "no material wall-time regression vs the current closure at 5× edges."
- Risk: position-join misses when tree-sitter symbol boundaries disagree with SCIP ranges (macro-generated code, build-conditioned files) — edges dropped — mitigation: FR-005's anomalous-join guard plus drop counters in the import stats.
- Risk: registry binaries are external moving targets (scip-swift builds from source on macOS only; scip-python ships via npm) — mitigation: install hints (FR-008), docs/scip.md (FR-016), independent degradation (FR-009).
- Risk: the closure redesign touches machinery shared by impact, dataflow, and taint paths — mitigation: AC5's byte-identical regression gate over existing multi-hop query results.
