# Test Cases: docs-human-readable

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-18
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. Derived from `spec.md` (user stories,
acceptance criteria, FRs) only; `survey.md` was used for exactly two things
— baseline citations and the verify commands regression cases cite as pass
conditions. No implementation docs were read.

**Conventions**
- Cases stay in business language; pass conditions name exact commands
  (that is the point — they are the proof commands the closing audit runs)
  or a human observation. Commands run from the repo root.
- Baseline numbers were re-counted this session and match the survey
  verbatim: 16 docs files (15 pages + the index); 30 benchmark JSON
  artifacts; the naming search prints 7 GAP lines; the back-link loop flags
  15/15 pages; the link checker reports 19 files checked, 0 broken.
- TC IDs are permanent. Dropped cases would be struck through with the
  decision ID that killed them, never renumbered.

---

## TC-001 — Every page opens with a what/when orientation summary
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** any page in the docs set — including the shortest (the docs
  index, 46 lines at baseline) and the longest (491 lines at baseline)
- **When** a reader opens it
- **Then** within the first ~30 lines under the title it carries a
  one-paragraph orientation block saying what the page covers and when to
  read it, before any body content
- **Pass condition** (manual, mechanically assisted):
  ```bash
  for f in docs/*.md; do echo "== $f"; head -30 "$f"; done
  ```
  Human confirms per page: a short prose paragraph (not a heading, not
  body content) directly under the title answering "what is this" and
  "when should I read it". Shortest and longest page both comply — length
  neither excuses nor waives the summary.

## TC-002 — Long pages carry a Contents / Quick-reference table
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** a docs page whose length exceeds ~100 lines
- **When** opened
- **Then** its orientation block includes a Contents or Quick-reference
  table letting the reader jump without reading the body
- **Pass condition** (auto; pair of commands, both re-run post-change):
  ```bash
  wc -l docs/*.md
  grep -n -E '^#{1,3} (Contents|Table of contents|Quick reference|Index)' docs/*.md
  ```
  Every page whose re-counted length exceeds ~100 lines appears in the
  grep output. Baseline (survey FR-001 verify run, re-confirmed this
  session): the grep matches 5 files — the bug log, the benchmarks
  reference, the audit checklist, the review checklist, the architecture
  overview — while 10 pages over 100 lines lack a table.

## TC-003 — The orientation convention is uniform across the set
- **Story**: US1 · **Traces to**: FR-001, AC2
- **Given** the full docs set
- **When** any page's structure changes
- **Then** the orientation block keeps the same shape on every page — the
  established scannable convention (index-table + TL;DR, as the bug log
  and benchmarks quick reference use at baseline), not one bespoke format
  per page
- **Pass condition** (manual):
  ```bash
  head -15 docs/*.md
  ```
  Viewed side by side, all pages show one repeated pattern — title,
  what/when paragraph, then (where required) the contents/quick-reference
  table — in the same positions and shape. A page whose summary sits
  after body content, spans multiple sections, or uses a structurally
  different layout fails this case even if TC-001 passes for it.

## TC-004 — Orientation work does not alter technical content (guard)
- **Story**: US1 · **Traces to**: FR-001, AC1, AC2
- **Given** a docs page that already carries technical content
- **When** the orientation block and back-link are added
- **Then** every pre-existing technical line survives verbatim — the
  change is additive (new opening block, new link); nothing reworded,
  moved, or dropped
- **Pass condition** (auto + review; diff against the branch point):
  ```bash
  git diff main...HEAD -- docs/
  git diff main...HEAD -- docs/ | grep '^-' | grep -v '^---'
  ```
  The deletion listing prints nothing from any content page's body; the
  only lines it may contain are docs-index table rows (index-entry
  rewording is in scope). The full diff, on review, shows additions
  confined to the opening orientation region and the back-link. The
  generated reference tables inside the benchmarks page must be untouched
  outside their guarded regions — covered mechanically by TC-008(b).

## TC-005 — Boundary: the ~100-line threshold (at it, and crossing it)
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** the page sitting exactly at the threshold (100 lines at
  baseline — the review checklist) and a page just past it (123 lines at
  baseline)
- **When** the table requirement is evaluated
- **Then** the exactly-100 page is not required to carry a table
  ("exceeds ~100" is not met at exactly 100; carrying one anyway is
  allowed but not demanded), while the just-over page is required to. A
  page that only crosses the threshold because the added orientation
  block pushed it past 100 now satisfies "exceeds ~100" and must carry
  the table; the "~" tolerance excuses only pages within a few lines of
  the boundary, judged by a human
