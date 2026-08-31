# Test Cases: wiki-generation

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-31
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Conventions:
- "Indexed workspace" = a workspace where a cairn build/index has completed
  and the code graph is populated. "Fresh workspace" = never indexed.
- Cases marked **regression guard** protect behavior that already exists
  (survey-verified); their pass condition may cite the existing verify run.
  The canonical local test invocation, where cited, is
  `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <path> -q`.
- Dashboard cases carry two pass conditions: an HTTP-level check (automatable
  without a browser) and a GUI walkthrough in web-gui-tester terms
  (open → observe → click → read-only DOM check + viewed screenshot).

## US1 — Generate the wiki queue

### TC-001 — Page plan is overview plus top modules
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** an indexed workspace with more top-level modules than the page limit
- **When** I run `cairn wiki generate --llm` (default options)
- **Then** the printed plan contains one overview page plus one page per top
  module ranked by how much the rest of the code refers to it, and the total
  number of planned pages never exceeds the default limit of 10; every planned
  page shows an identifier, a title, a short description, its module, and the
  seed files/symbols it will be written from
- **Pass condition**: command exits 0; plan output lists an overview entry plus
  module entries, count ≤ 10, and each entry displays id/title/description/
  module/seeds; the seed entries are paths and names that exist in this
  workspace.

### TC-002 — Same graph, same plan (determinism)
- **Story**: US1 · **Traces to**: FR-001
- **Given** an indexed workspace that has not changed between runs
- **When** I run `cairn wiki generate --llm` twice in a row
- **Then** both runs produce the identical page plan — same pages, same order,
  same identifiers
- **Pass condition**: the plan portions of the two runs' output are equal
  (byte-for-byte diff of the printed plan is empty).

### TC-003 — The --pages limit caps the plan
- **Story**: US1 · **Traces to**: FR-001
- **Given** an indexed workspace with many top-level modules
- **When** I run `cairn wiki generate --llm --pages 3`
- **Then** the plan holds at most 3 pages
- **Pass condition**: the number of planned page entries printed is ≤ 3, and
  the run exits 0.

### TC-004 — One pending task per planned page, with grounded seeds
- **Story**: US1 · **Traces to**: FR-002, AC1
- **Given** an indexed workspace
- **When** I run `cairn wiki generate --llm` and then inspect the task queue
- **Then** `cairn task list --kind wiki-page --status pending` shows exactly
  one pending task per planned page, and each task's details carry that page's
  seed files/symbols and its writing instructions
- **Pass condition**: pending wiki-page task count equals the planned page
  count, task-to-page pairing is 1:1 by page identifier, and for each task
  `cairn task show <id>` displays seed facts naming only files/symbols that
  exist in the workspace (spot-check every page of a small run).

### TC-005 — Unchanged, promoted pages are not re-queued
- **Story**: US1 · **Traces to**: FR-005, AC2
- **Given** a fully promoted wiki and no code changes since
- **When** I re-run `cairn wiki generate --llm`
- **Then** no new writing tasks are queued and the run reports the pages as
  up to date
- **Pass condition**: `cairn task list --kind wiki-page --status pending` is
  empty after the run; the run's output explicitly reports skipped/up-to-date
  pages; exit code 0.

### TC-006 — Changed inputs re-queue only the affected pages
- **Story**: US1 · **Traces to**: FR-005, AC2
- **Given** a promoted wiki, after which code edits touch files belonging to
  only one planned page's module
- **When** I re-run `cairn wiki generate --llm`
- **Then** only the page(s) whose inputs changed are re-queued; all other
  pages are skipped
- **Pass condition**: the set of newly pending wiki-page tasks contains only
  the page covering the edited module (plus any page whose recorded inputs
  the run itself reports as changed); every other page is reported
  unchanged/skipped; no duplicate tasks for the skipped pages.

### TC-007 — --force re-queues every page
- **Story**: US1 · **Traces to**: FR-005
- **Given** a fully promoted wiki and no code changes
- **When** I run `cairn wiki generate --llm --force`
- **Then** every planned page is queued again, including the unchanged ones
- **Pass condition**: pending wiki-page task count after the run equals the
  planned page count.

### TC-008 — Empty or unindexed graph fails cleanly
- **Story**: US1 · **Traces to**: FR-001, AC3 (boundary: empty input)
- **Given** a fresh workspace — no index has ever been built
- **When** I run `cairn wiki generate --llm`
- **Then** the command fails with a clear, human-readable error and exit code
  1, and queues nothing
