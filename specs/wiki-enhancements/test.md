# Test Cases: wiki-enhancements

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-31
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition (user-visible command + exit code, listing,
report, or HTTP response). No implementation details.

**Framing**: this spec extends shipped wiki machinery — cases for FR-001/002/
003/005/008/010 double as regression guards over behavior that already works;
cases for FR-004/006/007 guard brand-new behavior. Cases marked **(guard)**
must keep passing through every change in this spec and after it.

**Observability channels**: the `cairn` CLI (exit codes, printed plans/listings/
counts), the workspace's wiki manifest and knowledge-store page files (product
data a human can open), the generated agent docs, and dashboard HTTP responses.
Automated cases are executable as command/HTTP checks; when run inside the
repo's suite the canonical invocation is
`CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <test-path> -q`.

## US1 — Generation quality

### TC-001 — Test-majority module is absent even at equal degree with a code module
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** an indexed workspace where a module composed mostly of automated-test
  files and a module of product code have the same number of incoming
  cross-module references
- **When** the wiki plan is built (`cairn wiki generate`, deterministic mode)
- **Then** the test-majority module is absent from the plan entirely and the
  product-code module's page takes the slot
- **Pass condition**: exit 0; the reported plan / `cairn wiki status` queue
  contains no page for the test-majority module and does contain the code
  module's page.

### TC-002 — Page budget is never spent on a test-majority module, however highly it ranks
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** an indexed workspace where a test-majority module has MORE incoming
  cross-module references than every product-code module; page budget capped
  (`cairn wiki generate --pages 1`)
- **When** generation plans pages
- **Then** the test-majority module is never planned — not demoted to a later
  slot, absent outright — and the highest-ranked product-code module takes the
  slot; with a larger budget the test-majority module is still absent and the
  next product-code module fills the extra slot
- **Pass condition**: with `--pages 1`, exit 0 and the queued/planned pages name
  only the top code module's page; with `--pages 2`, both planned pages are
  product-code modules' pages and the test-majority module appears in neither.

### TC-003 — Majority-test modules are excluded even when mixed; small-minority modules keep normal rank
- **Story**: US1 · **Traces to**: FR-001
- **Given** an indexed workspace with (a) a module whose files are 100% test
  files, (b) a majority-test module — most of its files are automated tests but
  some are not — holding the highest incoming reference count, (c) a module
  with only a small minority of test files, and (d) an ordinary product-code
  module
- **When** the wiki plan is built
- **Then** both (a) and (b) are absent from the plan entirely — being majority
  test excludes a module even when some of its files are not tests, and no
  reference count buys the slot back — while (c) ranks first by its degree (the
  minority of test files does not exclude it) and (d) takes the next slot
- **Pass condition**: the reported plan orders: the small-minority module, then
  the ordinary code module, with no page planned for either the 100%-test or
  the majority-test module.

## US4 (renderer part) — Richer human surface

### TC-004 — Inline code and tables render on the dashboard wiki page
- **Story**: US4 · **Traces to**: FR-002, AC1
- **Given** a promoted wiki page whose body contains an inline code span and a
  GFM pipe table, shown on the dashboard
- **When** a reader opens that page's detail URL over HTTP
- **Then** the code span is served as a code element and the table as a table
  element with its rows and cells
- **Pass condition**: `GET /wiki/<page-id>` returns 200 and the response HTML
  contains `<code>…</code>` around the span and `<table>…</table>` containing
  the table's cell text.

### TC-005 — Table boundary forms all render as a table
- **Story**: US4 · **Traces to**: FR-002
- **Given** a promoted page whose body contains one table with an alignment
  declaration row, one cell containing an escaped pipe character, and one row
  written without a leading pipe
- **When** the page is rendered on the dashboard
- **Then** each construct renders as part of a table; the escaped pipe shows as
  a literal "|" inside its cell; no pipe row leaks out as plain paragraph text
- **Pass condition**: the detail-page HTML contains table markup for all three
  variants with cell contents intact (alignment row absent from cell text;
  the escaped pipe present as a character in its cell).

