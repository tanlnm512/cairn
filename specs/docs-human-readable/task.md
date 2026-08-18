# Tasks: docs-human-readable

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
Execution mode: docs-only spec, orchestrator runs tasks in waves
(Wave A = Phase 1 ∥ Phase 2 · Wave B = Phase 3 · Wave C = Phase 4);
one commit lands for the whole plan at the closing audit, so "commit-sized"
below means a coherent, independently verifiable edit set.

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 3     | 0    |
| 2     | 4     | 0    |
| 3     | 2     | 0    |
| 4     | 1     | 0    |
| **Σ** | 10    | 0    |

## Phase 1: Orientation blocks (FR-001)
<!-- Checkpoint (plan.md, After Phase 1): `grep -l -E '^#{1,3} (Contents|Table of contents|Quick reference|Index)' docs/*.md` → 15 files (5 pre-existing + 10 edited; docs/README.md exempt at 46 lines); per-page `head -30` shows the what/when paragraph directly under the title; `git diff --numstat` deletions column 0 on every edited page (insertions only). Wave A. -->
- [ ] T001 [P] Lock the canonical orientation-block shape on the pilot page — complete the what/when paragraph and add the `## Contents` table on `docs/methodology-precise-vs-fuzzy.md` (123 lines, the smallest PARTIAL page), copying the E1 skeleton verbatim (D-001) (FR-001)
  - Gap (survey FR-001, PARTIAL 10/16): page carries "one-line blockquote line 3, no table" per the survey's per-page first-30-lines check.
  - Insertions-only at the page top; the summary states structure ("what's where"), never re-asserts body facts (spec assumption; drift is audited in T010).
  - Pilot-first is the plan's de-risk: the shape locked here is what T002 replicates.
  - Proof: `grep -n -E '^#{1,3} (Contents|Table of contents|Quick reference|Index)' docs/methodology-precise-vs-fuzzy.md` → 1 hit; `git diff --numstat -- docs/methodology-precise-vs-fuzzy.md` → deletions column 0.
- [ ] T002 [P] (after T001) Replicate the locked block on the 9 remaining PARTIAL pages — complete the what/when paragraph and add the `## Contents` table (anchor-linked, per D-007) on `docs/architecture.md` (262), `docs/cli-reference.md` (491), `docs/configuration.md` (177), `docs/contribution-workflow.md` (181), `docs/mcp-tools.md` (229), `docs/query-flow.md` (200), `docs/quickstart.md` (161), `docs/release-checklist.md` (141), `docs/scip.md` (269) (FR-001)
  - Gap (survey FR-001): all 10 PARTIAL pages are over 100 lines, so every one needs the Contents/Quick-reference half; several also lack the "when to read" sentence.
  - [P] within the batch: nine distinct files, per-page concurrent (plan P1); gated on T001 only for the shape.
  - Keep the five recognized pre-existing headings as-is (`BUGS.md:29:## Index`, `architecture-overview.md:9:## Table of contents`, `benchmarks.md:17:## Quick reference`, `audit-checklist.md:21:## Index`, `review-checklist.md:97:## Quick reference: the one-line review`) — the verify regex already treats them as one convention (D-001).
  - Proof: table-header grep over `docs/*.md` → 15 files; `git diff --numstat` deletions column 0 for all nine.
- [ ] T003 [P] Review the `## Start here` index rows in `docs/README.md`; apply additive wording fixes only where a page's role clarifies — skip rows already accurate; sole Phase-1 writer of this file per plan S3 (FR-001)
  - TODO (not a survey gap): survey records README.md as CONFORM (6/16); no index-row deficiency is evidenced — edit only if clarity requires, never rewrite.
  - `docs/README.md` takes no back-link (plan A3) and Phase 3 never writes it (S3).
  - Proof: `git diff --numstat -- docs/README.md` → deletions column 0 (untouched also passes).

