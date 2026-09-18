# Test Cases: swe-bench-integration

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Business terms used throughout:

- *The suite* — the `swe-bench` arm of the bench command.
- *With-tools arm / baseline arm* — the same deterministic agent loop run with,
  respectively without, cairn's tools over the same tasks.
- *Fixed task subset* — the pinned, versioned selection of SWE-bench tasks the
  suite always measures (no task data is vendored in the repo).
- *Smoke subset* — the smaller, seconds-fast slice of the fixed task subset
  that CI runs to prevent rot.
- *Metrics report* — the persisted per-task and median token and tool-call
  figures for both arms.

## TC-001 — Both arms report per-task and median effort on the smoke subset
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** the fixed task subset has been prepared on this machine
- **When** the suite runs in smoke-subset mode
- **Then** the output shows, for every task executed, the token count and
  tool-call count for each of the two arms (with tools and baseline), followed
  by a median for each metric for each arm; no LLM is invoked at any point
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/cairn bench --suite swe-bench --smoke`

## TC-002 — Reruns reproduce the metrics report exactly
- **Story**: US1 · **Traces to**: FR-002, AC1
- **Given** the suite has run once and its persisted results are still present
- **When** the suite runs a second time in smoke-subset mode over those
  existing results, saving each run's metrics report
- **Then** the two persisted metrics reports are identical — same per-task
  rows, same medians, same arm ordering; wall-clock duration is not part of
  the reproducibility contract and stays outside the persisted report
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/cairn bench --suite swe-bench --smoke --save /tmp/swe-bench-rerun-a.json && /Users/tanle/Projects/cairn/.venv/bin/cairn bench --suite swe-bench --smoke --save /tmp/swe-bench-rerun-b.json && diff /tmp/swe-bench-rerun-a.json /tmp/swe-bench-rerun-b.json`

## TC-003 — Standing guard: the deterministic arm operates offline
- **Story**: US1 · **Traces to**: FR-002
- **Given** the fixed task subset has been prepared by a prior run
- **When** the suite runs in smoke-subset mode with external network egress
  blocked (all web traffic forced through an unreachable proxy)
- **Then** the run completes and reports medians for both arms; if any part of
  the measured agent loop ever starts needing network or model access, this
  case fails
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/cairn bench --suite swe-bench --smoke && env HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 /Users/tanle/Projects/cairn/.venv/bin/cairn bench --suite swe-bench --smoke`

## TC-004 — Boundary: an empty task selection fails explicitly
- **Story**: US1 · **Traces to**: FR-001
- **Given** the task source yields zero tasks (empty selection or unavailable
  source)
- **When** the suite starts
- **Then** it exits with an explicit error naming the empty selection, and it
  never prints medians or an all-zero report as if the run had succeeded
- **Pass condition**: Human observation: with the task source emptied or
  unavailable, the run exits non-zero with an explicit empty-selection error
  and no medians summary is printed.

## TC-005 — Boundary: the full fixed task subset runs to completion
- **Story**: US1 · **Traces to**: FR-001
- **Given** the complete fixed task subset (not the smoke slice) is prepared
- **When** the suite runs over the whole subset
- **Then** the run completes, produces one per-task row for every task in the
  fixed subset, and computes medians over all of them; this full-subset run is
  the standing verification for published numbers and may take far longer than
  the smoke run
- **Pass condition**: Human observation: a full-subset run (the same bench
  command without the smoke flag) completes and reports exactly one per-task
  row per task in the fixed subset.

## TC-006 — Boundary: concurrent runs never corrupt results
- **Story**: US1 · **Traces to**: FR-002
- **Given** no run is in progress and prior results are present
- **When** two suite runs are started against the same workspace at the same
  time
- **Then** both runs complete, or one explicitly waits for (or declines in
  favor of) the other; the persisted metrics report afterwards is a valid
  single-run report with no interleaved or duplicated rows
- **Pass condition**: Human observation: two simultaneous runs leave behind a
  well-formed metrics report whose rows all come from a single run.

## TC-007 — Published methodology names the exact runnable commands
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** the project's published README
- **When** an evaluator looks for the reproduction instructions
- **Then** the README contains a methodology section that names the exact
  suite commands to run (smoke and full subset), the pinned fixed task subset
  reference, and the published medians the commands are expected to reproduce
- **Pass condition**: `grep -q "cairn bench --suite swe-bench" README.md`

## TC-008 — A third party reproduces the published medians from a clean clone
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** a clean clone of the repository by a contributor with no local
  state, following only the published methodology
- **When** they run the documented commands in the documented order
- **Then** the medians they obtain match the README's published medians
  exactly (the deterministic contract means no tolerance band is needed)
- **Pass condition**: Human observation: an independent contributor following
  only the published methodology from a fresh clone obtains the README
  medians exactly.

## TC-009 — CI runs the smoke subset on every change
- **Story**: US1 · **Traces to**: FR-004
- **Given** the project's CI configuration
- **When** any change is proposed to the default branch's integration flow
- **Then** a CI job runs the suite in smoke-subset mode (the same entry a
  developer runs locally), the job is required for merge, and the smoke
  subset is sized to finish in seconds
- **Pass condition**: `/Users/tanle/Projects/cairn/.venv/bin/python -m pytest -m core --collect-only -q | grep swe_bench_smoke`

## TC-010 — Standing guard: a broken suite turns CI red
- **Story**: US1 · **Traces to**: FR-004
- **Given** the CI smoke job from TC-009 is in place
- **When** a change breaks the suite (it errors, or its smoke run stops
  reporting both arms' medians)
- **Then** the CI job fails and blocks the change, so the benchmark cannot
  silently rot
- **Pass condition**: Human observation: the first change that breaks the
  smoke run produces a red required CI job rather than a green one.

## TC-011 — Standing guard: the shipped suite needs no model credentials
- **Story**: US1 · **Traces to**: FR-005
- **Given** a machine with no model-provider credentials configured
- **When** the suite runs in smoke-subset mode with every known
  model-provider credential variable explicitly removed from the environment
- **Then** the run completes and reports medians; if an LLM-in-the-loop
  dependency ever creeps into the default path before the deferred arm ships
  as its own spec, this case fails
- **Pass condition**: `env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY /Users/tanle/Projects/cairn/.venv/bin/cairn bench --suite swe-bench --smoke`

## TC-012 — Standing guard: published results carry efficiency medians only
- **Story**: US2 · **Traces to**: FR-005
- **Given** the deferred LLM-in-the-loop arm has not shipped
- **When** an evaluator reads the published README results
- **Then** the published numbers are token and tool-call medians for the two
  deterministic arms only — no resolve-rate figure and no LLM-arm
  instructions appear until the separate follow-up spec delivers that arm
- **Pass condition**: Human observation: the README results section lists
  token and tool-call medians only, with no resolve-rate claim or LLM-arm
  run instructions.

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-004, TC-005 | auto + manual |
| FR-002      | TC-002, TC-003, TC-006 | auto + manual |
| FR-003      | TC-007, TC-008        | auto + manual |
| FR-004      | TC-009, TC-010        | auto + manual |
| FR-005      | TC-011, TC-012        | auto + manual |

Notes:
- TC-003, TC-010, TC-011, TC-012 are standing regression guards: they fail if
  an LLM/network dependency creeps into the deterministic default path (the
  FR-002/FR-005 promises) or if the CI rot guard stops biting.
- The auto pass conditions deliberately use the smoke subset so each finishes
  well under two minutes; the at-scale full-subset verification is named in
  TC-005's Then as the standing verify for published numbers.
