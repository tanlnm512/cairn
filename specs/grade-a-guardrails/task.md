# Tasks: grade-a-guardrails

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: pending — delivery evidence; the orchestrator writes `commit @ <sha>` here

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 2     | 0    |
| 2     | 3     | 0    |
| 3     | 4     | 0    |
| 4     | 4     | 0    |
| **Σ** | 13    | 0    |

## Phase 1: Resolution-quality evidence (FR-002, NFR-005)
<!-- Checkpoint: `cairn report --json` has a `resolution` section; zero-edge stores report zeros per language. -->
- [ ] T001 [P] Add per-language resolution aggregation to `get_stats` in `src/cairn/graph/stats.py` — extend the existing calls/references-pool query to count `unresolved`, drive the language list from `files.language`, and expose `stats["resolution_by_language"]` as `{language: {"exact": int, "ambiguous": int, "unresolved": int, "total": int, "exact_pct": float, "ambiguous_pct": float, "unresolved_pct": float}}` with zeros/0.0 for empty pools; keep `exact_share_by_language` byte-compatible; extend the stats boundary tests (FR-002)
  - Touches:
    - `src/cairn/graph/stats.py`
    - `tests/test_status_resource_health.py`
- [ ] T002 Add the `resolution` section to `cairn report` — `_build_report` includes `resolution` from the `resolution_by_language` key T001 produces (additive; `tests/test_report.py:75` is a superset pin), `_render_report` prints a `## Resolution` block, `--json` carries it unchanged (FR-002, NFR-005)
  - Touches:
    - `src/cairn/cli/system/report.py`
    - `tests/test_report.py`

## Phase 2: Self-healing doctor (FR-001, NFR-001, NFR-004)
<!-- Checkpoint: dead cairn SSE entry repointed with backup + atomic rename; second run zero actions; fresh mtime refused; foreign entries untouched. -->
- [ ] T003 [P] Extend `_enumerate_registrations` in `src/cairn/cli/system/doctor.py` to yield `(client, path, display, entry, workspace_owned)` and update its three call sites — `_registration_findings` plus the two exact-shape unpack pins at `tests/test_install_uninstall_fidelity.py:1525` and `:1548` (FR-001)
  - Touches:
    - `src/cairn/cli/system/doctor.py`
    - `tests/test_install_uninstall_fidelity.py`
- [ ] T004 Implement the fix engine as `apply_doctor_fixes(db) -> list[dict]` in new `src/cairn/cli/system/doctor_fix.py` (after T003 — consumes the 5-tuple enumeration): only cairn-owned entries whose `_sse_endpoint` does not respond; skip-and-guide workspace-owned files; refuse when `st_mtime` is inside the freshness window; back up via `_backup_to_bak`, replace only the cairn entry with the client's stdio generator output (`mcp_config_json`, `zcode_mcp_config_json`, `opencode_mcp_config_json`, `kilo_mcp_config_json`, `agy_mcp_config_json`, `mcp_config_json_desktop`), rewrite via `_atomic_write_text`; action records `{"client", "config", "action", "url"}` carry no env/secret fields; healthy pass returns `[]` (FR-001, NFR-001, NFR-004)
  - Touches:
    - `src/cairn/cli/system/doctor_fix.py`
    - `src/cairn/agent_install/merge.py`
    - `src/cairn/agent_install/clients/`
    - `tests/test_doctor_fix.py`
- [ ] T005 Wire `cairn doctor --fix` — add the flag to the `doctor` command, render the action summary with the stdio-vs-shared-daemon tradeoff line, splice a recomputed `_check_environment` over the pre-fix `_run_doctor` results leaving every other row untouched, include `actions` under `--json`, update the read-only docstring to "read-only unless `--fix`" (after T004 — consumes `apply_doctor_fixes(db) -> list[dict]`) (FR-001, NFR-004, NFR-005)
  - Touches:
    - `src/cairn/cli/system/doctor.py`
    - `tests/test_doctor_fix.py`
    - `tests/test_doctor.py`

