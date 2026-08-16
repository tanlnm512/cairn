# Tasks: benchmark-datasource

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Survey**: [survey.md](survey.md) | **Tech**: [tech-spec.md](tech-spec.md)
Status reflects code state per [survey.md](survey.md), not intent. Baseline
v0.11.0 @ 66882c2: 0 DONE / 3 PARTIAL (FR-001, FR-003, FR-004) / 3 TODO
(FR-002, FR-005, FR-006). No survey item is DONE, so no task below is
ticked; tasks on PARTIAL FRs name the survey gap they close, and every
"verify-before" command is the survey's recorded before-picture.

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 5     | 0    |
| 3     | 3     | 0    |
| 4     | 4     | 0    |
| 5     | 2     | 0    |
| 6     | 2     | 0    |
| **Σ** | 20    | 0    |

## Wave map
<!-- [P]/(after T###) chains are the execution map; PRs still land in phase
     order 1 → 2 → (3 ∥ 4) → 5 → 6 per plan.md Delivery. -->
- After T004: T005, T006 [P], T013 [P] run concurrently (P2 spine, NOTICE,
  P4 stamping code — disjoint files; plan: "P4's code track can start the
  moment P1 lands").
- After T005: T007 [P] ∥ T008 [P] (size check vs smoke test, disjoint files).
  After T013: T014 (shares `src/cairn/cli/bench.py` + bench tests with T013).
- After T009: T010 [P] ∥ T011 [P] (P3 ∥ P4 window; T014 may still be in
  flight — the plan-verified disjoint pair).
- Serial tail: T012 (consumes T010+T011) → with T014 gates T015 → T016 →
  T017 → T018 → T019 → T020.
- `benchmarks/datasource/manifest.json` is the serial spine — T002 mints it,
  T019 is the next and only other writer, never two at once (plan risk).
- `.github/workflows/ci.yml` has exactly one writer per phase: T004, T009,
  T016, T018 (additive steps; phase 3 and phase 6 add no CI steps).

## Phase 1: Datasource manifest + T1 content pin (FR-001)
<!-- Checkpoint: manifest exists with generator sha/seed/sizes/expected counts;
     regenerating T1 on a clean checkout reproduces the manifest's content hash
     and the CI datasource step is green; flip a seed constant → check exits
     non-zero. -->
- [x] T001 [P] Implement the datasource tree-hash + manifest helper in a new
      `src/cairn/bench/` module (e.g. `src/cairn/bench/datasource.py`):
      stdlib-only sorted-manifest digest `sha256("<mode> <relpath>\0<sha256(content)>")`
      over sorted paths (D-003 — path-order independent, byte-identical
      ubuntu/macOS), plus manifest load/validate/save; document whether the
      empty `.git` scanner marker (`corpus.py:50-52`) is inside the hashed
      set and keep that rule constant; unit tests incl. a
      path-order-independence test (FR-001). Closes survey gap "No manifest
      concept exists in src/cairn ... no path-order-independent content-hash".
      Verify-before: `grep -rn "manifest" src/cairn --include="*.py" | grep -v __pycache__`
      → no output today (survey FR-001).
      [DONE 2026-08-16: tests/test_bench_datasource.py 38 passed — path-order
      independence pinned; git-style normalized mode bits (umask-immune)
      documented as the D-003 realization]
- [x] T002 (after T001) Mint `benchmarks/datasource/manifest.json` via the
      helper: T1 section recording generator git-sha, seed (`DEFAULT_SEED = 0xC0DE`,
      `src/cairn/bench/corpus.py:19`), every size, complexity, expected counts
      (`corpus_stats` → files/lines/bytes, `corpus.py:99`), and the tree-hash
      computed at every declared size; generator sha comes from the manifest,
      never from the tree (tech-spec pitfall) (FR-001, TC-001).
      [DONE 2026-08-16: manifest validates clean; re-hash spot check (size 100)
      matches — manifest-is-true; single complexity "medium" per frozen validator]
- [x] T003 (after T002) Add `scripts/verify_datasource.py` (assert mode): read
      the manifest, regenerate the corpus per its recipe (`generate_corpus`,
      `corpus.py:22`) at every declared size into a temp root, assert tree-hash
      AND expected counts; exit 0 on match, non-zero naming the mismatched
      fact (hash/counts); two consecutive runs → identical hash; scratch
      seed-flip → non-zero (FR-001, AC2, TC-002/TC-003/TC-004/TC-005).
      [DONE 2026-08-16: 14 tests; exit contract 0/1/2 (drift vs schema distinct);
      fact-level naming per task text — aggregate digest cannot attribute files]
- [x] T004 (after T003) Wire the T1 check into the CI bench job
      (`.github/workflows/ci.yml:261-321`): new step running
      `scripts/verify_datasource.py` before the bench run; network-free.
      Verify-before: `grep -n "hash" .github/workflows/ci.yml` → only pip
      cache + `hashFiles('.pre-commit-config.yaml')` + actions/cache today
      (survey FR-001) (FR-001, AC2).
      [DONE 2026-08-16: one added CI step after install, before bench; real gate
      (deterministic content check); local --size 300 exit 0 in 0.16s]

## Phase 2: T2 vendored real-code snapshot (FR-002)
<!-- Checkpoint: snapshot vendored, attributed, guarded, exercised —
     `du -sk benchmarks/datasource` ≤ 5120; provenance names repo+commit+
     license; NOTICE has a vendored-content section; build+query smoke green
     (`uv run pytest -q -k "t2 or smoke"`). -->
- [x] T005 (after T004) Vendor the yarl source export at a pinned commit
      under `benchmarks/datasource/t2/` + `t2/provenance.json`
      {upstream repo, commit, license, export notes} (D-002; budget measures
      the vendored tree, not GitHub repo size). Closes survey gap "NO
      benchmarks/ dir ... No provenance manifests". Verify-before:
      `ls benchmarks 2>/dev/null || echo "NO benchmarks/ dir"` → "NO
      benchmarks/ dir" (survey FR-002) (FR-002, TC-015).
      [DONE 2026-08-16: pin dddcb82 (yarl master HEAD — default branch is master,
      not main); 33 files / 437 KB vendored; provenance.json complete; smoke
      test builds 1066 symbols / 2432 edges, URL findable]
- [x] T006 (after T005) Add the vendored-content attribution section to `NOTICE`
      (82 lines, deps-only today): upstream project + license, distinct from
      the dependency families; keep the existing scope intact incl. the
      bge-m3 note (NOTICE:79-82) (FR-002, TC-016).
- [x] T007 [P] (after T005) Add the size-budget mode to
      `scripts/verify_datasource.py`: `benchmarks/datasource/t2/` ≤ 3 MB and
      `benchmarks/datasource/` ≤ 5 MB, non-zero on breach; pre-commit
      `check-added-large-files --maxkb=500` is per-file, not a tree budget
      (survey FR-002), so this check is load-bearing not ceremony (FR-002,
      AC4, TC-017/TC-018).
      [DONE 2026-08-16: 12 new tests; budgets on every invocation; exit 3; __pycache__
      excluded so dev litter cannot false-breach the committed-tree budget]
- [x] T008 [P] (after T005) Add the build+query smoke test over `t2/` (new
      `tests/test_datasource_t2.py`, selected by `-k t2`): create the scanner
      marker at runtime exactly as `generate_corpus` does (git does not track
      empty dirs — tech-spec pitfall), build a graph over `t2/`, answer a
      known-symbol query and one cross-file callers query (FR-002, AC3,
      TC-013/TC-014).
      [DONE 2026-08-16: TC-013 identity asserts + TC-014 pinned cross-file pair
      (encode_url@_url.py -> split_url@_parse.py, resolution=exact); root-collection
      collision with vendored upstream tests found+fixed via testpaths config]
- [x] T009 (after T007, T008) Wire T2 checks into the CI bench job: steps
      running the size-budget check and the t2 smoke test (plan checkpoint
      command: `uv run pytest -q -k "t2 or smoke"` green locally and in CI)
      (FR-002, AC3/AC4).
      [DONE 2026-08-16: one added smoke step (+[test] extra install); budget half
      already live via T004 step per T007 every-invocation design — no duplicate]

## Phase 3: Hand-verified ground truth + validator (FR-003)
<!-- Checkpoint: the survey's probe now reports ≥ 50 L1 / ≥ 20 L5 with
     rationale (was "total 40 L1 30 L5 10"); validator re-verifies every
     expectation on a fresh build → exit 0; deliberately aging one entry
     names that entry. -->
- [x] T010 [P] (after T009) Add the graded ground-truth loader + matcher to
      `src/cairn/eval.py`: second loader for `queries.jsonl` +
      `expectations.tsv` alongside `load_eval_queries` (D-008 — the yaml
      fixture and its 30/10 set stay untouched as test fixture); matching
      prefers `file#symbol` identity with the existing substring rule
      (`eval.py:81`) as fallback; recall@10 over grade ≥ 1, MRR ranks
      grade-2 first; keep the report shape `{"L1": {...}, "L5": {...}}`
      (`eval.py:155-167` — `tests/test_cli_smoke.py:42-44` asserts "L1");
      `eval_cmd --queries` (`cli/system.py:491-527`) accepts the new set; add
      the dedicated eval tests that do not exist today (survey FR-003: 0 hits
      in `tests/`). Closes survey gap "no hand-verified expected symbols, no
      rationale" on the consumption side. Verify-before:
      `.venv/bin/python -c "from cairn.eval import load_eval_queries; ..."`
      → total 40 L1 30 L5 10 (survey before-picture) (FR-003).
      [DONE 2026-08-16: 41 tests (first dedicated eval suite); two-tier matcher;
      grade-2-first MRR; yaml path stash-diff byte-identical; real T011 pair
      parses 58/24 + 234 expectations]
- [x] T011 [P] (after T009) Author the ground-truth pair under
      `benchmarks/datasource/t2/ground_truth/` per D-004: `queries.jsonl`
      (query_id, level L1|L5, kind definition|callers|impact|flow|knowledge,
      text, rationale citing the snapshot) + `expectations.tsv` (query_id,
      symbol_id `file#symbol`, grade 1 = must-return | 2 = primary target);
      ≥ 50 L1 spanning all four kinds (TC-020) + ≥ 20 L5, hand-verified
      against the vendored yarl snapshot; this is the phase's long pole —
      one data commit, reviewable as a unit. Closes survey gap "Ground-truth
      set is not maintained data (generic shapes, 30/10 counts ...)" and
      moves the counts from 30/10 to ≥ 50/≥ 20 (FR-003, AC5, TC-019).
      [DONE 2026-08-16: 82 queries (58 L1: 18 def/20 callers/10 impact/10 flow; 24 L5),
      234 expectations (82 grade-2), 234/234 empirically verified vs a real
      graph build of the yarl snapshot — zero aspirational entries]
- [x] T012 (after T010, T011) Add `scripts/verify_ground_truth.py`: build a
      fresh graph (+ OKF knowledge bundle for L5) from
      `benchmarks/datasource/t2/`, re-verify every expectation through the
      T010 matcher, name stale entries (query text + missing symbol) and
      exit non-zero; a missing bundle root is an infrastructure error, not a
      stale entry (`eval.py:97-98` returns 0.0/0.0 there); the validator
      never auto-rewrites expectations (D-010 — stale sets ship as DS-v2)
      (FR-003, AC5, TC-021/TC-022).
      [DONE 2026-08-16: 16 tests; real pair 234/234 exit 0 on a fresh build; stale
      path names query+symbol, never rewrites (D-010); exit 2 > 1 > 0 precedence]
      [DONE 2026-08-16: 18-line section, 7/7 provenance facts scripted-verified in
      NOTICE]

## Phase 4: Stamped baselines + `--baseline` compare (FR-004)
<!-- Checkpoint: perf payload carries dataset-version, cairn-version,
     machine-profile keys (today only timestamp); `benchmarks/baselines/DS-v1/`
     exists; `cairn bench --compare --baseline DS-v1` renders the dataset-version
     header + mismatch warning; `pytest tests/test_bench.py
     tests/test_agent_suite.py` green (34 at baseline). -->
- [x] T013 [P] (after T004) Stamp every bench artifact at the CLI payload
      layer (D-006): a stamp step beside `payload["timestamp"] =
      datetime.now(timezone.utc).isoformat()` (`cli/bench.py:122/157`) adding
      `dataset {name, version, tree-hash, t3-entry?}` (read via the T001
      module), `cairn_version` (`__version__` = "0.11.0",
      `src/cairn/__init__.py:2`), and `machine_profile {arch, cpu,
      cpu_count, os, runner_class}` (D-005; precedent `_report_versions`
      `cli/system.py:1329`; `os.cpu_count()` today appears only in
      `graph/builder.py:215`); stamp at the CLI layer, NOT in `to_dict`, so
      `test_json_payload_shape_stable` (`tests/test_agent_suite.py`) stays
      green. Closes survey gap "No dataset-version/cairn-version/
      machine-profile stamping in artifacts". Verify-before:
      `grep -n "timestamp\|version\|profile\|baseline" src/cairn/cli/bench.py`
      → only timestamp lines + `--save/--compare/--threshold` (survey FR-004)
      (FR-004, TC-006).

      [DONE 2026-08-16: 16 tests; both payload sites stamped via one build_artifact_stamp();
      to_dict untouched (D-006); dataset_version=DS-v1 minted by orchestrator spine-edit
      after T013 found the field absent — stamp now carries it]
- [x] T014 (after T013) Add `--baseline <DS-version>` resolution to
      `cairn bench --compare` (`cli/bench.py`): resolve
      `benchmarks/baselines/<DS-version>/`, render a dataset-version header,
      print a loud machine-profile mismatch warning naming the mismatched
      fields (warn, never normalize — D-005), fail promptly with the missing
      version named on unknown input; the diff reuses `compare_reports`
      (`report.py:148`) / `compare_agent_reports` (`agent_suite.py:521`)
      unchanged and preserves `sys.exit(2)` regression semantics
      (`cli/bench.py:205`); serial after T013 — same file
      (`src/cairn/cli/bench.py`) and same tests (`tests/test_bench.py`,
      `tests/test_agent_suite.py`, TC-012 regression guard: 34 passing)
      (FR-004, AC1, TC-007..TC-012).
      [DONE 2026-08-16: 12 tests; exit contract 0 clean / 1 usage+baseline / 2 regression;
      field-by-field advisory mismatch warning; unknown version names available ones]
- [x] T015 (after T014, T012) Generate + commit `benchmarks/baselines/DS-v1/`
      on the maintainer's machine (`runner_class reference-local`):
      `perf.json`, `scaling.json`, `quality.json` as self-describing JSONs
      (schema tag + T013 stamp + existing payload keys — D-001); quality from
      `run_evaluation` over the T011 ground truth; expect minutes-scale
      scaling runs at sizes 100..5000; landed as its own data commit inside
      the P4 PR (plan Delivery); DS-v1 is immutable after commit (D-010).
      Closes survey gap "No committed baselines/DS-v1" (FR-004).
      [DONE 2026-08-16: 4 artifacts + README + mint script (752s wall); first real
      quality numbers L1 recall@10=0.4174 MRR=0.2862 (bge-m3); L5=0.0 documented
      as surface-absent (no bundle); compares exit 0 (perf max +8.7%)]
- [ ] T016 (after T015) (in-progress) Rewire the CI bench job to committed baselines
      (D-007): step 5 gains `--baseline DS-v1`; drop cache steps 4+7
      (actions/cache `bench-baseline-v1-*`, `cp bench-current.json
      bench-baseline.json`); keep steps 6/8/9 byte-compatible — advisory
      marker `<!-- cairn-bench-advisory -->`, `bench_compare.py` never exits
      non-zero, artifact upload + PR comment; a CI-vs-reference-local profile
      mismatch warns and the job stays green (TC-011 standing guard);
      `.github/scripts/bench_compare.py` reads additively (`.get("ops", [])`)
      so stamps are safe, but `ops`/`median_ms` shapes must not change
      (FR-004, AC1).

## Phase 5: Generated reference tables (FR-005)
<!-- Checkpoint: `grep -c "_fill_" docs/benchmarks.md` → 0 (was 47
     occurrences / 15 lines); regenerate twice → `git diff --exit-code
     docs/benchmarks.md` clean; sentinels around all three families; the CI
     docs check fails on a hand edit inside sentinels. -->
- [ ] T017 (after T016) Add `scripts/gen_benchmark_tables.py` + sentinel
      markers in `docs/benchmarks.md`: wrap the three `_fill_` families
      (retrieval quality :60-61, perf :109-117, scaling :143-146 — 47
      occurrences today) between sentinels; the generator reads committed
      baselines only (T015), maps op rows 1:1 to `run_perf_suite` op names
      (`OpTiming` keys `median_ms/p95_ms/ops_per_sec`, `ScalingPoint` keys
      `n_files/symbols/build_s/embed_s/db_mb/resolve_rate/peak_mem_mb`,
      quality rows from the `run_evaluation` shape), uses pinned decimal
      formatting + sorted rows for byte-idempotency, fails loudly on a
      missing family, and never touches bytes outside the sentinels; first
      regen replaces every `_fill_` cell. Closes survey gap "All three
      families unfilled; no sentinel markers; no table generator".
      Verify-before: `grep -c "_fill_" docs/benchmarks.md` → 15 lines /
      47 occurrences (survey FR-005) (FR-005, AC6, TC-024..TC-026/TC-028).
- [ ] T018 (after T017) Add the CI docs hand-edit check: bench-job step that
      runs `scripts/gen_benchmark_tables.py` and fails on `git diff
      --exit-code docs/benchmarks.md` (same spirit as
      `scripts/verify_no_code_change.py`); verified by a temporary local edit
      inside a sentinel (FR-005, AC6, TC-027).

## Phase 6: T3 scale pins + local fetch (FR-006)
<!-- Checkpoint: `jq '.t3 | length' benchmarks/datasource/manifest.json`
     ≥ 2, each entry with url + commit; the documented local command for one
     entry fetches by pin and its result JSON records the manifest entry;
     `grep -rn "t3\|fetch" .github/workflows/ci.yml` shows no T3 fetch step. -->
- [ ] T019 (after T018) Add the T3 section to
      `benchmarks/datasource/manifest.json`: ≥ 2 entries `{name, url, commit,
      scale hint}` at distinct scale points (20k-file class), extending the
      T002 schema; second and final writer of the serial-spine file (plan
      risk: one manifest, one writer at a time); T3 stays manifest-pinned,
      never vendored — a T3 addition must not invalidate DS-v1 (D-010)
      (FR-006, TC-029).
- [ ] T020 (after T019) Implement + document the local T3 fetch-by-pin
      command (new script under `scripts/`, NOT `src/cairn` — D-009; today
      `grep -rn "git clone" src/cairn --include="*.py"` → 0 matches and it
      stays that way): fetch a manifest entry by explicit pinned-commit
      checkout, never the default-branch HEAD, failing loudly with the entry
      named on an unreachable pin (codegraph's contamination lesson — pin
      enforcement lives in the command); run the bench with `--json --save`
      so the T013 stamp records the manifest entry (repo + commit + scale)
      in the result; document the command in `docs/benchmarks.md` outside
      T017's sentinels (additive region); no CI wiring — TC-033 standing
      guard: no T3 fetch step appears in `ci.yml`. Verify-before:
      `grep -rni "offline\|network" .github/workflows/ci.yml` → no output
      (survey FR-006) (FR-006, AC7, TC-030..TC-033).

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)`; serial runs need a reason, parallel
  runs need none. A `[P]` task with an `(after T###)` gate runs alongside
  every other task whose gates are satisfied and whose files are disjoint.
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
- Status comes from survey.md only: a task may be ticked solely with a
  passing verify command recorded in survey.md (or a later survey refresh);
  today that means every task above is open
