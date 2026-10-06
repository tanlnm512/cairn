# Plan: grade-a-ratchet

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-05

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1 | Audit data home | The tracked architecture HTML and audit report move with `git mv`, their repo-relative links remain browsable, and `docs/audits/` is the stable input location for the status tool | FR-001 | — |
| 2 | Standalone quality counters | The audit parser totals P0–P3 findings from relocated markdown plus status sidecars, and the Python comment scanner emits a deterministic shrink-only baseline; both expose stable JSON and stay under 5 s | FR-002, FR-003, NFR-003, NFR-004, NFR-005 | Phase 1 |
| 3 | Gates and report consumption | `make audit-status`, the local pre-commit hook, a dedicated CI job, and the existing diagnostic report consume the two counters, so drift is visible locally, in CI, and through machine-readable output | FR-004 | Phase 2 |

## Dependencies
Phase 1 is the serial data spine: `make audit-status` must read
`docs/audits/*.md`, so the audit markdown has to reach that contract before the
parser's default path can be demoed. The architecture move shares no producer
with the parser and is isolated in Phase 1 to keep the path change reviewable.

Phase 2 splits into two independent producers over that spine:

- Audit status reads `docs/audits/*.md` and matching `*.status.json` sidecars.
- Comment style scans `src/` and reads `docs/audits/comment-style-baseline.json`.

They meet only in Phase 3, where Make, pre-commit, CI, and report wiring
consume both command interfaces. No Phase 2 producer imports the other.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
- Independent: **audit-status counter** (`src/cairn/cli/system/audit_status.py`,
  `docs/audits/2026-10-02.status.json`, and its focused tests) ∥
  **comment-style counter** (`src/cairn/cli/system/comment_style.py`,
  `docs/audits/comment-style-baseline.json`, and its focused tests) — disjoint
  modules and data files; neither consumes the other's symbols or output.
- Strictly ordered: **root relocation** → **audit-status counter** — the
  counter's default input contract is the relocated `docs/audits/*.md` tree.
- Strictly ordered: **both Phase 2 counters** → **Make/pre-commit/CI wiring** —
  wiring invokes the module command interfaces and baseline produced by them.
- Strictly ordered: **both Phase 2 counters** → **diagnostic-report section** —
  the report imports their pure collection functions and publishes their counts.
- Independent: **gate wiring** (`Makefile`, `.pre-commit-config.yaml`,
  `.github/workflows/ci.yml`) ∥ **report section**
  (`src/cairn/cli/system/report.py` and its focused tests) — disjoint files;
  each consumes the Phase 2 interfaces directly.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: no tracked artifact by the two old root names remains;
  both destination paths resolve, and the moved HTML's documentation links
  resolve from its new directory. Verify with `git ls-files --directory | grep -v / | sort`
  and `python3 scripts/check_doc_links.py`.
- **After Phase 2**: audit counts read `P0=2`, `P1=51`, `P2=88`, `P3=3` from the
  relocated report; the comment checker passes an unchanged tree, prints its
  remaining count, and rejects both additions and stale baseline counts; both
  JSON modes parse. Verify with the focused pytest selectors for the two new
  test modules, then repeat each direct module command twice and compare output.
- **After Phase 3**: `make audit-status` and `make comment-style` exit zero;
  `make audit-status ARGS=--json` and `make comment-style ARGS=--json` emit one
  JSON object each; `pre-commit run check-comment-lengths --all-files` exits
  zero; the CI workflow has a dedicated quality-gate job; and `cairn report --json`
  carries both counters. Verify with those commands plus the report and quality
  test selectors.

## Risks & mitigations
- Risk: comment parsing misclassifies a legitimate block → mitigation: parser
  tests use real source shapes, and a false positive is removed by shrinking the
  baseline rather than adding checker options (tech-spec D-004).
- Risk: a baseline edit quietly grows the allowlist → mitigation: regeneration
  compares fingerprint counts and refuses a new fingerprint or higher count;
  initialization is allowed only while the baseline file is absent.
- Risk: audit markdown drift changes finding IDs or priority sections →
  mitigation: parse only the findings-index tables, cross-check row counts with
  priority-heading counts, reject duplicate/unknown sidecar IDs, and name the
  offending audit or sidecar on failure.
- Risk: JSON output is indistinguishable from human text in CI logs →
  mitigation: JSON is the only stdout payload on success; failures use concise
  diagnostics (JSON error object when `--json` was requested) and nonzero exits.
- Risk: the Make frontend cannot pass a literal long option after a target →
  mitigation: targets forward `ARGS` (so the supported spellings are
  `make audit-status ARGS=--json` and `make comment-style ARGS=--json`) while the
  modules remain directly executable for normal `--json` use.

## Delivery
Solo cadence: one PR per milestone. Each PR follows the mandatory
`branch → pre-commit run --all-files → conventional commit → push feature
branch → audited PR → watch CI` path; Phase 2 and Phase 3 branches stack on the
preceding milestone rather than reopening landed files.