- **Pass condition** (auto + human judgment at the edge):
  ```bash
  wc -l docs/*.md
  grep -n -E '^#{1,3} (Contents|Table of contents|Quick reference|Index)' docs/*.md
  ```
  Re-count post-change: every page clearly over 100 lines appears in the
  grep; the exactly-at-threshold page passes either way; no page in the
  borderline band (roughly 100–105 final lines) is failed for missing a
  table, and none well past it is excused.

---

## TC-006 — Every committed measurement artifact is named by human-readable docs
- **Story**: US2 · **Traces to**: FR-002, AC3
- **Given** any committed machine artifact under the benchmarks tree (30
  at baseline)
- **When** someone searches the repository's human-readable docs for that
  artifact's filename
- **Then** the search finds a companion — a sibling README/companion doc
  or a generated table row — that names it
- **Pass condition** (auto, re-runnable):
  ```bash
  for j in $(find benchmarks -name '*.json' | sort); do
    grep -rl --include='*.md' -F "$(basename $j)" benchmarks docs > /dev/null \
      || echo "GAP: $j"; done
  ```
  Prints nothing. Baseline (survey FR-002 verify run, re-confirmed this
  session): prints 7 GAP lines — 3 artifacts no doc names at all, plus 4
  named only via a shorthand list (`sweep-df0.{75,80,85,90}.json`) in one
  sibling README, which a strict filename search cannot expand. Post-change
  the search must print zero lines: gaps filled, and every
  shorthand-named artifact also named plainly somewhere (via TC-007), so
  the strict form stays a truthful one-command proof.

## TC-007 — The artifact→companion mapping is recorded in one place
- **Story**: US2 · **Traces to**: FR-002, AC3
- **Given** the complete artifact set
- **When** a contributor wants the mapping from each artifact to its
  human rendering
- **Then** a single inventory page enumerates every artifact with its
  companion, identifying each artifact unambiguously (by path where two
  artifacts share a filename — two provenance files and two manifest
  files do at baseline)
- **Pass condition** (auto, location-agnostic — it must hold whichever
  page ends up being the inventory):
  ```bash
  best=0
  for d in $(git ls-files '*.md'); do n=0
    for j in $(find benchmarks -name '*.json'); do
      grep -qF "$(basename "$j")" "$d" && n=$((n+1)); done
    [ "$n" -gt "$best" ] && best=$n; done
  echo "single-doc max coverage: $best/$(find benchmarks -name '*.json' | wc -l)"
  ```
  Reports `30/30` (or N/N for the re-counted total — the invariant is
  "one doc covers all current artifacts"). Baseline, this session:
  `9/30` — no single doc comes close, which is the gap this case guards
  against regressing back. Human observation additionally confirms
  shared-basename entries are distinguished by path.

## TC-008 — Sealed measurement artifacts stay byte-untouched (guard)
- **Story**: US2 · **Traces to**: FR-002, AC3
- **Given** the sealed and integrity-pinned measurement artifacts and
  generated tables
- **When** companion gaps are filled
- **Then** their bytes do not change — gaps are filled by new companion
  docs or table rows, never by editing the artifacts
- **Pass condition** (auto):
  - (a)
    ```bash
    git diff --name-only main...HEAD -- benchmarks -- '*.json'
    ```
    Prints nothing (no committed JSON artifact is modified on the branch;
    new companion docs may be added).
  - (b) The repository's existing integrity seals — the
    byte-identical-blob checks over sealed benchmark data, the tree-hash
    verification over the datasource corpora, and the byte-idempotence
    check over the generated reference tables documented in survey
    Supporting evidence C — all still succeed when the repository's
    verification suite runs.

## TC-009 — Boundary: an artifact added later is caught if unnamed
- **Story**: US2 · **Traces to**: FR-002, AC3
- **Given** the inventory contract
- **When** a new measurement artifact is committed under the benchmarks
  tree with no doc naming it
- **Then** the naming check flags it — the promise is a re-runnable
  invariant, not a one-time cleanup
- **Pass condition** (auto probe; safe, self-cleaning — the probe is a
  disposable copy created and removed by the check itself, touching no
  sealed artifact):
  ```bash
  cp benchmarks/quality/warm_time.json benchmarks/quality/future-probe.json
  for j in $(find benchmarks -name '*.json' | sort); do
    grep -rl --include='*.md' -F "$(basename $j)" benchmarks docs > /dev/null \
      || echo "GAP: $j"; done   # must print: GAP: benchmarks/quality/future-probe.json
  rm benchmarks/quality/future-probe.json
  for j in $(find benchmarks -name '*.json' | sort); do
    grep -rl --include='*.md' -F "$(basename $j)" benchmarks docs > /dev/null \
      || echo "GAP: $j"; done   # must print nothing (steady state from TC-006)
  ```
  The probe run prints exactly one GAP line naming the newcomer; after
  cleanup the steady-state run is silent.

