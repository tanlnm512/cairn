# Spec: on-demand-paths

**Status**: active
**Effort**: standard
**Created**: 2026-10-04
**Branch**: `feat/on-demand-paths`

## What
A general "how are these two symbols connected" query over the live graph:
`cairn path --from <ref> --to <ref>` and an MCP `path` tool return shortest
structural paths (calls / extends / implements) between symbols, walked on
demand over the `edges` table at query time. Alongside it, the precomputed
`transitive_edges` closure is demoted from built-by-default to opt-in
(`cairn build --with-closure`), with every existing closure reader staying on
its verified live-graph fallback.

## Why
The closure is the build's largest derived-index cost and its only semantic
consumer is `impact_analysis` index mode (shortest depths at depth ≤ 3);
every other reader already falls back to the live graph (pack centrality,
skillgen ranking, incremental maintenance probes). On-demand traversal
answers connectivity questions with no precompute step at all, making builds
faster and stores smaller by default while keeping the closure available —
byte-identical — for users who want the materialized index.

## Business value
Agents gain a first-class connectivity primitive ("trace how auth reaches the
DB layer") without depending on a materialized index; default builds skip the
most expensive derived work; the multi-hop contract survives on verified
fallbacks instead of a table. Success: path queries answer deterministically
on the scaling corpus, and default `cairn build`/`cairn update` complete with
zero closure work while impact callers see no functional regression.

## User stories
### US1 — Path query (P1)
As an agent, I want the shortest structural path between two symbols, so that
I can explain how components connect.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a store without the closure, When `cairn path --from A --to B`
  runs, Then the shortest exact-resolution path prints as an ordered hop
  chain with file:line per hop.
- AC2: Given symbols with no connecting path within the depth bound, When the
  query runs, Then it reports "no path within N hops" and exits 0.

### US2 — MCP parity (P1)
As an MCP client, I want the same connectivity answer over any transport, so
that CLI and tool consumers agree.

**Acceptance criteria**:
- AC1: Given the server running over stdio, SSE, or HTTP, When the `path`
  tool is invoked, Then it returns the same chain the CLI prints for the
  same inputs.

### US3 — Closure opt-in (P2)
As a user indexing a large repo, I want `cairn build` to skip the closure by
default so builds are faster, with a flag to restore it.

**Acceptance criteria**:
- AC1: Given a default `cairn build`, When it completes, Then
  `transitive_edges` has no rows and a following `cairn update` performs no
  closure maintenance.
- AC2: Given `cairn build --with-closure`, When it completes, Then the
  closure row set is identical to the current (default-on) output.

### US4 — Impact contract preserved (P1)
As a consumer of impact analysis, I want multi-hop depth reporting to keep
its current meaning on closure-free stores, so that demotion is not a silent
behavior change.

**Acceptance criteria**:
- AC1: Given a closure-free store, When `impact_analysis` runs at depth ≤ 3,
  Then depth numbers are shortest-path depths (FR-005), matching the
  closure-era output for the same graph.

## Requirements
- **FR-001**: The system shall provide a `cairn path --from <ref> --to <ref>`
  command returning shortest structural paths over the live `edges` table.
  Both endpoints accept name or qualified-name patterns under the same
  matching rules as `cairn taint`; the system shall return one shortest path
  per resolved (from, to) pair, with the number of printed paths capped.
- **FR-002**: The system shall expose an MCP tool `path` answering
  identically to the CLI, with the tool-count pins updated.
- **FR-003**: The path walk shall traverse structural edges with exact
  resolution by default; WHEN `--fuzzy` is set, the system shall include
  ambiguous/unresolved hops under the same rules as taint's fuzzy mode.
- **FR-004**: `cairn build` and `cairn import-scip` shall skip closure
  materialization by default; WHERE `--with-closure` is passed, the system
  shall produce a closure byte-identical to the current default-on output
  (the D-007 row-set contract is preserved, not redesigned).
- **FR-005**: WHEN the closure is absent, `impact_analysis` shall preserve
  shortest-path depth semantics: the live DFS shall track the minimum
  distance per reached symbol (min-depth tracking), so multi-hop depth
  numbers match the closure-era output.
- **FR-006**: `cairn update` shall skip closure maintenance when the closure
  is absent, and shall maintain it incrementally when present (existing
  behavior, pinned by test).
- **FR-007**: Path results shall be deterministic for an unchanged graph
  (same inputs → same output order across runs).

## Quality attributes
- **NFR-001**: Security — not applicable: no new network or auth surface; the
  path tool rides the existing server transports.
- **NFR-002**: Privacy — not applicable: no new data leaves the store.
- **NFR-003**: Performance — applicable: WHEN a max-depth-4 path query runs
  on the 1000-file scaling corpus, the system shall answer within 2s wall.
- **NFR-004**: Reliability — not applicable: no new persistence or recovery
  contract; the closure remains optional data, never required state.
- **NFR-005**: Observability — not applicable: CLI output plus existing
  telemetry suffice.
- **NFR-006**: Accessibility — not applicable: no user-facing UI surface is
  touched (CLI text only in this spec).

## Scope
**In**: `cairn path` CLI + MCP `path` tool sharing one BFS core with taint;
closure default-off with `--with-closure` opt-in on build/import-scip; the
FR-005 depth-contract resolution and its test updates; docs for the new
command and the demotion.
**Out (deferred)**: deleting `build_transitive_closure` or its budget gate
(both stay); weighted or semantic pathing; cross-repo federated paths;
all-simple-paths enumeration beyond the FR-001 resolution; HTTP transport
(its own spec); community-aware pathing (symbol-communities spec).

## Assumptions & risks
- Assumption: one shared BFS core can serve both taint and the new path
  query without changing taint behavior (existing taint tests must stay
  green untouched).
- Risk: closure demotion changes characteristics the traversal-parity tests
  pin — mitigation: the FR-005 resolution is implemented together with
  explicit pin updates, never silently.
- Risk: demotion contradicts the letter of scip-indexing-v2 D-007 (closure
  kept as default-built) — mitigation: record a superseding D-### citing
  the measured cost and the verified fallback coverage; the closure stays
  available and byte-identical under the flag.
- Risk: fuzzy paths on large fan-out graphs explode — mitigation: the depth
  bound caps traversal; result caps from the FR-001 resolution bound output.
