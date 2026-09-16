# Plan: swe-bench-integration

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17
**Evidence base**: [survey.md](survey.md) S1-S3, re-verified this session · **Team**: solo, PR-per-milestone (default)

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Pinned SWE-bench task source | Loader run twice, offline, yields a byte-identical pinned task subset; `validate_manifest` passes; `swebench` harness pinned as dev-only dep | FR-002 | — |
| 2 | Deterministic two-arm suite + CLI | `cairn bench --suite swe-bench` runs both arms (`_CairnArm` vs grep+read `_ControlAgent` shape, reused) over the pinned subset and prints per-task + median tokens/tool calls; reruns are byte-identical | FR-001 | Phase 1 |
| 3 | CI smoke guard + published reproduction | CI green including a swe-bench smoke-subset job; a clean clone following only documented commands reproduces the README medians; no LLM arm ships (FR-005 scope guard) | FR-004, FR-003, FR-005 | Phase 2 |

FR-002 is owned by Phase 1 (fixed subset, seeds, offline contract established at
the task-source layer so the suite inherits determinism); Phases 2-3 checkpoints
re-verify it end-to-end. FR-005 in Phase 3 is a scope assertion, not build work.

## Dependencies

```
Phase 1 (task source) ──blocks──> Phase 2 (suite + CLI) ──blocks──> Phase 3 (CI smoke ∥ docs)
```

- 1 → 2: `run_agent_suite` in `src/cairn/bench/agent_suite.py` consumes a
  pre-built workspace ("Assumes the corpus already exists at ``workspace``");
  without the pinned task source there is no input for the two arms. The suite
  module imports the Phase-1 loader (shared file).
- 2 → 3: the smoke job invokes `cairn bench --suite swe-bench`, which does not
  exist before `src/cairn/cli/bench.py` wires the suite (CLI already imports
  `run_agent_suite` from `cairn.bench.agent_suite` and dispatches on `--suite`);
  the docs quote the medians and commands Phase 2 produces.
- No other edges.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
- Independent: loader module (new `src/cairn/bench/swe_*.py` sibling of
  `perf_suite.py`/`scaling_suite.py`, loader tests) ∥ dev-dependency pin
  (`pyproject.toml` `[project.optional-dependencies]`) — disjoint files, same phase.
- Independent: CI smoke job (`.github/workflows/ci.yml`) ∥ published methodology
  (`README.md`/`docs/`) — disjoint files, same phase; neither reads the other.
- Mostly independent, fused by one shared token: suite arm
  (`agent_suite.py` or sibling `swe_suite.py`, suite tests) ∥ CLI wiring
  (`src/cairn/cli/bench.py`) — disjoint modules but both must agree on the
  `--suite swe-bench` choice name; one integration touch.
- Strictly ordered: task source → suite arm — Phase 1 produces the pinned
  workspace + loader interface Phase 2 consumes (import edge, shared file).
- Strictly ordered: suite + CLI → CI job and docs — Phase 2 produces the
  runnable `--suite swe-bench` command and median numbers Phase 3 executes
  (CI) and quotes (docs).

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: loader run twice with network disabled produces identical
  pinned task lists; manifest validates. Verify: `ls src/cairn/bench/` (loader
  module present) · run loader twice offline → `diff` of manifest outputs empty ·
  `validate_manifest` (`src/cairn/bench/datasource.py`) passes.
- **After Phase 2**: suite runs offline over the pinned subset, reports per-task
  and median tokens/tool calls for both arms; two full runs identical. Verify:
  `grep -n "class _CairnArm\|class _ControlAgent" src/cairn/bench/agent_suite.py`
  (arms reused, not rewritten) · `cairn bench --suite swe-bench` twice → report
  diff empty.
- **After Phase 3**: CI green including smoke; fresh-clone reproduction matches
  published medians. Verify: `grep -n "swe" .github/workflows/ci.yml` (smoke job
  present) · clean clone → documented commands → medians match README.

## Risks & mitigations
- Risk: SWE-bench harness format drift (spec risk) → mitigation: harness pinned
  as dev-only dependency; loader isolated behind a stable internal interface —
  both land in Phase 1, the earliest possible de-risk point.
- Risk: new pinned dev dependency fails the pip-audit hard gate in
  `.github/workflows/ci.yml` (Security job) → mitigation: pin and audit in
  Phase 1, not at ship time.
- Risk: subset licensing (spec assumption) → mitigation: loader fetches, repo
  does not vendor task data.
- Plan assumptions (no survey evidence — flagged, not invented):
  - Loader lands as a new sibling module in `src/cairn/bench/` rather than an
    extension of `datasource.py`, mirroring the per-suite module pattern;
    placement is the implementer's call.
  - Smoke job is a new job in `.github/workflows/ci.yml` following the existing
    job shape; the self-demo evidence (S2: `tests/test_self_demo.py`) proves
    the pattern, not the job layout.

## Delivery
Solo, one PR per phase (3 PRs on `feat/swe-bench-integration`): Phase 1 task
source + pin, Phase 2 suite + CLI, Phase 3 CI job + docs together. Each PR lands
that phase's code and docs as one unit; conventional-commit titles; the FR-005
scope guard is asserted in the Phase 3 PR description.
