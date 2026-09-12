# Tasks: indexing-exact-rate

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: pending — the orchestrator writes `passed @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 2     | 0    |
| 2     | 9     | 0    |
| 3     | 1     | 0    |
| **Σ** | 12    | 0    |

## Phase 1: Quiet corpus (FR-001)
<!-- Checkpoint (plan, After Phase 1): `git ls-files -- cairn.json` non-empty; a
     fresh pinned-CAIRN_HOME build via the repo venv binary records skipped_files
     config_exclude x179 (103 chunks + 76 datasource, survey FR-006b); zero symbols
     under src/cairn/dashboard/static/chunks/ and benchmarks/datasource/ (AC1); pool
     about 75.8 exact of about 19,786 (survey FR-006b projection, vs the 71.16 tree
     baseline in survey FR-006a). No new suite test this phase (plan: Layer C is
     covered by the existing scanner/config suites, survey FR-001a) — AC1 is this
     checkpoint's build measurement. Already in tree per plan, no tasks spawned:
     Layer C machinery end to end (survey FR-001a), noise measurement and the
     minified-skip miss explanation (survey FR-001c), bench/eval corpus-discovery
     safety (survey FR-001d). -->
- [ ] T001 [P] Commit repo-root `cairn.json` excluding the two noise corpora (FR-001)
  - File: new `cairn.json` at repo root; no source file touched. Content exactly
    `{"exclude": ["src/cairn/dashboard/static/chunks/", "benchmarks/datasource/"]}` —
    the shapes build B validated (survey FR-001b gap): zero symbols under either
    prefix, 7,467 remaining symbols identical to build A's non-noise count,
    skipped_files reason `config_exclude` x179.
  - Globs are repo-root-relative, trailing slashes are directory anchors
    (`src/cairn/graph/scanner.py:_build_config_spec:323`); Layer C config exclude
    only — do not widen `_is_minified` (D-004).
  - Verify before implementing (survey FR-001b): `git ls-files -- cairn.json` —
    empty today, non-empty once this lands.
- [ ] T002 (after T001) Measure the quiet corpus on a fresh pinned build — AC1 anchor (FR-001)
  - Consumes T001's committed `cairn.json` via `src/cairn/graph/config.py:load_config:70`
    (then `src/cairn/graph/scanner.py:classify_file:376`, Layer C at lines 418-420;
    skips persisted by `src/cairn/graph/builder.py:_record_skips:160`).
  - From repo root with the repo venv binary — never the installed 0.20.0
    (survey CONV) — and `CAIRN_HOME` pinned to a throwaway store:
    `T=$(mktemp -d /tmp/cairn-p1.XXXXXX)` then `CAIRN_HOME=$T .venv/bin/cairn build`;
    `sqlite3 $T/*/*/.kg "SELECT reason, COUNT(*) FROM skipped_files GROUP BY reason;"`
    expects `config_exclude|179`;
    `sqlite3 $T/*/*/.kg "SELECT CASE WHEN f.path LIKE 'src/cairn/dashboard/static/chunks/%' THEN 'chunks' WHEN f.path LIKE 'benchmarks/datasource/%' THEN 'datasource' ELSE 'other' END a, COUNT(*), COUNT(DISTINCT f.id) FROM symbols s JOIN files f ON s.file_id=f.id GROUP BY a;"`
    expects chunks 0 / datasource 0 (AC1);
    `sqlite3 $T/*/*/.kg "SELECT ROUND(100.0*SUM(CASE WHEN resolution='exact' THEN 1 ELSE 0 END)/COUNT(*),2), COUNT(*) FROM edges WHERE kind IN ('calls','references') AND resolution IN ('exact','ambiguous');"`
    expects about 75.8 of about 19,786 (AC2's before is 71.16 of 61,195,
    survey FR-006a; T008's stats surface is where AC2's after is read).
  - This task is the plan's After-Phase-1 checkpoint; landing it gates Phase 2's
    start (cadence anchor, not a file coupling — plan Dependencies).

## Phase 2: Complete, observable, multivector indexing (FR-002, FR-003, FR-004, FR-005)
<!-- Checkpoint (plan, After Phase 2) — one observable per track, all run with the
     repo venv binary and pinned CAIRN_HOME:
     A: `grep -rn embed_symbols src/cairn | grep -v "def embed_symbols"` shows the
        production call site beyond the line-1661 string (survey FR-002 verify,
        inverted); a no-backend update run succeeds and surfaces the deferred count.
     B: `T=$(mktemp -d /tmp/cairn-p2.XXXXXX); CAIRN_HOME=$T .venv/bin/cairn build && CAIRN_HOME=$T .venv/bin/cairn stats | grep -ci exact`
        returns at least 1 (survey FR-005 verify showed 0), three shares beside
        edge totals (AC6).
     C: `.venv/bin/cairn embed --help` prints no "Off by default"; a flagless embed
        leaves embeddings_mv rows, `--no-multivector` restores the single-vector
        build (AC5); the 4 mechanically-red pins rewritten failing-test-first,
        the 3 query-side pins untouched and green.
     Tracks A / B / C run concurrently — disjoint file sets per the plan map:
     A owns src/cairn/graph/incremental.py, the new update-path test file, and
     src/cairn/cli/update.py; B owns src/cairn/graph/stats.py and
     src/cairn/cli/core.py plus tests/test_status_resource_health.py; C owns
     src/cairn/graph/embeddings.py, src/cairn/cli/embed.py, the three bench
     call sites, and the three mv test files. -->
- [ ] T003 [P] Write the failing update-path embed test — new `tests/test_update_path_embedding.py` (FR-002)
  - C-02 test-first. Docstring states the pre-fix failure (survey FR-002 NOTE):
    reindex deletes changed files' embeddings (incremental.py lines 146-175,
    incl. vec0 cleanup) and never re-embeds, so changed symbols stay unembedded
    until a manual `cairn embed`.
  - Fixture and shape follow `tests/test_workflow_audit_fixes.py:test_incremental_repairs_incoming_edges:52`
    and the `embed_symbols` conventions of
    `tests/test_ann_incremental.py:test_embed_symbols_new_symbol_visible_without_rebuild:112`
    (survey CONV). Assert: after `reindex_paths` on a changed file with a
    backend available, the new symbol ids from `name_to_symbol_ids` carry
    embeddings under the current model (AC3), and the return dict exposes an
    `embedded_symbols` count.
  - Hermetic per C-04: no patching of global subprocess; canonical runner
    `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_update_path_embedding.py -q`.
  - Red bar: `embed_symbols` has zero production callers today (survey FR-002
    verify: `grep -rn embed_symbols src/cairn | grep -v "def embed_symbols"`
    shows only the note_contention string at embeddings.py line 1661).
- [ ] T004 (after T003) Wire `embed_symbols` post-COMMIT in `reindex_paths` (FR-002)
  - Consumes T003's red test in `tests/test_update_path_embedding.py`; implements
    its contract. File: `src/cairn/graph/incremental.py` only — consumes
    `src/cairn/graph/embeddings.py:embed_symbols:1549` and
    `embeddings_available:94` unchanged.
  - Immediately after the per-file `conn.execute("COMMIT")` (incremental.py line
    246), flatten the new ids collected by `insert_parsed_file` into
    `name_to_symbol_ids` (filled at incremental.py lines 232-235) and call
    `embed_symbols(conn, new_ids)` gated on `embeddings_available()`; add
    `embedded_symbols: int` to the return dict beside `reindexed`/`deleted`.
    One hook covers every producer: `incremental_update` (incremental.py:359),
    watcher `_do_catch_up` (`src/cairn/graph/watcher.py:131`), cli sync
    (`src/cairn/cli/system.py:594`), and `_reindex_file`
    (`src/cairn/graph/incremental.py:841` delegating at line 853) — survey FR-002.
  - Post-COMMIT, never pre-COMMIT: `embed_symbols` self-commits its batches
    (embeddings.py lines 1658-1661) and would break the rollback dance at
    incremental.py lines 259-265 (D-002).
  - Acceptance: T003 green; `grep -rn embed_symbols src/cairn | grep -v "def embed_symbols"`
    shows the production call site (survey FR-002 verify, inverted);
    `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_ann_incremental.py tests/test_workflow_audit_fixes.py tests/test_update_path_embedding.py -q`
    green.
- [ ] T005 (after T004) Write the failing deferred-embed observability test (FR-003)
  - Same file track A owns: `tests/test_update_path_embedding.py`, added after
    T004 so the call site exists to observe. C-02 test-first.
  - With no backend available (the runner already pins
    `CAIRN_LIB=/tmp/__no_such_lib__`), a reindex pass must succeed
    (errors == []) AND the deferral must be observable (AC4): one WARN per
    pass carrying the deferred count (warn-once doctrine —
    `src/cairn/graph/embeddings.py:warn_hash_fallback_once:632`,
    `src/cairn/graph/ann_index.py:warn_ann_fallback_once:69`, survey FR-003)
    plus a `deferred_embeds` count in the return dict.
  - Red today: no deferred-embed surface exists on the update path (survey
    FR-003 gap). Doctrine to mirror: the degrade precedent
    `src/cairn/knowledge/ingest/executor.py:execute_manifest:10` lines 63-66
    (`embedded: None` when deferred) — NOT the embed CLI's hard exit
    (`src/cairn/cli/embed.py:_exit_backend_unavailable:11`).
- [ ] T006 (after T005) Implement degrade-not-fail deferral on the update path (FR-003)
  - Files: `src/cairn/graph/incremental.py` — wrap T004's call site: absorb
    `embed_symbols` failures, log one WARN per pass with the deferred count, add
    `deferred_embeds: int` to the reindex_paths return dict and pass it through
    `incremental_update`'s return dict (incremental.py lines 394-398) — and
    `src/cairn/cli/update.py` prints the deferred count (D-002 names this
    surface; the doctor check is explicitly rejected there). The watcher
    (`src/cairn/graph/watcher.py:_update_pass:440` via line 506) inherits the
    counts through `incremental_update`.
  - Acceptance: T005 green; an update run with no backend available succeeds and
    surfaces the deferred count via the log/result channel (plan checkpoint,
    track A leg); `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_ann_incremental.py tests/test_workflow_audit_fixes.py tests/test_update_path_embedding.py -q`
    green.
- [ ] T007 [P] Write the failing stats resolution-share test (FR-005)
  - File: `tests/test_status_resource_health.py` — stats are asserted via its
    `get_stats` minimal-schema fixture convention (survey CONV); class-grouped,
    behavior-named.
  - C-02 red today: `src/cairn/graph/stats.py:get_stats:17` returns
    `edges_resolved` (lines 40-42) but no resolution breakdown (survey FR-005
    gap; live verify `CAIRN_HOME=/tmp/cairn-survey-A.H1ehk4 .venv/bin/cairn stats | grep -ci exact`
    returns 0).
  - Assert: `get_stats` exposes exact/ambiguous/unresolved counts over the
    `kind IN ('calls','references')` pool with exact/ambiguous as shares of the
    exact+ambiguous denominator, on a populated fixture; the minimal-schema
    fixture defaults the new counts to 0 and does not raise (tech-spec pitfall).
  - Runner: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_status_resource_health.py -q`.