## Phase 2: Artifact inventory + gap fill (FR-002)
<!-- Checkpoint (plan.md, After Phase 2): survey gap loop `for j in $(find benchmarks -name '*.json' | sort); do grep -rl --include='*.md' -F "$(basename $j)" benchmarks docs > /dev/null || echo "GAP: $j"; done` prints nothing (the inventory names every basename, resolving even the 4 brace-glob df sweeps); inventory rows = 30; `git status --porcelain benchmarks | grep '\.json'` empty; `python -m pytest tests/test_ablation_artifact.py tests/test_gen_benchmark_tables.py tests/test_verify_datasource.py` green. Wave A, parallel with Phase 1 (disjoint files). -->
- [ ] T004 [P] Create `benchmarks/README.md` (new additive file at the benchmarks root) — orientation block with back-link `../docs/README.md`, then the 30-row `| Artifact | Named by |` inventory transcribing survey Supporting evidence B verbatim, brace-glob family expanded to 4 individual rows, the 3 GAP rows flagged for T005/T006 (D-003) (FR-002)
  - Gap (survey FR-002): "No single inventory doc exists — mapping is scattered across sibling README/FIGURES/MEASURE/ablation.md + generated tables."
  - Key rows by repo-relative path, never basename — `quality.json` exists under both `baselines/DS-v1/` and `baselines/DS-v1.1/` (basename keying over-merges, survey Supporting evidence B).
  - Proof: inventory row count = 30; TC-007's single-doc coverage probe reports 30/30 (baseline 9/30).
- [ ] T005 [P] Append an `## Artifacts` table (`| Artifact | Role |`, one structure-stating line per row) naming `rows-fr004.json` to `benchmarks/quality/fr004-prf/FIGURES.md` (D-004) (FR-002)
  - Gap (survey FR-002): `benchmarks/quality/fr004-prf/rows-fr004.json` — "no .md anywhere names" it; the FIGURES.md "discuss the figures but never the filenames".
  - Sealed-set guard (survey Supporting evidence C): never edit `benchmarks/quality/ablation.json` or its blob-pinned companion `ablation.md`; never edit anything under `benchmarks/datasource/`.
  - Proof: gap-loop line for `rows-fr004.json` disappears.
- [ ] T006 [P] Append an `## Artifacts` table naming `rows-ds2.json` and `sweep-ds2-zeroshot.json` to `benchmarks/quality/ladder-v2/FIGURES.md` (D-004) (FR-002)
  - Gap (survey FR-002): both `benchmarks/quality/ladder-v2/rows-ds2.json` and `sweep-ds2-zeroshot.json` unnamed by any `.md`; `ablation.md:157` points only at the directory ("under `benchmarks/quality/ladder-v2/`.") and ablation.md is sealed — the fill goes to FIGURES.md, not there.
  - Proof: gap-loop lines for both ladder-v2 artifacts disappear.
- [ ] T007 (after T004) Add one hand-written pointer line in `docs/benchmarks.md` (hand-written territory only — outside the sentinel-generated regions) linking the new inventory (FR-002)
  - Connects the two hubs (D-003 consequence); the only Phase-2 write on `docs/benchmarks.md`, so no S2 conflict — Phase 3's edit of the same file lands in a later wave.
  - Proof: `python -m pytest tests/test_gen_benchmark_tables.py` green, including `test_second_run_is_byte_identical` (sentinel byte-idempotence, survey Supporting evidence C).