## Phase 3: Latency catastrophe gate (FR-003)
<!-- Checkpoint: enforced run exits 3 naming tool/measured/budget; PR legs advisory; main/merge-group step has no continue-on-error. -->
- [ ] T006 [P] Add budget data + evaluator — new `src/cairn/bench/budgets.py` with `load_budgets(path)`, `evaluate_budgets(report, budgets) -> list[Breach]` (`Breach` fields `tool`, `measured_ms`, `budget_ms`), keyed by the perf suite's exact op names for at least `find_definition`, `get_callers`, `impact_analysis`, `search_symbols`, `semantic_search`, `explore`; checked-in schema-tagged `benchmarks/baselines/perf_p95_budgets.json` sized ≥10× observed p95 (FR-003)
  - Touches:
    - `src/cairn/bench/budgets.py`
    - `benchmarks/baselines/perf_p95_budgets.json`
    - `tests/test_bench.py`
- [ ] T007 [P] Add the recalibration command — new `scripts/mint_perf_budgets.py --from <run.json> --factor 10 --out <budgets.json>` stamping provenance like the DS baselines; document the command in `docs/release-checklist.md` (FR-003)
  - Touches:
    - `scripts/mint_perf_budgets.py`
    - `docs/release-checklist.md`
    - `tests/test_mint_perf_budgets.py`
- [ ] T008 Wire `--budgets PATH --budget-mode {advise,enforce}` into the `bench` command — default advise/none preserves today's behavior; advise prints trend lines, enforce prints `tool <name>: p95 <X>ms exceeds budget <Y>ms` per breach and exits 3; add a `budgets` block to the emitted payload (after T006 — consumes `load_budgets`/`evaluate_budgets`) (FR-003, NFR-005)
  - Touches:
    - `src/cairn/cli/bench.py`
    - `tests/test_bench.py`
- [ ] T009 Split the ci.yml bench run step by event — PR-leg step keeps `continue-on-error: true` and advisory budgets; main/merge-group step runs `--budgets benchmarks/baselines/perf_p95_budgets.json --budget-mode enforce` with no `continue-on-error`; leave mint/cache/compare steps unchanged (after T008 — consumes the exit-3 breach contract) (FR-003)
  - Touches:
    - `.github/workflows/ci.yml`

## Phase 4: Weekly smoke + protection verifier (FR-004, FR-005, NFR-002)
<!-- Checkpoint: runner green on the list, red naming the first failure; weekly workflow scheduled, telemetry off, read-only; make verify-protection exits 0/1 with --json. -->
- [ ] T010 [P] Add the smoke runner — new `scripts/run_cli_smoke.py` taking a command-list file, `--fixture DIR`, `--db PATH`, `--json`; executes each line with pinned cwd/env, `::group::` per command, `::error::` + exit 1 on the first failure naming it; no network calls of its own (FR-004, NFR-002)
  - Touches:
    - `scripts/run_cli_smoke.py`
    - `tests/test_cli_smoke.py`
- [ ] T011 Add the scheduled workflow + command list — new `.github/workflows/weekly-smoke.yml` (cron + `workflow_dispatch`, `permissions: contents: read`, `CAIRN_TELEMETRY=off`) invoking the runner over checked-in `.github/smoke/cli-commands.txt` of high-signal store-local commands against the vendored deterministic fixture (after T010 — consumes the runner CLI `run_cli_smoke.py LIST --fixture DIR --db PATH [--json]`) (FR-004, NFR-002)
  - Touches:
    - `.github/workflows/weekly-smoke.yml`
    - `.github/smoke/cli-commands.txt`
- [ ] T012 [P] Add the protection verifier — new `scripts/verify_protection.py`: read-only `gh api` GETs to `/repos/{slug}/branches/main/protection` and `/repos/{slug}/rulesets[/{id}]`; union-semantics checks for required reviews ≥1, force-push denial, and required status checks ⊇ `MANDATORY_CHECKS`; per-check provenance; `--json`; exit 0/1; gh/auth failures surfaced per check (FR-005, NFR-005)
  - Touches:
    - `scripts/verify_protection.py`
    - `tests/test_verify_protection.py`
- [ ] T013 Wire `make verify-protection` and document the mandated settings — Makefile target invoking the script, maintainer-run/admin-auth note, and a release-checklist section mirroring the `MANDATORY_CHECKS` constant (after T012 — consumes the constant and the script's exit contract); not referenced by any workflow (FR-005)
  - Touches:
    - `Makefile`
    - `docs/release-checklist.md`

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `(implemented)` landed —
      implementation evidence durable before the one all-at-once tick ·
      `- [x]` done + proof note: `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T014~~ dropped <date> (D-###)` — never delete the line;
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
- Test-first per the repo constitution: each task lands its failing-test-first
      case with the implementation in the same commit; loopback-binding tests
      follow the core-suite/out-of-sandbox pattern, never silent flakes
