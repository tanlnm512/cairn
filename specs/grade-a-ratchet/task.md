# Tasks: grade-a-ratchet

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: pending — delivery evidence; the orchestrator writes `commit @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 1     | 0    |
| 2     | 2     | 0    |
| 3     | 3     | 0    |
| **Σ** | 6     | 0    |

## Phase 1: Audit data home (FR-001)
<!-- Checkpoint: no tracked artifact by the two old root names remains; both destination paths resolve; the moved HTML's documentation links resolve from its new directory -->
- [ ] T001 Move the tracked root artifacts with `git mv` — `architecture.html` to `docs/diagrams/architecture.html` and `audit-findings-2026-10-02.md` to `docs/audits/2026-10-02.md` — and retarget the moved HTML's internal `docs/architecture.md`, `docs/indexing.md`, and `docs/mcp-tools.md` references to `../` equivalents without changing historical CHANGELOG mentions (FR-001)
  - Survey basis: FR-001 records both files tracked at root, both destinations absent, and no live inbound link outside historical changelog text.
  - Touches:
    - `architecture.html`
    - `audit-findings-2026-10-02.md`
    - `docs/diagrams/architecture.html`
    - `docs/audits/2026-10-02.md`
  - Verify before implementing: `git ls-files --directory | grep -v / | sort`
  - Proof when complete: `git ls-files --directory | grep -v / | sort` shows neither old root name, both destination files resolve, and `python3 scripts/check_doc_links.py` exits zero.

## Phase 2: Standalone quality counters (FR-002, FR-003, NFR-003, NFR-004, NFR-005)
<!-- Checkpoint: audit counts read P0=2, P1=51, P2=88, P3=3; the comment checker passes an unchanged tree and rejects additions and stale counts; JSON parses; repeated runs are identical -->
- [ ] T002 (after T001 — consumes relocated `docs/audits/2026-10-02.md` and its findings-index tables headed `### P0 — High (2)`, `### P1 — Medium (51)`, `### P2 — Low (88)`, and `### P3 — Systemic clusters (3)`) Add failing tests for index-only parsing, fixed-ID sidecars, P0–P3 aggregation, clean file-naming failures, JSON, and repeated identical output; then implement executable `src/cairn/cli/system/audit_status.py` with pure `collect_audit_status(root: Path) -> dict` and create strict initial `docs/audits/2026-10-02.status.json` with schema version 1 and an empty `fixed` list (FR-002, NFR-003, NFR-005)
  - Survey basis: FR-002 records no Make target, script, audits directory, sidecar schema, or sidecar.
  - Touches:
    - `src/cairn/cli/system/audit_status.py`
    - `docs/audits/2026-10-02.status.json`
    - `tests/test_audit_status.py`
  - Verify before implementing: `make audit-status`
  - Proof when complete: `uv run pytest tests/test_audit_status.py -q` passes; direct human mode prints all four priorities and total; direct `--json` mode parses with the tech-spec count fields; a second run is byte-identical.

- [ ] T003 [P] Add failing tests for four-line comment blocks, four-line docstrings, block splitting, syntax failure, duplicate growth, stale entries, initialization, shrink-only updates, JSON, and deterministic regeneration; then implement executable `src/cairn/cli/system/comment_style.py` with `scan_comment_violations(root: Path) -> list[dict]` and `check_comment_baseline(root: Path, baseline_path: Path) -> dict`, and initialize `docs/audits/comment-style-baseline.json` from the unchanged `src/` tree (FR-003, NFR-003, NFR-004, NFR-005)
  - Survey basis: FR-003 records no checker script, baseline, pre-commit hook, or CI job.
  - Touches:
    - `src/cairn/cli/system/comment_style.py`
    - `docs/audits/comment-style-baseline.json`
    - `tests/test_check_comment_lengths.py`
  - Verify before implementing: `git grep -n 'comment_length\|docstring.*checker\|check_comments\|comment_policy' -- ':!specs'`
  - Proof when complete: `uv run pytest tests/test_check_comment_lengths.py -q` passes; unchanged-tree mode exits zero and prints current remaining count; new and stale cases exit nonzero; initialization/update output is stable; the initial baseline is generated, not hand-written.

