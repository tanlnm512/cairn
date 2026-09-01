# Spec: wiki-enhancements

**Status**: active          <!-- amended set approved 2026-08-31 ("start implement"); single end-of-plan commit shape (recommended default, unchallenged); first task spawned same day → active -->          <!-- draft while writing → approved at the Stage-4 user gate
                                → active once the first task spawns → done when all
                                tasks are ticked and `check.py` re-runs green -->
**Created**: 2026-08-31
**Branch**: `feat/wiki-enhancements`

## What
Ten targeted improvements to cairn's wiki system, grouped by theme: generation
quality (planner stops spending pages on test-only modules; the critic stops
double-reporting dead references and the revise chain keeps wiki instructions),
trust context (promoted pages carry the workspace commit they were generated
at, surfaced as a fresh/stale badge), operations (`cairn task drop` and
kind-prefix listing so queue maintenance needs no store surgery), human
surface (the dashboard renderer supports inline code and tables; wiki export
to a directory of markdown files), capability (a critic-gated enrichment task
kind; the `--lang` option was deferred at the approval gate with FR-009), and
onboarding (generated agent docs mention the wiki workflow).

## Why
The 0.16.1/0.16.2 wiki feature works end to end but its first real run
surfaced concrete gaps: a whole page was spent on `tests/` while higher-value
modules went unqueued; the critic reported the same dead path twice (body
backtick + footer) and the doubly-derived revise kind lost its output-spec
instructions; orphaned duplicate tasks could only be removed by direct store
surgery; the dashboard renderer forces writers to avoid inline code and
tables — the two constructs technical wikis lean on; nothing answers "how old
is this page"; enrichment requires `--force` regenerating everything; and the
consumer side (compass routing surfaces wiki) is invisible to new workspaces.

## Business value
- Every regeneration spends its page budget on modules humans actually read.
- Promoted pages become auditable ("generated at commit X") and visibly stale
  when the code moves on.
- Queue maintenance becomes a CLI operation instead of SQLite surgery.
- Wiki pages can use the constructs technical writers expect, and can be
  exported as plain markdown for any external consumer.
- Enrichment becomes a queueable, repeatable, critic-gated operation.
- New agent workspaces discover the wiki workflow automatically.

## User stories
### US1 — Generation quality (P1)
As a repo owner, I want the planner to leave test-only modules out of page
plans entirely and the critic to report each dead reference once with
instructions intact, so that page budgets and revise cycles are spent on
content.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a graph where a test-majority module would rank highest by
  degree, When the plan is built, Then it is absent from the plan and the
  next code module takes the slot.
- AC2: Given a completion citing the same dead path in body and footer, When
  the critic runs, Then the error appears once.
- AC3: Given a task of any `wiki-page*` kind, When its body is rendered,
  Then it carries the full wiki output spec (Sources footer requirement),
  regardless of revise depth.

### US2 — Trust context (P1)
As a repo owner, I want each promoted page to record the workspace commit it
was generated from and to see fresh/stale at a glance, so that I know what
the wiki describes.

**Acceptance criteria**:
- AC1: Given a promotion, When the concept is written, Then its extensions
  carry the workspace HEAD sha at generation time.
- AC2: Given the store's HEAD has moved past a page's recorded sha, When I
  view status or the dashboard detail, Then the page reads as stale.

### US3 — Queue operations (P1)
As a repo owner, I want `cairn task drop` and kind-prefix listing, so that
maintaining the queue never requires SQLite surgery.

**Acceptance criteria**:
- AC1: Given a pending or in-progress task, When I run `cairn task drop <id>`,
  Then it is marked dropped (visible in listings) and never claimable again;
  done tasks are refused.
- AC2: Given wiki chains, When I run `cairn task list --kind-prefix wiki-page`,
  Then every chain hop is listed.

### US4 — Richer human surface (P1)
As a wiki reader, I want inline code and tables rendered in the dashboard,
and pages exportable as markdown, so the pages read properly and can live
outside cairn.

**Acceptance criteria**:
- AC1: Given a body with inline code spans and a GFM table, When rendered,
  Then code spans render as code elements and the table as a table.
- AC2: Given a promoted wiki, When I run `cairn wiki export --dir out`,
  Then each page is written as a markdown file (frontmatter included) named
  by page id, and the command reports the count.

### US5 — Enrichment (P2)
As a repo owner, I want to queue enrichment for existing pages, so page depth
is an operational choice. (The output-language half of this story was deferred
at the approval gate 2026-08-31 with FR-009 — see the struck AC2 below.)

**Acceptance criteria**:
- AC1: Given a promoted page, When I run `cairn wiki enrich <page-id>`, Then
  an enrichment task is queued whose completion APPENDS its new sections to
  the page's existing body through the same critic gate (the prior body
  remains in the page itself; the task result records the appended
  sections).
- ~~AC2: Given `--lang zh` (or `en`) on generate/enrich, When the task body
  is rendered, Then the output spec instructs that language.~~ (deferred with
  FR-009)

