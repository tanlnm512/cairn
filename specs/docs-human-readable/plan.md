# Plan: docs-human-readable

**Spec**: [spec.md](spec.md) | **Survey**: [survey.md](survey.md) | **Created**: 2026-08-18
**Team context**: solo dev; the orchestrator (AI agent session) executes tasks in
waves. Docs-only spec — no production code changes. All counts below re-run
against the working tree this session (branch `chore/spec-cleanup-2026-08-18`,
baseline main @ cb65cae); statuses and evidence trace to survey.md.

## Ground facts the sequencing rests on (re-counted this session)

- `ls docs/*.md | wc -l` → 16 files = 15 pages + `docs/README.md` (the index).
  Line counts match survey.md exactly (largest: cli-reference.md 491,
  architecture-overview.md 481, benchmarks.md 468; smallest: README.md 46).
- Table-header grep `^#{1,3} (Contents|Table of contents|Quick reference|Index)`
  → 5 files: BUGS.md, architecture-overview.md, benchmarks.md,
  audit-checklist.md, review-checklist.md. With docs/README.md (46 lines,
  under the ~100-line threshold) these are the 6 pages survey.md records as
  conforming; the remaining **10 pages** carry the FR-001 gap:
  architecture.md 262, cli-reference.md 491, configuration.md 177,
  contribution-workflow.md 181, mcp-tools.md 229,
  methodology-precise-vs-fuzzy.md 123, query-flow.md 200, quickstart.md 161,
  release-checklist.md 141, scip.md 269 — all >100 lines, so all 10 need a
  Contents/Quick-reference table plus the what/when paragraph.
- FR-002 ground: 30 JSONs, 14 companion .mds under `benchmarks/` (re-counted);
  the 3 gap files exist on disk: `benchmarks/quality/fr004-prf/rows-fr004.json`,
  `benchmarks/quality/ladder-v2/rows-ds2.json`,
  `benchmarks/quality/ladder-v2/sweep-ds2-zeroshot.json`.
- FR-003 ground: `grep -c '](README.md)' docs/*.md` → 0 in all 16 files.
  Relative-link resolution is already green per survey (0 broken over
  19 files); only the back-link half is work.
- Sealed-set guards exist on disk: `tests/test_ablation_artifact.py`,
  `tests/test_gen_benchmark_tables.py`, `tests/test_verify_datasource.py`,
  `scripts/verify_datasource.py`, `scripts/gen_benchmark_tables.py`.

Coupling in this spec is file overlap, not call-graph (markdown targets, no
symbols) — the parallelization map is derived from these re-counted file
sets, which is the real coupling surface here.

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Orientation blocks | Open any of the 10 named pages → uniform what/when paragraph + Contents/Quick-ref table at the top; zero content drift | FR-001 | — |
| 2 | Artifact inventory + gap fill | Run the gap loop → no true GAP; one-place inventory names all 30 JSONs; sealed set byte-untouched | FR-002 | — |
| 3 | Navigation back-links | Every docs page links back to docs/README.md; link checker stays 0-broken | FR-003 | Phase 1 |
| 4 | Closing audit | One command pass re-runs all checkpoints green; drift/sentinel/blob audits clean | (audit of FR-001..003 — owns no FR) | Phases 1, 2, 3 |

### Phase detail

**Phase 1 — orientation blocks (FR-001).** Install the uniform orientation
block on the 10 pages listed above, replicating the in-repo exemplars
(BUGS.md index-table shape, benchmarks.md Quick reference — survey
Supporting evidence A). `docs/README.md` index rows may be touched only where
a page's role clarifies (spec scope In). Edits are purely additive at the top
of each page; summaries state structure ("what's where"), not re-asserted
facts (spec assumption). De-risk ordering inside the phase: the block shape
is locked on the smallest page first (methodology-precise-vs-fuzzy.md, 123
lines) before batching the remaining 9 — this fronts the spec's uniformity
risk (AC2: one convention, not 10 bespoke formats) and its content-drift
risk (long pages tempt rewrites).

**Phase 2 — inventory + gap fill (FR-002).** One-place inventory documenting
all 30 `benchmarks/**/*.json` → companion mappings (27 already named —
survey Supporting evidence B is the starter list), plus companion notes/rows
naming the 3 gap artifacts (rows-fr004.json, rows-ds2.json,
sweep-ds2-zeroshot.json — a row each in their sibling FIGURES.md/README or
the inventory table). Gap-fills touch only companion `.md` files; the
sealed/blob-pinned set is never an edit target (survey Supporting evidence
C). De-risk: the three guard checks in the checkpoint run at this phase, not
only at the closing audit, so a sentinel/blob violation surfaces immediately.