### TC-006 — (guard) Escape-first contract: inline HTML still never passes through
- **Story**: US4 · **Traces to**: FR-002
- **Given** a promoted page whose body embeds inline HTML such as a script tag
  and a bold tag
- **When** the page is rendered on the dashboard
- **Then** the markup appears only as escaped text; no raw executable HTML is
  served (pre-existing contract the new code/table passes must not break)
- **Pass condition**: the detail-page HTML contains the escaped forms
  (e.g. `&lt;script&gt;`) and no unescaped `<script>` / inline-HTML element
  originating from the page body.

## US2 — Trust context

### TC-007 — Promotion records the workspace commit the page was generated at
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** an indexed git workspace; capture its current commit (`git rev-parse
  HEAD`), then run `cairn wiki generate --llm` and complete a page task with a
  critic-passing body
- **When** the page is promoted
- **Then** the promoted page's stored record and the wiki manifest row for that
  page both carry the commit captured before generation
- **Pass condition**: the page's file in the knowledge store's wiki-pages area
  and the wiki manifest (the JSON the wiki maintains in the knowledge store)
  both show a commit sha identical to the pre-generation `git rev-parse HEAD`.

### TC-008 — Promotion in a non-git workspace leaves freshness unknown, not broken
- **Story**: US2 · **Traces to**: FR-003, FR-007
- **Given** an indexed workspace that is not a git repository
- **When** a page task is completed and promoted
- **Then** promotion succeeds exactly as before and no commit sha is recorded
- **Pass condition**: exit 0; the page shows as promoted in `cairn wiki status`;
  its manifest row / stored record carries no sha (absent or empty); TC-020's
  freshness display reads unknown, never stale, for this page.

## US3 — Queue operations

### TC-009 — Drop a pending task: visible and unclaimable
- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** a pending queued task (any task listing shows it)
- **When** `cairn task drop <id>` runs
- **Then** the task is marked dropped, appears as dropped in listings, and can
  never be claimed afterwards
- **Pass condition**: exit 0; `cairn task list --status dropped` includes the id
  (and it no longer appears under pending); a subsequent claim attempt for that
  id is refused / reports the task is not claimable.

### TC-010 — Drop an in-progress task: dropped and the page's queue slot freed
- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** a task claimed (in progress) for a wiki page
- **When** `cairn task drop <id>` runs
- **Then** the task is dropped and any claim it held is released, so a fresh
  task for the same page can be claimed without a conflict
- **Pass condition**: exit 0; the id shows under dropped listings; after
  re-queueing that page (e.g. via `cairn wiki retry` or `cairn wiki enrich`),
  claiming the fresh task succeeds with exit 0 (no stale-claim error).

### TC-011 — Drop a done task is refused
- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** a task that has been completed (done)
- **When** `cairn task drop <id>` runs
- **Then** the command refuses with a clear message and the task stays done
- **Pass condition**: non-zero exit with the refusal on stderr; the id still
  appears under done listings, never under dropped.

### TC-012 — Dropping an already-dropped task is refused (idempotency edge)
- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** a task already dropped via TC-009/TC-010
- **When** `cairn task drop <id>` runs a second time
- **Then** the command refuses (the task is neither pending nor in progress)
  and the listing is unchanged
- **Pass condition**: non-zero exit; `cairn task list --status dropped` still
  shows the id exactly once with no state change.

### TC-013 — Kind-prefix listing separates wiki chains from catalog tasks
- **Story**: US3 · **Traces to**: FR-004, AC2
- **Given** a queue holding wiki page tasks across their revise hops plus at
  least one wiki catalog task
- **When** `cairn task list --kind-prefix wiki-page` runs, then
  `cairn task list --kind-prefix wiki-catalog`
- **Then** the first listing shows every wiki-page chain hop (initial and revise
  kinds, enrichment kinds when present) and no catalog tasks; the second shows
  only catalog tasks — the two prefixes never bleed into each other
- **Pass condition**: the two listings are disjoint; the wiki-page listing
  contains every hop id of each page chain; the catalog listing contains only
  catalog-kind ids.

