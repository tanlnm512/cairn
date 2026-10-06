# Tasks: pr-tooling

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: commit @ 2b3515e

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 2 | 2 |
| 2 | 3 | 3 |
| 3 | 3 | 3 |
| **Σ** | 8 | 8 |

## Phase 1: gh foundation + PR list (FR-001, FR-002, FR-006, NFR-001, NFR-004)
<!-- Checkpoint: cairn prs --help exits zero; on a repo with open PRs the six-column list prints; gh unauthenticated prints one guidance error and nothing else; python3 -m pytest tests/ -q -k prs green -->
- [x] T001 (implemented) [P] Add the gh subprocess wrapper `_gh` + `GhError` (fixed list/view/diff argv, stderr-carrying errors, missing-binary guidance, timeout — tech-spec D-001) and the defensive `pr list` record parser (unknown fields ignored, author object-or-string, missing required field fails closed naming the gh version, `statusCheckRollup` classified conservatively to pass/fail/pending/none — tech-spec D-002) in a new `src/cairn/graph/prs.py`, with the committed fixture snapshots and failing-test-first parser pins (FR-001, FR-002, NFR-001, NFR-004)
  - done 2026-10-05 — done 2026-10-05 — gh wrapper 36 tests green (argv allowlist, GhError stderr, defensive parses, CI classification)
  - Touches:
    - `src/cairn/graph/prs.py`
    - `tests/test_prs_gh.py`
    - `tests/fixtures/gh/pr_list.json`
    - `tests/fixtures/gh/pr_view.json`
  - Verify before implementing: `grep -rn '"gh"' src/cairn --include="*.py"` (still no match — survey S5); `gh --version` available (survey supporting evidence)
- [x] T002 (after T001 — consumes `_gh`, `GhError`, and the parsed list records number/title/headRefName/author/ci_state/reviewDecision from `src/cairn/graph/prs.py`) Add the `cairn prs` command in a new `src/cairn/cli/prs.py`: `--db`, six-column list render, store opened via `get_db(db, read_only=True)`, assemble-then-render so any gh failure prints exactly one actionable error with no partial table, registered by one import line in `src/cairn/cli/__init__.py` (tech-spec D-007) (FR-001, FR-002, FR-006)
  - done 2026-10-05 — done 2026-10-05 — cairn prs list command; assemble-then-render; fake-gh e2e green
  - Touches:
    - `src/cairn/cli/prs.py`
    - `src/cairn/cli/__init__.py`
    - `tests/test_prs_cli.py`
  - Verify before implementing: `cairn prs --help` exits non-zero before this task (survey S1 verify); `cairn --help` after (survey S6 verify)

## Phase 2: Per-PR graph impact (FR-003, FR-007, NFR-003)
<!-- Checkpoint: cairn prs --impact <open-PR-number> prints changed symbols with dependents, the explicit unindexed list, and the store line; python3 -m pytest tests/ -q -k "blast and not infra" green -->
- [x] T003 (implemented) [P] Add `compute_pr_impact(conn, workspace, *, diff_text, base_ref, fuzzy=False, limit=500)` and `pr_seed_symbols(conn, workspace, diff_text)` beside `compute_blast` in `src/cairn/graph/blast.py`, reusing `_parse_diff`, `_seed_symbols`, `_radius`, `_annotate_radius` unchanged, returning the `compute_blast` result shape with `basis={"kind": "pr", "base": …}` — no `refresh_for_query` call, caps inherited from `impact_analysis` (max_depth=10, limit=500), deleted files listed never seeded, unindexed files surfaced, failing-test-first on fixture diff text (tech-spec D-006, D-007, D-008) (FR-003, FR-006, NFR-003)
  - done 2026-10-05 — done 2026-10-05 — compute_pr_impact + pr_seed_symbols; blast suite 30 passed unchanged; read-only proven
  - Touches:
    - `src/cairn/graph/blast.py`
    - `tests/test_prs_impact.py`
  - Verify before implementing: `python3 -m pytest tests/ -q -k "blast and not infra"` green before and after (survey S2 verify)