**Phase 3 — back-links (FR-003).** All 15 docs pages (docs/README.md itself
exempt — it is the index/link target, assumption A3) carry a back-link to
`docs/README.md`, same shape on every page, slotted into the orientation
block Phase 1 established. Strictly after Phase 1 (serial exception S1).

**Phase 4 — closing audit.** spec.md names this gate explicitly ("the
closing audit diffs for content drift"; "the generator's byte-idempotence
check runs in the closing audit"). Re-runs every phase checkpoint in one
pass over the union of edited files (commands under Checkpoints).

## Dependencies
<!-- Short graph or prose: what blocks what; what can run in parallel. -->

```
Phase 1 (FR-001) ─────► Phase 3 (FR-003) ──► Phase 4 (audit)
Phase 2 (FR-002) ────────────────────────────► Phase 4 (audit)
Phase 1 ─────────────────────────────────────► Phase 4 (audit)
```

No other edges: Phase 1 needs nothing; Phase 2 needs nothing from Phase 1 or
3 (disjoint files per A2); Phase 3 needs Phase 1 (S1); Phase 4 needs all.
Wave structure: **Wave A** = Phase 1 ∥ Phase 2 (start together) ·
**Wave B** = Phase 3 (after Phase 1 lands) · **Wave C** = Phase 4.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->

Independent (default-concurrent — disjoint files, checkable):
- **P1 — Phase 1's ten pages, per-page concurrent.** Ten distinct files:
  docs/{architecture,cli-reference,configuration,contribution-workflow,
  mcp-tools,methodology-precise-vs-fuzzy,query-flow,quickstart,
  release-checklist,scip}.md; no page's edit reads or writes another page.
  (Ordering nuance, not a file conflict: the smallest page goes first to
  lock the block shape — see Phase 1.)
- **P2 — Phase 2 alongside Phase 1.** Phase 2's files: companion .mds under
  benchmarks/quality/fr004-prf/ and benchmarks/quality/ladder-v2/, plus one
  inventory doc (placement per A2). None is among Phase 1's ten pages —
  disjoint unless A2's fallback activates (S2).
- **P3 — within Phase 2**: fr004-prf gap-fill ∥ ladder-v2 gap-fills ∥
  inventory doc — three separate file sets under benchmarks/.
- **P4 — Phase 3's fifteen pages, per-page concurrent** (once Phase 1 has
  landed): fifteen distinct files, one link line each.

Strictly ordered (the exceptions; burden of proof on serial):
- **S1 — Phase 3 after Phase 1.** (a) *Shared files*: 10 of Phase 3's 15
  targets are exactly Phase 1's ten pages — same wave would put two writers
  on each page top. (b) *Produces/consumes*: Phase 1 produces the uniform
  orientation-block shape (AC2) that Phase 3's back-link consumes — the
  link's placement and formatting are defined by the block Phase 1 installs.
- **S2 — docs/benchmarks.md, Phase 2↔Phase 3, only if A2's fallback
  activates** (inventory placed in docs/benchmarks.md rather than a new
  file). Then Phase 2's inventory insert and Phase 3's back-link on that
  single file must not share a wave: inventory first, back-link after, and
  the insert stays outside the sentinel-generated regions (survey Supporting
  evidence C). If the inventory is a new file, S2 does not exist.
- **S3 — docs/README.md single owner.** Only Phase 1 may touch it (index-row
  clarification), in one wave. It is FR-003's link *target* and takes no
  self-back-link (A3), so Phase 3 never writes it.
- **S4 — Phase 4 strictly last**: it audits the union of all prior edits.
- **S5 — sealed set excluded from all writers**: the 30 benchmark JSONs, the
  sentinel regions of docs/benchmarks.md, and the blob/tree-pinned artifacts
  (survey Supporting evidence C) are never edit targets in any phase;
  gap-fills land in companion .md files only.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->

- **After Phase 1**:
  - `grep -l -E '^#{1,3} (Contents|Table of contents|Quick reference|Index)' docs/*.md`
    → 15 files (the 5 pre-existing + all 10 edited pages); docs/README.md
    (46 lines) exempt; review-checklist.md already matched at baseline.
  - Per-page `head -30` shows the what/when paragraph directly under the title.
  - No content drift, mechanically: `git diff --numstat -- docs/<page>.md`
    shows 0 in the deletions column for every edited page (insertions only —
    no pre-existing line removed or modified).