## US1 (critic part) — Generation quality

### TC-014 — A dead path is reported once regardless of citation form
- **Story**: US1 · **Traces to**: FR-005, AC2
- **Given** a page task completed with a body that cites the same nonexistent
  path once in prose and once in the Sources footer, plus one different
  nonexistent path
- **When** the critic evaluates the completion
- **Then** the result reports the first path exactly once (both citation forms
  collapse) and the second path once on its own
- **Pass condition**: the task's stored result (shown by `cairn task show <id>`)
  lists exactly one unresolved-path error line per distinct dead path — two
  lines total for two paths, never two for the same path.

### TC-015 — Every wiki-page-kind task carries the full wiki output spec
- **Story**: US1 · **Traces to**: FR-005, AC3
- **Given** queued tasks of derived wiki page kinds — an enrichment task and a
  revise-hop task (any kind whose name starts with the wiki-page prefix)
- **When** each task's rendered body is displayed (`cairn task show`)
- **Then** each carries the complete wiki output instructions, including the
  mandatory Sources footer requirement and the only-in-graph reference rule —
  identical to the base wiki page task's instructions, at any revise depth
- **Pass condition**: `cairn task show` output for both derived kinds contains
  the Sources-footer instruction and reference rule; none falls back to the
  generic process-only instruction.

## US4 (export part) — Richer human surface

### TC-016 — Export writes every promoted page as frontmatter markdown and reports the count
- **Story**: US4 · **Traces to**: FR-006, AC2
- **Given** a workspace with some promoted pages and at least one queued (not
  promoted) page; target directory `out` does not exist or is empty
- **When** `cairn wiki export --dir out` runs
- **Then** each promoted page is written as one markdown file named by its page
  id, frontmatter preserved, and the command reports how many pages were
  exported; non-promoted pages are not written
- **Pass condition**: exit 0; `out` contains exactly one `.md` file per promoted
  page named by page id, each beginning with that page's frontmatter (title and
  sources present); the printed count equals the number of promoted pages.

### TC-017 — Export into a non-empty directory refuses without --force, proceeds with it
- **Story**: US4 · **Traces to**: FR-006
- **Given** a target directory that already contains files (e.g. a prior export)
- **When** `cairn wiki export --dir <target>` runs, then
  `cairn wiki export --dir <target> --force`
- **Then** the first run refuses and changes nothing; the second run exports and
  overwrites
- **Pass condition**: first run: non-zero exit with the refusal on stderr and
  the pre-existing files byte-identical afterwards; second run: exit 0 and the
  files now match the current pages, with the count reported.

### TC-018 — Export with zero promoted pages reports zero and writes nothing
- **Story**: US4 · **Traces to**: FR-006
- **Given** a workspace whose wiki has queued/failed pages but none promoted;
  an empty target directory
- **When** `cairn wiki export --dir <target>` runs
- **Then** the command reports zero pages exported and creates no files
- **Pass condition**: exit 0; output states 0 exported; the target directory
  remains empty.

## US2 (display part) — Trust context

### TC-019 — Fresh vs stale at a glance in status and on the dashboard
- **Story**: US2 · **Traces to**: FR-007, AC2
- **Given** a page promoted while the workspace HEAD equals its recorded commit
- **When** `cairn wiki status` runs, then a new commit is added to the workspace
  and status runs again (and the dashboard detail page is opened both times)
- **Then** the page reads fresh before the commit and stale after it, on both
  surfaces
- **Pass condition**: status output labels the page fresh, then stale after
  `git commit --allow-empty -m x` moves HEAD; the dashboard detail page's badge
  matches the status label at each check.

### TC-020 — Unknown freshness when either side of the comparison is unavailable
- **Story**: US2 · **Traces to**: FR-007
- **Given** a page promoted in a non-git workspace (no recorded sha, per TC-008),
  or a page whose workspace HEAD cannot be resolved at display time
- **When** `cairn wiki status` runs or the dashboard detail page is opened
- **Then** the page reads unknown — never fresh, never stale
- **Pass condition**: exit 0; the status line / badge for that page shows the
  unknown state.