- [x] T004 (implemented) [P] Add the PR fetchers `fetch_pr_view` (returns number/head_ref/baseRefName) and `fetch_pr_diff` (patch text — never local git), base ref resolved via `_resolve_base` tried as `<baseRefName>` then `origin/<baseRefName>`, `--impact` argument shape validation (digits with optional `#`, or a branch name not starting with `-`), and `store_build_age` (guarded newest-`build_runs` read) to `src/cairn/graph/prs.py` (tech-spec D-003, D-004), with failing-test-first fetcher and age tests (FR-003, FR-007)
  - done 2026-10-05 — done 2026-10-05 — fetchers in prs_fetch.py (D-009); 12 tests green; store_build_age guarded
  - Touches:
    - `src/cairn/graph/prs.py`
    - `tests/test_prs_gh.py`
  - Verify before implementing: `grep -n "last_build_age" src/cairn/mcp_server/_server_core.py | head -3` (survey S7 verify)
- [x] T005 (after T003, T004 — consumes `compute_pr_impact(conn, workspace, diff_text=…, base_ref=…)`, `fetch_pr_view`/`fetch_pr_diff`, and `store_build_age` from the graph modules) Wire `--impact PR|branch` into `src/cairn/cli/prs.py`: resolve the PR, fetch diff + base, compute impact, render changed symbols with dependents, the explicit unindexed-changed-files list, deleted files, and the `Store: local index (built <age>|never)` line before the impact body (FR-003, FR-007)
  - done 2026-10-05 — done 2026-10-05 — --impact wired; Store-age line; BlastBaseError clean; e2e sample verified
  - Touches:
    - `src/cairn/cli/prs.py`
    - `tests/test_prs_cli.py`
  - Verify before implementing: `python3 -m pytest tests/ -q -k "blast and not infra"` (blast suite stays green — survey S2 verify)

## Phase 3: Merge-order risk + JSON (FR-004, FR-005)
<!-- Checkpoint: cairn prs --conflicts ranks a same-community pair naming the community and omits the section with the hint on a store without the tables; cairn prs --json carries list, impact, and conflicts sections -->
- [x] T006 Add `community_overlaps(conn, per_pr_seeds)` to `src/cairn/graph/prs.py` — guarded reads of `communities(id, label, size)` and `symbol_communities(community_id, symbol_id, structural_degree)`, seed symbol ids joined to labels, pairs ranked by shared-label count with PR-number tie-break, graceful-absent payload on `sqlite3.Error` or empty tables (tech-spec D-005) — and the `--conflicts` flag in `src/cairn/cli/prs.py` rendering ranked pairs or omitting the section with the `cairn communities` hint; read-path tests create the tables from the symbol-communities DDL in fixture stores only (FR-004)
  - done 2026-10-05 — done 2026-10-05 — community_overlaps + --conflicts; ranked pairs + graceful hint pinned
  - Touches:
    - `src/cairn/graph/prs.py`
    - `src/cairn/cli/prs.py`
    - `tests/test_prs_conflicts.py`
  - Verify before implementing: `sqlite3 "$STORE_DB" "SELECT COUNT(*) FROM communities"` returns 0 until symbol-communities lands (survey S4 verify — graceful-absent is the live path)
- [x] T007 (after T006 — consumes the conflicts result shape `{"pairs": [{"a", "b", "shared", "communities"}], "hint"}` returned by `community_overlaps`) Add `--json` to `src/cairn/cli/prs.py` emitting `{"store": …, "prs": […], "impact": …|null, "conflicts": …|null}` carrying every requested section (FR-005)
  - done 2026-10-05 — done 2026-10-05 — --json payload shape pinned (store/prs/impact/conflicts)
  - Touches:
    - `src/cairn/cli/prs.py`
    - `tests/test_prs_cli.py`
  - Verify before implementing: `cairn prs --json` currently unknown-flag (command exists from T002; flag does not)
- [x] T008 (implemented) (after T006, T007 — documents the shipped flags and sections) Document the command — list / `--impact` / `--conflicts` / `--json` usage, the read-only contract, and the store-age line — in README plus a CHANGELOG entry (FR-001)
  - done 2026-10-05 — done 2026-10-05 — cli-reference + README + CHANGELOG; doc-links 0 broken
  - Touches:
    - `README.md`
    - `CHANGELOG.md`
  - Verify before implementing: `cairn prs --help` shows the shipped flags

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
- All statuses trace to survey.md: S1 (no prs command — all TODO) and S4
  (community tables absent — graceful-absent path); no task is marked done
  without a passing verify command