- **After Phase 2**:
  - Gap loop (survey's verify): `for j in $(find benchmarks -name '*.json' | sort); do grep -rl --include='*.md' -F "$(basename $j)" benchmarks docs > /dev/null || echo "GAP: $j"; done`
    → no GAP line outside the 4 df-sweep files (those are named via the
    brace-glob at fr003-calibration/README.md:30 — assumption A6).
  - Inventory doc names all 30 artifacts (count rows/entries = 30).
  - `git status --porcelain benchmarks | grep '\.json'` → empty (no JSON byte touched).
  - `python -m pytest tests/test_ablation_artifact.py tests/test_gen_benchmark_tables.py tests/test_verify_datasource.py`
    → green (blob pins, sentinel byte-idempotence, datasource tree hashes).
- **After Phase 3**:
  - `grep -c '](README.md)' docs/*.md` → ≥1 for the 15 pages; docs/README.md exempt.
  - Link checker over docs/**/*.md + README.md → 0 broken, exit 0 (survey's
    checker behavior: strip inline code, resolve relative targets, ignore
    http/mailto/#anchors — assumption A4 covers the script itself).
- **After Phase 4**: all of the above green in one pass, plus
  `python -m pytest tests/test_gen_benchmark_tables.py` (incl.
  `test_second_run_is_byte_identical`) and a `git diff --numstat` drift
  audit over every docs page touched by Phases 1/3 (deletions column 0).

## Risks & mitigations
- Risk: long pages (cli-reference 491, architecture 262, scip 269) tempt
  content rewrites during orientation work → mitigation: Phase 1 edits are
  insertions-only at the page top; the pure-additive `git diff --numstat`
  check runs at Phase 1 and again in Phase 4 (spec risk note honored).
- Risk: 10 pages drift into 10 bespoke block formats (AC2) → mitigation:
  shape locked on one small page first; exemplars pinned in survey
  Supporting evidence A.
- Risk: inventory work collides with sentinel-generated regions in
  docs/benchmarks.md → mitigation: S2 serialization rule + byte-idempotence
  test runs at Phase 2 checkpoint, not only Phase 4.
- Risk: sealed/blob-pinned artifacts edited by accident → mitigation: S5
  excludes them from every writer; three guard test files gate Phases 2 and 4.

## Delivery
Branch `docs/human-readable-docs` (per spec.md header). One commit per task,
docs + any companion changes together; conventional commits (`docs: ...`).
Per AGENTS.md: never push to main; pre-commit must pass (never
`--no-verify`); PR with the audit checklist; CI green before merge.
Root README.md is not edited by any phase (A1).

## Explicit assumptions (marked where survey/spec lacks decisive evidence)

- **A1 — root README.md untouched.** spec.md's Out list defers it (revamped
  2026-08-18, cb65cae). FR-003's literal requirement is docs pages →
  docs/README.md, not root → index. Survey's surprise ("the docs index is
  linked from nowhere, not even root README.md") is real but out of scope;
  the single additive root-README link is a follow-up needing an explicit
  orchestrator decision, not part of any FR here.
- **A2 — inventory placement.** Plan assumes the one-place inventory lands
  in a file outside Phase 1's ten pages (a new file, or docs/benchmarks.md
  outside sentinels — placement is tech-spec's call). If it lands in
  docs/benchmarks.md, exception S2 activates for that one file.
- **A3 — no self-back-link.** The back-link count is over the 15 pages;
  docs/README.md is the index itself (survey: "16 files in docs/ (15 pages +
  README.md index)").
- **A4 — link-check script provenance.** Survey's checker ran from
  `/tmp/check_doc_links2.py` (ephemeral). Phase 3/4 checkpoints depend on it
  being re-created or checked in with the same behavior; tech/test specs
  own that.
- **A5 — review-checklist.md boundary.** Exactly 100 lines; FR-001's ">~100"
  threshold means no table is required, and it already carries a
  Quick-reference heading (survey) — no Phase 1 work expected there.
- **A6 — brace-glob caveat in the gap check.** The strict basename loop
  prints 4 false GAP lines for the df sweeps (named via
  `sweep-df0.{75,80,85,90}.json` at fr003-calibration/README.md:30). The
  Phase 2 criterion is "no GAP outside those 4" unless the checker is
  refined to expand brace-globs (tech-spec may fold that in).
