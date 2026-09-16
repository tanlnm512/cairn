# Spec: swe-bench-integration

**Status**: draft
**Created**: 2026-09-16
**Branch**: `feat/swe-bench-integration`

## What
A `cairn bench --suite swe-bench` arm that measures agent efficiency and
resolution on SWE-bench tasks with and without cairn's tools, plus published
results — giving cairn a standard-benchmark number comparable to published
graph-agent results (RepoGraph: +32.8% relative on SWE-bench).

## Why
Cairn's 99.5% token-reduction claim is measured on its own corpus; the
community trusts standard benchmarks. RepoGraph published +32.8% on
SWE-bench with repo-level code graphs — without a comparable number,
cairn's claims are self-referential.

The measurement machinery already exists and only needs a new task source:
`bench/agent_suite.py` runs the exact two-arm shape this spec needs — a
deterministic cairn-equipped arm versus a grep+read control agent over a
corpus, reporting tokens/tool-calls/wall-clock — and the self-demo test
already proves the CI-rot-prevention pattern (cairn indexes itself in CI).
An SWE-bench arm re-points that harness at public task inputs; it does not
invent a new benchmarking methodology.

## Business value
A reproducible, CI-protected benchmark number on a public benchmark set that
third parties can rerun. Success: README publishes with/without-cairn
medians (tokens, tool calls, and — for the LLM arm — resolve rate).

## User stories
### US1 — Deterministic efficiency arm (P1)
As a maintainer, I want a reproducible no-LLM arm measuring token/tool-call
efficiency on SWE-bench tasks.

**Acceptance criteria** (each trace to an FR below):
- AC1: Given the suite, When it runs, Then medians for tokens and tool calls
  per task are reported for grep-baseline vs cairn-equipped arms, fully
  deterministic and rerunnable (FR-001, FR-002).

### US2 — Credible published number (P2)
As an evaluator, I want a published methodology and results I can verify.

**Acceptance criteria**:
- AC1: Given the suite, When run per the documented commands, Then a third
  party reproduces the README numbers from a clean clone (FR-003).

## Requirements
- **FR-001**: The system shall provide a `swe-bench` bench suite arm that
  runs a deterministic (no-LLM) agent loop over SWE-bench task inputs with
  and without cairn's tools, reporting per-task and median tokens and tool
  calls.
- **FR-002**: The suite shall be reproducible: fixed task subset and seeds,
  offline operation for the deterministic arm, and identical outputs across
  reruns.
- **FR-003**: Documentation shall publish methodology and commands sufficient
  for a third party to reproduce the reported medians from a clean clone.
- **FR-004**: A CI job shall run a smoke subset of the suite to prevent rot
  (same pattern as the self-demo test).
- **FR-005**: The LLM-in-the-loop arm (end-to-end resolve-rate measurement
  using a standard agent framework) is deferred until the deterministic arm
  ships (ruled 2026-09-17: deterministic arm is the P1 story; LLM arm costs
  ~2-3 weeks plus eval compute and follows as a separate spec).

## Scope
**In**: deterministic arm, task-subset loader, with/without tool arms,
reporting, reproducibility docs, CI smoke job.
**Out (deferred)**: LLM arm (deferred by FR-005 ruling); full SWE-bench
leaderboard submission; non-SWE benchmarks (HumanEval, etc.).

## Assumptions & risks
- Assumption: SWE-bench task data licensing permits redistribution of a
  fixed subset reference (loader fetches, repo does not vendor task data).
- Risk: SWE-bench harness format drift — mitigation: pin the harness version
  as a dev-only dependency (constitution C-03 decision recorded); loader
  isolated behind a stable internal interface.
