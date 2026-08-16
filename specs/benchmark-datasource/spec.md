# Spec: benchmark-datasource

**Status**: draft
**Created**: 2026-08-16
**Branch**: `feat/benchmark-datasource`
**Origin**: `docs/phases/benchmark-datasource/` (spec/plan/task merged at `66882c2`) — this file is the execution-grade restatement; the phase doc remains the narrative rationale.

## What

A pinned, versioned benchmark datasource and committed baseline artifacts for cairn, so any future performance or retrieval-quality comparison — release to release, PR to PR, or against competitors — resolves against a named dataset version, a named cairn version, and a recorded machine profile, instead of today's run-local JSON over an on-the-fly synthetic corpus and a rolling CI cache entry.

## Why

Cairn measures constantly but has no durable comparison substrate. Four pieces of evidence: (1) the CI bench job compares PRs against a rolling `actions/cache` baseline that rotates — regressions are unattributable to cairn vs runner; (2) `bench/corpus.py` is deterministic but pathological — dense random call graphs (340 s closure at 1,000 files, ~20× real shapes) and templated docstrings that broke the rerank-gate calibration; (3) three `_fill_` reference-table families in `docs/benchmarks.md` (retrieval quality, perf, scaling) have no committed data; (4) `src/cairn/eval.py`'s recall@10/MRR harness has no maintained ground truth, so retrieval-quality regressions ship invisibly.

## Business value

- Maintainers can answer "did cairn get slower or worse, compared to what?" with an attributable reference (SC-1).
- Retrieval-quality trend lines exist across releases, the quality analogue of the timing advisory that already exists (SC-2).
- `docs/benchmarks.md` reference tables become generated, regenerable artifacts rather than hand-typed promises (SC-3).
- Competitor comparisons (codegraph's 7-repo benchmark class) can cite cairn's dataset by name and version (SC-4).

## User stories

### US1 — Attributable regression comparison (P1)
As a maintainer, when a bench number moves, I want the comparison to name its reference (dataset version, cairn version, machine profile), so that I know whether cairn changed or the environment did.

**Acceptance criteria**:
- AC1: Given committed baselines under `benchmarks/baselines/<DS-version>/`, when I run `cairn bench --compare --baseline <DS-version>`, then the comparison renders with a dataset-version header and a loud warning when the machine profile mismatches the artifact's stamp. (FR-004)
- AC2: Given the T1 manifest, when CI regenerates the synthetic corpus on any runner, then a content hash matches the manifest or the job fails. (FR-001)

### US2 — Realistic, licensed benchmark content (P1)
As a maintainer, I want a small real-code snapshot with genuine call shapes vendored under a permissive license, so quality and effort numbers reflect deployed reality rather than synthetic pathologies.

**Acceptance criteria**:
- AC3: Given the vendored snapshot, when a build+query smoke runs in CI, then it builds green and answers a known-symbol query. (FR-002)
- AC4: Given a size budget check, when the datasource tree exceeds its budget, then CI fails. (FR-002)

### US3 — Quality trend lines (P1)
As a maintainer, I want a hand-verified ground-truth query set consumed by the recall/MRR harness, so retrieval changes are measured against stable expectations.

**Acceptance criteria**:
- AC5: Given ≥ 50 L1 code queries and ≥ 20 L5 knowledge queries with expected results, when the validator runs against a freshly built graph, then every expectation verifies or names the stale entry. (FR-003)

### US4 — Generated reference tables (P2)
As a docs reader, I want `docs/benchmarks.md`'s reference tables generated from committed baselines, so they can never silently drift from reality.

**Acceptance criteria**:
- AC6: Given committed baseline JSONs, when the table generator runs, then the `_fill_` families are replaced and regeneration is byte-idempotent; a hand-edit fails a CI check. (FR-005)

### US5 — Scale coverage without vendoring (P3)
As a maintainer, I want manifest-pinned external repos for scale runs, so 20k-file behavior is measurable without shipping that content.

**Acceptance criteria**:
- AC7: Given a T3 manifest entry and the documented local command, when it runs, then the fetch is by pinned commit and results record the manifest entry. (FR-006)

## Requirements

- **FR-001**: The system shall version the synthetic benchmark corpus (T1) via `benchmarks/datasource/manifest.json` (generator git-sha, seed, sizes, complexity, expected counts) and a CI check shall regenerate it and assert a path-order-independent content hash.
- **FR-002**: The system shall vendor a real multi-language code snapshot (T2, ≤ 3 MB) under `benchmarks/datasource/t2/` with a provenance manifest (upstream repo, commit, license) and NOTICE attribution, exercised by a build+query smoke test and guarded by a CI size budget (5 MB total for `benchmarks/datasource/`).
- **FR-003**: The system shall provide a ground-truth query set under `benchmarks/datasource/t2/ground_truth/` — ≥ 50 L1 queries (definition/callers/impact/flow, each with hand-verified expected symbols and a rationale citing the snapshot) and ≥ 20 L5 knowledge queries — in a schema `src/cairn/eval.py` consumes, with a validator script that re-verifies every expectation against a freshly built graph.
- **FR-004**: The system shall stamp every bench artifact with dataset version, cairn version, and machine profile (arch, cpu count, runner class), store reference baselines under `benchmarks/baselines/DS-v1/`, and extend `cairn bench --compare` with `--baseline <DS-version>` resolution that warns on machine-profile mismatch.
- **FR-005**: The system shall generate the retrieval-quality, perf, and scaling reference tables in `docs/benchmarks.md` from committed baseline artifacts between sentinel markers, byte-idempotently, with a CI check that fails on hand edits.
- **FR-006**: The system shall define T3 manifest entries pinning external repositories (url + commit) at ≥ 2 scale points and document a network-free-in-CI local command that fetches by pin and records the manifest entry in its results.

## Scope

**In**: datasource tree + manifests, ground truth, baseline artifacts, `--baseline` compare, docs table generation, CI wiring (advisory posture preserved).

**Out (deferred)**: gating CI on timing; live-LLM benchmark arms; T3 content in CI (network-free CI is hard); new benchmark metrics — later suites adopt the datasource as they land.

## Assumptions & risks

- Assumption: baseline artifacts are generated on the maintainer's machine (runner class `reference-local`) — CI comparisons remain advisory and warn on profile mismatch, per the observability doctrine "advisory first, gate later if stable".
- Assumption: a suitable ≤ 3 MB permissively-licensed real repo exists whose license permits vendoring with attribution (survey/research to confirm candidates).
- Risk: ground truth rots as resolution improves — mitigation: the validator re-verifies against fresh builds and expectations record symbol identity, not incidental rank.
- Risk: dataset becomes a maintenance tax — mitigation: versioned directories, never edit a shipped baseline, deprecate whole versions.

## Research questions (for Stage 1)

- RQ1: How do comparable tools pin benchmark datasets and baselines (codegraph's 7-repo benchmark, pytest-benchmark's storage, pyperf, codspeed, BEIR for retrieval datasets)? What do they stamp and how do they compare across machines?
- RQ2: Which small (≤ 3 MB), multi-language, permissively-licensed real repositories are strong T2 candidates (genuine call depth, non-trivial docstrings, license permitting vendoring with attribution)?
- RQ3: What deterministic, path-order-independent tree-hash approach is standard for content-pinning generated corpora in CI?
- RQ4: What ground-truth formats do retrieval-eval datasets use (BEIR/MS MARCO style, ids vs graded relevance) and what minimal schema fits cairn's L1/L5 harness?
- RQ5: What machine-profile fields do cross-machine benchmark comparisons normalize on (cpu governor, runner class), and what's the accepted warning practice?
