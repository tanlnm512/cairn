# Tasks: swe-bench-integration

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: passed @ ecd55bd (2026-09-17: check.py 0 fail; suite 3631 green at ecd55bd; clean tree; branch feat/swe-bench-integration; 0/11 pre-done; C-01..C-04 reviewed — C-03: datasets dep rides D-004 as a bench extra, not runtime)

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 4 | 4 |
| 2 | 4 | 4 |
| 3 | 3 | 3 |
| **Σ** | 11 | 11 |

## Phase 1: Pinned SWE-bench task source (FR-002)
<!-- Checkpoint (plan, After Phase 1): loader run twice with network disabled
     produces identical pinned task lists; manifest validates. Verify:
     `ls src/cairn/bench/` (loader module present) · offline double-run diff
     empty · the pin file validates. FR-002 is owned here (plan): fixed
     subset, seeds, offline contract established at the task-source layer so
     the suite inherits determinism. `validate_manifest` (datasource.py) stays
     untouched — the loader validates its own pin file (unknown top-level
     sections already ignored, tech-spec grep). The plan milestone's "swebench
     harness pinned as dev-only dep" cell is superseded by D-004: `datasets`
     in a `bench` extra; no swebench harness. -->
- [x] T001 [P] Pin `datasets` behind a new `bench` optional extra — `pyproject.toml` (FR-002)
  - done 2026-09-17 — bench extra datasets>=4.0 pinned (lock 5.0.1); pip-audit green; default install dep-free
  - C-03 gate satisfied by D-004: `datasets` goes in a new `bench` entry under
    `[project.optional-dependencies]`; core wheel and platform matrix
    unchanged. No `swebench` harness dependency in this spec (D-004 — it is
    resolve-rate machinery for the deferred LLM arm, FR-005).
  - Grounding: survey S3 gap — no bench-dataset dependency exists; the pin is
    D-004's recorded decision, imported lazily only inside the loader (T004).
  - Verify before implementing: `uv run --extra bench python -c "import datasets; print(datasets.__version__)"`
    prints a version; a plain core install does not gain the dependency; the
    CI Security pip-audit leg stays green with the extra's tree.
  - [P] with T003 — disjoint files (this manifest-only change vs the new
    loader test module).
- [x] T002 (after T001) Freeze the 50-instance subset manifest — new `benchmarks/datasource/swe-bench-subset.json` (FR-002)
  - done 2026-09-17 — manifest verified — 50 ids @ revision 6ec7bb89, integrity-checked parquet
  - Consumes T001's installed `datasets`: one online enumeration of SWE-bench
    Lite `test` at the pinned dataset revision sha, then freeze the ordered
    subset — the first 50 instance ids in dataset order (the positional-slice
    idiom, [R6]; size 50 is the first-principles call from D-002).
  - File format (D-002, tech-spec): `{schema, dataset_revision_sha,
    subset: [instance_id...], reported: true}`. Ids only — no task content is
    vendored (spec assumption; the loader fetches, survey S3). Any later
    change is a new subset version, never an edit of the frozen list (D-002).
  - Verify before implementing (tech-spec): `python -c "import json; json.load(open('benchmarks/datasource/swe-bench-subset.json'))"`
    parses; every id exists in Lite `test` at the revision (asserted by the
    loader once T004 lands).
- [x] T003 [P] Write the failing loader tests — new `tests/test_swe_bench_loader.py` (FR-002)
  - done 2026-09-17 — 31 loader contract tests (red→green with T004)
  - C-02 test-first; red bar is the absent loader module. Covers: pin-file
    validation before any fetch (ordered unique instance ids, revision sha
    hex, malformed schema rejected); the five-field projection
    `instance_id`, `repo`, `base_commit`, `problem_statement`,
    `environment_setup_commit` with gold-patch fields absent (tech-spec [R5]);
    lazy `datasets` import raising an actionable error without the `bench`
    extra (D-004); the offline rerun path.
  - Hermetic (C-04): tmp_path fixtures; validation and error legs need no
    network; the fetch leg is mocked/injected — never a live Hub call.
  - Verify before implementing (tech-spec): `uv run pytest tests/test_swe_bench_loader.py -q` — red.
  - [P] with T001 — disjoint files (new test module vs the packaging
    manifest only).
