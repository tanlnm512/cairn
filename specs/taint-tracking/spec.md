# Spec: taint-tracking

**Status**: draft
**Created**: 2026-09-16
**Branch**: `feat/taint-tracking`

## What
Security-aware blast radius: configurable taint sources (user input, env,
file reads, external responses) and sinks (SQL, shell, filesystem, network,
eval) propagated inter-procedurally over the existing call graph and
dataflow tables, queryable via `cairn taint` and surfaced as warnings in
explore/blast when changed code touches a taint path.

## Why
As agents write more code, security analysis belongs in the agent's own
context loop. Cairn's graph layer already maintains the exact substrate
taint analysis runs on: `dataflow.py` materializes a precomputed index of
within-repo impact chains for public symbols and a `transitive_edges`
closure table that answers multi-hop caller→callee reachability in one
indexed statement, and every edge carries a resolution label
(`exact`/`ambiguous`/`unresolved`) that taint propagation can gate on — the
same precision contract `impact_analysis` already exposes via precise vs
fuzzy modes. Adding labeled sources/sinks and a propagation pass over
those structures is incremental; the honest boundary is that propagation
stays call-graph-level (no intra-procedural modeling), which the spec
states explicitly.

## Business value
Agents and reviewers see which changes touch user-input-to-dangerous-sink
paths. Success: on fixture code with known CWE-pattern flows, `cairn taint`
traces each path; `explore`/`blast` flag changes intersecting taint paths;
zero false positives on a clean fixture.

## User stories
### US1 — Trace a taint path (P1)
As a security-minded reviewer, I want the path from a source to a sink.

**Acceptance criteria** (each trace to an FR below):
- AC1: Given configured sources and sinks and a fixture containing a flow,
  When `cairn taint --from <source> --to <sink>` runs, Then the full
  inter-procedural path is printed with file:symbol resolution labels (FR-001, FR-002).

### US2 — Blast warnings (P2)
As an agent editing code, I want to know my change touches a taint path.

**Acceptance criteria**:
- AC1: Given a diff intersecting a taint path, When `cairn blast`/`explore`
  runs, Then a warning badge names the path (FR-004).

## Requirements
- **FR-001**: The system shall provide configurable taint sources and sinks
  (default set covering HTTP request params, CLI args, env vars, file reads,
  external API responses as sources; SQL execution, shell, filesystem
  writes, network sends, eval/deserialization as sinks), overridable via
  workspace config.
- **FR-002**: `cairn taint --from <source-pattern> --to <sink-pattern>` shall
  trace and display inter-procedural taint paths using the call graph and
  precomputed dataflow tables, labeling each hop with its resolution label.
- **FR-003**: Taint propagation shall follow only `exact` edges by default,
  with an explicit `--fuzzy` opt-in mirroring the graph tools' precision
  contract.
- **FR-004**: `explore` and `cairn blast` shall surface a warning WHERE
  queried/changed code intersects a known taint path.
- **FR-005**: The default source/sink set shall ship generic call-name-keyed
  defaults (per FR-001) overridable via workspace config; framework-aware
  packs (Express, Django, Rails, Spring entry/sink points) are deferred
  (ruled 2026-09-17: false-positive risk over untested parser coverage
  outweighs onboarding aid).

## Scope
**In**: source/sink config, propagation pass, taint query command, explore/
blast integration, fixture tests with known CWE-pattern flows and a clean
fixture.
**Out (deferred)**: sanitizer/propagator inference; per-language deep
semantic modeling beyond call-graph propagation; SARIF export; CI security
gate mode.

## Assumptions & risks
- Assumption: call-graph-level propagation (no dataflow through assignments
  beyond existing tables) is the honest first tier; document the precision
  boundary.
- Risk: false positives erode trust — mitigation: precise-edges-only default
  (FR-003), conservative sink matching, opt-in warnings rather than hard
  failures.
