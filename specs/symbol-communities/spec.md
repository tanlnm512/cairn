# Spec: symbol-communities

**Status**: approved
**Effort**: large
**Created**: 2026-10-04
**Branch**: `feat/symbol-communities`

## What
Subsystem-level structure over the symbol graph: `cairn communities` runs
Louvain community detection (networkx, under a new `[graph-analytics]`
extra) over structural edges and persists the partition plus per-community
hubs ("god nodes") into derived tables. The partition surfaces in three
places: the CLI summary, a new deterministic "Subsystems" section in compass
guides, and a dashboard `/communities` view.

## Why
Cairn navigates hop-by-hop but has no subsystem or centrality surface —
nothing answers "what are this codebase's major clusters and which symbols
dominate them" without an agent walking the graph manually. Community
detection gives orientation views for unfamiliar codebases and a natural
substrate for later consumers (PR merge-order risk in the pr-tooling spec).

## Business value
Agents and users orient faster on unfamiliar repos; subsystem views become a
first-class query surface in CLI, compass, and dashboard. Success: on any
built store, `cairn communities` produces a deterministic partition in
seconds; compass and dashboard render it; pr-tooling can consume the tables
without re-deriving.

## User stories
### US1 — CLI compute and show (P1)
As a user, I want one command that computes and shows subsystem clusters and
their hub symbols, so that I can see the codebase's shape.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a store with structural edges, When `cairn communities` runs,
  Then the derived tables are populated and the summary prints community
  count, sizes, and top hubs.
- AC2: Given an unchanged graph, When the command re-runs, Then the
  partition and printed output are identical (seeded, deterministic).

### US2 — Compass integration (P2)
As an agent reading a compass guide, I want a Subsystems section naming the
major clusters and their hubs, so that orientation comes free with the
navigation guide.

**Acceptance criteria**:
- AC1: Given community data exists, When a compass guide generates
  deterministically, Then it carries a "Subsystems" section listing
  communities with representative hub symbols.
- AC2: Given no community data, When a compass generates, Then the section
  is absent and generation succeeds unchanged.

### US3 — Dashboard view (P2)
As a dashboard user, I want a communities view with drill-down to member
symbols, so that I can explore subsystems visually.

**Acceptance criteria**:
- AC1: Given the dashboard running, When `/communities` is opened, Then it
  renders communities, sizes, hubs, and member drill-downs from the derived
  tables.
- AC2: Given the nav inventory, When any view-inventory or accessibility
  test runs, Then it passes with the new view registered exactly once.

### US4 — Optional extra discipline (P1)
As a user without the analytics extra, I want clear behavior instead of a
crash, so that core installs stay unaffected.

**Acceptance criteria**:
- AC1: Given `[graph-analytics]` is not installed, When `cairn communities`
  runs, Then it fails with an actionable install hint and the store is
  unchanged.

## Requirements
- **FR-001**: The system shall provide a `cairn communities` command that
  computes Louvain communities over the symbol graph and persists them to
  derived tables `communities` and `symbol_communities` (additive schema,
  full refresh on each run).
- **FR-002**: The clustering input shall be structural edge kinds only
  (`calls`/`call`/`extends`/`implements`, the closure-traversed set; taint
  uses its `calls` subset), kind-weighted; imports and references are
  excluded.
- **FR-003**: The system shall identify hub symbols ("god nodes") by highest
  combined structural degree, reported globally and per community, top-K
  with K defaulting to 10.
- **FR-004**: WHERE networkx is not importable in the running environment,
  the system shall fail with an actionable install hint (installing the
  `[graph-analytics]` extra) and leave store state unchanged.
- **FR-005**: The compass deterministic body shall include a "Subsystems"
  section when community data exists and omit it otherwise; the critic
  section vocabulary shall recognize the new section.
- **FR-006**: The dashboard shall expose a `/communities` view registered in
  the nav inventory, with member drill-down; all view-inventory and
  accessibility pins shall be updated to the new count.
- **FR-007**: Community results shall be deterministic for an unchanged
  graph (fixed Louvain seed; same partition, labels, and ordering across
  runs).
- **FR-008**: Community labels shall be deterministic and LLM-free (derived
  from member/edge facts, e.g. top hub or dominant file paths).

## Quality attributes
- **NFR-001**: Security — not applicable: no new network or auth surface.
- **NFR-002**: Privacy — not applicable: no new data leaves the store.
- **NFR-003**: Performance — applicable: WHEN `cairn communities` runs on
  the 1000-file scaling corpus, the system shall complete within 10s wall.
- **NFR-004**: Reliability — not applicable: derived tables are always
  rebuildable; absence degrades to omitted sections/views, never errors on
  read paths.
- **NFR-005**: Observability — not applicable: existing telemetry covers CLI
  runs.
- **NFR-006**: Accessibility — applicable: the new dashboard view shall pass
  the same accessibility inventory the existing views are crawled against.

## Scope
**In**: `[graph-analytics]` extra (networkx); communities module + additive
derived tables; `cairn communities` CLI; compass Subsystems section +
critic vocab extension; dashboard `/communities` view + nav/icon +
view-inventory pin updates; determinism seeding; docs.
**Out (deferred)**: Leiden/igraph; LLM-generated community labels;
auto-refresh riding `cairn update` (explicit command only in this spec);
community-aware pathing (on-demand-paths spec); PR merge-order consumption
(pr-tooling spec, which depends on this one); any change to embeddings or
the closure.

## Assumptions & risks
- Assumption: networkx `louvain_communities` with a fixed seed is
  deterministic enough for the FR-007 pin (verified by a double-run test).
- Risk: partition quality varies with edge density and kind weighting —
  mitigation: fixed kind weighting documented in tech-spec, deterministic
  tests on a fixture graph rather than quality judgments.
- Risk: the dashboard view touches the widest pin surface in the repo
  (nav/palette/restyle/accessibility inventories) — mitigation: enumerate
  every pin in the survey and update them in one task.
- Risk: a new dependency in an extra still lands in the lockfile —
  mitigation: networkx is pure-Python, already transitive under
  [semantic]; core install remains untouched.
