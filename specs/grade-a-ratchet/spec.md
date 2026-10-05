# Spec: grade-a-ratchet

**Status**: approved
**Effort**: standard
**Created**: 2026-10-05
**Branch**: `feat/grade-a-ratchet`

## What
Two stop-growth gates and one hygiene move: tracked root-level artifacts
relocate under `docs/`; a `make audit-status` tool makes remaining audit debt
a visible, machine-readable number; and a shrink-only comment-style ratchet
enforces the AGENTS.md minimal-comment contract under `src/` — new long
comment blocks fail CI from the moment it lands, and the pre-existing
baseline may only shrink.

## Why
The repo claims standards it does not gate. `AGENTS.md` mandates one-line
docstrings and no rationale-narrative comments while 741 lines sit in 4+-
line comment blocks under `src/`; audit debt (141 validated findings, 51 P1)
has no burndown counter; and `architecture.html` plus
`audit-findings-2026-10-02.md` are tracked at the repo root. A ratchet over
a known baseline is the standard industrial pattern — stop growth now,
shrink monotonically — and it lands as a small reviewable change, unlike a
bundled mass cleanup.

## Business value
Maintainers see remaining audit debt in one command; CI makes comment drift
impossible to land; the repo root returns to standard entries. Success:
both gates run green on main the day they land and every later PR.

## User stories
### US1 — Audit-debt visibility (P1)
As a maintainer, I want remaining audit findings counted by priority, so the
burndown is a visible, ratcheting number.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given the relocated audit file and its status sidecar, When `make
  audit-status` runs, Then it prints P0/P1/P2/P3 remaining-vs-total counts
  and exits 0.
- AC2: Given a missing or unparseable audit file or sidecar, When `make
  audit-status` runs, Then it fails cleanly naming the offending file.

### US2 — Comment-style ratchet (P1)
As a maintainer, I want CI to reject new long comment blocks and docstrings,
so the AGENTS.md style contract cannot drift further.

**Acceptance criteria**:
- AC1: Given the committed baseline, When the checker runs on an unchanged
  tree, Then it passes and prints the current violation count.
- AC2: Given a new 4-line comment block or 4-line docstring in `src/`, When
  the checker runs, Then it fails naming file and block.
- AC3: Given a baseline entry whose violation no longer exists, When the
  checker runs, Then it fails reporting the stale entry (shrink-only).

### US3 — Root hygiene (P2)
As a contributor, I want the repo root to hold only standard entries, so
artifacts are findable where docs live.

**Acceptance criteria**:
- AC1: Given the tracked root artifacts, When relocation lands, Then
  `architecture.html` lives under `docs/diagrams/` and the audit file under
  `docs/audits/`, with inbound links updated.

## Requirements
- **FR-001**: The system shall relocate tracked root-level artifacts via
  `git mv` with inbound links updated — `architecture.html` →
  `docs/diagrams/architecture.html`, `audit-findings-2026-10-02.md` →
  `docs/audits/2026-10-02.md` — leaving the repo root to standard entries
  only.
- **FR-002**: The system shall provide `make audit-status` reading finding
  totals by priority from `docs/audits/*.md` and fixed-status per finding
  from a machine-readable sidecar (`docs/audits/<audit>.status.json`),
  printing remaining-vs-total counts per priority (P0–P3); it shall support
  `--json`; WHERE the audit file or sidecar is missing or unparseable, it
  shall fail cleanly naming the offending file.
- **FR-003**: The system shall provide a comment-style checker (script +
  pre-commit hook + CI job) that flags comment blocks longer than 3 lines
  and docstrings longer than 3 lines under `src/`, gated by a
  machine-generated baseline allowlist that only shrinks: new violations
  fail, stale allowlist entries fail until removed, and the checker prints
  the remaining violation count on success.
- **FR-004**: The system shall expose the checker's violation count and the
  audit-status counts in machine-readable form (`--json`) consumable by CI
  and `cairn system report`.

## Quality attributes
- **NFR-001**: Security — not applicable: no secrets, auth, or untrusted
  input are touched.
- **NFR-002**: Privacy — not applicable: no data leaves the repo.
- **NFR-003**: Performance — applicable: the checker and audit-status shall
  each complete in under 5 s on this repo.
- **NFR-004**: Reliability — applicable: the checker shall be deterministic
  (same tree, same result) and its baseline regeneration idempotent.
- **NFR-005**: Observability — applicable: both tools shall expose machine-readable
  `--json` output (per FR-004).
- **NFR-006**: Accessibility — not applicable: no UI surface is touched.

## Scope
**In**: artifact relocation + link updates; audit-status tool + status
sidecar format; comment checker + generated baseline + pre-commit hook + CI
job; Makefile targets; docs updates.

**Out (deferred)**: the ≥50% comment sweep and decision-content migration to
`record_memory` (chained follow-up spec `comment-sweep`); checker coverage
outside `src/` (tests, benchmarks, pyproject); executing the remaining audit
P1 fixes (tracked by the sidecar, not this spec); LSP/SCIP precision work
and the parser conformance matrix (separate follow-ups).

## Assumptions & risks
- Assumption: finding ids in the audit (namespaces C/Q/S/SY per survey) are
  stable keys for the status sidecar.
- Risk: the checker's block detection heuristics misclassify legitimate
  content — mitigation: golden tests over real snippets from this repo plus
  the shrink-only escape (a false positive is removed from the baseline,
  not suppressed in code).
- Risk: ratchet viewed as gate-over-known-violations — mitigation: the
  chained `comment-sweep` spec is registered at delivery and the baseline
  count is printed on every run.