- [ ] T008 (after T007) Add resolution shares to `get_stats` and the stats CLI (FR-005)
  - Files: `src/cairn/graph/stats.py:get_stats:17` — one GROUP BY over
    `kind IN ('calls','references')` x `resolution` (the FR-006-comparable
    denominator; `edges_resolved` at lines 40-42 stays untouched) — and
    `src/cairn/cli/core.py:stats:449` — one `display.kv` row beside the edges
    line at 462 with counts plus exact/ambiguous pool shares (AC6).
  - Keys are additive: the other `get_stats` consumers
    (`src/cairn/cli/system.py:431`, `src/cairn/mcp_server/_server_core.py:477`,
    `src/cairn/wiki/generator.py:54`) read specific keys and stay unaffected
    (tech-spec impact table).
  - Acceptance: T007 green; plan checkpoint track B:
    `T=$(mktemp -d /tmp/cairn-p2.XXXXXX); CAIRN_HOME=$T .venv/bin/cairn build && CAIRN_HOME=$T .venv/bin/cairn stats | grep -ci exact`
    returns at least 1, three shares printed beside edge totals.
- [ ] T009 [P] Rewrite the four mechanically-red build-side pins for the new default (FR-004)
  - C-02 test-first (spec FR-004; D-003 pin set): these go red the moment T010's
    flip lands, so rewrite them first — red against today's default-off, green
    after the flip. Files, this track's own test files only:
    `tests/test_embeddings_mv.py` — `test_flag_off_writes_zero_mv_rows_and_keeps_summary_shape:148`
    (kwarg-less `embed_all` now writes mv rows and reports the `mv_embedded` key)
    and `test_cli_multivector_flag_wires_to_embed_all:455` (the CLI default run
    asserts mv rows); `tests/test_ann_vecmv.py` —
    `test_cli_multivector_rebuilds_both_indexes:279` (default run builds `vec_`
    and `vecmv_`); `tests/test_multivector_query.py` —
    `test_empty_mv_table_flag_on_equals_flag_off:365` (beyond the survey's list;
    kwarg-less `embed_all` writes mv rows).
  - The query-side pins at `tests/test_multivector_query.py` lines 210/221/462
    (`test_flag_off_param_shapes_byte_identical`,
    `test_flag_off_never_reads_embeddings_mv`, `test_ann_flag_off_shapes_and_sql`)
    stay green and untouched — if they break, the query default leaked (D-003).
  - Red bar (tech-spec verify, inverted): `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_embeddings_mv.py tests/test_ann_vecmv.py tests/test_multivector_query.py -q`
    fails on exactly the rewritten cases and passes the survivors.
