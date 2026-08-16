# Plan: benchmark-datasource

**Spec**: [spec.md](spec.md) | **Survey**: [survey.md](survey.md) | **Created**: 2026-08-15
**Baseline**: v0.11.0 @ 66882c2 · FR coverage at start: 0 DONE / 3 PARTIAL / 3 TODO (survey.md)

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Datasource manifest + T1 content pin | `benchmarks/datasource/manifest.json` exists (T1 section: generator git-sha, seed, sizes, complexity, expected counts) and a path-order-independent content-hash check regenerates the corpus and asserts the manifest hash — locally and as a CI step; tamper with seed/profiles → check fails. | FR-001 | — |
| 2 | T2 vendored real-code snapshot | `benchmarks/datasource/t2/` carries a ≤ 3 MB pinned-commit, permissively-licensed multi-language snapshot with provenance (upstream repo, commit, license) and a NOTICE attribution section; a build+query smoke test runs in CI against it; a CI size budget fails if `benchmarks/datasource/` exceeds 5 MB. | FR-002 | Phase 1 |
| 3 | Hand-verified ground truth + validator | ≥ 50 L1 and ≥ 20 L5 queries with graded expectations and rationale, in a schema `src/cairn/eval.py` consumes, under `benchmarks/datasource/t2/ground_truth/`; a validator script re-verifies every expectation against a freshly built graph and names stale entries. | FR-003 | Phase 2 |
| 4 | Stamped baselines + `--baseline` compare | Every bench artifact (`PerfReport`, `ScalingReport`, `AgentReport` payloads) carries dataset version, cairn version, machine profile (arch, cpu count, runner class); `benchmarks/baselines/DS-v1/` committed (generated on `reference-local`); `cairn bench --compare --baseline <DS-version>` resolves the committed baseline, renders a dataset-version header, and warns on machine-profile mismatch. | FR-004 | Phases 1, 2 |
| 5 | Generated reference tables | The three `_fill_` families in `docs/benchmarks.md` (retrieval quality, perf, scaling) are replaced by generator output between sentinel markers, byte-idempotent on regeneration, sourced from committed baselines + the ground-truth eval; a CI check fails on hand edits inside the sentinels. | FR-005 | Phases 3, 4 |
| 6 | T3 scale pins + local fetch | Manifest T3 entries pin ≥ 2 external repos (url + commit) at distinct scale points; a documented local command fetches by pin and records the manifest entry in its results; CI never fetches T3 content. | FR-006 | Phase 1 (land after Phase 2; see map) |

## Dependencies
<!-- Data-dependency spine; file evidence in the map below. -->

```
P1 (manifest schema + DS-version identity)
 └─> P2 (t2 tree mints the rest of the datasource identity)
      ├─> P3 (ground truth cites t2 symbols; validator builds over t2)
      │    └─┐
      └─> P4 (DS-v1 baselines measured over the pinned T1+T2 datasource;
           ^   stamping/--baseline code needs only P1's version concept)
           │
           └─> P5 (tables consume P3's quality numbers + P4's baselines)
P1 ─> P6 (T3 extends the same manifest; P3 priority, lands last)
```

What blocks what, and why:

- **P1 → P2**: P1 creates `benchmarks/datasource/manifest.json` and mints the DS-version concept every later artifact stamps. P2's provenance and size budget key off that tree and manifest schema. Building both in isolation risks two manifest schemas and a re-mint of DS-v1.
- **P2 → P3**: ground-truth expectations record symbol identity (`file#symbol` at the pinned commit, per research RQ4 synthesis) and the validator builds a fresh graph over `benchmarks/datasource/t2/`. The snapshot is the producer; the query set is the consumer.
- **P1, P2 → P4**: `--baseline <DS-version>` resolves versions P1 defines; the committed `DS-v1` baselines must be measured over the finalized datasource (T1 corpus params from P1, T2 snapshot from P2) or they are not regenerable. No dependency on P3.
- **P3 + P4 → P5**: the retrieval-quality rows come from running the eval harness over P3's ground truth; the perf/scaling rows come from P4's committed baselines. One generator wraps all three families, so both inputs must exist.
- **P1 → P6**: T3 entries extend the manifest schema P1 defines. Recommended to land after P2 (same file — see map), and it must not invalidate DS-v1 (T3 is manifest-pinned, never vendored; whether a T3 addition bumps DS-version is a tech-spec decision recorded there).

## Parallelization map
<!-- Parallel is the DEFAULT; serial must justify itself. The task-breaker
     turns this into [P] markers per task. -->

**Independent (develop concurrently — file evidence):**