## Phase 3: Gates and report consumption (FR-004)
<!-- Checkpoint: make audit-status and make comment-style exit zero; ARGS=--json modes emit one parseable object; the local hook and dedicated CI job run; cairn report --json carries quality_gates; docs name the shipped surfaces -->
- [ ] T004 (after T002, T003 — consumes command interfaces `uv run --no-sync python -m cairn.cli.system.audit_status --json` and `uv run --no-sync python -m cairn.cli.system.comment_style --json` plus the initialized baseline) Wire phony `audit-status`, `comment-style`, and baseline-shrink Make targets with `ARGS` forwarding; add the local `check-comment-lengths` pre-commit hook; and add a dedicated `quality-ratchet` CI job that runs both human gates and parses both JSON outputs (FR-003, FR-004, NFR-003, NFR-005)
  - Survey basis: FR-003 records no hook or checker job; the Makefile inventory contains no quality targets.
  - Touches:
    - `./Makefile`
    - `.pre-commit-config.yaml`
    - `.github/workflows/ci.yml`
  - Verify before implementing: `grep -n 'comment' .pre-commit-config.yaml .github/workflows/ci.yml`
  - Proof when complete: `make audit-status`, `make comment-style`, both `ARGS=--json` variants, and `uv run --no-sync pre-commit run check-comment-lengths --all-files` exit zero; workflow schema remains valid.

- [ ] T005 (after T002, T003 — consumes `collect_audit_status(root: Path) -> dict` and `check_comment_baseline(root: Path, baseline_path: Path) -> dict`) Extend `_build_report` in `src/cairn/cli/system/report.py` with a count-only nested `quality_gates` section, render the equivalent human lines, preserve never-raise behavior outside a source checkout, and pin the JSON/human shapes with fixture quality roots in `tests/test_report.py` (FR-004, NFR-005)
  - Survey basis: FR-004 records no audit/comment fields in the existing report bundle; `_build_report` and `report` are the only direct implementation path cited.
  - Touches:
    - `src/cairn/cli/system/report.py`
    - `tests/test_report.py`
  - Verify before implementing: `rg -n 'audit|comment_violation' src/cairn/cli/system/report.py`
  - Proof when complete: `uv run pytest tests/test_report.py -q` passes and `cairn report --json` carries audit total/remaining/by-priority plus comment remaining without paths or raw errors.

- [ ] T006 (after T004, T005 — documents the shipped `audit-status`, `comment-style`, `ARGS=--json`, baseline-shrink, hook, CI job, and `quality_gates` surfaces) Document the two maintainer workflows and link the relocated audit from the docs index, with a concise CHANGELOG entry for the new ratchet (FR-004, NFR-005)
  - Survey basis: FR-004 records no consumer documentation for these outputs; docs/README.md currently has no audits index entry.
  - Touches:
    - `README.md`
    - `docs/README.md`
    - `CHANGELOG.md`
  - Verify before implementing: `rg -n 'audit-status|comment-style|quality_gates' README.md docs/README.md`
  - Proof when complete: documented commands match Make/pre-commit/CI/report implementations and `python3 scripts/check_doc_links.py` exits zero.

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `(implemented)` landed —
      implementation evidence durable before the one all-at-once tick ·
      `- [x]` done + proof note: `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
      dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
      chained tasks note `(after T###)` and name the exact interface they
      consume from their upstream — symbols, signatures, file formats; serial
      runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
      only if the count lives here, in the status holder. From round 2 on, an
      implementer's scratch note (what was tried, why it failed) may live at
      `notes/T###.md` — the one file an implementer may write under specs/,
      never read by check.py, never counted as status
- Every task cites FR-### or an applicable NFR-###; a task with neither is
      scope creep — fix the spec first
- Every code task carries a `Touches:` block (repo-relative files,
      directories, or globs); `[P]` tasks whose touches overlap are chained, not
      spawned together
- All statuses trace to survey.md items FR-001 through FR-004 and NFR-003
      through NFR-005, each of which records the implementation absent; no task
      is marked complete without its named passing proof.