- [ ] T010 (after T009) Flip the multivector build default on and add the opt-out (FR-004)
  - Survey FR-004 status PARTIAL — machinery fully built (producers
    `src/cairn/graph/embeddings.py:_embed_mv_kinds:1267` with per-kind
    `_chunk_hash` staleness at lines 1276-1281, `MV_KINDS` at line 292,
    embeddings_mv schema, `src/cairn/graph/ann_index.py:rebuild_index:216`
    source closed set at lines 156-160); the gap this task closes: default off,
    no opt-out, superseded pins.
  - Files: `src/cairn/graph/embeddings.py:embed_all:1403` — `multivector: bool = False`
    at line 1410 flips to `True` (mv pass gated at lines 1525-1528 starts running
    by default); `src/cairn/cli/embed.py` — the is_flag at lines 160-166 becomes
    the secondary pair `--multivector/--no-multivector` with `default=True`, help
    text rewritten (the current "Off by default … byte-identical to before"
    wording is the retired contract), and the mv index rebuild wiring at lines
    313-317 plus the "mv vectors" summary row at lines 328-329 follow the flag;
    bench pins `src/cairn/bench/agent_suite.py:606`,
    `src/cairn/bench/perf_suite.py:159`, `src/cairn/bench/scaling_suite.py:91`
    pin `multivector=False` explicitly (they time embed as an op — inheriting
    the default would silently change what they measure; D-003).
  - Query side stays opt-in: `src/cairn/graph/semantic.py:RetrievalParams:343`
    field at line 456 untouched (D-003). The 78 kwarg-less `embed_all` test
    sites across 17 files inherit mv writes and stay green except the pin set
    (D-003 consequence; blast radius, tech-spec impact table).
  - Acceptance (plan checkpoint track C): `.venv/bin/cairn embed --help` prints
    no "Off by default"; a flagless embed on a built graph leaves
    `embeddings_mv` rows (count greater than 0) while `--no-multivector`
    restores the single-vector build (AC5); T009's rewritten pins plus the
    query-side pins green:
    `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_embeddings_mv.py tests/test_ann_vecmv.py tests/test_multivector_query.py -q`.
  - Interface for T011: the `--no-multivector` CLI opt-out and the explicit
    `embed_all(multivector=False)` library leg.