- **P3 ground truth/eval** ∥ **P4 stamping + `--baseline` code** — disjoint files: P3 touches `src/cairn/eval.py`, `benchmarks/datasource/t2/ground_truth/`, a validator script, eval tests; P4 touches `src/cairn/bench/report.py`, `src/cairn/bench/agent_suite.py`, `src/cairn/cli/bench.py`, `benchmarks/baselines/`, `tests/test_bench.py`/`tests/test_agent_suite.py`. Graph-verified: `cairn impact run_evaluation` → exactly 1 symbol (`eval_cmd`, src/cairn/cli/system.py:507); `cairn impact PerfReport` → `run_perf_suite` → `cli/bench.py` + `tests/test_bench.py` — no overlap with the eval surface. The only ordering inside this pair: P4's *final* baseline-generation-and-commit step needs P2 landed (not P3), so P4's code track can start the moment P1 lands.
- **P6 T3 pins** ∥ **P3 / P4 / P5** — P6's files (`manifest.json` T3 section, a fetch script under `scripts/`, its doc section) are disjoint from P3/P4 code files. Two caveats: (a) P6 shares `manifest.json` with P1/P2 — run it after P2 to avoid two writers of one file; (b) if P6's "documented local command" lands in `docs/benchmarks.md` (tech-spec call; a separate docs page avoids this), it shares that file with P5 — different regions (outside P5's sentinels), additive, resolvable in the PR chain.
- **P2's preparation** (candidate selection, trimming, license review — research.md already shortlists yarl 2.75 MB / markupsafe 1.0 MB / uvloop 1.8 MB) ∥ **P1** — no shared files; only the *landing* of P2's manifest/provenance section waits for P1's schema.

**Strictly ordered (burden of proof on serial):**

- **P1 → P2**: P1 produces the manifest schema + DS-version identity that P2's provenance section and size budget consume (`benchmarks/datasource/manifest.json`, one file, one schema author).
- **P2 → P3**: P2 produces the vendored snapshot; P3's expectations cite its symbols and its validator builds over it (`benchmarks/datasource/t2/` is producer, `.../t2/ground_truth/` is consumer).
- **P2 → P4 (baseline commit only)**: P4's committed `DS-v1` artifacts must be regenerable against the pinned datasource P1+P2 finalize; stamping code itself needs only P1.
- **P3 + P4 → P5**: P5's generator consumes what P3 (quality numbers) and P4 (perf/scaling baselines) produce; byte-idempotency is only checkable once real inputs exist.
- **P2 → P6**: shared `manifest.json` (T2 provenance section vs T3 section) — the one file-level serialization beyond the spine; P6 is P3-priority so this costs nothing.

**Cross-cutting shared file — `.github/workflows/ci.yml`** (P1 hash-check step, P2 size-budget + smoke steps, P4 baseline mechanics rewiring the rolling `actions/cache`, P5 hand-edit check): all edits are additive, distinct steps with no shared logic, and the PR-per-milestone cadence serializes merges — treated as parallel-safe with merge care, not a serialization reason. P4 must keep `.github/scripts/bench_compare.py` working: it reads the saved payload via `.get()` (`for op in current.get("ops", [])`), so P4's additive stamps are safe, but changing the `ops`/`median_ms` shape is P4-internal blast radius (verified: `cairn callers compare_reports` → `cli/bench.py:182` + `bench_compare.py:73` + 3 tests).

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->

- **After Phase 1**: manifest exists with generator sha/seed/sizes/expected counts; regenerating T1 on a clean checkout reproduces the manifest's content hash, and the CI datasource step is green. Verify: `ls benchmarks/datasource/manifest.json`; `grep -rn "manifest" src/cairn --include="*.py" | grep -v __pycache__` now matches the check code (was empty at baseline — survey FR-001); run the hash-check script twice → identical hash, exit 0; flip a seed constant → exit ≠ 0. *(Script name is a tech-spec decision; the condition — hash equality asserted, CI-failing — is the checkpoint.)*
- **After Phase 2**: snapshot vendored, attributed, guarded, exercised. Verify: `du -sk benchmarks/datasource` ≤ 5120 (survey used `ls`/`du` — now expects the tree to exist); provenance manifest names upstream repo + commit + license; `grep -n -i "vendored\|attribution" NOTICE` non-empty (NOTICE:1-82 today is deps-only); the build+query smoke test passes in CI and locally (`uv run pytest -q -k "t2 or smoke"` — name per tech-spec).
- **After Phase 3**: the query set is real data, not the 30/10 generic fixture. Verify: `PYTHONDONTWRITEBYTECODE=1 uv run python -c "from cairn.eval import load_eval_queries; ..."` reports ≥ 50 L1 / ≥ 20 L5 with rationale present (survey's exact probe — was "total 40 L1 30 L5 10"); the validator re-verifies every expectation against a fresh build → exit 0, and deliberately aging one expectation names that entry (spec AC5).
- **After Phase 4**: stamps and resolution observable end-to-end. Verify: `uv run cairn bench --suite perf --n-files 60 --complexity medium --embed-backend hash --repeats 3 --json` payload contains dataset-version, cairn-version, machine-profile keys (today's payload has only timestamp — survey FR-004); `ls benchmarks/baselines/DS-v1/`; `uv run cairn bench --suite perf --compare --baseline DS-v1 ...` renders a dataset-version header and warns on machine-profile mismatch (expect the warning on any non-`reference-local` machine); `uv run pytest tests/test_bench.py tests/test_agent_suite.py -q` green (34 passing at baseline).
- **After Phase 5**: no hand-typed numbers remain. Verify: `grep -c "_fill_" docs/benchmarks.md` → 0 (survey's command — was 47 occurrences / 15 lines); regenerate twice → `git diff --exit-code docs/benchmarks.md` clean (byte-idempotent); sentinel markers present around all three families; the CI docs check fails on a hand edit inside sentinels (verify by temporary local edit, or trust the CI job).
- **After Phase 6**: pins exist, CI stays network-free. Verify: `jq '.t3 | length' benchmarks/datasource/manifest.json` ≥ 2, each entry with url + commit; running the documented local command for one entry fetches by pin and its result JSON records the manifest entry; `grep -rn "t3\|fetch" .github/workflows/ci.yml` shows no T3 fetch step (survey confirmed zero network-clone machinery today — it must stay that way).

## Risks & mitigations
- Risk: ground truth exposes harness weakness (docs record recall@10 = 0.0 on the generic set — survey FR-003) → mitigation: P3 lands before P4/P5 depend on its numbers; the validator is the checkpoint; expectations record symbol identity, not rank (spec's own mitigation), so resolution improvements don't rot the set.
- Risk: T2 license/size surprise at the 3 MB line (yarl is 2.75 MB) → mitigation: research shortlist with two under-budget fallbacks (markupsafe 1.0 MB, uvloop 1.8 MB); the size-budget CI guard ships inside P2, not after.
- Risk: manifest.json multi-writer contention (P1 schema, P2 provenance, P6 T3) → mitigation: the serial spine P1 → P2 → P6 on that one file.
- Risk: P4 rewiring the bench CI job breaks the advisory pipeline (rolling cache → committed baselines) → mitigation: `bench_compare.py` reads additively (verified); advisory posture is scope-out (spec: "gating CI on timing" deferred) — P4 must not turn the warning into a gate.
- Risk: cross-machine comparisons mislead → mitigation: warn-don't-normalize (research RQ5 — no mainstream tool normalizes on shared runners); baselines generated only on `reference-local` (spec assumption).
- Risk: unproven claims inherited from the phase doc ("closure 340 s @ 1,000 files" — survey marks unknown) → mitigation: P4's baseline generation on the reference machine produces the first attributed numbers; nothing in this plan cites the unverified figure as a target.

## Assumptions (plan-level, explicit)
- Script/file names shown in checkpoints (hash check, validator, table generator, T3 fetch, smoke test names) are placeholders — tech-spec.md owns the real names; checkpoints state observable conditions, not paths.
- Whether T2 provenance lives in `manifest.json` or a t2-local file is a tech-spec call; the P1 → P2 ordering stands either way (DS-version identity), but P2 ∥ P6 file-disjointness depends on it.
- P3 may migrate `eval.py`'s default queries path (`tests/eval/queries.yaml` today) to the datasource; blast radius is verified small (`eval_cmd` only + the CLI smoke test that asserts exit 0), but whether the old fixture is retired is a task-level decision.
- Baseline artifacts are generated on the maintainer's machine (`reference-local`) per spec assumption; CI comparisons stay advisory.

## Delivery
Branch `feat/benchmark-datasource` (per spec). One PR per phase, landed in phase order 1 → 2 → (3 ∥ 4) → 5 → 6; conventional commits (`feat(bench): ...`, `docs(bench): ...`), code + docs together; follow `docs/contribution-workflow.md` for each PR. P4's baseline-generation commit happens on the reference machine and lands as its own commit inside P4's PR (data + code separated for reviewability). Post-merge per phase: `cairn update` + `record_memory`.