- **Pass condition**: exit code 1; the error output names the actual cause
  (workspace not indexed / no code graph) rather than a stack trace or a
  silent crash; `cairn task list --kind wiki-page` shows no tasks.

### TC-009 — Concurrent generate runs do not corrupt the wiki state
- **Story**: US1 · **Traces to**: FR-005 (boundary: concurrent access)
- **Given** an indexed workspace
- **When** two `cairn wiki generate --llm` runs are started at the same time
- **Then** both finish without corrupting the generation bookkeeping, and the
  result is a coherent per-page state with no duplicated page tasks
- **Pass condition**: after both runs complete, `cairn wiki status` lists each
  page exactly once with a single coherent state, and no page has two pending
  tasks for the same attempt.

## US2 — Agent writes a page, critic gates it

### TC-010 — Page tasks require a Sources footer and in-graph references
- **Story**: US2 · **Traces to**: FR-002
- **Given** pending wiki-page tasks from a generate run
- **When** an agent inspects any page task's writing instructions
  (`cairn task show <id>`)
- **Then** the output spec instructs a markdown article that must end in a
  `## Sources` footer, and forbids referencing anything outside the code graph
- **Pass condition**: the displayed task text contains both the Sources-footer
  requirement and the reference-scope restriction.

### TC-011 — Diagram instructions appear only when diagrams are requested
- **Story**: US2 · **Traces to**: FR-002, AC3 (boundary: option off vs on)
- **Given** two generate runs over the same workspace — one with the diagrams
  option enabled, one without
- **When** an agent inspects a page task's output spec from each run
- **Then** the diagrams-enabled run's spec instructs Mermaid fenced diagrams;
  the other run's spec contains no such instruction
- **Pass condition**: the Mermaid instruction appears in the task text only in
  the diagrams-enabled run (inspect one task from each run via
  `cairn task show <id>`).

### TC-012 — Critic-passing completion promotes a verified article with sources
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** a pending wiki-page task
- **When** an agent claims the task and completes it (via
  `cairn task complete <id> --result-file <path>`) with an article whose
  backticked references and `## Sources` footer entries all resolve in the
  workspace graph
- **Then** the completion is accepted and the article is promoted into the
  knowledge base as a wiki article listing its verified sources
- **Pass condition**: the complete command's outcome reports success with
  promotion; `cairn wiki status` shows that page promoted; viewing the article
  (dashboard wiki view or the knowledge concept listing) shows the standard
  article metadata plus a populated sources list naming exactly the footer
  files that were verified.

### TC-013 — Unresolvable reference blocks promotion and spawns a revise task
- **Story**: US2 · **Traces to**: FR-004, AC2
- **Given** a pending wiki-page task
- **When** an agent completes it with a body citing a file or symbol that does
  not exist in the graph
- **Then** the critic rejects the attempt, a revise task for the same page is
  spawned carrying the reported problems, and nothing is promoted for that
  attempt
- **Pass condition**: `cairn task list --kind wiki-page-revise --status pending`
  shows one new pending revise task tied to the same page, and
  `cairn wiki status` still shows that page as not promoted.

### TC-014 — The revise cycle is bounded; exhausted attempts are dropped
- **Story**: US2 · **Traces to**: FR-004 (boundary: repeated failure)
- **Given** a wiki page whose every attempt keeps failing the critic
- **When** each spawned revise task is in turn completed with invalid
  references until the cycle's bound is reached
- **Then** the last failing attempt is dropped, no further revise task is
  spawned, and no promotion ever happened for that page
- **Pass condition**: the task chain for the page ends in a dropped state
  (visible in `cairn task list`), no pending revise task remains for that
  page, and `cairn wiki status` shows the page failed/dropped — never promoted.

### TC-015 — Critic-fail → revise → drop mechanism keeps working (regression guard)
- **Story**: US2 · **Traces to**: FR-004
- **Given** the shipped task-queue behavior (survey-verified as already
  working for any task kind)