### US6 — Onboarding (P3)
As a new agent workspace, I want the generated agent docs to mention the wiki
workflow, so the consumer side is discoverable.

**Acceptance criteria**:
- AC1: Given a fresh `cairn install-agents`, Then the generated AGENTS.md
  template includes the wiki generate/claim/complete and ask_compass workflow.

## Requirements
- **FR-001**: The planner shall exclude modules whose indexed files are
  majority test files (`test`/`spec` path segments) from page plans
  entirely — the wiki documents product code only (ZCode's explorer applies
  the same rule).
- **FR-002**: The dashboard markdown renderer shall render inline code spans
  (`` `code` ``) as code elements and GFM pipe tables as tables, preserving
  the escape-first contract (no inline HTML passthrough).
- **FR-003**: WHEN a wiki page is promoted, THEN the system shall record the
  workspace HEAD commit sha in the concept's extensions and the manifest row.
- **FR-004**: The system shall provide `cairn task drop <id>` (pending or
  in-progress only; done refused) and `cairn task list --kind-prefix PREFIX`.
- **FR-005**: The critic shall report each unresolved path once per completion
  regardless of how many citation forms mention it, and the output-spec lookup
  shall serve the wiki spec to any kind whose name starts with `wiki-page`.
- **FR-006**: The system shall provide `cairn wiki export --dir DIR` writing
  every promoted page as a markdown file (frontmatter preserved), reporting
  the exported count, and refusing a non-empty directory without `--force`.
- **FR-007**: WHEN wiki status or the dashboard detail view renders a page, THEN the system shall display it as fresh or stale
  by comparing the recorded commit sha with the workspace's current HEAD
  (stale when they differ; unknown when either is unavailable).
- **FR-008**: The system shall provide a `wiki-page-enrich` task kind — queued
  via `cairn wiki enrich [<page-id>] [--repo R] [--all]` carrying the page's
  current body plus fresh seeds — whose critic-passing completion APPENDS the
  result's sections to the promoted concept's body and merges its Sources
  entries into the frontmatter, reusing the existing wiki promotion branch
  and revise cycle.
- ~~**FR-009**: WHERE `--lang en|zh` is passed to generate or enrich, THEN
  the task facts shall carry the language and the output spec shall instruct
  writing in it (default `en`).~~ **Deferred at the approval gate (2026-08-31):
  no proven second-language consumer yet; it is a facts-gated spec appendix
  and lands trivially later.**
- **FR-010**: The install-agents AGENTS.md template shall include a wiki
  section (generate → claim → complete → ask_compass consumption).

## Scope
**In**: FR-001..FR-008, FR-010 (nine FRs); docs + CHANGELOG.
**Out (deferred)**: `--lang en|zh` (FR-009, deferred at the gate); `wiki export --remote` push automation (outward side
effect — `--dir` only this round); live page re-rendering on code change
(file-watch); per-page delete/edit MCP tools; bold/italic inline rendering;
non-wiki task kinds' output-spec prefixes.

## Assumptions & risks
- Default decisions recorded at Stage 0 (re-presented at the approval gate):
  tables = GFM pipe-table subset only; export is `--dir` only. AMENDED at the
  approval gate (2026-08-31): test-majority modules are EXCLUDED (not
  demoted); enrich APPENDS sections (does not replace).
- Risk: commit-sha resolution needs git access at promotion time
  (`complete_task` is generic) — mitigation: sha resolved by the CLI/MCP
  pipeline into task facts (facts carry `commit_sha`), promotion just copies
  it; `complete_task` stays generic.
- Risk: test-majority heuristic misfiles mixed modules — mitigation (as
  amended at the approval gate 2026-08-31): majority is a STRICT majority of
  the module's indexed files, so a mixed module keeps its page unless test
  files outnumber code files; the sort key stays `(-degree, name-ASC)` with no
  class tier (D-014). The pre-gate mitigation ("rank demotion only, never
  exclusion") was superseded by the gate's choice of exclusion.
- Risk: enrich tasks could churn pages — mitigation: enrich requires an
  already-promoted page and rides the same critic + bounded revise cycle.

## Decisions locked at Stage 0 (2026-08-31), as ruled at the approval gate
Defaults were chosen per established repo patterns and put to the user at the
approval gate. What survived, and what the gate changed (2026-08-31):

| Stage-0 default | Gate ruling |
|---|---|
| GFM-subset tables | kept (D-015) |
| test-majority modules demoted in rank | **AMENDED → excluded entirely** (D-014) |
| enrich replaces the body, result-sibling audit trail | **AMENDED → enrich APPENDS its sections; prior body stays visible in the page** (D-021) |
| export `--dir` only | kept (D-019) |
| `--lang en\|zh` | **DEFERRED → FR-009 struck from scope this round** (notes preserved in tech-spec D-022) |
| commit sha rides facts (generic `complete_task` preserved, extending the landed repo-in-facts pattern from the wiki-generation spec) | kept (D-016) |
