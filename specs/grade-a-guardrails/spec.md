# Spec: grade-a-guardrails

**Status**: done
**Effort**: standard
**Created**: 2026-10-05
**Branch**: `feat/grade-a-guardrails`

## What
Four operational guardrails: a self-healing `cairn doctor --fix` for stale
agent-client registrations; a resolution-quality report (call-edge counts by
resolution label per language); catastrophe-sized per-tool p95 latency
budgets in the perf suite; and a weekly scheduled CLI smoke workflow — plus
a maintainer-run branch-protection verification command. Together they make
runtime rot and environment drift fail loudly instead of silently.

## Why
`cairn doctor` reports a stale-registration FAIL with no remediation path; a
148 s p95 `impact` regression class would land silently because the perf
suite records p95 but gates nothing; precision planning has no per-language
ambiguity evidence to target; and branch-protection settings are claimed in
docs but verified by nobody. These are guardrails, not features: each turns
an already-measured signal into a failure or a one-command fix.

## Business value
Maintainers get: environment FAILs remediable in one command; evidence
(which languages hold the ambiguous-edge mass) to aim the precision
follow-up spec; latency catastrophes failing the main-gating bench job; and
weekly proof the CLI surface still runs. Success: each instrument green (or
zero-action) on a healthy repo and demonstrably red on an injected fault.

## User stories
### US1 — Self-healing doctor (P1)
As a user with stale agent-client registrations, I want `cairn doctor --fix`
to remediate them safely, so a FAIL becomes actionable in one command.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a cairn-owned SSE registration whose endpoint does not respond,
  When `cairn doctor --fix` runs, Then the registration is repointed to the
  equivalent stdio registration, the config is backed up and atomically
  rewritten, and the summary lists every action with the stdio-vs-daemon
  tradeoff stated.
- AC2: Given a healthy environment, When `cairn doctor --fix` runs, Then it
  changes nothing and reports zero actions.
- AC3: Given a config file modified within the freshness window, When
  `--fix` considers writing it, Then it refuses that file with guidance.

### US2 — Latency catastrophe gate (P1)
As a maintainer, I want per-tool p95 budgets on the gating bench job, so a
148 s-class regression fails CI.

**Acceptance criteria**:
- AC1: Given the pinned corpus and completed warm-up, When the budgeted perf
  run executes on the main/merge-group bench job, Then each tool's p95 is
  compared to its budget and a breach fails naming tool, measured p95, and
  budget.
- AC2: Given a PR-leg run, When the perf suite executes, Then budgets are
  reported as advisory trends only.

### US3 — Resolution-quality evidence (P2)
As a maintainer planning precision work, I want ambiguous/unresolved
call-edge mass per language, so the follow-up spec targets the top languages
by evidence.

**Acceptance criteria**:
- AC1: Given any built store, When the report runs, Then it prints call-edge
  counts by resolution label per language with percentages, plus `--json`.
- AC2: Given a store with no call edges, When the report runs, Then it
  reports zeros per language, not an error.

### US4 — Weekly smoke + protection check (P2)
As a maintainer, I want scheduled CLI replay and a protection verifier, so
rot between releases and setting drift are caught.

**Acceptance criteria**:
- AC1: Given the checked-in command list and fixture repo, When the weekly
  workflow runs, Then each command exits 0 and the job is red on first
  failure naming it.
- AC2: Given maintainer gh auth, When `make verify-protection` runs, Then it
  checks main's protection (legacy branch-protection and rulesets) for
  required reviews, force-push denial, and required checks, exiting 0/1.

## Requirements
- **FR-001**: The system shall provide `cairn doctor --fix` that remediates
  actionable environment failures: a cairn-owned agent-client SSE
  registration whose endpoint does not respond is repointed to the
  equivalent stdio registration; each written config shall be backed up and
  rewritten atomically (temp file + rename); WHERE a target config's mtime
  falls inside a freshness window, the write shall be refused with guidance;
  registrations not owned by cairn shall never be touched; the summary shall
  list every action and state the stdio-vs-shared-daemon tradeoff; a healthy
  environment shall yield zero actions; no other check result shall be
  mutated.
- **FR-002**: The system shall add a resolution-quality report (surface per
  the survey's finding: `cairn doctor` section, `cairn system report`, or a
  dedicated flag) giving call-edge counts by resolution label (`exact` /
  `ambiguous` / `unresolved`) per language with percentages and `--json`;
  WHERE a store has no call edges, it shall report zeros per language.
- **FR-003**: The perf suite shall enforce per-tool p95 latency budgets for
  at least `find_definition`, `get_callers`, `impact_analysis`,
  `search_symbols`, `semantic_search`, and `explore`, with budgets sized at
  ≥10× the pinned-corpus observed p95 (catastrophe-class), measured only
  after a warm-up pass; the hard gate shall run in the main/merge-group
  bench job; PR-leg runs shall emit advisory trend output only; WHEN a
  budget is breached, the run shall fail naming tool, measured p95, and
  budget.
- **FR-004**: The system shall add a scheduled weekly GitHub workflow
  replaying a checked-in list of high-signal CLI commands against a
  deterministic fixture repo, red on the first failing command with the
  command named.
- **FR-005**: The repo shall provide `make verify-protection` invoking gh
  read-only to check main's branch protection through both the legacy
  branch-protection API and the rulesets API for required reviews,
  force-push denial, and required status checks, plus the mandated settings
  documented in the release checklist; the command is maintainer-run
  (admin-scoped gh auth) and shall not be wired as a CI gate.

## Quality attributes
- **NFR-001**: Security — applicable: `doctor --fix` shall write only
  cairn-owned agent-client config files, back up each before writing, and
  never log or persist secrets found therein; atomic rewrite via temp +
  rename.
- **NFR-002**: Privacy — applicable: the weekly workflow shall use only the
  checked-in command list and local fixtures; no telemetry or usage data
  leaves the repo.
- **NFR-003**: Performance — not applicable: the budget gate consumes the
  existing perf-suite runs; no new hot path is introduced.
- **NFR-004**: Reliability — applicable: `doctor --fix` shall be idempotent
  (second run reports zero actions) and crash-safe (config is original or
  fully rewritten, never partial).
- **NFR-005**: Observability — applicable: every instrument shall expose a
  machine-readable mode (`--json`) consumable by CI and `cairn system
  report`.
- **NFR-006**: Accessibility — not applicable: no UI surface is touched.

## Scope
**In**: `doctor --fix` for stale cairn-owned registrations; resolution-
quality report; perf-suite budgets + bench-job wiring + advisory PR output;
weekly smoke workflow + command list; `make verify-protection` + release-
checklist documentation; docs updates.

**Out (deferred)**: extending the LSP/SCIP exact-edge upgrade beyond Python
(follow-up spec, targets chosen from FR-002's evidence); the 15-language
parser conformance matrix (follow-up spec); executing the remaining audit
P1 fixes; recruiting/external-reviewer onboarding and the 30-PR review
streak (organizational, not codeable); the comment sweep (chained spec
`comment-sweep`).

## Assumptions & risks
- Assumption: catastrophe-sized (≥10×) budgets keep hosted-runner noise
  below the failure threshold; tuned regression detection stays advisory.
- Assumption: the agent_install clients' cairn-marking convention
  identifies cairn-owned registration entries reliably across clients.
- Risk: `doctor --fix` races a live client writing its own config —
  mitigation: freshness-window refusal + atomic rename + backup.
- Risk: budget thresholds age as the corpus or hardware changes —
  mitigation: budgets checked in as data with a documented recalibration
  command, not hardcoded magic numbers.
