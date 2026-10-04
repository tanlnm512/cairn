# Tasks: symbol-communities

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: pending — delivery evidence; the orchestrator writes `commit @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 3     | 0    |
| 3     | 1     | 0    |
| 4     | 1     | 0    |
| 5     | 2     | 0    |
| **Σ** | 11    | 0    |

Satisfied in tree — no task required (survey.md items, all "not applicable" and holding): NFR-001, NFR-002, NFR-004, NFR-005. Evidence and verify commands live in the survey items; NFR-005's telemetry coverage is inherited free by T004 via `_RecordingGroup`.

## Phase 1: Analytics extra + communities core (FR-001, FR-002, FR-004)
<!-- Checkpoint: command exists, gated, and persists — grep "communities|louvain" > 0; tomllib shows graph-analytics; `uv run --no-sync python -c "import cairn"` green; without the extra: hint + non-zero exit + store hash unchanged; with it: `SELECT COUNT(*) FROM communities` > 0 on a built store. -->
- [ ] T001 [P] Add the `[graph-analytics]` extra pinning `networkx>=3.4` to pyproject.toml (FR-004)
  - Survey gap (PARTIAL): no `[graph-analytics]` extra exists — tomllib inventory is ann, bench, dev, ingest, otlp, scip, semantic, test, watch; networkx reaches the lockfile only transitively (D-002 floor: `>=3.4`, at or below every observed lock resolution).
  - Verify: `python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(d['project']['optional-dependencies'].get('graph-analytics'))"` → `['networkx>=3.4']`; `uv run --no-sync python -c "import cairn"` still green (core install untouched).
  - Touches:
    - `pyproject.toml`
- [ ] T002 [P] Add the additive `communities` + `symbol_communities` tables to schema.py (FR-001)
  - Plain `CREATE TABLE IF NOT EXISTS` beside the `transitive_edges` exemplar (schema.py:420); NO MIGRATIONS entry (schema.py:214-216) — survey: absence of both tables is the FR-001 gap.
  - Exact columns (D-004, downstream contract): `communities(id INTEGER PRIMARY KEY, label TEXT NOT NULL, size INTEGER NOT NULL)`; `symbol_communities(community_id INTEGER NOT NULL, symbol_id INTEGER NOT NULL, structural_degree INTEGER NOT NULL)`.
  - Verify: open a pre-feature fixture store — both tables exist empty, all suites green.
  - Touches:
    - `src/cairn/graph/schema.py`
- [ ] T003 (after T001) (after T002) Implement the communities writer module src/cairn/graph/communities.py (FR-001, FR-002, FR-004)
  - Consumes from T002 the exact table/column shapes above; from T001 the extra name `graph-analytics`.
  - Probe FIRST (D-006): lazy `from networkx.algorithms.community import louvain_communities` before any `get_db` (a writable open applies schema, violating store-unchanged); ImportError raises with hint `pip install 'cairn[graph-analytics]'`, copied from swe_bench.py:fetch_dataset_rows:89-101.
  - Survey gap (PARTIAL, FR-002): the shared kind set exists (`STRUCTURAL_EDGE_KINDS`, traversal.py:8-12) but nothing clusters over it and no per-kind weights exist anywhere — import the tuple verbatim (never re-declare) and add the D-003 weight constant: calls/call=1.0, extends/implements=2.0, undirected weighted projection.
  - Full refresh per the dataflow.py:474-485 shape: single DELETE then one sorted executemany; louvain returns sets — sort every emission (D-005).
  - Verify: `uv run --no-sync python -c "from networkx.algorithms.community import louvain_communities; import inspect; print('seed' in inspect.signature(louvain_communities).parameters)"` → True; new tests in tests/test_communities.py cover the seeded run populating both tables on a fixture store.
  - Touches:
    - `src/cairn/graph/communities.py`
    - `tests/test_communities.py`
- [ ] T004 (after T003) Register the `cairn communities` command (FR-001, FR-004)
  - Consumes from T003 the module's public compute+persist entry in `src/cairn/graph/communities.py` (exact symbol name `unknown — verify` at implementation; tech-spec pins module ownership only).
  - `@main.command("communities")` onto the click group (cli/main.py:136) plus one side-effect import in cli/__init__.py:14-41; invocation recording is inherited from `_RecordingGroup` — add nothing (NFR-005).
  - Probe-first ordering at the command layer: without the extra → install hint, non-zero exit, store byte-unchanged (plan checkpoint); with it → prints community count and sizes (hubs/labels land in Phase 2).
  - Verify: `uv run cairn communities --help`; the Phase 1 checkpoint commands from plan.md.
  - Touches:
    - `src/cairn/cli/communities.py`
    - `src/cairn/cli/__init__.py`
    - `tests/test_communities.py`