## US5 — Enrichment and language

### TC-021 — Enrich queues a task with the page's current body; critic-passing completion appends its sections
- **Story**: US5 · **Traces to**: FR-008, AC1
- **Given** a promoted page with a known body and a known sources list
- **When** `cairn wiki enrich <page-id>` runs, the enrichment task is claimed and
  completed with new, critic-passing sections carrying their own source
  citations (some of them overlapping sources the page already has)
- **Then** the queued task's facts carry the page's current body and fresh seed
  references; on completion the new sections are APPENDED to the promoted
  page's existing body through the same critic gate — the prior body's content
  remains visible in the page itself, the page's sources list merges the new
  entries without duplicates, and the task's result records exactly the
  appended sections
- **Pass condition**: `cairn task show` on the enrichment task shows the old body
  text and seed references in its facts; after completion the dashboard /
  `cairn wiki search` surface the page containing BOTH a distinctive phrase
  from the prior body AND the new sections' text; the page's sources list shows
  each source exactly once (no duplicate entries for the overlapping ones);
  the task's result record contains the appended sections, not a rewritten
  page.

### TC-022 — Enrich on a never-promoted page is refused
- **Story**: US5 · **Traces to**: FR-008
- **Given** a page id that has never been promoted (queued, failed, or unknown)
- **When** `cairn wiki enrich <page-id>` runs
- **Then** the command refuses and queues nothing
- **Pass condition**: non-zero exit with the refusal on stderr; the task listing
  contains no new enrichment task afterwards.

### TC-023 — Enrichment failing the critic leaves the page byte-unchanged
- **Story**: US5 · **Traces to**: FR-008
- **Given** a promoted page and an enrichment completion that fails the critic
  (e.g. body without the required Sources footer or citing a dead path)
- **When** the enrichment cycle runs to the end of its bounded revise budget
- **Then** the page is left byte-unchanged — no failing section is appended,
  nothing is removed, and the sources list is untouched; revise hops spawn;
  once the budget is exhausted the chain ends dropped/failed and the page
  still shows exactly its original content
- **Pass condition**: the page's stored content is identical, character for
  character, before and after the failed run (same body text, same sources
  list); the task listing shows the enrichment chain's revise hops ending in
  the dropped/failed terminal state.

### TC-024 — Enrich --all and --repo scope the queue correctly
- **Story**: US5 · **Traces to**: FR-008
- **Given** promoted pages in two indexed repositories
- **When** `cairn wiki enrich --all` runs, then (after those complete)
  `cairn wiki enrich --all --repo <one-repo>`
- **Then** the first run queues one enrichment task per promoted page across all
  repositories; the second queues only the named repository's pages
- **Pass condition**: task listings show the expected per-page enrichment task
  count: all promoted pages first, then only the scoped repository's pages.

### ~~TC-025 — --lang zh instructs Chinese; default is English~~ — deferred at the approval gate 2026-08-31
- ~~**Story**: US5 · **Traces to**: the deferred language-option requirement
  (struck from the spec at the same gate)~~
- ~~**Given** an indexed workspace with planned pages~~
- ~~**When** `cairn wiki generate --llm --lang zh` runs, and separately
  `cairn wiki generate --llm` (no flag), and `cairn wiki enrich <id> --lang zh`
  for a promoted page~~
- ~~**Then** each flagged task's facts record language zh and its rendered
  instructions direct writing in Chinese; the unflagged task defaults to English~~
- ~~**Pass condition**: `cairn task show` on a `--lang zh` task shows the language
  fact zh plus a write-in-Chinese instruction; the unflagged task shows English
  as the language; `--lang zh` is accepted on both generate and enrich (exit 0).~~

### ~~TC-026 — Invalid --lang value is refused before anything is queued~~ — deferred at the approval gate 2026-08-31
- ~~**Story**: US5 · **Traces to**: the deferred language-option requirement
  (struck from the spec at the same gate)~~
