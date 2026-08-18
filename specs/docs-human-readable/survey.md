# Survey: docs-human-readable

**Created**: 2026-08-18 | **Baseline**: main @ cb65cae (working tree, branch `chore/spec-cleanup-2026-08-18`)
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item FR-001: "Every docs/*.md page opens with an orientation block
  (what/when summary + Contents/Quick-reference table when >~100 lines)"
  evidence:   `wc -l docs/*.md README.md` (this session) — 16 files in docs/
              (15 pages + README.md index). Pages >100 lines WITHOUT any
              Contents/Quick-ref/Index table (grep -E '^#{1,3} (Contents|Table
              of contents|Quick reference|Index)' matched only 5 files, below):
                cli-reference.md 491 · architecture-overview.md 481 (has ToC)
                benchmarks.md 468 (has Quick reference) · BUGS.md 365 (has
                Index) · audit-checklist.md 336 (has Index) · scip.md 269 ·
                architecture.md 262 · mcp-tools.md 229 · query-flow.md 200 ·
                contribution-workflow.md 181 · configuration.md 177 ·
                quickstart.md 161 · release-checklist.md 141 ·
                methodology-precise-vs-fuzzy.md 123 · review-checklist.md 100
                (exactly 100 — no table required) · README.md 46
              Table-header grep hits (verbatim):
                BUGS.md:29:## Index
                architecture-overview.md:9:## Table of contents
                benchmarks.md:17:## Quick reference
                audit-checklist.md:21:## Index
                review-checklist.md:97:## Quick reference: the one-line review
              Per-page first-30-lines check (head -30, this session):
                CONFORM (6/16): README.md, BUGS.md, architecture-overview.md,
                  audit-checklist.md, benchmarks.md, review-checklist.md
                PARTIAL (10/16 — a what-summary exists under the title but no
                  when + no Contents table): architecture.md ("This document
                  describes cairn's design: what it is, how a query flows
                  through its five layers..." lines 3-5), cli-reference.md
                  ("The `cairn` command is the human-facing interface..." lines
                  3-5), configuration.md (what/when lines 3-6, no table),
                  contribution-workflow.md (blockquote lines 3-8 + "**The
                  flow:** branch → local gates → ... post-task." lines 12-13,
                  no table), mcp-tools.md (lines 3-6, no table),
                  methodology-precise-vs-fuzzy.md (one-line blockquote line 3,
                  no table), query-flow.md (lines 3-4, no table), quickstart.md
                  (lines 3-6 incl. "This guide gets you from zero to querying a
                  real repo in a few minutes." line 6, no table),
                  release-checklist.md (lines 3-5, no table), scip.md (lines
                  3-7, no table)
              Matches spec.md's claim of "10 of the 15" non-conforming pages.
  status:     PARTIAL
  verify:     grep -n -E '^#{1,3} (Contents|Table of contents|Quick reference|Index)' docs/*.md
              (run: 5 files match; every other page >100 lines lacks a table)
  gap:        10 pages need the Contents/Quick-reference half of the block
              (several also lack the "when to read" sentence); convention
              exemplars already exist in-repo (see Supporting evidence A)

item FR-002: "Complete documented JSON→companion inventory for
  benchmarks/**/*.json; fill gaps; sealed artifacts byte-untouched"
  evidence:   `find benchmarks -name '*.json' | wc -l` → 30 artifacts (list
              under Supporting evidence B). `find benchmarks -name '*.md' |
              wc -l` → 14 companion docs. Per-artifact grep (basename, -F,
              over benchmarks/ + docs/) maps 27/30 to ≥1 naming companion —
              including `benchmarks/quality/fr003-calibration/README.md:30`:
                | `sweep-baseline.json`, `sweep-df0.{75,80,85,90}.json` | raw k-fold sweep documents |
              (brace-glob names the 4 df sweeps). GAPS — no .md anywhere names
              these 3 (grep -rF over basename returned nothing):
                benchmarks/quality/fr004-prf/rows-fr004.json
                benchmarks/quality/ladder-v2/rows-ds2.json
                benchmarks/quality/ladder-v2/sweep-ds2-zeroshot.json
              (ladder-v2/FIGURES.md and fr004-prf/FIGURES.md discuss the
              figures but never the filenames; ablation.md:157 points only at
              the directory: "under `benchmarks/quality/ladder-v2/`.")
              No single inventory doc exists — mapping is scattered across
              sibling README/FIGURES/MEASURE/ablation.md + generated tables.
  status:     PARTIAL
  verify:     for j in $(find benchmarks -name '*.json' | sort); do grep -rl
              --include='*.md' -F "$(basename $j)" benchmarks docs > /dev/null
              || echo "GAP: $j"; done
              (run: prints 7 GAP lines — the 3 above PLUS the 4 df sweeps,
              which the strict basename form misses because
              fr003-calibration/README.md:30 names them as the brace-glob
              `sweep-df0.{75,80,85,90}.json`; true gap set = 3)
  gap:        3 unnamed artifacts (all in the T023-era quality campaigns); no
              one-place inventory; sealed/blob-pinned set that must stay
              byte-untouched identified in Supporting evidence C

item FR-003: "Every docs/*.md links back to docs/README.md; all relative
  links resolve"
  evidence:   Back-link half — TODO. `grep -c '](README.md)' docs/*.md | grep
              -v ':0'` → empty (0 of 15 pages). `grep -rn 'docs/README'
              docs/*.md README.md AGENTS.md` → empty. The only README links
              found: docs/README.md:3 → ../README.md (root) and
              methodology-precise-vs-fuzzy.md:4 → ../README.md (root); the
              docs index is linked from nowhere, not even root README.md
              (README.md links 12+ docs pages by name, never docs/README.md).
              audit-checklist.md's two "README.md" hits are table/checklist
              text (audit-checklist.md:32, :263), not links.
              Link-resolution half — DONE. python3 /tmp/check_doc_links2.py
              (strips `inline code`, extracts ](target), resolves relative to
              each file, ignores http/mailto and #anchors) over docs/**/*.md
              + README.md: 19 files, "TOTAL broken: 0", exit 0. Per-file: all
              "pass". (First draft without code-span stripping flagged 1
              false positive: BUGS.md:26 `[→ postmortem](postmortems/...)` is
              an inline-code placeholder, not a rendered link.)
  status:     PARTIAL
  verify:     grep -c '](README.md)' docs/*.md   (run: all :0)
              python3 /tmp/check_doc_links2.py                (run: 0 broken)
  gap:        0/15 pages carry the back-link to docs/README.md (link
              resolution itself is already green)
```

## Supporting evidence

### A. Convention exemplars FR-001 will replicate (verbatim, `head`/`sed` this session)

BUGS.md opener (lines 3-5):
```
Lessons learned, root-caused. Each entry is a one-time discovery converted to
permanent, queryable memory — so the same bug doesn't get solved twice.
```
BUGS.md usage steps (lines 10-13, the index-table shape):
```
1. **Scan the index table** below for the symptom, area, or date you need.
2. **Jump to the entry** (same slug) under `## Entries` for the full
   symptom → root cause → fix → prevention → related detail.
```
BUGS.md index rows (sed -n '29,38p'):
```
| Date | Slug | Area | Symptom (one line) |
|------|------|------|--------------------|
| 2026-08-06 | comments-only-code-drift | agent-safety | Sub-agents silently altered code during a "comments-only" trim task. |
| 2026-08-06 | portable-path-stale-comments | graph | Comments claimed relative paths; builder stored absolute. [→ postmortem](postmortems/2026-08-06-portable-paths.md) |
```
benchmarks.md `## Quick reference` (lines 17-24, first rows):
```
| Command | Measures | Output |
|---------|----------|--------|
| `cairn eval` | Retrieval quality — Recall@10 and MRR vs ground truth; `--sweep` / `--kfold` lever sweeps (pooled paired bootstrap) | per-corpus table, JSON, or sweep document |
| `cairn bench --suite perf` | Build phase timings, embed cost, query latency | per-op table (median / p95 / ops/sec) |
```
review-checklist.md opener (lines 3-13):
```
> The review/audit gate for every change (feature, improvement, bugfix). Run by
> the author before requesting review, and by the reviewer before approving.
...
**TL;DR — every PR must answer four questions:**
```
docs/README.md index shape (lines 7-10):
```
## Start here

| Doc | What it covers |
|-----|----------------|
```

### B. Full JSON→companion inventory (re-counted this session: 30 JSONs, 14 .md)

`find benchmarks -name '*.json' | sort` (verbatim list; → names it):
```
benchmarks/baselines/DS-v1.1/quality.json -> DS-v1.1/README.md, DS-v1/README.md, ablation.md, docs/benchmarks.md
benchmarks/baselines/DS-v1/agent.json -> DS-v1/README.md, DS-v1.1/README.md
benchmarks/baselines/DS-v1/perf.json -> DS-v1/README.md, DS-v1.1/README.md
benchmarks/baselines/DS-v1/quality.json -> DS-v1/README.md, DS-v1.1/README.md, ablation.md, docs/benchmarks.md
benchmarks/baselines/DS-v1/scaling.json -> DS-v1/README.md, DS-v1.1/README.md
benchmarks/datasource/ds2/ground_truth/VERIFICATION.json -> VERIFICATION.md, AUTHORING.md
benchmarks/datasource/ds2/ground_truth/manifest.json -> VERIFICATION.md, AUTHORING.md, docs/benchmarks.md
benchmarks/datasource/ds2/power-analysis.json -> power-analysis.md
benchmarks/datasource/ds2/second-corpus/attrs-26.1.0/provenance.json -> DECISION.md
benchmarks/datasource/manifest.json -> AUTHORING.md, VERIFICATION.md, docs/benchmarks.md
benchmarks/datasource/t2/provenance.json -> DECISION.md
benchmarks/quality/ablation.json -> MEASURE.md, ablation.md, power-analysis.md, VERIFICATION.md, fr003-calibration/README.md
benchmarks/quality/fr003-calibration/analysis.json -> fr003-calibration/README.md, power-analysis.md
benchmarks/quality/fr003-calibration/d03-diagnostic.json -> fr003-calibration/README.md, ablation.md
benchmarks/quality/fr003-calibration/p95-remeasure.json -> fr003-calibration/README.md, ablation.md
benchmarks/quality/fr003-calibration/rows-fr003.json -> fr003-calibration/README.md
benchmarks/quality/fr003-calibration/sweep-baseline.json -> fr003-calibration/README.md
benchmarks/quality/fr003-calibration/sweep-df0.75.json  } named by brace-glob
benchmarks/quality/fr003-calibration/sweep-df0.80.json  }  `sweep-df0.{75,80,85,90}.json`
benchmarks/quality/fr003-calibration/sweep-df0.85.json  }  fr003-calibration/README.md:30
benchmarks/quality/fr003-calibration/sweep-df0.90.json  }
benchmarks/quality/fr004-prf/rows-fr004.json -> GAP (no .md names it)
benchmarks/quality/fr004-prf/sweep-prf-docs10.json -> MEASURE.md
benchmarks/quality/fr004-prf/sweep-prf-docs3.json -> MEASURE.md
benchmarks/quality/fr005-mv/sweep-mv.json -> MEASURE.md (sibling dir doc: fr005-mv/SIZE.md)
benchmarks/quality/ladder-v2/rows-ds2.json -> GAP (no .md names it)
benchmarks/quality/ladder-v2/sweep-ds2-zeroshot.json -> GAP (no .md names it)
benchmarks/quality/ladder-v2/sweep-ladder-enrich-rerankoff.json -> MEASURE.md
benchmarks/quality/ladder-v2/sweep-ladder-enrichidf-rerankoff.json -> MEASURE.md
benchmarks/quality/warm_time.json -> docs/benchmarks.md (Quick reference row)
```
Companion .md set (`find benchmarks -name '*.md' | sort`, 14 files):
DS-v1.1/README.md · DS-v1/README.md · ds2/ground_truth/AUTHORING.md ·
ds2/ground_truth/VERIFICATION.md · ds2/power-analysis.md ·
ds2/second-corpus/DECISION.md · ds2/second-corpus/attrs-26.1.0/CHANGELOG.md ·
ds2/second-corpus/attrs-26.1.0/README.md · quality/MEASURE.md ·
quality/ablation.md · quality/fr003-calibration/README.md ·
quality/fr004-prf/FIGURES.md · quality/fr005-mv/SIZE.md ·
quality/ladder-v2/FIGURES.md

### C. Sealed / blob-pinned machinery (never edit; accompany only)

- `tests/test_ablation_artifact.py:43`: `ARTIFACT = QUALITY / "ablation.json"`;
  blob-pinned at :96-110 (`test_embedded_first_campaign_is_byte_identical_to_its_recorded_blobs`,
  `V1_JSON_BLOB`/`V1_MD_BLOB` sha checks via `_git_blob_sha`, :81-82).
- `scripts/verify_datasource.py` + `tests/test_verify_datasource.py:97`
  (`test_flipped_byte_in_generated_file_exits_nonzero`) — datasource corpora
  (ds2, t2) are tree-hash pinned; one flipped byte fails verification.
- `docs/benchmarks.md` reference tables are sentinel-generated by
  `scripts/gen_benchmark_tables.py`; `tests/test_gen_benchmark_tables.py:231`
  `test_second_run_is_byte_identical` (byte-idempotence) and :14 "TC-028
  bytes outside the sentinels are never touched".

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
