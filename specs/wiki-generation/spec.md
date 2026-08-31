# Spec: wiki-generation

**Status**: draft          <!-- draft while writing → approved at the Stage-4 user gate
                                → active once the first task spawns → done when all
                                tasks are ticked and `check.py` re-runs green -->
**Created**: 2026-08-31
**Branch**: `feat/wiki-generation`

## What
Cairn gains an agent-decoupled project-wiki generator: a deterministic catalog
planner turns the code graph into a page outline (overview + one page per
top module), one writing task is queued per page for any AI agent to claim,
and each completed page is fact-checked by the deterministic critic before it
is promoted into the knowledge base as a wiki article with a verified Sources
footer. The user drives it from `cairn wiki generate --llm`, the
`wiki_generate` MCP tool, and a dashboard wiki tab; a manifest makes
regeneration incremental and failed pages retryable.

## Why
Today `cairn wiki generate` emits a single deterministic stats page per repo —
accurate but thin, and it never gets LLM-quality prose. AI coding agents
meanwhile have no wiki-writing workflow in cairn: the task queue has compass
and flow kinds but the bare `wiki` kind has no output spec, no promotion
branch, and no orchestration. ZCode (the harness this repo is developed in)
ships a "Generate Project wiki" feature whose shape we are porting — two
phases (catalog → per-page writing), Sources footers, per-page retries,
incremental regeneration — but cairn's version keeps its two invariants:
it never calls an LLM itself (agents claim tasks), and everything the critic
cannot verify against the graph is labeled rather than trusted.

## Business value
- Any agent (ZCode, Claude, droid, …) can produce a maintained,
  critic-verified architectural wiki for a workspace without leaving the
  cairn workflow.
- Wiki pages become first-class knowledge: searchable via the existing
  knowledge tools, surfacing in compass routing, with machine-checkable
  provenance (Sources frontmatter).
- Regeneration after code churn costs only the changed pages (input-hash
  manifest), keeping the wiki maintainable as a living artifact.
- Success measure: on this repo, `generate --llm` + claim/complete of the
  queue yields ≥8 promoted pages, 0 broken references surviving the critic,
  and a repeat run re-queues only pages whose module inputs changed.

## User stories
<!-- Ordered by priority; each independently demoable. -->
### US1 — Generate the wiki queue (P1)
As a repo owner, I want `cairn wiki generate --llm` to plan pages from the
graph and queue one writing task per page, so that agents can write the wiki
without me hand-authoring prompts.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given an indexed workspace, When I run `cairn wiki generate --llm`,
  Then a page plan (overview + top modules, capped by `--pages`) is computed
  deterministically and one pending task per planned page appears in
  `cairn task list`, each carrying graph-grounded seed facts.
- AC2: Given pages already promoted whose module inputs are unchanged,
  When I re-run the command, Then only changed/new pages are queued
  (no redundant tasks), unless `--force` is passed.
- AC3: Given the graph is empty/unindexed, When I run the command,
  Then it fails with a clear error and exit code 1, queueing nothing.

### US2 — Agent writes a page, critic gates it (P1)
As a claiming agent, I want the page task to carry its seeds and output
spec, so that I can write the article and have the critic verify it.

**Acceptance criteria**:
- AC1: Given a pending wiki-page task, When an agent completes it with a
  body whose `## Sources` footer and backticked references resolve in the
  graph, Then the result is promoted to a Wiki-Article concept whose
  `sources` frontmatter lists the verified source files.
- AC2: Given a completed body containing references that do not resolve,
  When `cairn task complete` runs, Then the critic fails it and a revise
  task is spawned (bounded by the existing retry cycle) instead of a
  promotion.
- AC3: Given `facts.diagrams` is set, When the agent writes the page,
  Then the output spec instructs Mermaid fences; WHEN unset, the spec
  omits them.

### US3 — Track progress, retry failures (P1)
As a repo owner, I want `cairn wiki status` and `cairn wiki retry`, so that
I can see per-page state and re-queue only what failed.

**Acceptance criteria**:
- AC1: Given a partially generated wiki, When I run `cairn wiki status`,
  Then I see per-page states (queued/in-progress/promoted/failed) with
  aggregate counts, derived from the manifest and task states.
- AC2: Given failed or dropped pages, When I run `cairn wiki retry`,
  Then exactly those pages are re-queued with attempt counters preserved;
  promoted pages are untouched.

### US4 — Optional LLM-refined catalog (P2)
As a repo owner, I want `--refine-catalog`, so that an agent can reorganize
the deterministic outline before page tasks spawn.

**Acceptance criteria**:
- AC1: Given `--refine-catalog`, When generate runs, Then a wiki-catalog
  refinement task is queued and page tasks spawn only from the validated
  refined outline (or the deterministic one if refinement fails/drops).
- AC2: Given a refined outline naming a module that does not exist in the
  graph, When validation runs, Then that entry is rejected and the
  deterministic entry is kept.

### US5 — Trigger from MCP (P2)
As an agent in an MCP session, I want a `wiki_generate` tool, so that I can
start wiki generation without shelling out.

