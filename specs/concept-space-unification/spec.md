# Spec: concept-space-unification

**Status**: draft
**Effort**: large
**Created**: 2026-10-05
**Branch**: TBD

## What
Merge cairn's two content planes — the symbol graph (code) and the
knowledge-doc layer (docs, compass, wiki, memory) — into one homogeneous
concept space where a function, a doc paragraph, a wiki claim, and a memory
are nodes in the same queryable graph, following graphify's
one-graph-across-artifacts model while keeping cairn's verification
machinery.

## Why
Today the planes are joined by edges but queried separately: explore
returns symbols plus memory hints; knowledge_search returns docs. Questions
that span both ("which docs discuss this function's constraint?", "which
code implements the claim this ADR makes?") require the agent to bridge
manually. This is the architecture-level change the steal-campaign
deliberately parked rather than rushing.

## Business value
Cross-artifact questions become structural queries; "surprising
connections" (code↔doc↔memory links) surface without agent orchestration.
This draft is parked pending the decisions below.

## User stories
### US1 — TBD
Parked: stories are authored only after the clarify pass below resolves.

## Requirements
- **FR-001**: TBD. [NEEDS CLARIFICATION: unification model — promote
  knowledge-doc concepts into the graph schema as first-class nodes, or
  build a federated join layer over the two existing stores?]
- **FR-002**: TBD. [NEEDS CLARIFICATION: does unification change the
  verification contract (refs-verified fractions over mixed node types,
  critic gates for promoted code-adjacent claims)?]
- **FR-003**: TBD. [NEEDS CLARIFICATION: which existing surfaces migrate —
  explore, ask_compass, semantic_search — and does the 25-tool MCP surface
  change?]

## Quality attributes
TBD pending clarify.

## Scope
**In**: TBD post-clarify. **Out (deferred)**: corpus-breadth artifact types
(that spec's nodes ride this one's model); any Postgres/cloud backend
(de-scoped permanently).

## Assumptions & risks
- Risk: the biggest architectural change in cairn's history collides with
  every consumer of both planes (24+ MCP tools, dashboard, compass, wiki,
  memory) — mitigation: full waves + reviewer + staged rollout behind a
  query flag; this is exactly why it is parked as a draft.
- Risk: homogeneous nodes dilute the precision of code-only queries —
  mitigation: node-kind filtering stays first-class; unification is
  additive, not a replacement.