## Phase 2: Deterministic hubs and labels (FR-003, FR-007, FR-008)
<!-- Checkpoint: two consecutive runs on an unchanged graph → identical printed output and table contents; global + per-community hub lists present, K=10 default; seeded double-run owner test green. -->
- [ ] T005 (after T004) Pin the determinism contract: LOUVAIN_SEED, sorted inputs/emissions, double-run owner test (FR-007)
  - Survey gap: no seeded double-run test and no communities module pre-existing this phase; `seed` verified only on the 3.6.1 arm — the double-run test on every CI arm is the guard for the 3.4.2 lock arm (D-002).
  - D-005 contract fixed here for T006/T007: `LOUVAIN_SEED` module constant passed as `seed=`; nodes inserted sorted by (qualified_name, symbol id); communities persisted by (-size, label), members by (path, qualified_name) — stable ids across runs.
  - Owner test: two consecutive runs on an unchanged fixture graph → byte-equal tables and printed output.
  - Touches:
    - `src/cairn/graph/communities.py`
    - `tests/test_communities.py`
- [ ] T006 (after T005) Add hub ranking: combined structural degree, global + per community, --top-k default 10 (FR-003)
  - Consumes from T005 the sorted-emission contract (hubs sort key (-structural_degree, path, qualified_name)) and the `symbol_communities.structural_degree` column from T002/T003.
  - Survey gap (PARTIAL): repo_map hubs are all-kind in-degree capped at 3 — no combined in+out structural degree, no global ranking, no per-community hubs, no K-default-10 constant exist; this task adds them (repo_map stays byte-identical, D-001).
  - CLI prints global + per-community top-K hubs; `--top-k` validated positive int (`_validate_cap` shape, repo_map.py:32).
  - Touches:
    - `src/cairn/graph/communities.py`
    - `src/cairn/cli/communities.py`
    - `tests/test_communities.py`
- [ ] T007 (after T006) Add deterministic LLM-free community labels (FR-008)
  - Consumes from T006 the per-community top hub (first entry of the (-structural_degree, path, qualified_name)-sorted hub list) as the label fallback.
  - Survey gap: no label machinery exists — derive from member facts per D-005: dominant member path segment when share exceeds 0.60 (reuse repo_map `_dominant_segment`, repo_map.py:99, threshold :13), else top hub's qualified_name, prefixed so the two cases stay distinguishable.
  - Labels persist into `communities.label`; determinism covered by the T005 double-run test extended to label bytes.
  - Touches:
    - `src/cairn/graph/communities.py`
    - `tests/test_communities.py`

