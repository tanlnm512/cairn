# Spec: comment-sweep

**Status**: done
**Effort**: standard
**Created**: 2026-10-07
**Branch**: `feat/comment-sweep`

## What
A mechanical sweep of `src/` that halves the comment-style baseline (1213 →
≤606 grandfathered violations) by trimming comment blocks and docstrings to
the AGENTS.md contract, migrating every durable rationale block into
`cairn memory` records or `docs/` before deletion, and landing the parked
runpy double-import fix from the ratchet spec's D-012.

## Why
The shrink-only ratchet landed in PR #149 enforces the style contract but
ships over a known-violating baseline: 1213 grandfathered blocks and
docstrings across `src/`. A gate over an unshrunk baseline is debt with a
counter; the contract is only real once the debt is paid down. The ratchet
guarantees monotonic progress, but nothing currently drives the number
down. D-012 also parked the cosmetic-but-noisy runpy import warning on
this spec as its natural vehicle.

## Business value
`make comment-style` reports ≤606; the style contract holds over a
half-paid debt with a visible downward trend; durable engineering
rationale moves from rotting prose into the memory layer that agents and
maintainers actually query.

## User stories
### US1 — Halved baseline (P1)
As a maintainer, I want the grandfathered violation count halved, so the
style contract reflects real code, not a warehouse of exceptions.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given the landed ratchet, When the sweep lands, Then `make
  comment-style` reports ≤606 remaining and exits 0.
- AC2: Given any intermediate step of the sweep, When the full suite runs,
  Then it is green (no behavioral change rides the sweep).

### US2 — Rationale preserved, not deleted (P1)
As an agent consumer, I want durable "why" content migrated into memory or
docs before its prose is removed, so knowledge survives the trim.

**Acceptance criteria**:
- AC1: Given a removed block that states a non-obvious constraint or
  decision, When the sweep processes it, Then a `record_memory` entry
  (decision/pattern/mistake as fitting) or a docs landing exists and is
  listed in the delivery record.
- AC2: Given a removed block that only narrates history or the obvious,
  When the sweep processes it, Then it is deleted with no migration.

### US3 — Quieter module entry points (P2)
As a gate consumer, I want the `python -m cairn.cli.system.*` invocations
free of the runpy double-import warning, so make/hook/CI output is clean.

**Acceptance criteria**:
- AC1: Given `make audit-status` or `make comment-style`, When either
  runs, Then stderr carries no runpy RuntimeWarning.

## Requirements
- **FR-001**: The system shall reduce the `src/` comment-style baseline by
  at least 50% (1213 → ≤606 entries) through comment-block and docstring
  trims that change no executable semantics.
- **FR-002**: The sweep shall migrate durable rationale content to
  `cairn memory record` entries or `docs/` before deletion; WHERE a block
  states only history or the obvious, it shall be deleted without
  migration; the delivery record shall list every migration.
- **FR-003**: The sweep shall keep the full test suite green at every
  intermediate step and shall touch no file outside `src/`, `docs/`,
  memory records, and the import-time wiring named in FR-004.
- **FR-004**: The sweep shall land the ratchet D-012 fix: the
  `cairn.cli.system` package's eager `report` import chain shall stop
  double-importing the executable modules under `python -m`, removing the
  runpy RuntimeWarning from every gate invocation.
- **FR-005**: The sweep shall re-derive the shrink-only baseline after the
  trims via the documented regeneration entry point, and the remaining
  count reported by `make comment-style` shall be ≤606.

## Quality attributes
- **NFR-001**: Security — not applicable: no secrets, auth, or untrusted
  input are touched.
- **NFR-002**: Privacy — not applicable: memory records stay local.
- **NFR-003**: Performance — not applicable: comment-only changes; the
  checker's budget is already gated.
- **NFR-004**: Reliability — applicable: the suite shall be green after
  every intermediate step; any test that pinned removed prose text shall
  be updated in the same step, never deleted to pass.
- **NFR-005**: Observability — applicable: the sweep's progress shall be
  observable as the existing `make comment-style` count, and the delivery
  record shall carry the migration list.
- **NFR-006**: Accessibility — not applicable: no UI surface.

## Scope
**In**: `src/` comment/docstring trims; rationale migration to memory/docs;
test text-pin updates forced by trims; the D-012 import-chain fix;
baseline re-derivation; CHANGELOG entry.

**Out (deferred)**: comment cleanup outside `src/` (tests, benchmarks,
pyproject prose); any behavioral change however small (rides its own
spec); the further 50% after the first halving (the ratchet carries it).

## Assumptions & risks
- Assumption: most of the 899 long docstrings collapse to one-line
  contracts without information loss; the migration step is the safety
  net for the rest.
- Risk: wide mechanical diff obscures review — mitigation: phased
  per-area commits with `--stat`-reviewable shape and suite-green gates
  between phases.
- Risk: prose-pinned tests churn — mitigation: NFR-004's same-step update
  rule; pins assert behavior, not narration.
