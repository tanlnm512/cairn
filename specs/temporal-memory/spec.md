# Spec: temporal-memory

**Status**: draft
**Created**: 2026-09-16
**Branch**: `feat/temporal-memory`

## What
Bi-temporal memory semantics: every memory carries `valid_from` and
`valid_until`; `recall_memory --as-of <date>` answers point-in-time queries;
builds auto-invalidate memories whose referenced symbols were renamed or
removed (linking successors) instead of deleting them; the timeline command
shows a symbol's memory history.

## Why
Zep's bi-temporal model leads agent-memory benchmarks; temporal reasoning —
what was true when a decision was made — matters in codebases where symbols
get renamed and refactored. Cairn's lifecycle machinery stops at tier
movement: promote/demote/decay re-tier memories by score and age, and
`cairn validate-paths` (via the compass critic) already detects stale
file/symbol references and can mark concepts stale — but staleness is a
boolean flag with no validity interval, no successor link, and no way to
ask what was true before the flag was set. Recall itself already recomputes
reference liveness per query, so the system knows a memory cites dead
symbols; it just forgets when that became true. Adding `valid_from`/
`valid_until` turns that boolean into an interval and makes the existing
stale detector the write-time trigger for auto-invalidation.

## Business value
Decisions stay auditable across refactorings: users can answer "what did we
believe about this symbol at ship time." Success: a renamed symbol's old
memories expire automatically with successor links; as-of recall returns
exactly the memories valid at that date; default recall unchanged.

## User stories
### US1 — Point-in-time recall (P1)
As an archaeologist of past decisions, I want memories as of a date.

**Acceptance criteria** (each trace to an FR below):
- AC1: Given memories with overlapping validity, When
  `recall_memory --as-of <date>` runs, Then only memories valid at that date
  return (FR-002).

### US2 — Auto-invalidation (P1)
As a user, I want rename/refactor to expire stale memories automatically,
not delete them.

**Acceptance criteria**:
- AC1: Given a memory referencing a symbol that a build detects renamed or
  removed, When the build completes, Then the memory's `valid_until` is set
  and a successor link is recorded where one exists (FR-003).

## Requirements
- **FR-001**: The memory store shall carry `valid_from` (default: creation
  time) and `valid_until` (nullable) on every memory record, added
  additively with a migration for existing rows.
- **FR-002**: Recall surfaces (MCP `recall_memory`, CLI `cairn memory
  search`) shall support `--as-of <date>` returning only memories valid at
  that date; default recall shall return only currently-valid memories.
- **FR-003**: WHEN a build detects a memory's referenced symbol renamed or
  removed, the system shall set `valid_until` to the build time rather than
  deleting, and record a successor-symbol link where the graph identifies
  one — extending the existing stale-reference detection
  (`cairn validate-paths`), which already marks concepts whose file/symbol
  references no longer resolve, not building a parallel detector.
- **FR-004**: The system shall provide `cairn memory timeline <symbol>`
  showing the temporal history of memories for that symbol.
- **FR-005**: Temporal queries shall not regress recall latency measurably
  (indexed columns; benchmark before/after recorded in the tech spec).

## Scope
**In**: schema migration, as-of recall, build-time auto-invalidation with
successor links, timeline command, latency benchmark, tests.
**Out (deferred)**: bitemporal transaction-time dimension (only valid-time
here); memory branching/merging; retroactive edits; cross-workspace
timeline.

## Assumptions & risks
- Assumption: stale-reference detection exists (`cairn validate-paths`
  marks unresolved references); successor-symbol linking is new work — the
  incremental builder carries no rename signals today, so links form only
  where the graph can identify a unique renamed successor; where ambiguous,
  no link is recorded.
- Risk: schema migration on existing stores — mitigation: additive columns
  with defaults; migration tested against a fixture store from the prior
  schema version.