- [ ] T011 (after T010) Rewrite the two stale-green pins to pin the explicit opt-out (FR-004)
  - Files: `tests/test_embeddings_mv.py:test_flag_off_base_table_identical_to_flag_on_base_table:165`
    (passes explicit kwargs on both legs — stays green, but exists to pin the
    retired default's byte-identity; reframe to pin the explicit opt-out) and
    `tests/test_ann_vecmv.py:test_default_build_never_creates_vecmv_table:85`
    (API-level `rebuild_index` never creates `vecmv_` regardless of the embed
    default — green, but its "multivector off: the default" contract text is
    now false; rewrite the wording to the opt-out).
  - Consumes T010's interface: the opt-out leg and the unchanged API-level
    rebuild behavior.
  - Acceptance: the mv trio green end to end — `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_embeddings_mv.py tests/test_ann_vecmv.py tests/test_multivector_query.py -q`
    — and no test anywhere still pins the retired wording (searching the tests
    tree for "Off by default" returns nothing).

## Phase 3: Acceptance measurement (FR-006)
<!-- Checkpoint (plan, After Phase 3): acceptance numbers recorded on the
     implemented tree; any shortfall vs the re-derived target is an adjudicated
     fresh D-### re-deriving the target from the measured residual — never a
     silent pass/fail (plan Risk 1, spec Risk). Target per D-001: at least 75%
     exact / at most 25% ambiguous of the calls+references exact+ambiguous pool;
     embedding coverage 100% of indexed symbols after an embed pass. Baselines
     are for comparison, not re-derivation: tree 71.16% exact (43,546/61,195,
     survey FR-006a), exclusion projection 75.81% (15,000/19,786, survey
     FR-006b). -->
- [ ] T012 (after T006) (after T008) (after T011) Measure acceptance against the re-derived targets (FR-006)
  - No code — audit SQL over the implemented tree (tech-spec Code guide,
    FR-006). Consumes the Phase 2 surfaces: T008's stats shares are where AC2's
    before/after is read; the coverage leg runs a flagless embed (T010's
    default) over a graph whose update path T004/T006 wired.
  - Fresh quiet-corpus build and embed, pinned store, repo venv binary (never
    the installed 0.20.0 — survey CONV): `T=$(mktemp -d /tmp/cairn-p3.XXXXXX)`;
    `CAIRN_HOME=$T .venv/bin/cairn build && CAIRN_HOME=$T .venv/bin/cairn embed`;
    `sqlite3 $T/*/*/.kg "SELECT ROUND(100.0*SUM(CASE WHEN resolution='exact' THEN 1 ELSE 0 END)/COUNT(*),2), COUNT(*) FROM edges WHERE kind IN ('calls','references') AND resolution IN ('exact','ambiguous');"`
    must return at least 75.00 exact share (D-001 gate; projection 75.81);
    `sqlite3 $T/*/*/.kg "SELECT ROUND(100.0*COUNT(*)/(SELECT COUNT(*) FROM symbols),2) FROM embeddings;"`
    must return 1.0 (the live DB today is 10,880/20,296, survey FR-006b).
  - Cross-check on the stats surface: `CAIRN_HOME=$T .venv/bin/cairn stats`
    prints the same shares beside edge totals (AC2 after; before is 71.16%,
    survey FR-006a).
  - Below 75% exact or above 25% ambiguous: record a fresh D-### adjudication
    re-deriving the target from the measured residual (spec Risk names this
    path; D-001 is the precedent) — never a silent audit failure; the 95%
    north-star stays out of scope (D-001).
  - Verify-before anchor (tech-spec): the pool query runs today against build
    B's DB and returns 75.81 | 19786 (survey FR-006b verify).

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
