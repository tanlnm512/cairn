# Spec: ui-dashboard-workspace-launcher

**Status**: done
**Created**: 2026-08-20
**Branch**: `docs/dashboard-v2-specs`

## What
A workspaces overview for the dashboard: it lists every cairn store on the
machine (workspace, store size, last-indexed time, recorded tool-call
count, index health at a glance) and opens any of them without restarting
the server. The dashboard becomes the front door to all local cairn data,
not one workspace's.

## Why
cairn keeps one store per workspace — hash-keyed directories under
CAIRN_HOME, registered in `workspaces.json` — and the registry grows by
one every time `cairn init` runs anywhere on the machine. The dashboard
binds to exactly one store per launch (`cairn dashboard --db ...`); seeing
another workspace means relaunching with a different path. `cairn config
--list` can name every registered workspace, but only as path + key — no
size, freshness, or usage. A launcher turns switching into a click and
gives a machine-wide overview with stats that doesn't exist today.

## Business value
- One URL answers "what cairn knows across everything on this machine":
  which workspaces are indexed, stale, or empty.
- Success criteria:
  - **SC-1**: the overview lists every local store with size,
    last-indexed time, and tool-call count, rendering in under 2 seconds
    for 200+ stores.
  - **SC-2**: switching to a workspace serves its full dashboard without a
    server restart.

## User stories
### US1 — See everything (P1)
As a cairn owner with many workspaces, I want one overview of all local
stores, so that I can see at a glance what's indexed, stale, or unused.

**Acceptance criteria**:
- AC1: Given multiple local stores, When I open the overview, Then each is
  listed with workspace identity, store size, last-indexed time, and
  recorded tool-call count.
- AC2: Given stores in different states (populated, empty, unreadable),
  When the overview renders, Then each is presented with its state and
  nothing crashes.

### US2 — Switch without restarting (P1)
As a viewer, I want to open a workspace's dashboard from the overview, so
that comparing workspaces doesn't mean relaunching anything.

**Acceptance criteria**:
- AC1: Given the overview, When I select a workspace, Then the dashboard's
  views serve that workspace's data.
- AC2: Given a selected workspace, When I return to the overview, Then I
  can pick a different one.

### US3 — Keep it read-only (P1)
As the owner, I want the launcher to never write to any store, so that
browsing workspaces has zero side effects.

**Acceptance criteria**:
- AC1: Given any launcher interaction, When it completes, Then every
  visited store's content is byte-identical to before.

## Requirements
- **FR-001**: The dashboard SHALL provide a workspaces overview listing
  every local cairn store with workspace identity, store size,
  last-indexed time, and recorded tool-call count.
- **FR-002**: Stores that are empty, missing, or unreadable SHALL be listed
  with their state rather than omitted or crashing the overview.
- **FR-003**: Selecting a workspace from the overview SHALL serve the
  dashboard's views against that workspace's store without a server
  restart.
- **FR-004**: The launcher SHALL open every store read-only — no writes to
  any store, visited or listed (standing guard).
- **FR-005**: The overview SHALL render completely within 2 seconds on a
  machine with 200+ stores. 200+ is a headroom scenario grounded in
  observed reality — a test-suite leak once accumulated 227 bogus
  registrations in ~8 days before a manual cleanup left 2 real
  workspaces — so the budget is proven against synthesized stores in
  tests, not against the current registry.

## Scope
**In**: overview discovery and listing; in-server workspace switching;
read-only discipline; overview render budget.
**Out (deferred)**: cross-workspace aggregation (combined token totals,
merged histories); remote/networked stores; creating or deleting
workspaces; per-workspace drill-down beyond opening its dashboard.

## Assumptions & risks
- Assumption: "every local store" is enumerable from cairn's existing
  layout — the `workspaces.json` registry maps workspace path → store
  key, and stores are hash-keyed directories under CAIRN_HOME; no new
  registry is invented. The two sources are reconciled: a store directory
  without a registry entry, or a registered path whose store is gone, is
  exactly the FR-002 "listed with its state" case.
- Assumption: workspace identity can be shown as the workspace's path or
  name as cairn already records it (the registry keys on the absolute
  workspace path).
- Risk: many store probes (size + last-index + count) could exceed the
  render budget if done naively per store — mitigation: FR-005 forces a
  budgeted probe strategy (batched or cached with explicit refresh).
- Risk: concurrent writes to a listed store while probing — read-only
  opens must not lock or complain (the existing read-only discipline
  covers this; the guard test extends to listed stores).
- Risk: stale registry entries are a proven shape, not hypothetical (the
  dev machine's registry accumulated 227 test-leaked registrations before
  a manual cleanup) — the overview treats registered-but-gone paths and
  orphan store dirs as normal FR-002 states, and listing them never
  writes or re-registers anything (FR-004).
