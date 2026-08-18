# Spec: docs-human-readable

**Status**: draft          <!-- draft while writing → active once the branch is cut
                                and the first task starts → done when all tasks are
                                ticked and `check.py` re-runs green -->
**Created**: 2026-08-18
**Branch**: `chore/spec-cleanup-2026-08-18` (stacked on the unpushed docs-cleanup branch this session built; the index page FR-003 links to exists only there)

## What
Every page in `docs/` becomes human-navigable at a glance: each opens with a
standard scannable block (what this page covers, when to read it, and — for
long pages — a quick-reference/contents table), every committed machine
artifact under `benchmarks/` has a named human-readable companion, and
cross-page navigation (back-link to the docs index, valid internal links)
holds across the set.

## Why
After the 2026-08-18 README revamp and docs index, 10 of the 15 `docs/` pages
still open with dense prose and no orientation: a human landing on
`cli-reference.md` or `scip.md` must read paragraphs to learn whether the page
is relevant. The repo already has the scannable convention (BUGS.md's index
table + TL;DR per entry, benchmarks.md's quick reference, review-checklist) —
it is just applied inconsistently. Benchmark artifact coverage is better but
unverified as a contract (some JSONs are named by a sibling README, others
only by distant docs).

## Business value
A new user or contributor can decide in ~10 seconds whether a docs page is
theirs to read, and can always find the human rendering of a committed
benchmark artifact. Success is checkable mechanically: every docs page passes
a has-orientation-block check, the link checker is green, and the
JSON→companion inventory is complete and documented.

## User stories
### US1 — Orient on any page in seconds (P1)
As a new user/contributor, when I open any docs/ page I want a short
what/when summary (and a contents/quick-reference table on long pages), so
that I can decide without reading the body whether this page is mine.

**Acceptance criteria**:
- AC1: Given any `docs/*.md` page, when opened, then within the first ~30
  lines it carries an orientation block: a one-paragraph what/when summary,
  plus a Contents/Quick reference table when the page exceeds ~100 lines
  (traces to FR-001).
- AC2: Given the docs set, when a page's structure changes, then the
  orientation convention is uniform across pages — same block shape as
  BUGS.md/benchmarks.md, not 15 bespoke formats (FR-001).

### US2 — Find the human rendering of any artifact (P2)
As a contributor inspecting measurement evidence, I want every committed
JSON artifact under `benchmarks/` to have a named human-readable companion,
so that I never parse raw JSON to understand a result.

**Acceptance criteria**:
- AC3: Given the inventory of `benchmarks/**/*.json`, when each is checked,
  then it has a companion (sibling `.md`/`README.md`/`FIGURES.md`, or a row
  in a generated docs table) that names it, and the mapping is recorded in
  one place (FR-002).

### US3 — Navigate without dead ends (P2)
As a reader moving between pages, I want every docs page to link back to the
docs index and all internal links valid, so that navigation never dead-ends.

**Acceptance criteria**:
- AC4: Given any `docs/*.md` page, when rendered, then it links back to
  `docs/README.md`, and a repo-wide relative-link check over docs passes with
  zero broken links (FR-003).

## Requirements
- **FR-001**: Every `docs/*.md` page shall open with an orientation block —
  a one-paragraph what/when summary directly under the title, and a
  Contents/Quick-reference table when the page exceeds ~100 lines — in the
  established scannable style (index-table + TL;DR), without altering the
  pages' technical content.
- **FR-002**: The system shall keep a complete, documented JSON→companion
  inventory for `benchmarks/**/*.json` (every artifact named by a sibling
  human-readable doc or a generated table), and shall fill any gaps the
  inventory reveals; sealed/blob-pinned artifacts themselves remain
  byte-untouched.
- **FR-003**: Every `docs/*.md` page shall link back to the docs index
  (`docs/README.md`), and all relative links within docs shall resolve
  (mechanically checkable).

## Scope
**In**: the 15 `docs/*.md` pages (incl. updating `docs/README.md` index
entries if a page's role clarifies); a JSON→companion inventory (gaps filled
by small companion notes or a table row, never by editing sealed artifacts);
orientation blocks and back-links only — no technical-content rewrites;
link validation.
**Out (deferred)**: `specs/archive/` (frozen provenance), root `README.md`
(revamped 2026-08-18), `AGENTS.md` (agent-facing by design), `.knowledge/`
(already OKF markdown), generated sentinel tables inside `docs/benchmarks.md`
(CI-guarded; hand edits fail CI), any prose-quality rewrite beyond the
orientation blocks, i18n/translations.

## Assumptions & risks
- Assumption: "human readable" = scannable orientation (TL;DR + index/
  quick-reference + navigable links), per the established BUGS.md convention
  and the user's documented preference for scannable navigation — not a
  full rewrite of technical prose.
- Assumption: orientation blocks are added without changing technical
  content; where a summary risks drifting from the body, it states structure
  ("what's where") rather than re-asserting facts.
- Risk: long pages (cli-reference, architecture) tempt content rewrites —
  mitigation: FR-001 restricts to orientation blocks; the closing audit
  diffs for content drift.
- Risk: docs/benchmarks.md mixes hand-written and sentinel-generated
  regions — mitigation: edits stay outside sentinels; the generator's
  byte-idempotence check runs in the closing audit.
