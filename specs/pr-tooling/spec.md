# Spec: pr-tooling

**Status**: approved
**Effort**: standard
**Created**: 2026-10-05
**Branch**: `feat/pr-tooling`

## What
`cairn prs` — one read-only command that fuses GitHub PR state with cairn's
graph: open PRs with CI and review status, per-PR graph impact (the PR's
changed files resolved to symbols and their blast radius), and merge-order
risk ranking from subsystem overlap between in-flight PRs, using the
community tables from the symbol-communities spec. `--json` emits the full
payload for agents. The command never creates, reviews, or merges anything.

## Why
PR triage today means bouncing between `gh` and the repo: which PRs are
green, what does each diff actually touch, which PRs will collide when
merged. Cairn holds the one thing gh doesn't — the verified symbol graph —
and (with communities) the subsystem partition that makes "these two PRs
will fight" a computable question instead of a reviewer's hunch.

## Business value
Reviewers and agents triage an in-flight batch in one command; merge-order
risk becomes evidence-based. Success: on a repo with ≥2 open PRs, `cairn
prs` answers "what's open, what's safe to merge first, what will conflict"
in one invocation with no store writes and no gh mutations.

## User stories
### US1 — PR list (P1)
As a reviewer, I want open PRs with CI and review state in one table, so
that triage starts in the terminal.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given gh authenticated and ≥1 open PR, When `cairn prs` runs, Then
  each PR prints number, title, branch, author, CI state, and review
  decision.
- AC2: Given gh missing or unauthenticated, When `cairn prs` runs, Then it
  fails cleanly with guidance and no partial table.

### US2 — Graph impact per PR (P1)
As a reviewer, I want each PR's diff resolved to symbols and caller counts,
so that review attention goes where the blast radius is.

**Acceptance criteria**:
- AC1: Given `cairn prs --impact PR#`, When it runs, Then the PR's changed
  files resolve to changed symbols and each prints with its caller count
  from the live graph.
- AC2: Given a changed file with no indexed symbols, When impact runs, Then
  it is listed as unindexed rather than silently dropped.

### US3 — Merge-order risk (P2)
As a maintainer, I want PR pairs ranked by shared subsystems, so that
merge order minimizes rebase pain.

**Acceptance criteria**:
- AC1: Given two PRs touching symbols in the same community, When `cairn
  prs --conflicts` runs, Then the pair is reported with the shared
  community named.
- AC2: Given no community data, When `--conflicts` runs, Then the section
  is omitted with a hint to run `cairn communities` (no failure).

### US4 — Agent payload (P3)
As an agent, I want the full report as JSON, so that I can consume it
without parsing tables.

**Acceptance criteria**:
- AC1: Given `--json`, When `cairn prs` runs, Then the payload carries the
  list, impact (when requested), and conflicts (when requested) sections.

## Requirements
- **FR-001**: The system shall provide `cairn prs` listing open PRs (number,
  title, branch, author, CI state, review decision) sourced from the gh CLI
  over the workspace's remote.
- **FR-002**: WHEN gh is absent, unauthenticated, or errors, the system
  shall fail cleanly with actionable guidance and produce no partial
  output.
- **FR-003**: The system shall provide `cairn prs --impact PR|branch`
  computing graph impact from the PR's changed files (diff vs the PR base),
  reusing the existing review-pack diff machinery and impact analysis, and
  listing unindexed changed files explicitly.
- **FR-004**: The system shall provide `cairn prs --conflicts` ranking PR
  pairs by shared-community overlap of their touched symbols; WHERE
  community tables are absent or empty, the system shall omit the section
  with a hint (graceful degradation — the symbol-communities spec's tables
  are consumed, never built, here).
- **FR-005**: The system shall support `--json` emitting the full report
  payload.
- **FR-006**: The command shall be strictly read-only: no store writes and
  no gh mutations (list/view calls only).
- **FR-007**: Impact output shall state which store it reflects (the local
  workspace's index and its build age), never implying branch-fresh data.

## Quality attributes
- **NFR-001**: Security — applicable: the system shall invoke gh only as a
  subprocess with list/view arguments; no tokens are read, logged, or
  passed beyond gh's own configuration.
- **NFR-002**: Privacy — not applicable: PR metadata goes to gh, not beyond.
- **NFR-003**: Performance — applicable: WHEN impact runs for a PR, the
  system shall reuse the impact depth caps (no unbounded traversal) and
  complete within the review-pack's existing per-invocation cost profile.
- **NFR-004**: Reliability — applicable: gh subprocess failures (network,
  auth, rate limit) shall surface as clean errors with gh's message.
- **NFR-005**: Observability — not applicable: command output suffices.
- **NFR-006**: Accessibility — not applicable: no UI surface is touched.

## Scope
**In**: `cairn prs` command (list, `--impact`, `--conflicts`, `--json`);
gh subprocess integration with defensive parsing; reuse of the review-pack
diff → symbols machinery; community-overlap ranking; docs.
**Out (deferred)**: creating/reviewing/merging PRs; watch mode; cross-repo
PR federation; building communities on the fly (that is symbol-communities'
job); CI-log ingestion beyond status.

## Assumptions & risks
- Assumption: gh is the only supported GitHub surface (repo precedent: gh
  CLI already assumed by project workflows); no direct API client is added.
- Risk: gh's JSON output shape evolves — mitigation: parse defensively
  against pinned fixture snapshots; unknown fields ignored, missing fields
  error with the gh version in the message.
- Risk: the local store may lag a PR branch (impact reflects the local
  index, not a fresh build of the PR) — mitigation: FR-007 makes the store
  age explicit in output; stale-store impact is advisory, labeled as such.
- Dependency: `--conflicts` consumes the `communities`/`symbol_communities`
  tables defined in specs/symbol-communities (FR-001 there); this spec
  never builds them.