- ~~**Given** an indexed workspace with a planned/queued wiki~~
- ~~**When** `cairn wiki generate --llm --lang fr` runs (a value outside en|zh)~~
- ~~**Then** the command refuses with a usage error and the queue is untouched~~
- ~~**Pass condition**: non-zero exit; `cairn task list` before and after shows an
  identical set of tasks (nothing queued).~~

## US6 — Onboarding

### TC-027 — Fresh install-agents docs describe the wiki workflow
- **Story**: US6 · **Traces to**: FR-010, AC1
- **Given** a fresh agent workspace with no agent docs yet
- **When** `cairn install-agents` runs
- **Then** the generated AGENTS.md includes a wiki section walking through
  generating pages, claiming and completing wiki tasks from the queue, and
  consuming pages via the compass routing/ask surface
- **Pass condition**: the generated AGENTS.md on disk contains a wiki workflow
  section mentioning the wiki generate command, the claim/complete queue steps,
  and asking via the compass tool for wiki content.

## Standing guards (must hold through and after this spec)

### TC-028 — (guard) The deterministic no-LLM generate path is unchanged
- **Traces to**: FR-001 (planner change must not touch the no-llm path)
- **Story**: all · **Traces to**: standing guard
- **Given** an indexed workspace
- **When** `cairn wiki generate` runs with no LLM flags
- **Then** it plans and queues pages offline exactly as shipped: plan produced,
  tasks queued, manifest updated, no network or model access
- **Pass condition**: exit 0; queued task ids reported; `cairn wiki status`
  shows the pages queued. Re-run after every change batch in this spec; any
  failure is a regression.

### TC-029 — (guard) The critic still rejects unresolvable references
- **Story**: US1 · **Traces to**: FR-005 (invariant), standing guard
- **Given** a page task completed with a body citing a path that does not exist
  in the graph
- **When** the completion is evaluated
- **Then** the critic fails it, nothing is promoted, and a revise task spawns —
  dedupe changes must not weaken rejection itself
- **Pass condition**: the result records the unresolved-path error; no wiki page
  appears as promoted for that id; a revise task exists in the listing.

### TC-030 — (guard) The agent tool surface stays at exactly 28 tools
- **Traces to**: FR-008 (enrich is CLI-only by design; no MCP surface this round)
- **Story**: all · **Traces to**: standing guard
- **Given** the shipped agent interface and its generated docs
- **When** the MCP server is started and its advertised tools are listed, and
  `cairn install-agents` regenerates the agent docs
- **Then** exactly 28 tools are advertised and the docs state the same count —
  none of the ten changes adds or removes a tool
- **Pass condition**: the server's tool listing counts 28; the generated
  AGENTS.md tool-count line reads the same number.

## GUI walkthrough (human/browser variant of the dashboard cases)

### TC-031 — Reader walkthrough: wiki page renders rich content with a freshness badge
- **Story**: US4, US2 · **Traces to**: FR-002, FR-007 · **Type**: manual (browser)
- **Given** the dashboard running with a promoted page containing a table, an
  inline code span, and a known freshness state (per TC-004/TC-019)
- **When** a reader opens the dashboard, clicks the wiki entry in the sidebar,
  then clicks the page in the wiki list
- **Then** the list shows the page with its state, and the detail view shows the
  table rendered as a grid, the code span monospaced, and a freshness badge
  matching `cairn wiki status`
- **Pass condition**: screenshots at list and detail steps show the rendered
  table/code and a badge whose label (fresh/stale/unknown) equals the CLI
  status output for that page.

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003 | auto |
| FR-002      | TC-004, TC-005, TC-006, TC-031 | auto (+ TC-031 manual) |
| FR-003      | TC-007, TC-008 | auto |
| FR-004      | TC-009, TC-010, TC-011, TC-012, TC-013 | auto |
| FR-005      | TC-014, TC-015, TC-029 | auto |
| FR-006      | TC-016, TC-017, TC-018 | auto |
| FR-007      | TC-019, TC-020, TC-031 | auto (+ TC-031 manual) |
| FR-008      | TC-021, TC-022, TC-023, TC-024 | auto |
| FR-010      | TC-027 | auto |
| Standing    | TC-028, TC-029, TC-030 | auto |