- **When** the standing task-safety suite runs
- **Then** the critic-failure branch still spawns a bounded revise task and
  drops at the bound, with no promotion on failing attempts
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test
  pytest tests/test_tasks_safety.py -q` passes (survey verify: 8 passed).

## US3 — Track progress, retry failures

### TC-016 — Wiki status shows per-page states with aggregate counts
- **Story**: US3 · **Traces to**: FR-006, AC1
- **Given** a partially generated wiki — some pages promoted, one task claimed
  and in progress, one attempt failed, the rest queued
- **When** I run `cairn wiki status`
- **Then** every planned page is listed with exactly one of the states
  queued / in-progress / promoted / failed, together with aggregate counts per
  state
- **Pass condition**: the output lists each planned page exactly once with a
  state from the allowed set, the per-state totals match the listed rows, and
  the in-progress/failed rows agree with what `cairn task list` shows for the
  corresponding tasks.

### TC-017 — Retry re-queues exactly the failures and never touches promoted pages
- **Story**: US3 · **Traces to**: FR-006, AC2 (doubles as the standing guard
  for promoted-page inviolability)
- **Given** a wiki where at least one page failed or was dropped and at least
  one page is promoted
- **When** I run `cairn wiki retry`
- **Then** exactly the failed/dropped pages are re-queued as new pending
  tasks, their prior attempt counts are carried forward (not reset to first
  attempt), and promoted pages are untouched
- **Pass condition**: new pending wiki-page tasks exist only for the
  failed/dropped pages; each retried task's displayed attempt count continues
  from the failed attempt's count; promoted pages gained no new tasks and
  still show promoted in `cairn wiki status`.

### TC-018 — Retry with nothing to retry is a peaceful no-op
- **Story**: US3 · **Traces to**: FR-006 (boundary: empty failure set)
- **Given** a wiki with no failed or dropped pages
- **When** I run `cairn wiki retry`
- **Then** no tasks are queued, a friendly message says there is nothing to
  retry, and the command succeeds
- **Pass condition**: exit code 0, zero new pending wiki-page tasks.

## US4 — Optional LLM-refined catalog

### TC-019 — --refine-catalog queues a catalog task first and defers page tasks
- **Story**: US4 · **Traces to**: FR-007, AC1
- **Given** an indexed workspace
- **When** I run `cairn wiki generate --llm --refine-catalog`
- **Then** exactly one catalog refinement task is queued and no page tasks are
  queued yet — page tasks appear only after the refined outline is validated
- **Pass condition**: `cairn task list --kind wiki-catalog --status pending`
  shows one task while `cairn task list --kind wiki-page` shows none at this
  point.

### TC-020 — A valid refined outline drives the page tasks
- **Story**: US4 · **Traces to**: FR-007, AC1
- **Given** a pending catalog refinement task from a `--refine-catalog` run
- **When** an agent completes it with a reordered/reorganized outline whose
  every entry maps to real modules and files in the graph
- **Then** page tasks spawn from that refined outline — one per refined entry
- **Pass condition**: after completion, pending wiki-page tasks match the
  refined outline's entries (count and modules), and no wiki-catalog task
  remains pending.

### TC-021 — An invalid refined entry reverts to the deterministic entry
- **Story**: US4 · **Traces to**: FR-007, AC2 (boundary: phantom module)
- **Given** a pending catalog refinement task, completed with an outline in
  which one entry names a module that does not exist in the graph, the rest
  valid
- **When** the completion is validated
- **Then** the invalid entry is rejected, the deterministic plan's entry is
  kept in its place, and the valid refined entries are honored
- **Pass condition**: the spawned page tasks correspond to the valid refined
  entries plus the deterministic plan's entry for the rejected slot — verified
  by comparing the queued pages' identifiers/modules against the refined
  outline and the deterministic plan from the same run's output.

### TC-022 — A failed or dropped refinement falls back to the deterministic plan
- **Story**: US4 · **Traces to**: FR-007 (boundary: refinement never lands)
- **Given** a `--refine-catalog` run whose catalog task never completes
  successfully (it fails repeatedly and is dropped at the cycle bound)
- **When** the catalog task reaches its dropped state
- **Then** generation still proceeds — page tasks spawn from the deterministic
  outline
- **Pass condition**: after the drop, pending wiki-page tasks exist and match
  the deterministic plan (same pages/order as a plain generate run over the
  same graph).

## US5 — Trigger from MCP

### TC-023 — The wiki_generate tool starts generation from an MCP session
- **Story**: US5 · **Traces to**: FR-008, AC1
- **Given** the cairn MCP server connected in an agent session and an indexed
  workspace
- **When** the agent calls the `wiki_generate` tool with repo, pages,
  refine-catalog, diagrams, and force options
- **Then** the response carries the page plan and the queued task ids, and the
  same tasks are visible in the CLI queue
- **Pass condition**: the tool response lists the planned pages and their task
  ids; the ids match `cairn task list --kind wiki-page --status pending`;
  passing each option produces the same behavior as the matching CLI flag
  (spot-check: force with a promoted wiki re-queues everything, as TC-007).

### TC-024 — The server exposes 28 tools including wiki_generate
- **Story**: US5 · **Traces to**: FR-008, AC1
- **Given** the cairn MCP server
- **When** a client enumerates the server's tools
- **Then** the list contains 28 tools and includes `wiki_generate`
- **Pass condition**: an MCP client session lists 28 tools with
  `wiki_generate` present; server startup does not fail its own tool-count
  check; automated pin: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test
  pytest tests/test_status_resource_health.py -q` passes with the count
  updated to 28.