## Phase 3: Compass Subsystems section (FR-005)
<!-- Checkpoint: compass suites green with the section coupled to the critic — pytest tests/test_compass_critic.py tests/test_compass_generator.py green; "Subsystems" hits in both generator.py and critic.py; owner test green with and without community data. -->
- [ ] T008 [P] Add the rows-gated compass Subsystems section with critic vocabulary in one lockstep change set (FR-005)
  - ONE lockstep change set per D-007 — generator, critic, prompts, and pins move together, never split:
    (a) `generate_compass` resolves a Subsystems fact — top 5 communities by (-size, label), each with its top hub — via a compass-side read helper (not in the communities module); `_template_body` (generator.py:277) emits `# Subsystems` immediately after "# What Does This Module Do?" only when the fact is non-empty; flow body untouched.
    (b) `_DEFAULT_SECTION_VOCAB` (critic.py:75-85) grows 9→10 with "# Subsystems".
    (c) the `total = 5.0` literal (critic.py:133) becomes named `_DEFAULT_COMPLETE_SECTIONS = 5.0` with an updated adjacent comment — it must NOT track vocab length (a 10-pool would score complete 5-section bodies 5/10 = 0.5 < 0.7 warn threshold; see D-007); `min(sections/total, 1.0)` saturates the 6-section module body at 1.0.
    (d) tasks.py module prompt enumeration (:821-822) gains the optional section in the same task.
  - Reads the frozen Phase 2 tables read-only: `communities(id, label, size)` + `symbol_communities(community_id, symbol_id, structural_degree)`; gate on ROWS, never table presence (D-006 — presence is always true post-open).
  - Pins moved/kept in this task: owner test `test_deterministic_template_passes_own_critic` (tests/test_compass_generator.py:204) green with and without rows; wiki explicit-vocab callers stay bit-identical (tests/test_wiki_promotion.py:383); graft parity stays green via rows-gating (tests/test_graft_parity_notes.py:134).
  - Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_compass_critic.py tests/test_compass_generator.py tests/test_wiki_promotion.py tests/test_graft_parity_notes.py -q` green (baseline: 42 passed on the first pair).
  - Touches:
    - `src/cairn/compass/generator.py`
    - `src/cairn/compass/critic.py`
    - `src/cairn/llm/tasks.py`
    - `tests/test_compass_generator.py`
    - `tests/test_compass_critic.py`
    - `tests/test_wiki_promotion.py`

## Phase 4: Dashboard /communities view (FR-006, NFR-006)
<!-- Checkpoint: dashboard suites green at 15 nav ids / 17 crawled paths (survey baseline: 10 passed + 31 passed at 14/16). -->
- [ ] T009 [P] Add the dashboard /communities view with the full pin surface in one lockstep change set (FR-006, NFR-006)
  - ONE lockstep task per D-008 — route, reader, nav, icon, and every pin move in one commit:
    new `src/cairn/dashboard/routes/communities.py` exposing `register(routes, context)` (routes/memory.py:register:14 convention), registered in app.py:357-365; rows-gated `get_communities` in dashboard/data.py on `get_read_only_db` (:1912) — empty stores render an empty panel, never an error; template `templates/communities.html` with per-community member drill-down as real table semantics; Explore-section append after `graph` in NAV_SECTIONS/NAV_LABELS (shell.py:18-41, 14→15 — keeps the positional pins `sections[1]["items"][1]` = graph and the section-label pin green); 15th icon arm in `_icons.html` (:8-34); `/communities/` into all three byte-identical `_MAIN_VIEWS` tuples (tests/test_dashboard_accessibility.py:28-45, tests/test_dashboard_restyle.py:23-40, tests/test_dashboard_assets.py:23-38, 16→17, moved together) and the readonly ROUTES list (tests/test_dashboard_readonly.py:27-56).
  - Survey gap (PARTIAL, FR-006): no route/module/template/icon branch; six count pins to move. Survey gap (PARTIAL, NFR-006): once registered the crawl extends automatically — markup must pass focus-visible (:138-150), no-outline (:153-160), button-name (:283), reduced-motion (:333-357), table-semantics (:365-375) audits.
  - Reads the frozen Phase 2 tables read-only (same columns as T008); packaging is glob-auto-covered (tests/test_dashboard_packaging.py:36) — no edit.
  - Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_shell.py tests/test_dashboard_accessibility.py tests/test_dashboard_restyle.py tests/test_dashboard_assets.py tests/test_dashboard_readonly.py -q` green at 15/17.
  - Touches:
    - `src/cairn/dashboard/routes/communities.py`
    - `src/cairn/dashboard/app.py`
    - `src/cairn/dashboard/data.py`
    - `src/cairn/dashboard/templates/communities.html`
    - `src/cairn/dashboard/templates/_icons.html`
    - `src/cairn/dashboard/shell.py`
    - `tests/test_dashboard_shell.py`
    - `tests/test_dashboard_accessibility.py`
    - `tests/test_dashboard_restyle.py`
    - `tests/test_dashboard_assets.py`
    - `tests/test_dashboard_readonly.py`

## Phase 5: Performance gate + docs closeout (NFR-003)
<!-- Checkpoint: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_scaling_gate.py -q -m "not infra"` green with the communities ≤10s row at GATE_FILES=1000; docs/CHANGELOG reflect the command, extra, and view. -->
- [ ] T010 [P] Add the communities budget row to the 1000-file scaling gate (NFR-003)
  - Survey gap (PARTIAL): nothing to time until the command exists; the budget row mirrors the closure-budget gate shape — suite point scaling_suite.py:84-98 (`sizes` incl. 1000, :87), assertion shape `point.closure_seconds <= wall_budget` (tests/test_scaling_gate.py:73) at `GATE_FILES = 1000` (:15) — per D-009: communities wall seconds ≤ 10 at the 1000-file gate, timing the whole pipeline (compute + persist + hubs + labels).
  - Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_scaling_gate.py -q -m "not infra"` green with the new row.
  - Touches:
    - `src/cairn/bench/scaling_suite.py`
    - `tests/test_scaling_gate.py`
- [ ] T011 [P] Document the command, extra, and view in cli-reference and CHANGELOG (NFR-003)
  - Phase 5 milestone closeout: `docs/cli-reference.md` gains the `cairn communities` entry (command, `--top-k`, the `[graph-analytics]` gate and install hint); `CHANGELOG.md` records the command, extra, and dashboard view.
  - Verify: the entries name the exact command spelling and extra name from T001/T004.
  - Touches:
    - `docs/cli-reference.md`
    - `CHANGELOG.md`

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
- Phase order is the schedule: Phase 1 → Phase 2 strictly ordered (same
  module/schema/CLI files); Phases 3 ∥ 4 ∥ 5 after Phase 2, disjoint file
  sets, reading the frozen Phase 1-2 tables read-only (plan.md
  parallelization map)
