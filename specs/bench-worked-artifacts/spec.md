# Spec: bench-worked-artifacts

**Status**: draft
**Effort**: standard
**Created**: 2026-10-05
**Branch**: `feat/bench-worked-artifacts`

## What
Reproducible public benchmark artifacts: `cairn bench --worked DIR` writes,
alongside the existing report, a worked-directory bundle — the raw result
JSON, an inputs manifest (suite, dataset version, seed, machine stamp,
exact command line), and a human README companion — under
benchmarks/worked/ with one directory per run named suite-datasetversion (e.g. perf-DS-v1.1), with one inventory row per artifact
in `benchmarks/README.md`. Anyone can re-run the manifest's command and
compare.

## Why
Cairn publishes benchmark claims (perf, scaling, agent suites) as committed
baseline JSONs, but a reader cannot reproduce the run that produced them:
the inputs (seed, backend pins, machine profile, command) live only in the
session that minted them. Worked bundles make every published number
auditable — the graphify project's `worked/` convention shows the trust
value of shipping inputs and outputs together.

## Business value
Benchmark claims become self-verifying: each artifact carries its own
reproduction recipe. Success: a fresh `cairn bench --worked` run produces a
directory whose manifest command re-runs green on another machine (modulo
the documented machine-stamp drift), and `benchmarks/README.md` indexes
every worked artifact.

## User stories
### US1 — Worked bundle (P1)
As a maintainer publishing a benchmark, I want one flag that writes the
reproducible bundle, so that claims ship with their evidence.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given any suite run, When `cairn bench --worked benchmarks/worked`
  completes, Then the directory holds the raw result JSON, an inputs
  manifest, and a README companion.
- AC2: Given the manifest's command line, When it is re-run on a fresh
  checkout, Then the run completes with the same deterministic fields
  (calls/est_tokens-class), wall-clock fields advisory.

### US2 — Inventory (P2)
As a reader of benchmarks/README.md, I want every worked artifact indexed,
so that raw files are never orphans.

**Acceptance criteria**:
- AC1: Given a worked run, When it completes, Then `benchmarks/README.md`
  carries one row per new artifact (repo-relative path + companion note),
  with no duplicate rows on re-run.

## Requirements
- **FR-001**: The system shall provide `cairn bench --worked DIR` writing,
  per run: the raw result JSON (identical payload to `--save`), an inputs
  manifest (suite name, dataset version, seed/repeats/runs, embed backend,
  machine stamp, full command line), and a README.md companion naming each
  file.
- **FR-002**: The bundle layout shall be
  benchmarks/worked/ with one directory per run named suite-datasetversion (e.g. perf-DS-v1.1) when DIR is the benchmarks root,
  or exactly DIR when given; existing files of the same run are overwritten
  atomically (temp file + rename), never appended.
- **FR-003**: The system shall append one inventory row per new artifact to
  `benchmarks/README.md`, keyed repo-relative per the existing inventory
  convention, idempotently (re-runs do not duplicate rows).
- **FR-004**: WHEN the worked directory target is inside the repo, the
  system shall leave all existing inventory rows and companion docs
  untouched except for the appended rows.

## Quality attributes
- **NFR-001**: Security — not applicable: local file writes only.
- **NFR-002**: Privacy — applicable: the manifest shall not embed absolute
  home paths or tokens (repo-relative or $VAR-templated paths only).
- **NFR-003**: Performance — not applicable: bundle writing is a single
  serialization pass after the run.
- **NFR-004**: Reliability — applicable: WHEN the worked directory cannot
  be written, the system shall fail cleanly after (not during) the bench
  run, reporting the bench results to stdout regardless.
- **NFR-005**: Observability — not applicable: command summary output
  suffices.
- **NFR-006**: Accessibility — not applicable: no UI surface is touched.

## Scope
**In**: `--worked` flag on `cairn bench`; bundle writer (manifest, raw
JSON, README companion); inventory-row append; tests (infra-marked,
determinism-only); benchmarks/README.md row for the first worked bundle.
**Out (deferred)**: re-running CI automatically from manifests; worked
bundles for quality campaigns; changing baseline formats; publishing
outside the repo.

## Assumptions & risks
- Assumption: `_stamp_and_emit`'s payload already carries the machine
  stamp and suite identity; the manifest extends rather than reinvents it.
- Risk: README.md appends could clobber concurrent edits — mitigation:
  idempotent append keyed on the artifact path; worked runs are explicit
  and rare.
- Risk: manifest drift when CLI flags change — mitigation: the manifest
  records the argv it was produced with (self-describing), and the TC
  asserts the manifest's command re-parses.