## US6 — Browse the wiki (dashboard)

### TC-025 — The dashboard wiki view lists pages with states
- **Story**: US6 · **Traces to**: FR-009, AC1
- **Given** the dashboard server running against a workspace with a partially
  generated wiki (some promoted, some queued/failed)
- **When** a human opens the dashboard in a browser and navigates to the wiki
  view
- **Then** the page list shows every wiki page with its generation state
- **Pass condition (HTTP, auto)**: a GET of the wiki view returns HTML
  containing one entry per planned page, each with its state label.
  **Pass condition (GUI, manual — web-gui-tester terms)**: open the dashboard
  URL in the browser, click through to the wiki tab, take a read-only DOM
  snapshot confirming one row per page with a state badge, and view a
  screenshot as visual evidence.

### TC-026 — Opening a page renders its markdown body and sources
- **Story**: US6 · **Traces to**: FR-009, AC1
- **Given** a promoted wiki article whose body has headings, lists, and a
  Sources footer
- **When** a human clicks that page's entry in the dashboard wiki list
- **Then** the detail view shows the article rendered as markdown (headings
  and lists displayed as formatted content, not raw markup) with its verified
  sources listed
- **Pass condition (HTTP, auto)**: a GET of the page's detail view returns
  HTML containing rendered heading/list elements for the body and the source
  entries — not the raw markdown source.
  **Pass condition (GUI, manual — web-gui-tester terms)**: from the wiki list,
  click the page entry, read-only DOM check confirms rendered heading
  elements and a visible sources section, and a viewed screenshot confirms
  the rendering visually.

## FR-010 — Wiki pages are first-class knowledge

### TC-027 — Promoted pages are findable via wiki/knowledge search
- **Story**: (cross-cutting) · **Traces to**: FR-010
- **Given** a promoted wiki article about a recognizable topic
- **When** I search for that topic via `cairn wiki search "<topic>"`
- **Then** the promoted article is among the results
- **Pass condition**: the search output includes the article's title/concept.
  Note: the business-knowledge search surface is deliberately scoped to the
  knowledge area and excludes the wiki area by design; the FR's searchable
  surfaces are the bundle-wide wiki search (this TC) and compass routing
  (TC-028).

### TC-028 — Promoted pages surface in compass routing over the wiki area
- **Story**: (cross-cutting) · **Traces to**: FR-010
- **Given** a promoted wiki article on a module's architecture
- **When** a compass-routed query (`cairn ask "<topic>"` or the ask-compass
  MCP tool) runs about that topic
- **Then** the response includes a wiki-layer result naming that article
- **Pass condition**: the routed answer's layer breakdown lists a wiki hit
  for the article.

### TC-029 — A promoted article reads as a normal knowledge concept plus sources
- **Story**: (cross-cutting) · **Traces to**: FR-010
- **Given** a promoted wiki article
- **When** its concept listing/metadata is viewed (knowledge listing or
  dashboard page detail)
- **Then** it presents with the standard knowledge-concept metadata (type,
  title, description, generated/verified/status fields) plus the populated
  sources field — no other structural deviation
- **Pass condition**: the article's displayed metadata matches the common
  concept shape used elsewhere in the knowledge base, with the addition of the
  sources list; compare against any existing knowledge doc's metadata view.

## FR-011 — Docs and CHANGELOG

### TC-030 — Documentation covers the feature end to end
- **Story**: (cross-cutting) · **Traces to**: FR-011
- **Given** the shipped feature
- **When** a reader opens the CLI reference, the MCP tools reference, the
  knowledge/memory documentation, and the CHANGELOG
- **Then** the CLI reference documents the new wiki commands (generate with
  the new flags, status, retry); the MCP tools reference lists
  `wiki_generate` and its tool-count heading reflects 28; the knowledge/memory
  docs describe the wiki generation workflow (queue → agent claim/complete →
  critic → promotion); the CHANGELOG has an `[Unreleased]` Added entry for the
  feature
