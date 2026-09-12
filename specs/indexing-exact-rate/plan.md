# Plan: indexing-exact-rate

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-13
**Branch**: `feat/indexing-exact-rate` (baseline `ac09d4b`; survey builds A/B are the measured anchors)

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Quiet corpus | Committed root `cairn.json` excludes dashboard static chunks + benchmark datasource corpora; fresh build shows zero noise symbols, `config_exclude` skips x179, pool exact share 75.81% (from 71.16%) | FR-001 | — |
| 2 | Complete, observable, multivector indexing | Update path embeds changed symbols when a backend is available and defers observably when not; no-flag `cairn embed` builds `embeddings_mv`; `cairn stats` prints exact/ambiguous/unresolved shares beside edge totals | FR-002, FR-003, FR-004, FR-005 | Phase 1 |
| 3 | Acceptance measurement | Final pool + embedding-coverage numbers on the implemented tree; any shortfall vs the 95% target is recorded as an adjudicated D-### decision re-deriving the target, never a silent pass/fail | FR-006 | Phase 2 |

Already in tree per survey evidence — **noted here, no tasks spawned** (task-breaker cites these as context, never re-implements):
- Layer C exclusion machinery, end to end (survey item FR-001a: `src/cairn/graph/scanner.py:_build_config_spec:323`, `classify_file:376` running Layer C at lines 418-420; skips persisted by `src/cairn/graph/builder.py:_record_skips:160`).
- Noise measurement and the minified-skip miss explanation (survey FR-001c) and bench/eval corpus-discovery safety (survey FR-001d: corpora read from disk or built fresh over copies).
- Baselines: tree 71.16% exact (43,546/61,195) and exclusion projection 75.81% (15,000/19,786) (survey FR-006a/FR-006b). Phase 3 measures against these; it does not re-derive them.

## Dependencies
```
Phase 1  FR-001 — commit cairn.json + validation build
   │  cadence anchor (re-baselines the tree), not a file coupling:
   │  Phase 1 touches only cairn.json, untracked today
   v
Phase 2  three concurrent tracks, disjoint files (map below)
   │  A: FR-002 --> FR-003   (embed wiring, then deferral observability)
   │  B: FR-005              (stats resolution shares)
   │  C: FR-004              (multivector default flip)
   v
Phase 3  FR-006 — final measurement + adjudication
```
- **Phase 1 → Phase 2** is checkpoint cadence, not code coupling: the exclusion commit touches only `cairn.json` (untracked: `git ls-files -- cairn.json` is empty, survey FR-001b), and Phase 2's tracks touch no file Phase 1 touches. Phase 1 lands first because it is the smallest measured win (+4.65pp, zero code) and every number measured afterward — including Phase 3's acceptance run — is read against the quiet corpus.
- **Within track A, FR-002 strictly precedes FR-003**: the deferral signal (warn-once log + doctor-visible count) fires at the exact call site FR-002 introduces — post-COMMIT at `src/cairn/graph/incremental.py:reindex_paths:24` line 246, using the new-symbol ids collected in `name_to_symbol_ids` (lines 232-235), plus the single-file `src/cairn/graph/incremental.py:_reindex_file:841` path (survey FR-002).
- **Phase 2 → Phase 3**: the acceptance phase consumes Phase 2's surfaces — the stats shares are the surface AC2's before/after is read on, and the 100%-coverage leg runs an embed pass over the FR-004 default on a graph whose update path FR-002 wired.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
Parallel is the default; this map names where it yields. All three Phase 2 tracks carry [P]; their file sets are disjoint and checkable against the survey:

- **Independent: Track A (FR-002 + FR-003) ∥ Track B (FR-005) ∥ Track C (FR-004)** — disjoint files:
  - Track A owns `src/cairn/graph/incremental.py` (reindex_paths:24 / _reindex_file:841 wiring), `src/cairn/graph/embeddings.py` (calling `embed_symbols:1549`; any unembedded-symbol count helper mirrors `unembedded_memory_hint:1906`), `src/cairn/cli/system.py` (doctor check beside `_check_ann:787`); tests: `tests/test_ann_incremental.py` plus new update-path test files this track creates (survey CONV: one file per area).
  - Track B owns `src/cairn/graph/stats.py` (`get_stats:17` + one GROUP BY over kind IN calls/references x resolution, per survey FR-005) and `src/cairn/cli/core.py` (`stats:449` rendering, beside the existing `edges N (M resolved)` line); tests follow the `get_stats` minimal-schema fixture convention in `tests/test_status_resource_health.py`.
  - Track C owns `src/cairn/cli/embed.py` (flag default at lines 160-166, mv index rebuild wiring at lines 313-317) and the three pinning test files `tests/test_embeddings_mv.py`, `tests/test_ann_vecmv.py`, `tests/test_multivector_query.py` — the 8 build-side pins are superseded failing-test-first (C-02, spec FR-004); the query-side pins in `tests/test_multivector_query.py` stay (same file, same track — no cross-track collision).
- **Strictly ordered**:
  - FR-002 → FR-003 (within A): FR-003's deferred-embed observability exists only at FR-002's call site; there is nothing to observe before the wiring lands.
  - Phase 2 → Phase 3: measurement reads the surfaces Phase 2 lands.
- **Boundary condition keeping A ∥ C true** (tech-spec must honor or explicitly overturn): FR-004 flips the default at the **CLI layer** — `embed_all` keeps its signature default (`src/cairn/graph/embeddings.py:embed_all:1403`, `multivector=False` at line 1410), because the bench suites call it explicitly (`src/cairn/bench/perf_suite.py:159`, `agent_suite.py:606`, `scaling_suite.py:91`). If the decision flips the library default instead, `src/cairn/graph/embeddings.py` becomes shared and C must be chained after A in task.md.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
Run everything from the repo root with `CAIRN_HOME` pinned to a throwaway store (survey CONV: the live store is written by the stale installed daemon) and the repo venv binary — never `~/.local/bin/cairn` 0.20.0.

