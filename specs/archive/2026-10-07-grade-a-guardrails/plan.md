# Plan: grade-a-guardrails

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-05

## Milestones
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1 | Resolution-quality evidence | `cairn report` grows a `resolution` section: call-edge counts by `exact`/`ambiguous`/`unresolved` per language with percentages, `--json` included; a store with no call edges prints zeros per language, not an error | FR-002, NFR-005 | — |
| 2 | Self-healing doctor | `cairn doctor --fix` repoints dead cairn-owned SSE registrations to stdio (backup + atomic rename + freshness refusal), leaves foreign entries and other check results untouched, lists every action with the stdio-vs-daemon tradeoff, and is a no-op on a healthy environment | FR-001, NFR-001, NFR-004 | — |
| 3 | Latency catastrophe gate | Checked-in per-tool p95 budgets (≥10× observed) evaluated by `cairn bench`; breach fails naming tool/measured/budget; enforced on the main/merge-group bench job, advisory trend output on PR legs; recalibration command documented | FR-003, NFR-003 (n/a — tracks the perf gate) | — |
| 4 | Weekly smoke + protection verifier | Scheduled workflow replays a checked-in CLI command list against a deterministic fixture, red on first failing command with it named; `make verify-protection` probes legacy + rulesets APIs read-only for reviews/force-push/required-checks | FR-004, FR-005, NFR-002, NFR-006 (n/a — no UI touched) | — |

## Dependencies
No phase consumes another phase's output — the four areas touch disjoint
files (Phase 1: `src/cairn/graph/stats.py`, `src/cairn/cli/system/report.py`;
Phase 2: `src/cairn/cli/system/doctor.py`, `src/cairn/agent_install/`;
Phase 3: `src/cairn/bench/`, `src/cairn/cli/bench.py`, `.github/workflows/ci.yml`;
Phase 4: new workflow/scripts/Makefile/docs files). The phase order is
delivery order (smallest first, the config-writing risk in Phase 2 pulled
ahead of the CI-only phases), not a dependency chain. Within phases, only
shared-file work is serialized (map below).

## Parallelization map
- Independent: Phase 1 ∥ Phase 2 ∥ Phase 3 ∥ Phase 4 — disjoint file sets
  above; no shared symbols (`_run_doctor` is imported by `report.py` but its
  Phase 2 change is additive flag plumbing, not a signature flip).
- Independent within Phase 2: the `agent_install` reuse surface (backup,
  atomic write, stdio generators — already exists, no edits) ∥ the doctor
  flag/summary in `src/cairn/cli/system/doctor.py`.
- Independent within Phase 3: budget data + loader/evaluator
  (`src/cairn/bench/`, `src/cairn/cli/bench.py`) ∥ the mint script
  (`scripts/mint_perf_budgets.py`) ∥ docs (`docs/release-checklist.md`).
- Independent within Phase 4: FR-004 workflow + runner + command list
  (`.github/workflows/weekly-smoke.yml`, `scripts/run_cli_smoke.py`,
  `.github/smoke/cli-commands.txt`) ∥ FR-005 verifier
  (`scripts/verify_protection.py`, `Makefile`, `docs/release-checklist.md`).
- Strictly ordered within Phase 1: the stats aggregation
  (`src/cairn/graph/stats.py`) → the report section
  (`src/cairn/cli/system/report.py`) — the report consumes the new
  `resolution_by_language` key `get_stats` produces.
- Strictly ordered within Phase 2: the fix engine (new
  `src/cairn/cli/system/doctor_fix.py`, incl. the `_enumerate_registrations`
  5-tuple extension) → the `--fix` CLI flag and summary rendering in
  `doctor.py`'s `doctor` command — the command consumes
  `apply_doctor_fixes(db) -> list[dict]`.
- Strictly ordered within Phase 3: budget evaluator (`src/cairn/bench/`) →
  `bench` CLI `--budgets/--budget-mode` wiring → the ci.yml step split — the
  workflow steps consume the CLI's exit-3 breach contract.

## Checkpoints
- **After Phase 1**: `uv run cairn report --db <store> --json` contains a
  `resolution` section whose per-language rows sum to the store's labeled
  call edges; against an empty/zero-edge store every language row is zeros;
  `rg -n resolution src/cairn/cli/system/report.py src/cairn/graph/schema.py`
  still passes; `.venv/bin/pytest tests/ -q -k "report or stats"` green.
- **After Phase 2**: a fixture config with a dead cairn SSE entry is
  repointed to stdio with a `.bak` beside it and foreign servers untouched;
  a second `--fix` run reports zero actions; a fresh-mtime file is refused
  with guidance; `rg -n '"--fix"|st_mtime' src/cairn/cli/system/doctor.py src/cairn/agent_install`
  hits; `.venv/bin/pytest tests/test_atomic_config_writes.py -q` green, and
  the doctor suite green via the core-suite/out-of-sandbox pattern for
  loopback-binding cases (never treated as product flakes).
- **After Phase 3**: `cairn bench --suite perf --budgets benchmarks/baselines/perf_p95_budgets.json --budget-mode enforce` exits 3 on an injected breach naming tool, measured
  p95, and budget; advise mode prints trends and exits 0;
  `rg -n "find_definition|continue-on-error|p95" src/cairn/bench/perf_suite.py .github/workflows/ci.yml`
  shows the enforced main/merge-group step without `continue-on-error`;
  `.venv/bin/pytest tests/test_bench.py -q -k budget` green.
- **After Phase 4**: `scripts/run_cli_smoke.py` over the checked-in list
  against the fixture exits 0 and, with one injected failing command, exits 1
  naming it; `.github/workflows/weekly-smoke.yml` carries the schedule,
  `CAIRN_TELEMETRY=off`, and read-only permissions; with admin gh auth
  `make verify-protection` exits 0 on a protected repo and 1 with a named
  missing setting, `--json` in both cases.

## Risks & mitigations
- Risk: `doctor --fix` races a live client writing its own config →
  freshness-window refusal + atomic temp+rename + never-clobber `.bak`
  backup (NFR-001/NFR-004).
- Risk: budget thresholds age as corpus/hardware changes → budgets live in
  a checked-in JSON minted by a documented ≥10× recalibration command, not
  hardcoded constants.
- Risk: hosted-runner noise reddens the gate → budgets stay catastrophe-class
  (≥10×) and PR legs keep advisory-only output.
- Risk: weekly workflow leaks usage data → checked-in command list, local
  fixture, `CAIRN_TELEMETRY=off`, `permissions: contents: read` (NFR-002).
- Risk: gh protection APIs drift or disagree → both APIs probed, union
  semantics, per-check provenance in output; maintainer-run, never a CI gate.
- Risk: sandbox `EPERM` on loopback binds flakes the doctor verification →
  fix-path tests monkeypatch the SSE responder; any real-socket leg is run
  via the core-suite/out-of-sandbox pattern and recorded as such.

## Delivery
Solo; PR per milestone on `feat/grade-a-guardrails` (4 PRs), one
code+tests+docs commit per PR, conventional commit titles, pre-commit
`--all-files` before each, per the shipping workflow in AGENTS.md.
