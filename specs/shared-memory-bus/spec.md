# Spec: shared-memory-bus

**Status**: draft
**Created**: 2026-09-16
**Branch**: `feat/shared-memory-bus`

## What
Multi-agent coordination over cairn's store: agents working the same
codebase share learnings (`cairn memory share`), get overlap warnings before
editing symbols another agent recently touched or recorded memory for, and
surface newly-shared entries on their next relevant recall — without
changing single-agent behavior.

## Why
Multi-agent development (sub-agents, forked threads) is common; agents
duplicate learnings and collide on overlapping edits. Cairn already holds
one store with symbol-keyed memory — it needs only the coordination surface.

The concurrency substrate is real and battle-tested: the store runs in WAL
journal mode with a busy timeout on every connection, and the shared SSE
daemon exists precisely because per-client stdio servers contended for the
database lock.
Session-scoped activity is already recorded — memory reference tracking is
session-keyed (`memory_refs`), MCP tool usage lands in `tool_metrics`, and
the telemetry sink emits named events with session ids — so "who touched
what recently" is a query over existing tables, not new instrumentation.
What is missing is agent identity, a sharing visibility rule, and the
overlap check itself.

## Business value
Parallel agents stop re-deriving each other's findings and stop colliding on
the same symbols. Success: two concurrent agents in a fixture workspace —
one records a learning, the other's recall surfaces it; an attempted edit
overlap produces a warning naming the other agent and symbols.

## User stories
### US1 — Share a learning (P1)
As an agent that just learned something, I want to broadcast it to agents
working on overlapping code.

**Acceptance criteria** (each trace to an FR below):
- AC1: Given a memory and symbol list, When `cairn memory share` runs, Then
  the memory becomes visible to other agents' recall for those symbols (FR-001).

### US2 — Overlap warning (P1)
As an agent about to edit, I want to know another agent recently touched or
recorded memory for the same symbols.

**Acceptance criteria**:
- AC1: Given another agent's recent memory/edit activity on symbols in my
  intended edit set, When I check, Then a warning names the agent and
  overlapping symbols (FR-002).

### US3 — Single-agent unchanged (P1)
As a single-agent user, I want zero behavior change.

**Acceptance criteria**:
- AC1: Given no sharing configured, When memory operations run, Then behavior
  matches today's (FR-004).

## Requirements
- **FR-001**: The system shall provide `cairn memory share --agent <id>
  --symbols a,b,c` making the shared memory visible to other agents'
  recall for those symbols.
- **FR-002**: The system shall provide overlap detection: before an agent
  edits a symbol set, a check against other agents' recent memory/edit
  activity on those symbols produces a warning naming agent and overlap.
- **FR-003**: Shared memories shall surface in other agents' recall
  seamlessly on next relevant query (no polling command required for
  correctness; a subscription/notification mechanism is optional).
- **FR-004**: WHERE sharing is unused, all existing single-agent memory
  behavior shall remain byte-identical.
- **FR-005**: Concurrent access shall be safe: reuse the store's existing
  WAL journal mode plus explicit locking; per-agent memory namespaces with
  read-through sharing.
- **FR-006**: Agent identity shall use caller-supplied agent ids, validated
  at the share/check surface (non-empty, length-capped, charset-sanitized);
  MCP session ids are process-scoped and a registration command adds a step
  without adding trust (ruled 2026-09-17).

## Scope
**In**: share command, overlap detection, recall integration, concurrency
safety, tests with two concurrent simulated agents.
**Out (deferred)**: MCP-resource subscriptions (push notifications); conflict
auto-resolution; agent presence/heartbeat; cross-workspace sharing.

## Assumptions & risks
- Assumption: agents on one codebase share one cairn store path (the
  multi-agent premise).
- Risk: race conditions under concurrency — mitigation: WAL + locking, tests
  exercising concurrent writers.
- Risk: identity spoofing in shared mode — mitigation: FR-006 id validation
  plus opt-in sharing per workspace.