**Acceptance criteria**:
- AC1: Given the cairn MCP server, When I call `wiki_generate` with
  options (repo, pages, refine-catalog, diagrams, force), Then I get back
  the page plan and queued task ids, and the tool count assertion updates
  from 27 to 28.

### US6 — Browse the wiki (P2)
As a human, I want a dashboard wiki tab, so that I can read promoted pages
and see generation state without the CLI.

**Acceptance criteria**:
- AC1: Given promoted wiki articles, When I open the dashboard wiki view,
  Then I see the page list with states and can open a page rendered as
  markdown with its sources listed.

## Requirements
- **FR-001**: The system shall compute a deterministic wiki page plan from
  the code graph — one overview page plus one page per top module ranked by
  incoming reference degree, capped by a `--pages` limit (default 10) —
  where each planned page carries an identifier, title, description,
  module, seed files/symbols, and an input hash derived from those inputs.
- **FR-002**: The system shall queue exactly one pending task per planned
  page under a `wiki-page` task kind whose facts carry the page's seeds and
  whose output spec requires a markdown article ending in a `## Sources`
  footer, includes Mermaid-fence instructions only when diagrams are
  requested, and forbids references outside the graph.
- **FR-003**: WHEN a `wiki-page` (or its revise) task is completed with a
  result whose backticked references and Sources footer entries all resolve
  in the graph, THEN the system shall promote it to a Wiki-Article concept
  under the wiki area with `sources` frontmatter populated from the
  verified footer and lineage extensions linking it to its page id and
  input hash.
- **FR-004**: WHEN a `wiki-page` completion fails the critic, THEN the
  system shall spawn a revise task under the existing bounded retry cycle
  and shall not write a promoted concept for that attempt.
- **FR-005**: The system shall persist a wiki manifest recording each
  page's plan entry, input hash, task id, and state; WHEN `generate` runs
  without `--force`, THEN pages whose recorded input hash matches the
  current plan and whose concept is promoted shall be skipped.
- **FR-006**: The system shall expose `cairn wiki status` aggregating
  per-page state from the manifest plus live task states, and
  `cairn wiki retry` re-queuing exactly the pages whose latest attempt
  failed or was dropped while never touching promoted pages.
- **FR-007**: WHERE `--refine-catalog` is passed, the system shall queue a
  `wiki-catalog` refinement task whose validated result (every page entry
  must map to a real module/files in the graph, else that entry reverts to
  the deterministic plan) becomes the page plan.
- **FR-008**: The system shall expose an MCP tool `wiki_generate` accepting
  repo, pages, refine-catalog, diagrams, and force options, returning the
  page plan and queued task ids; the server's expected tool count shall
  move from 27 to 28.
- **FR-009**: The system shall serve a dashboard wiki view listing pages
  with states and rendering a selected page's markdown body and sources,
  following the existing dashboard data/route/template pattern.
- **FR-010**: The system shall keep wiki pages first-class knowledge:
  searchable via the existing knowledge search and included in compass
  routing over the wiki area, with the promoted concepts' OKF frontmatter
  unchanged apart from the now-populated `sources` field.
- **FR-011**: The system shall document the feature in the CLI reference,
  MCP tools reference, and knowledge/memory docs, with a CHANGELOG
  `[Unreleased]` entry.

## Scope
**In**: catalog planner; `wiki-page`/`wiki-catalog` task kinds with output
specs and promotion; critic-gated Sources verification; manifest with
incremental skip and retry; `generate --llm` / `status` / `retry` CLI;
`wiki_generate` MCP tool; dashboard wiki tab; docs + CHANGELOG. Deterministic
(no-LLM) `wiki generate` behavior stays as-is.
**Out (deferred)**: non-English page language (`--lang`); direct LLM calls
from cairn (architecture invariant, not a deferral choice); per-page MCP
delete/edit tools; wiki export/publish beyond the dashboard; GitHub-wiki
sync automation (the docs→wiki sync stays a manual standing rule).

## Assumptions & risks
- Assumption: catalog determinism ("both paths") and English-only output
  were chosen by the user at plan time (2026-08-31) — `--refine-catalog` is
  the opt-in LLM path, default is deterministic.
- Assumption: agent-decoupled writing means generation "completes" only
  when tasks are claimed/completed by some agent; `generate` itself never
  blocks on writing.
- Risk: module ranking by degree may pick poor page boundaries for
  unconventional layouts — mitigation: `--pages` cap + refined-catalog path.
- Risk: Sources-footer parsing plus critic verification may reject
  legitimate prose formatting — mitigation: parser tolerates list/inline
  link forms; critic verdicts distinguish errors from warnings as today.
- Risk: concurrent `generate` runs racing the manifest — mitigation:
  atomic manifest writes (same pattern as config), manifest read before
  queue decisions.

## Decisions locked at plan approval (2026-08-31)
1. Catalog: BOTH paths — deterministic graph-derived default, optional
   `--refine-catalog` refinement task behind a flag.
2. Surfaces: CLI + MCP tool + dashboard tab in v1.
3. Language: English only (no i18n surface added).