- **After Phase 1** — exclusions committed and effective (AC1):
  ```
  git ls-files -- cairn.json            # non-empty now (survey FR-001b verify, inverted)
  T=$(mktemp -d /tmp/cairn-p1.XXXXXX)
  CAIRN_HOME=$T .venv/bin/cairn build
  sqlite3 $T/*/*/.kg "SELECT reason, COUNT(*) FROM skipped_files GROUP BY reason;"
    # config_exclude|179  (103 chunks + 76 datasource; survey FR-006b)
  sqlite3 $T/*/*/.kg "SELECT CASE WHEN f.path LIKE 'src/cairn/dashboard/static/chunks/%' THEN 'chunks' WHEN f.path LIKE 'benchmarks/datasource/%' THEN 'datasource' ELSE 'other' END a, COUNT(*), COUNT(DISTINCT f.id) FROM symbols s JOIN files f ON s.file_id=f.id GROUP BY a;"
    # chunks|0..., datasource|0...  (AC1: zero noise symbols)
  sqlite3 $T/*/*/.kg "SELECT ROUND(100.0*SUM(CASE WHEN resolution='exact' THEN 1 ELSE 0 END)/COUNT(*),2), COUNT(*) FROM edges WHERE kind IN ('calls','references') AND resolution IN ('exact','ambiguous');"
    # about 75.8 | about 19,786 on the ac09d4b corpus (survey FR-006b projection)
  ```
  No new suite test in this phase: Layer C behavior is already covered by the existing scanner/config suites (survey FR-001a); AC1 is a build-measurement assertion, which this checkpoint runs.
- **After Phase 2** — one observable per track:
  - Track A: `grep -rn embed_symbols src/cairn | grep -v "def embed_symbols"` now shows the production call site(s) beyond the docstring string at line 1661 (survey FR-002 verify, inverted); `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_ann_incremental.py tests/test_workflow_audit_fixes.py -q` green plus this track's new test files; an update run with no backend available succeeds and surfaces the deferred count via the log/doctor channel (FR-003's failing-test-first case).
  - Track B: `T=$(mktemp -d /tmp/cairn-p2.XXXXXX); CAIRN_HOME=$T .venv/bin/cairn build && CAIRN_HOME=$T .venv/bin/cairn stats | grep -ci exact` returns at least 1 (survey FR-005 verify showed 0), with the three shares printed beside edge totals (AC6).
  - Track C: `.venv/bin/cairn embed --help` no longer prints "Off by default"; a no-flag embed pass on a built graph leaves `embeddings_mv` rows (count greater than 0) while `--no-multivector` restores the single-vector build (AC5); the 8 build-side pins are rewritten failing-test-first, query-side pins untouched.
- **After Phase 3** — acceptance numbers recorded, adjudication explicit:
  ```
  T=$(mktemp -d /tmp/cairn-p3.XXXXXX)
  CAIRN_HOME=$T .venv/bin/cairn build && CAIRN_HOME=$T .venv/bin/cairn embed
  sqlite3 $T/*/*/.kg "SELECT ROUND(100.0*SUM(CASE WHEN resolution='exact' THEN 1 ELSE 0 END)/COUNT(*),2), COUNT(*) FROM edges WHERE kind IN ('calls','references') AND resolution IN ('exact','ambiguous');"
  sqlite3 $T/*/*/.kg "SELECT ROUND(100.0*COUNT(*)/(SELECT COUNT(*) FROM symbols),2) FROM embeddings;"
    # embedding-coverage leg: 1.0 after the embed pass (live DB today: 10,880/20,296, survey FR-006b)
  ```
  Exact share at or above 95% → targets met. Below → record the D-### adjudication in tech-spec: re-derive the target from the measured residual (survey FR-006b: exclusion alone measures 75.81%; calls ambiguity persists beyond current machinery). The shortfall is a recorded decision, never a silent audit failure (spec Risk 1).

## Risks & mitigations
- Risk: the 95% exact target exceeds the machinery's measured ceiling (75.81% post-exclusion projection). → Mitigation: Phase 3's adjudication path is first-class — a D-### records the measured residual and the re-derived target; no scope silently expands inside this spec to chase 95%.
- Risk: tracks A and C collide on `src/cairn/graph/embeddings.py` if FR-004 flips the library default. → Mitigation: the map pins the flip to the CLI layer; a tech-spec decision to flip `embed_all`'s default must also chain C after A in task.md.
- Risk: update-path embedding slows incremental updates. → Mitigation: bounded to changed symbols (`name_to_symbol_ids`), gated on `embeddings_available` (`src/cairn/graph/embeddings.py:embeddings_available:94`), degrade-not-fail mirrors `src/cairn/knowledge/ingest/executor.py:execute_manifest:10` lines 63-66 — never the embed CLI's hard exit (survey FR-003).
- Risk: measurements taken with the stale installed 0.20.0 binary poison the baselines. → Mitigation: every checkpoint pins `CAIRN_HOME` to a mktemp store and uses the repo venv binary (survey CONV + supporting evidence).
- Risk: C-02 supersession churn — the 8 build-side pins pass today and fail the moment the flip lands. → Mitigation: each supersession is a failing-test-first rewrite inside track C's own test files; no other track edits them (parallelization map).

## Delivery
Single end-of-plan commit on `feat/indexing-exact-rate` — code + docs together, never per task (ADR-001, tick-commit node). Ship per C-01: `pre-commit run --all-files`, conventional commit, push feature branch, PR with the audit checklist filled, watch CI.