## Phase 3: Navigation back-links (FR-003)
<!-- Checkpoint (plan.md, After Phase 3): `grep -c '](README.md)' docs/*.md` → ≥1 for the 15 pages (docs/README.md exempt); link checker over docs/**/*.md + README.md + benchmarks/README.md → 0 broken, exit 0. Wave B — strictly after Phase 1 (S1: same 10 files, and the back-link consumes the block shape Phase 1 installs). -->
- [ ] T008 [P] Commit the link checker as `scripts/check_doc_links.py` (new additive standalone script; no existing script, test, or workflow modified — D-005), implementing the survey-validated algorithm: strip `inline code` spans first, extract link targets, resolve each relative to the containing file, ignore `http`/`mailto`/`#anchors`, scope `docs/**/*.md` + root `README.md` + `benchmarks/README.md`, plus the resolution-based back-link half (a link that resolves to `docs/README.md`, not a string match); exit 0 = green (FR-003)
  - TODO as a deliverable: the link-resolution half is already green per survey (`/tmp/check_doc_links2.py`: 19 files, "TOTAL broken: 0", exit 0), but that script was ephemeral — the committed form does not exist (session `ls scripts/check_doc_links.py` → No such file).
  - Wave-A eligible by design: shares no file with any Phase 1/2 task and consumes nothing from them — land it early so T009 and T010 are proofed with it.
  - Proof: `python3 scripts/check_doc_links.py` from repo root reproduces the survey baseline (0 broken, exit 0) BEFORE back-links exist, then stays green after T009.
- [ ] T009 (after T002) Install the back-link line `← [Docs index](README.md)` as the first line under the H1 on all 15 non-index `docs/*.md` pages — the 10 Phase-1 pages plus the 5 already-conform pages `docs/BUGS.md`, `docs/architecture-overview.md`, `docs/audit-checklist.md`, `docs/benchmarks.md`, `docs/review-checklist.md`; `docs/README.md` exempt (D-002, plan A3) (FR-003)
  - Gap (survey FR-003): `grep -c '](README.md)' docs/*.md | grep -v ':0'` → empty — 0 of 15 pages link the index; "the docs index is linked from nowhere, not even root README.md".
  - S1: strictly after Phase 1 — 10 of the 15 targets are exactly Phase 1's pages, and the link's placement and formatting are defined by the block T001/T002 install; per-page concurrent within the batch (plan P4).
  - On `docs/benchmarks.md`: hand-written territory only, outside sentinels; on `methodology-precise-vs-fuzzy.md:4` the existing `../README.md` link resolves to the ROOT README and must not be counted as the back-link (resolution-based check, tech-spec E3).
  - Proof: `grep -c '](README.md)' docs/*.md` → 15 files ≥1; `python3 scripts/check_doc_links.py` → exit 0.

## Phase 4: Closing audit (FR-001, FR-002, FR-003)
<!-- Checkpoint (plan.md, After Phase 4): every phase checkpoint green in one pass over the union of edited files. Wave C — S4: strictly last. This phase owns no FR; it audits FR-001..003. -->
- [ ] T010 (after T009) Run the closing audit in one pass over the union of edited files — FR-001: table-header grep → 15 files, per-page `head -30` what/when check, `git diff --numstat` deletions-column-0 drift audit over every page Phases 1/3 touched; FR-002: gap loop prints nothing, inventory rows = 30, `git status --porcelain benchmarks | grep '\.json'` empty, `python -m pytest tests/test_ablation_artifact.py tests/test_gen_benchmark_tables.py tests/test_verify_datasource.py` green; FR-003: back-link grep 15 ≥1 and `python3 scripts/check_doc_links.py` exit 0 (FR-001, FR-002, FR-003)
  - S4: strictly last — audits the union of ALL Phase 1-3 edits (T001-T009); do not start while any earlier task is open.
  - Then land per the repo's execution mode: single conventional commit for the whole plan, AGENTS.md shipping workflow (branch `docs/human-readable-docs`, pre-commit, PR with the audit checklist).
  - Proof: every command above green in one pass; the audit's outputs are the done-notes recorded against T001-T009.

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T011~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them (none
  dropped in this spec)
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)`; serial runs need a reason, parallel
  runs need none
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
- Status derives from survey.md evidence only: a task may be ticked only
  with a passing verify command recorded in survey.md or re-run this
  session (none qualify today — all three FRs are PARTIAL)
