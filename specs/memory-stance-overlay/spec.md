# Spec: memory-stance-overlay

**Status**: approved
**Effort**: standard
**Created**: 2026-10-04
**Branch**: `feat/memory-stance-overlay`

## What
A stance overlay over cairn's codebase memory: each tribal memory can carry
an explicit stance — preferred, tentative, or contested — computed by a new
`cairn memory reflect` command from the evidence cairn already has
(refs-verification against the live graph, overlap between memories citing
the same symbols), and surfaced inline wherever memory already appears in
agent-facing output (explore's tribal-memory section and recall results).

## Why
Tribal memory accumulates across sessions and eras: two memories citing the
same symbol can contradict each other (a rule that was true before a
refactor, a workaround superseded by a fix), and today nothing distinguishes
the trustworthy one from the stale one except prose. The refs-verified
fraction already measures whether a memory's citations still exist; the
missing piece is the synthesis that turns verification + agreement into a
per-memory stance the agent can weigh at a glance.

## Business value
Agents stop acting on contradicted or stale guidance; memory review stops
requiring a human to cross-read every overlap. Success: `cairn memory
reflect` is idempotent and deterministic on an unchanged store, contested
memories are visibly flagged with their contradicting peer, and stale
memories (citations rotted by code change) downgrade to tentative without
any human pass.

## User stories
### US1 — Reflect (P1)
As a user or agent, I want one command that recomputes stances across the
store, so that memory quality is synthesized rather than guessed.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a store with two memories citing the same symbol where one's
  citations are all verified and the other's contradict it, When `cairn
  memory reflect` runs, Then the verified one is preferred and the other
  contested (referencing its peer).
- AC2: Given an unchanged store, When reflect runs twice, Then stances are
  identical (idempotent, deterministic).

### US2 — Surfacing (P1)
As an agent consuming cairn tools, I want stance visible where memories are
already surfaced, so that weighing guidance costs nothing extra.

**Acceptance criteria**:
- AC1: Given a contested memory matching a query, When explore or recall
  returns it, Then the result renders its stance inline with the
  contradicting peer reference.

### US3 — Staleness signal (P2)
As a maintainer, I want memories whose citations rotted to downgrade
automatically, so that "code changed — re-verify" is visible without a
human pass.

**Acceptance criteria**:
- AC1: Given a memory whose refs-verified fraction drops because the cited
  symbol changed, When reflect runs, Then that memory is marked tentative.

## Requirements
- **FR-001**: Memory records shall support an optional stance field
  (`preferred` | `tentative` | `contested`), unset by default, orthogonal
  to the existing lifecycle tiers (a promoted memory can be contested).
- **FR-002**: The system shall provide `cairn memory reflect` that
  recomputes stances from verified-citation agreement and contradiction
  among memories sharing symbol refs. Stance authority is dual: a stance
  may be declared explicitly at record time (a prior), and reflect
  recomputes from evidence — the evidence verdict overrides a declaration
  when they conflict, and the declaration survives when reflect produces
  no verdict.
- **FR-003**: Reflect shall modify only stance metadata — never memory
  bodies, titles, or lifecycle state — and contested stances shall record
  the contradicting peer memory's identity.
- **FR-004**: Recall and explore output shall render stance inline where
  memories are listed, with the peer reference for contested entries.
- **FR-005**: WHEN a memory's refs-verified fraction drops below its
  recorded baseline (code changed under it), reflect shall set stance to
  `tentative`.
- **FR-006**: Reflect shall be idempotent and deterministic on an unchanged
  store (same stances, same peer references, same order).

## Quality attributes
- **NFR-001**: Security — not applicable: local file metadata only, no new
  surface.
- **NFR-002**: Privacy — not applicable: no new data leaves the store.
- **NFR-003**: Performance — applicable: WHEN reflect runs on the primary
  workspace store, the system shall complete within 30s wall.
- **NFR-004**: Reliability — applicable: WHEN reflect is interrupted
  mid-run, the system shall leave memory files uncorrupted (per-file atomic
  writes).
- **NFR-005**: Observability — not applicable: the command's own summary
  output suffices.
- **NFR-006**: Accessibility — not applicable: no UI surface is touched.

## Scope
**In**: stance field in the memory model (frontmatter/row); `cairn memory
reflect`; inline stance rendering in recall/explore; staleness downgrade;
docs. **Out (deferred)**: an LLM-synthesized LESSONS.md-style aggregate
(cairn's knowledge docs remain the curated layer); auto-reflect riding
`cairn update` (explicit command only in this spec); stance propagation
into compass/wiki; editing memory bodies.

## Assumptions & risks
- Assumption: refs-verification already recomputes live per query
  (recall_memory's refs-verified fraction), so reflect can reuse the same
  machinery rather than re-deriving verification.
- Risk: contradiction detection by symbol-ref overlap alone may flag
  benign complementary memories as peers — mitigation: stance rules are
  conservative (contradiction requires disagreeing guidance signals, not
  mere co-citation; exact rule pinned in tech-spec with fixture evidence).
- Risk: reflect writing memory files could collide with concurrent agents —
  mitigation: per-file atomic writes; reflect is explicit and short-lived.