---

## TC-010 — Every page links back to the docs index
- **Story**: US3 · **Traces to**: FR-003, AC4
- **Given** any docs page — except the index page itself, which need not
  link to itself
- **When** rendered
- **Then** it carries a relative link back to the docs index, so one
  click always returns the reader to the hub
- **Pass condition** (auto, re-runnable):
  ```bash
  for f in docs/*.md; do [ "$(basename "$f")" = "README.md" ] && continue
    grep -qE '\]\(README\.md[)"]' "$f" || echo "NO BACK-LINK: $f"; done
  ```
  Prints nothing. Baseline (survey FR-003 verify run, re-confirmed this
  session): flags all 15 pages — none carries the back-link today.

## TC-011 — Zero broken relative links across the docs tree (regression)
- **Story**: US3 · **Traces to**: FR-003, AC4
- **Given** the docs set including pages nested in subdirectories
  (post-mortem pages and the like) plus the root README
- **When** a repo-wide relative-link check runs
- **Then** every relative link target resolves — zero broken links
- **Pass condition** (auto, self-contained — strips inline code, ignores
  web/mailto/anchor links, resolves targets relative to each file):
  ```bash
  python3 - <<'EOF'
  import re, pathlib, glob
  files = sorted(glob.glob('docs/**/*.md', recursive=True)) + ['README.md']
  bad = 0
  for f in files:
      t = pathlib.Path(f).read_text()
      t = re.sub(r'`[^`]*`', '', t)  # strip inline code spans
      for m in re.findall(r'\]\(([^)]+)\)', t):
          if m.startswith(('http:', 'https:', 'mailto:', '#')) or m == '':
              continue
          p = pathlib.Path(f).parent / m.split('#')[0]
          if not p.exists():
              print('BROKEN:', f, '->', m); bad += 1
  print(f'files checked: {len(files)} | TOTAL broken: {bad}')
  EOF
  ```
  Prints `TOTAL broken: 0`. Baseline (survey FR-003 verify run; re-run
  this session with this equivalent checker): 19 files checked, 0 broken —
  this case is the regression guard that keeps it at zero. The checker's
  teeth were verified this session: a planted broken link is reported.

## TC-012 — Standing regression: a page without the back-link is flagged
- **Story**: US3 · **Traces to**: FR-003, AC4
- **Given** the back-link check from TC-010
- **When** any page — existing or added in the future — omits the link
  back to the docs index
- **Then** the check names it, so a dead-end page cannot creep into the
  docs set silently
- **Pass condition** (auto; the TC-010 command must have teeth, not
  merely pass):
  ```bash
  printf '# Scratch page — no back-link probe\n' > docs/zz-backlink-probe.md
  for f in docs/*.md; do [ "$(basename "$f")" = "README.md" ] && continue
    grep -qE '\]\(README\.md[)"]' "$f" || echo "NO BACK-LINK: $f"; done
  rm docs/zz-backlink-probe.md
  ```
  The probe run lists `docs/zz-backlink-probe.md` among the offenders (at
  baseline the loop lists every page; post-change, exactly the probe);
  after cleanup the TC-010 steady-state run prints nothing. This case
  fails the suite if the check is ever reduced to a form that cannot
  detect a missing back-link.

---

## Coverage matrix

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 · AC1 (summary) | TC-001 | manual + command aid |
| FR-001 · AC1 (table on long pages) | TC-002 | auto |
| FR-001 · AC2 (uniform shape) | TC-003 | manual |
| FR-001 guard (content preservation) | TC-004 | auto + review |
| FR-001 boundary (~100-line threshold) | TC-005 | auto + judgment |
| FR-002 · AC3 (every artifact named) | TC-006 | auto |
| FR-002 · AC3 (one-place inventory) | TC-007 | auto + manual |
| FR-002 guard (sealed bytes) | TC-008 | auto |
| FR-002 boundary (future artifact) | TC-009 | auto |
| FR-003 · AC4 (back-link) | TC-010 | auto |
| FR-003 · AC4 (link resolution, regression) | TC-011 | auto |
| FR-003 standing regression (no-back-link flagged) | TC-012 | auto |

**Boundary edges by story**: US1 — shortest page (46 lines), longest page
(491 lines), exactly-at-threshold page, page crossing the threshold only
due to the added block (TC-001, TC-002, TC-005). US2 — shorthand-named
artifacts (brace-listed filenames), duplicate basenames in the inventory,
artifact added after the fix (TC-006, TC-007, TC-009). US3 — the index page
itself (self-link exempt), nested sub-directory pages, a future page added
without the back-link (TC-010, TC-011, TC-012).

**Untestable FRs**: none — all three FRs have fully observable pass
conditions above.