- [x] T004 (after T002) (after T003) Implement the SWE-bench loader — new `src/cairn/bench/swe_bench.py` (FR-002)
  - done 2026-09-17 — pytest tests/test_swe_bench_loader.py — 31 passed
  - Greens T003. Consumes T002's manifest format `{schema,
    dataset_revision_sha, subset, reported}` — validated before any fetch.
    New sibling of `corpus.py`/`datasource.py` in `src/cairn/bench/` (survey
    S3 seam; per-suite module pattern, plan assumption confirmed here).
  - Behavior: lazy `import datasets` with an actionable error (D-004);
    `load_dataset("princeton-nlp/SWE-bench_Lite", split="test", revision=<manifest sha>)`
    [R1][R3]; project rows to the five-field
    contract [R5] (gold-patch fields stay out — deferred LLM arm only,
    FR-005); check out `base_commit` into a content-addressed workspace
    cache — after warm-up fully offline (`HF_HUB_OFFLINE=1` reruns, [R3]).
  - This module is the only one that knows the HF schema (drift isolation,
    spec risk); never writes task content into the repo (D-002).
  - Verify before implementing: `uv run pytest tests/test_swe_bench_loader.py -q` green;
    loader run twice with network disabled produces identical task lists;
    `ls src/cairn/bench/` shows the new module (plan Phase 1 checkpoint).

## Phase 2: Deterministic two-arm suite + CLI (FR-001)
<!-- Checkpoint (plan, After Phase 2): suite runs offline over the pinned
     subset, reporting per-task + median tokens/tool calls for both arms; two
     full runs byte-identical (FR-002 re-verified end to end). Verify:
     `grep -n "class _CairnArm\|class _ControlAgent" src/cairn/bench/agent_suite.py`
     (arms reused, not rewritten) · `cairn bench --suite swe-bench` twice →
     report diff empty. The `--suite swe-bench` choice token is the one shared
     contract between the suite and CLI tracks (plan parallelization map);
     the integration task anchors this checkpoint. -->
- [x] T005 [P] Write the failing suite tests — new `tests/test_swe_bench_suite.py` (FR-001)
  - done 2026-09-17 — suite contract tests (red→green with T006)
  - C-02 test-first; red bar is the absent suite module. Asserts over a local
    fixture task set (five-field dicts, no network): both arms report
    non-zero effort per task; per-task rows keyed by `instance_id` with
    `ArmEffort`-shaped arms; cross-task medians for tokens and tool calls,
    both arms; reduction percentages; determinism — two runs yield identical
    calls/est_tokens.
  - Additive-shape guard (D-007): the swe-bench report lives at its own
    payload level — no keys added to the shared `to_dict` shapes pinned
    exactly at tests/test_agent_suite.py:120-124 (tech-spec grep).
  - Verify before implementing: `uv run pytest tests/test_swe_bench_suite.py -q` — red.
  - [P] with T007 — disjoint files (this test module vs the bench CLI
    module only).
- [x] T006 (after T004) (after T005) Implement the swe-bench suite runner — new `src/cairn/bench/swe_bench_suite.py` (FR-001)
  - done 2026-09-17 — pytest tests/test_swe_bench_suite.py — 12 passed; D-011 id-masking determinism
  - Greens T005. Consumes T004's loader output (five-field task inputs) and
    accepts an injected task iterable so the CI smoke (T009) can run fixture
    tasks without the fetch branch. Exports module-level `run_swe_bench_suite`
    — the agreed symbol T007's dispatch branch calls and T008 integrates;
    returns the report payload dict the CLI stamps and prints.
  - Reuse, not rewrite (survey S1): `_CairnArm` (agent_suite.py:94),
    `_ControlAgent` (agent_suite.py:111), `CHARS_PER_TOKEN` (agent_suite.py:58),
    the median/`ArmEffort`/reduction logic, and `run_agent_suite`'s env
    snapshot/restore discipline (`CAIRN_DB`, `CAIRN_EMBED_BACKEND`,
    `CAIRN_RERANK=0`, hash embed — tech-spec grep agent_suite.py:573-590).
  - Per task (D-006): probe targets extracted from `problem_statement` by a
    fixed identifier regex, lexical tie-break (the `_select_targets`
    discipline, survey S1); both scripted arms run `runs` times; per-task
    medians, then medians across tasks. The recipes are part of the published
    metric — no hidden heuristics.
  - Report at its own payload level (D-007); calls/est_tokens deterministic
    within a build, wall time advisory (tech-spec grep agent_suite.py:32-36).
  - Verify before implementing: `uv run pytest tests/test_swe_bench_suite.py -q` green;
    the arms-reuse grep from the phase checkpoint unchanged.
- [x] T007 [P] Extend the bench CLI with the swe-bench suite — `src/cairn/cli/bench.py` (FR-001, FR-002)
  - done 2026-09-17 — 111 bench tests green; TC-001/002/003 proofs PASS; D-012/D-013
  - The `--suite` click.Choice (bench.py:193, tech-spec grep) gains
    `swe-bench`; new `--slice` and `--manifest` options, manifest defaulting
    to benchmarks/datasource/swe-bench-subset.json.
  - Dispatch branch resolves and validates the manifest before any suite work
    (the `_resolve_baseline_file` fails-promptly pattern — tech-spec grep
    bench.py:78-111) and calls `run_swe_bench_suite` (T006's export — the
    agreed integration symbol; T008 completes the wiring).
  - Stamping (D-007): a new swe-bench stamp builder (dataset revision sha +
    subset-manifest digest + instance count) applied beside
    `payload["timestamp"]` at the CLI layer (survey S3 additive pattern);
    `build_artifact_stamp` (datasource.py:532; 12 direct callers / 277
    recursive, tech-spec impact table) gains no keys.
  - Exit-code contract 0 clean / 1 usage / 2 regressions unchanged (tech-spec
    grep bench.py:15-17); `--suite swe-bench` with `--baseline` follows the
    existing per-suite baseline resolution or exits 1.
  - Verify before implementing (tech-spec): `uv run pytest tests/test_bench.py tests/test_agent_suite.py tests/test_bench_stamping.py -q` —
    every exact-traffic pin stays green (D-007).
  - [P] with T005 — disjoint files (the bench CLI module vs the new suite
    test module only).
- [x] T008 (after T006) (after T007) Wire and verify the end-to-end suite run — integration (FR-001, FR-002)
  - done 2026-09-17 — pytest tests/test_swe_bench_cli.py — 9 passed; byte-identical rerun pinned
  - Consumes `run_swe_bench_suite` (T006) and the `--suite swe-bench` dispatch
    (T007): the CLI branch calls the real runner, applies the swe-bench
    stamp, and prints per-task + median rows for both arms.
  - Phase checkpoint (plan): `uv run cairn bench --suite swe-bench` twice →
    report diff empty (FR-002 end to end, offline over the pinned subset);
    arms-reuse grep `grep -n "class _CairnArm\|class _ControlAgent" src/cairn/bench/agent_suite.py`
    unchanged — reused, not rewritten (survey S1).
  - Interface for Phase 3: the runnable `cairn bench --suite swe-bench`
    command and the stamped report payload (medians, both arms) — the docs
    quote it (T010) and the smoke exercises the runner (T009).

## Phase 3: CI smoke guard + published reproduction (FR-004, FR-003, FR-005)
<!-- Checkpoint (plan, After Phase 3): CI green including the smoke; a clean
     clone following only the documented commands reproduces the README
     medians; no LLM arm ships (FR-005 scope assertion, not build work).
     Verify: clean clone → documented commands → medians match README. The
     plan's "new CI job" assumption is resolved by D-005: the smoke is a
     `-m core`-marked test collected by the existing per-PR `-m core` leg
     (ci.yml:175, tech-spec grep) — no workflow edit. -->
- [x] T009 (after T008) Write the hermetic CI smoke test — new `tests/test_swe_bench_smoke.py` (FR-004)
  - done 2026-09-17 — -m core leg collects smoke (27 passed leg); D-014 reconciliation
  - `pytestmark = pytest.mark.core` — the self-demo CI-gating pattern
    (test_self_demo.py:30, tech-spec grep; survey S2), collected automatically
    by the per-PR `-m core` leg; no new CI job and no workflow edit (D-005).
  - Drives `run_swe_bench_suite` end to end over a tiny local fixture task
    set (T006's injected-task iterable): no network, no `datasets` import,
    never the fetch branch; isolated temp dirs, no `~/.cairn` contact, no
    patching the global `subprocess.Popen` (C-04).
  - Asserts both arms report non-zero effort and the payload shape (per-task
    rows, medians, stamp) per T005's contract.
  - Verify before implementing (tech-spec): `uv run pytest -m core -q` —
    the smoke is collected and green.
- [x] T010 (after T008) Write the methodology docs and README pointer — new `docs/benchmarks.md` (FR-003)
  - done 2026-09-17 — docs/benchmarks.md + README published medians (empirical 50-task run): 84.6%/99.0% reduction
  - Consumes T008's stamped medians and the exact command sequence that
    produced them.
  - Contents: metric-class definition — efficiency medians (tool calls +
    est_tokens) as their own metric class, explicitly not numerically
    comparable to RepoGraph's +32.8% or any resolve-rate figure (D-003); the
    scripted probe recipes documented as part of the metric (D-006); subset
    manifest contents (revision sha, ordered ids, digest — D-002); determinism
    caveats (calls/est_tokens deterministic within a build, wall time
    advisory); the clean-clone sequence `pip install -e '.[bench]'` →
    `cairn bench --suite swe-bench` with the default manifest → medians;
    Lite `dev` and `--slice 0:10` documented as run options, not gates (D-005).
  - No published number without its stamp (manifest revision, D-002); README
    gains a pointer from the Measured Results section to the new doc.
  - Verify before implementing (tech-spec): follow the documented commands on
    a fresh clone — they run as written.
- [x] T011 (after T009) (after T010) Verify fresh-clone reproduction and assert the FR-005 scope guard (FR-003, FR-005, FR-004)
  - done 2026-09-17 — fresh-clone EXACT-MATCH reproduction (notes/T011.md); FR-005 scope guard PASS
  - Clean clone → documented commands only → medians match the published
    numbers (plan Phase 3 checkpoint; FR-003).
  - CI green with the smoke collected by the `-m core` leg (FR-004; D-005).
  - FR-005 scope assertion: no LLM arm, no `swebench` dependency (D-004), no
    gold-patch fields in the loader contract — the deferral is stated in the
    docs (T010) and asserted in the Phase 3 PR description (plan Delivery).
  - Verify: fresh-clone reproduction matches the README medians exactly;
    `uv run pytest -m core -q` green.

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      `done <date> — <test/command that proves it>`
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
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