- **Pass condition** (manual doc review): all four documents contain the new
  content above; a text search for `wiki status`, `wiki retry`,
  `wiki_generate`, and the feature name finds matches in the respective
  documents, and the CHANGELOG's `[Unreleased]` section names the feature.

## Standing regression guards (spec assumptions)

### TC-031 — The deterministic (no-LLM) wiki path is unchanged
- **Story**: (standing) · **Traces to**: Scope ("deterministic `wiki generate`
  behavior stays as-is"), FR-001
- **Given** an indexed workspace
- **When** I run `cairn wiki generate --dry-run` (no `--llm`)
- **Then** the pre-existing deterministic behavior is intact: the single
  architecture summary is produced and critic-checked informationally, and no
  writing tasks are queued
- **Pass condition**: exit code 0, the familiar single-summary verdict output
  appears, and `cairn task list --kind wiki-page` shows no tasks. The existing
  deterministic verify path (`CAIRN_LIB=/tmp/__no_such_lib__ uv run cairn wiki
  generate --dry-run`) keeps producing its verdict line.

### TC-032 — Generation never blocks on writing
- **Story**: (standing) · **Traces to**: FR-002 (queueing semantics; spec
  assumption "generate itself never blocks on writing")
- **Given** an indexed workspace with no agent running to claim anything
- **When** I run `cairn wiki generate --llm`
- **Then** the command returns as soon as tasks are queued — it does not wait
  for pages to be written, and it succeeds while every page is still pending
- **Pass condition**: the command exits 0 within normal command time (no
  hang/timeout) while `cairn task list --kind wiki-page --status pending`
  shows all page tasks still pending.

### TC-033 — Cairn never writes page prose itself
- **Story**: (standing) · **Traces to**: FR-003 (promotion exists only via an
  agent's completion; architecture invariant "it never calls an LLM itself
  (agents claim tasks)")
- **Given** an indexed workspace with no agent claiming tasks
- **When** I run `cairn wiki generate --llm`, then `cairn wiki status`, then
  `cairn wiki retry`
- **Then** no article content appears anywhere as a side effect of these
  commands — wiki pages come into existence only after an agent completes a
  task
- **Pass condition**: after all three commands, `cairn wiki status` shows
  every page queued (zero promoted) and the knowledge listing contains no new
  wiki article; a wiki article exists only after a recorded
  `cairn task complete` for its task.

## Coverage matrix

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-001, TC-002, TC-003, TC-008, TC-031 | auto (TC-031 auto/manual) |
| FR-002 | TC-004, TC-010, TC-011 | auto |
| FR-003 | TC-012 | auto |
| FR-004 | TC-013, TC-014, TC-015 | auto (TC-015 = suite run) |
| FR-005 | TC-005, TC-006, TC-007, TC-009 | auto |
| FR-006 | TC-016, TC-017, TC-018 | auto |
| FR-007 | TC-019, TC-020, TC-021, TC-022 | auto |
| FR-008 | TC-023, TC-024 | auto (TC-024 = MCP enumeration + suite run) |
| FR-009 | TC-025, TC-026 | auto (HTTP) + manual (GUI walkthrough) |
| FR-010 | TC-027, TC-028, TC-029 | auto |
| FR-011 | TC-030 | manual (doc review) |
| Standing guards | TC-031, TC-032, TC-033 | auto |

## Notes on observability (spec smells surfaced, not papered over)

1. **FR-010 "searchable via the existing knowledge search"** is ambiguous: the
   business-knowledge search surface excludes the wiki area by design
   (survey caveat), while the bundle-wide wiki search and compass routing do
   reach wiki concepts. TC-027/TC-028 target the surfaces the FR's "wiki
   area" phrasing points at; if the intent was the business-knowledge search
   tool specifically, that part of FR-010 would be ⚠ untestable-as-specified
   without a scope change.
2. **FR-008's tool-count assertion** is an internal server invariant; its only
   user-visible faces are the MCP tool enumeration (TC-024's primary pass
   condition) and the pinned health suite (cited as the automated
   equivalent).
3. **--pages cap semantics** (whether the overview counts toward the cap) are
   not pinned by the FR text; TC-003 asserts the safe observable reading
   (total ≤ limit). If the cap is meant to exclude the overview, tighten
   TC-003 at implementation time — do not loosen it.
