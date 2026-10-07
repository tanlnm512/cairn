# Plan: pr-tooling

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1     | gh foundation + PR list | `cairn prs` prints open PRs (number, title, branch, author, CI state, review decision) for the workspace's remote; gh absent, unauthenticated, or erroring prints one actionable error and exits non-zero with no partial table | FR-001, FR-002, FR-006, NFR-001, NFR-004 | — |
| 2     | Per-PR graph impact | `cairn prs --impact PR#|branch` prints the PR's changed files resolved to symbols with their dependents (depth-capped), lists unindexed changed files explicitly, and states the local store and its build age | FR-003, FR-007, NFR-003 | Phase 1 |
| 3     | Merge-order risk + JSON | `cairn prs --conflicts` ranks open-PR pairs by shared community (or omits the section with a `cairn communities` hint when the tables are absent/empty); `--json` emits the full report payload | FR-004, FR-005 | Phase 2 |

## Dependencies
Phase 1 is the serial spine: the gh subprocess wrapper and its defensive JSON
parsers (with the pinned fixtures) are the only producers of PR records, and
the CLI command consumes them — the command cannot render what the wrapper
cannot fetch. Verified against the survey: nothing named `prs`/`gh` exists in
`src/cairn` yet (S1, S5), so both halves are greenfield and there is no
existing caller to break.

Phase 2 splits into two independent producers over that spine — the impact
pipeline entry (graph/blast.py, pure computation over a diff text) and the gh
fetchers that produce its inputs (diff text + base ref) — joined by the CLI
wiring task. Phase 3 builds on the fetchers (one diff per open PR) and the
shipped sections (--json must carry all of them).

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
- Independent: **impact pipeline** (`src/cairn/graph/blast.py` additions +
  their tests) ∥ **gh diff/base fetchers + store age** (`src/cairn/graph/prs.py`
  + their tests) within Phase 2 — the pipeline's entry takes `diff_text` and
  `base_ref` as plain arguments (tech-spec D-003/D-006 contract), so it is
  testable on fixture diff text with no gh and no network; disjoint files
- Strictly ordered: **gh wrapper + parsers + fixtures** → **CLI command** —
  the command imports the wrapper and renders the parsed records; both would
  touch a shared import edge
- Strictly ordered: **pipeline + fetchers** → **`--impact` CLI wiring** — the
  wiring consumes the pipeline's result dict and the fetchers' outputs
- Strictly ordered: **community overlap reader** → **`--conflicts`/`--json`
  CLI** — the JSON payload carries the conflicts section the reader computes
- Strictly ordered: **all of the above** → **docs** — documents the shipped
  flags and sections only

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: `cairn prs --help` exits zero (survey S1 verify — the
  command now exists) and, on a repo with open PRs, prints the six-column
  list; with gh unauthenticated it prints one guidance error and nothing
  else. `python3 -m pytest tests/ -q -k prs` green.
- **After Phase 2**: `cairn prs --impact <open-PR-number>` prints changed
  symbols with dependents, an explicit unindexed-files list, and the store
  line; the blast suite is untouched:
  `python3 -m pytest tests/ -q -k "blast and not infra"` (survey S2 verify).
- **After Phase 3**: `cairn prs --conflicts` ranks a same-community pair with
  the community named, and omits the section with the hint on a store without
  the tables (survey S4 verify — count 0); `cairn prs --json` carries list,
  impact (when requested), and conflicts (when requested).

## Risks & mitigations
- Risk: gh's JSON output shape evolves (spec risk) → mitigation: committed
  fixture snapshots + defensive parsing, unknown fields ignored, missing
  required fields error naming the gh version (tech-spec D-002).
- Risk: the local checkout lacks the PR branch, so no local diff exists →
  mitigation: the diff always comes from `gh pr diff`, never local git; only
  the base ref is resolved locally via `_resolve_base` with an
  `origin/`-prefixed fallback and fetch guidance (tech-spec D-003).
- Risk: the symbol-communities tables do not exist yet (survey S4 TODO) →
  mitigation: graceful-absent is the contract (FR-004): omit the section with
  a hint; read-path tests create the consumed tables from that spec's pinned
  DDL in fixture stores only — never in product code.
- Risk: the local store lags a PR branch, making impact stale (spec risk) →
  mitigation: FR-007's store line names the local index and its build age in
  every impact output (tech-spec D-004); advisory, labeled as such.
- Assumption: gh is installed and authenticated on target machines (spec
  Assumptions; verified available on this machine in the survey's supporting
  evidence) — absence is a first-class clean-failure path, not a crash.

## Delivery
Solo, PR-per-milestone: three PRs from `feat/pr-tooling` (branch per
spec.md), each landing code + tests + spec-doc updates together; docs ride
the Phase 3 PR. Every PR follows the C-01 shipping workflow (branch →
pre-commit → conventional commit → PR with audit checklist → CI).
