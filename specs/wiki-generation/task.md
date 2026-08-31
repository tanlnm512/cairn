# Tasks: wiki-generation

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: passed @ 264647ae4cf286e7efed52afc87d98589b81258a (2026-08-31:
check.py 0 fail; full suite 2619 passed / 3 skipped; clean tree after spec-set
commit; branch feat/wiki-generation cut from the audited sha; constitution
C-01..C-04 verified — single end-of-plan PR per approved plan, red-first pairs
in every phase, zero new runtime deps per D-002, C-04 rules embedded in task
texts)

Statuses trace to survey.md: FR-004 is survey-DONE (regression guards only, no
implementation task); FR-010 is PARTIAL wiring (verification tests only — the
substantive `sources` population belongs to FR-003's tasks); all other FRs are
PARTIAL/TODO per survey and get implementation pairs. All tasks below start
open — no implementation exists at baseline 264647ae.

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| Phase 1 | 2 | 2 |
| Phase 2 | 5 | 5 |
| Phase 3 | 2 | 2 |
| Phase 4 | 2 | 2 |
| Phase 5 | 2 | 2 |
| Phase 6 | 1 | 1 |
| Phase 7 | 2 | 2 |
| Phase 8 | 2 | 2 |
| Phase 9 | 2 | 2 |
| **Σ** | 20 | 20 |

## Phase 1: Page-plan core (FR-001)
<!-- Checkpoint (plan.md): plan is deterministic (two runs → identical plan, same
input hashes); empty/unindexed graph run exits 1 queueing nothing (US1 AC3).
Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_planner.py -q`
(new file, created this phase) plus substrate regression
`CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_tasks_safety.py tests/test_compass_critic.py -q` (24 passed at baseline). -->

- [x] T001 Write failing planner-contract tests in `tests/test_wiki_planner.py` (FR-001)
      done 2026-08-31 — red pinned: collection ModuleNotFoundError on cairn.wiki.catalog (orchestrator-verified)
      Red-first (C-02): pin the contract of `build_page_plan(conn, repo, pages_cap=10)`
      in a new `src/cairn/wiki/catalog.py` before it exists — tests fail on the missing
      module/symbol (red-for-the-right-reason). Pin: an overview page planned first;
      modules ranked by cross-module incoming edge degree DESC with module-name-ASC
      tiebreak (D-005); plan capped at `pages_cap` (default 10); each page record
      carries `page_id` (filesystem-safe slug), `title`, `description`, `module`,
      `seeds` (file paths + top symbols), and `input_hash` (sha256 over canonical JSON
      of the plan entry); two builds over one unchanged graph are identical
      (determinism); an empty/unindexed graph raises a clean planner error (the CLI
      maps it to exit 1 in Phase 2, US1 AC3). Seed the graph mirroring
      `tests/test_tasks_safety.py` (`_seed_graph` at :27, `_create_bundle` at :44) with
      the `fresh_db` fixture (tests/conftest.py:106).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_planner.py -q` → red.
- [x] T002 Implement `src/cairn/wiki/catalog.py` — `build_page_plan(conn, repo, pages_cap=10)` (FR-001) (after T001)
      done 2026-08-31 — `pytest tests/test_wiki_planner.py tests/test_tasks_safety.py tests/test_compass_critic.py -q` -> 39 passed (orchestrator-verified)
      Consumes T001's pinned contract exactly (ordered page records with
      `page_id`/`title`/`description`/`module`/`seeds`/`input_hash`; overview first;
      empty-graph error). Module = first 2-3 path segments of `files.path` (same
      bucketing as `src/cairn/graph/stats.py:group_by_top_level:67`, incl. the legacy
      absolute-path strip) — do NOT reuse its symbol-count ordering; module incoming
      degree = planner-local SQL counting edges whose target symbol's file is under
      the prefix and whose source symbol's file is not (JOIN precedent
      `src/cairn/wiki/generator.py` lines 59-67, `COUNT(e.id) AS incoming ... ORDER BY
      incoming DESC`); `files` has no module column (`src/cairn/graph/schema.py`
      lines 24-33). Seeds = top symbols by that JOIN + module file paths.
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_planner.py tests/test_tasks_safety.py tests/test_compass_critic.py -q` → green (substrate baseline 24 passed).

## Phase 2: Queue & promotion core (FR-002, FR-003, FR-004, FR-010)
<!-- Checkpoint (plan.md): queue a page task, complete with resolvable refs →
Wiki-Article concept with `sources` frontmatter + page-id/input-hash lineage
exists under `wiki/`; completion with an unresolvable ref → revise task spawned,
no promoted concept. Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_tasks_safety.py tests/test_compass_critic.py tests/test_wiki_promotion.py -q`,
must include the FR-004 regression class and the FR-010 search/compass
assertions; suite tripwires stay green: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_suite_hygiene.py -q`. -->
<!-- plan.md serial spine: FR-002 (`_output_spec`, tasks.py:501) and FR-003
(`complete_task`, tasks.py:210) both edit `src/cairn/llm/tasks.py`, and all test
tasks share `tests/test_wiki_promotion.py` — Phase 2 is one serial workstream;
no task here is [P]. -->

- [x] T003 Write failing FR-002 kind/spec tests plus FR-004 regression guards in `tests/test_wiki_promotion.py` (FR-002, FR-004)
      done 2026-08-31 — verified split 4 red (spec-registration/Mermaid gating) / 2 green (FR-004 guards)
      Red-first (C-02). Pin `_output_spec` (`src/cairn/llm/tasks.py:501`; today only
      the placeholder `"wiki"` line at :527) gaining `wiki-page`, `wiki-page-revise`,
      `wiki-catalog`, `wiki-catalog-revise` entries — revise kinds are derived by
      appending `-revise` (tasks.py:373-376), so register all four defensively; the
      unknown-kind fallback is `unknown — verify` (survey leaves it unstated). Spec
      text must require a markdown article ending in a `## Sources` footer, include
      Mermaid-fence instructions only when `facts.diagrams` is set (facts pass through
      verbatim — only memory-* stripped, tasks.py:81-87; rendered by
      `src/cairn/llm/tasks.py:_render_body:484`), and forbid references outside the
      graph. FR-004 regression guards (mechanism is survey-DONE, tasks.py:368-419,
      verify 8 passed — no implementation task exists for FR-004): a failing
      `wiki-page` completion spawns a `wiki-page-revise` task carrying `errors` +
      `parent_task_id`, and the chain drops at `MAX_REVISE_CYCLES = 3` (tasks.py:28)
      with `dropped: True`. Drive claim/complete directly per `tests/test_tasks_safety.py`.
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_promotion.py -q` → new spec assertions red, FR-004 guards green.
- [x] T004 Register the wiki output specs and add the `generate --llm` queueing loop in `src/cairn/llm/tasks.py` and `src/cairn/cli/wiki.py` (FR-002) (after T002) (after T003)
      done 2026-08-31 — T003's four reds green + guards green (14 passed at wave time; full file 17/17 after T006)
      Consumes T002's `build_page_plan(conn, repo, pages_cap=10)` (ordered records
      with `page_id`/`title`/`description`/`module`/`seeds`/`input_hash`; empty-graph
      error) and T003's pinned spec keys. Add the four `_output_spec` dict entries
      (tasks.py:501 — additive; its 2 callers `_build_prompt`/`_render_body` are
      lookup-style). Extend `wiki_generate` (`src/cairn/cli/wiki.py:21`) with `--llm`,
      `--pages` (default 10), and `--diagrams`: plan via `build_page_plan`;
      empty/unindexed graph → `click.echo(..., err=True)` + `sys.exit(1)` queueing
      nothing (US1 AC3; convention cli/task.py:44,76,97,109); else one
      `create_task(bundle, "wiki-page", resource=page_id, facts={seeds..., input_hash,
      diagrams})` per planned page (`create_task` unchanged, tasks.py:64).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_promotion.py tests/test_tasks_safety.py -q` → green.
- [x] T005 Write failing FR-003 promotion tests in `tests/test_wiki_promotion.py` (FR-003) (after T003)
      done 2026-08-31 — 10 red FR-003 pins quoted (ModuleNotFoundError sources / TypeError section_vocab / promoted=False), 1 green-by-design guard
      Same file as T003 — append, do not rewrite. Pin: (1) a Sources-footer parser in
      new `src/cairn/wiki/sources.py` tolerating list and inline-link forms, entries
      resolved via the backtick machinery (`src/cairn/refs.py:BACKTICK_RE:19`,
      `extract_file_refs:38`, `extract_symbol_refs:50`, `file_exists:67`,
      `symbol_exists:83`); unresolved entries are errors. (2) `critic_concept`
      (`src/cairn/compass/critic.py:38`) gains an optional `section_vocab` keyword —
      default bit-identical for existing callers; wiki vocab `("## Sources",)` →
      footer present = quality 1.0 pass, absent = 0.0 fail (D-001); `CriticResult`
      (critic.py:28) unchanged. (3) `complete_task` (`src/cairn/llm/tasks.py:210`)
      third branch keyed on `task_kind.startswith("wiki-page")` writing
      `type="Wiki-Article"`, `concept_id = wiki/pages/{repo}/{page_id}` (module slug
      `/`→`-`; overview → `overview`), tags `[repo, "wiki"]`, `sources` frontmatter
      from the verified footer, extensions `page_id`/`input_hash`/`task_id` (D-007).
      Red reasons from survey: a passed non-compass/flow task today returns
      `promoted: False` (tasks.py:362) and no code sets `sources=` anywhere
      (concept.py:136 is the parse path only).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_promotion.py -q` → new assertions red.
- [x] T006 Implement the Sources parser, critic `section_vocab`, and the Wiki-Article promotion branch (FR-003) (after T004) (after T005)
      done 2026-08-31 — `pytest tests/test_wiki_promotion.py tests/test_tasks_safety.py tests/test_compass_critic.py -q` -> 41 passed; critic callers bit-identical; D-011 recorded
      Files: new `src/cairn/wiki/sources.py`; `src/cairn/llm/tasks.py:complete_task:210`
      (contended with T004 — same serial workstream, hence the chain); an
      optional-keyword-only edit to `src/cairn/compass/critic.py:critic_concept:38`.
      The branch sits inside the critic-passed region (after the flow branch,
      tasks.py:330-352); do not double-write the Task-Result sibling
      (tasks.py:293-297); a critic exception promotes nothing (tasks.py:420-429); the
      ownership guard is untouched (tasks.py:237-248); the outcome-dict shape for
      non-wiki kinds must not drift (`test_return_dict_shape_exact_match` in
      tests/test_tasks_safety.py is the trap). Do NOT auto-key the vocab on
      `concept.type` (blind at the Task-Result call site — rejected alternative) and
      do not touch the deterministic generator's informational critic run
      (`generate_wiki_with_critic:31`; spec scope freeze).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_promotion.py tests/test_tasks_safety.py tests/test_compass_critic.py -q` → green (critic baseline 16 passed).
- [x] T007 Add FR-010 wiring-verification tests in `tests/test_wiki_promotion.py` (FR-010) (after T006)
      done 2026-08-31 — `pytest tests/test_wiki_promotion.py tests/test_suite_hygiene.py -q` -> 23 passed
      No new search code (D-004): after driving one promotion through T006's branch,
      assert the Wiki-Article surfaces via `src/cairn/okf/bundle.py:search:182`
      (bundle-wide, no area filter — the `cairn wiki search` path) and via the compass
      wiki layer (`src/cairn/compass/router.py:108`, `_search_wiki:237`); assert the
      frontmatter is unchanged apart from populated `sources` (`to_markdown` emission
      rules, `src/cairn/okf/concept.py` lines 166-197). These are wiring tests — green
      once T006 lands; they pin that the D-007 identity convention actually lands
      inside the already-wired paths (the only substantive FR-010 piece, populating
      `sources`, is FR-003's work).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_promotion.py tests/test_suite_hygiene.py -q` → green.

## Phase 3: Manifest & incremental (FR-005)
<!-- Checkpoint (plan.md): first run queues N pages + writes manifest; immediate
re-run queues 0; touch a planned page's module inputs, re-run → exactly that page
re-queued; `--force` re-queues all. Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_manifest.py -q`
(new) plus the Phase-2 set. -->

- [x] T008 Write failing manifest tests in `tests/test_wiki_manifest.py` (FR-005)
      done 2026-08-31 — red pinned: collection ModuleNotFoundError on cairn.wiki.manifest (orchestrator-verified)
      Red-first (C-02). Pin (D-006): JSON manifest at `<knowledge>/_wiki/manifest.json`
      with schema marker `"cairn-wiki-manifest-1"`; per-page rows keyed by page_id
      carrying the plan entry, `input_hash`, `task_id`, `state`, and cumulative
      `attempts`; the file is non-`.md` so `src/cairn/okf/bundle.py:list_concepts:144`
      (rglob *.md) never lists it; writes copy the atomic pattern
      `src/cairn/paths.py:set_config_values:292` (mkstemp in target dir → flush +
      os.fsync → `os.replace` at :315 → unlink-on-error → False on OSError). Pin the
      skip rule: recorded hash == current plan hash AND promoted concept readable via
      `bundle.read_concept` → skip, unless `--force`; a changed module input re-queues
      exactly that page; lifecycle states planned→queued→in_progress→promoted and
      queued→failed (drop at the revise cap).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_manifest.py -q` → red.
- [x] T009 Implement `src/cairn/wiki/manifest.py`, incremental skip, and the public generate pipeline (FR-005) (after T008)
      done 2026-08-31 — 40 passed (manifest+promotion); fix 1/5 (D-016): 59 passed scoped, live-store re-run queued 0 new with byte-identical pending list
      Consumes T002's `build_page_plan` hashes and T004's queue loop. Implement the
      D-006 manifest (load/save per T008's pinned format) and expose the public
      pipeline function the MCP lane consumes:
      `run_wiki_generate(conn, bundle, repo, pages_cap=10, force=False, diagrams=False,
      refine_catalog=False) -> {"plan": [...], "queued_task_ids": [...]}` under
      `src/cairn/wiki/` (exact module placement is the implementer's choice within
      Lane-A files, e.g. co-located with the manifest code). Reads the manifest before
      queue decisions, writes atomically after; promoted state is detected by reading
      the concept, never via complete_task callbacks (D-006). Delegate
      `cairn wiki generate --llm` to it (same-file serial spine).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_manifest.py tests/test_wiki_promotion.py -q` → green.

## Phase 4: Status & retry CLI (FR-006)
<!-- Checkpoint (plan.md): `cairn wiki status` prints per-page states + aggregates;
`cairn wiki retry` re-queues only failed/dropped, attempt counters preserved,
promoted untouched. Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_cli.py -q`
(new, CliRunner pattern per tests/test_knowledge_cli.py fixture style) and
`grep -c 'wiki.command' src/cairn/cli/wiki.py` shows 4 registered subcommands
(baseline: 2). -->

- [x] T010 Write failing status/retry CLI tests in `tests/test_wiki_cli.py` (FR-006)
      done 2026-08-31 — red pinned: `No such command 'status'/'retry'` (orchestrator-verified)
      Red-first (C-02); new file, no shared files — starts Lane A. CliRunner pattern
      per `tests/test_knowledge_cli.py` `cli_env` fixture (:19-25: chdir tmp_path;
      CAIRN_DB + CAIRN_KNOWLEDGE into tmp); import only the specific module
      `cairn.cli.wiki`, never the `cairn.cli` package root (C-04 — the package
      `__init__` imports every CLI module). Consume T009's manifest format by writing
      fixtures directly (JSON at `<knowledge>/_wiki/manifest.json`, marker
      `"cairn-wiki-manifest-1"`, rows with `input_hash`/`task_id`/`state`/`attempts`).
      Pin: `wiki status` lists each planned page exactly once with a state from
      queued/in-progress/promoted/failed plus aggregate counts; `wiki retry` re-queues
      exactly failed/dropped pages as fresh chains (`create_task` with
      `parent_attempt=0`, D-008) while bumping the manifest's cumulative `attempts`,
      never touching promoted pages; retry with nothing to retry → friendly message,
      exit 0. Baseline: `grep -n 'wiki.command' src/cairn/cli/wiki.py` → 2.
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_cli.py -q` → red.
- [x] T011 Implement `wiki status` and `wiki retry` on the `wiki` group in `src/cairn/cli/wiki.py` (FR-006) (after T010)
      done 2026-08-31 — 3 passed; fix 1/5: 41 passed scoped, live-store chain-drop -> status failed + retry re-queue verified
      Conventions: `from .main import DEFAULT_DB_PATH, get_db, main` (cli/wiki.py:6);
      `--knowledge` default `str(DEFAULT_DB_PATH.parent / ".knowledge")`
      (cli/wiki.py:16,73); `sys.exit(1)` + `click.echo(..., err=True)` on errors.
      `status` joins T009's manifest rows with live task state via `list_tasks`
      (`src/cairn/llm/tasks.py:101`, status=/kind= filters) and `get_task`
      (tasks.py:451); promoted is detected by reading the concept at
      `wiki/pages/{repo}/{page_id}` (derived state, D-006 — `complete_task` stays
      generic). `retry` per D-008: fresh task chains, cumulative attempts preserved
      for audit, promoted untouched; treat "failed + exhausted chain" as dropped
      (tasks.py:404-419, `dropped: True`).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_cli.py -q` → green; grep shows 4 subcommands.

## Phase 5: Refine-catalog path (FR-007)
<!-- Checkpoint (plan.md): `--refine-catalog` queues a `wiki-catalog` task; a
refined outline naming a nonexistent module reverts that entry to deterministic;
page tasks spawn only from the validated plan. Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_refine.py -q`
(new) and `grep -rn 'wiki-catalog' src/ tests/` now matches (baseline: zero
matches). -->

- [x] T012 Write failing refine-catalog tests in `tests/test_wiki_refine.py` (FR-007)
      done 2026-08-31 — verified split 11 red (NoSuchOption --refine-catalog / ModuleNotFoundError refine) / 1 green queue-guard
      Red-first (C-02); new file, no shared files. Pin (D-003 two-step):
      `generate --llm --refine-catalog` queues exactly one `wiki-catalog` task and
      zero page tasks, echoing claim/complete instructions (precedent
      `src/cairn/cli/compass.py` lines 96-105); a re-run after the catalog task
      completes (its result is a Task-Result sibling read via
      `src/cairn/llm/tasks.py:read_result:442`, not a promoted concept) spawns page
      tasks from the validated refined outline; an entry naming a nonexistent module
      is rejected and the deterministic entry kept in its slot; a failed/dropped
      catalog falls back to the deterministic plan. Baseline:
      `grep -rn 'wiki-catalog' src/ tests/` → zero matches.
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_refine.py -q` → red.
- [x] T013 Implement `--refine-catalog` on `wiki_generate` with the catalog validator (FR-007) (after T011) (after T012)
      done 2026-08-31 — `pytest tests/test_wiki_refine.py tests/test_wiki_cli.py -q` -> 15 passed
      Files contended with T011/T009 (Lane-A serial per plan.md M4→M5, hence the
      chain): `src/cairn/cli/wiki.py` and `src/cairn/wiki/`. Add the flag to
      `wiki_generate` (cli/wiki.py:21); the `wiki-catalog`/`wiki-catalog-revise`
      output-spec entries already landed in T004; with the flag, generate queues the
      catalog task via `create_task` and returns with instructions (mirroring
      compass.py:96-105) through T009's `run_wiki_generate(refine_catalog=True)`; the
      re-run validates entries — each module must match a `files.path` prefix (LIKE
      precedent `src/cairn/viz/query.py:get_module_graph:190`) and seed files must
      resolve via `src/cairn/refs.py:file_exists:67`; invalid entries revert to the
      deterministic plan entry. No task-spawns-tasks completion hook
      (`complete_task` untouched — D-003).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_refine.py tests/test_wiki_cli.py -q` → green.

## Phase 6: MCP tool (FR-008)
<!-- Checkpoint (plan.md): `wiki_generate` MCP tool returns plan + task ids;
tool-count assertion updated. Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_status_resource_health.py tests/test_server_robustness.py tests/test_agent_surface.py -q`
(the three pinned tests from survey FR-008) and
`grep -n '_EXPECTED_TOOL_COUNT' src/cairn/mcp_server/server.py` shows 28. -->

- [x] T014 Add the `wiki_generate` MCP tool with the coordinated 27→28 count bumps, red-first in-task (FR-008)
      done 2026-08-31 — 5-file verify 41 passed; 28 decorated tools; fix 1/5 (stale refine pin) green; D-013 recorded
      Lane B — file-disjoint from Lanes A/C; starts after Phase 3 (consumes only the
      generate pipeline's public function). Red-first inside this task: a test
      asserting the tool is registered and returns plan + queued task ids, stubbing
      the module-level helpers (`src/cairn/mcp_server/_server_core.py`: `mcp` at :78,
      `_conn` at :159, `_rw_conn` at :212, `_bundle` at :222) via monkeypatch rather
      than booting the server or calling `run()`; import only the specific submodules
      the existing pinned tests use (`cairn.mcp_server.server`,
      `cairn.mcp_server._server_core`), never the package root (C-04). Implement new
      `src/cairn/mcp_server/tools_wiki.py` per the tools_knowledge pattern
      (tools_knowledge.py:31-58): `@mcp.tool(annotations=ToolAnnotations(...))` over
      `@instrument`, primitive args only (`repo`, `pages`, `refine_catalog`,
      `diagrams`, `force`), `_clamp` for ints, lazy body imports calling T009's
      `run_wiki_generate(conn, bundle, repo, pages_cap, force, diagrams,
      refine_catalog)`; prose return of the page plan + queued task ids (D-009).
      Register the import at server.py:49-52 and move the count ATOMICALLY in this
      one change: `_EXPECTED_TOOL_COUNT = 27` → 28 at
      `src/cairn/mcp_server/server.py:55`, tests/test_status_resource_health.py:281,
      tests/test_server_robustness.py:192, tests/test_agent_surface.py:11, and the
      `docs/mcp-tools.md:21` heading "The 27 tools by layer" — a partial bump fails
      `verify_tool_count` (server.py:162, called from `run()` at :201) at startup,
      not at import. Count decorated functions, not raw `@mcp.tool` grep hits (the
      28th hit is a docstring line, tools_graph.py:6).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_status_resource_health.py tests/test_server_robustness.py tests/test_agent_surface.py -q` → green; grep shows 28.

## Phase 7: Dashboard wiki view (FR-009)
<!-- Checkpoint (plan.md): dashboard wiki route lists pages with states; detail
view renders markdown + sources; import-guard still holds. Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_app.py tests/test_dashboard_data.py -q`
— must include `test_importing_dashboard_never_loads_server_stack`. -->

- [x] T015 Write failing dashboard wiki-view tests in `tests/test_dashboard_app.py` and `tests/test_dashboard_data.py` (FR-009)
      done 2026-08-31 — verified split 14 red (missing data fns/routes/renderer) / 176 passed, import guard green
      Lane C — file-disjoint from Lanes A/B. Red-first (C-02). Pin: a data function
      in the `get_task_queue`/`get_recent_memories` shape
      (`src/cairn/dashboard/data.py:613` / `:587` — take knowledge_dir, wrap
      OKFBundle, return plain dicts, skip unreadable concepts) joining T009's
      manifest with `wiki/pages/` concepts; GET `/wiki` returns HTML with one entry
      per page + a state badge; GET `/wiki/{page_id}` returns rendered heading/list
      elements plus the sources list (not raw markdown); the new renderer module
      imports no starlette/uvicorn/jinja2; and
      `test_importing_dashboard_never_loads_server_stack`
      (tests/test_dashboard_app.py:41) stays green throughout.
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_app.py tests/test_dashboard_data.py -q` → new assertions red, import guard green.
- [x] T016 Implement the dashboard wiki routes, data function, template, and stdlib markdown renderer (FR-009) (after T015)
      done 2026-08-31 — `pytest tests/test_dashboard_app.py tests/test_dashboard_data.py -q` -> 190 passed
      Lane C files: `src/cairn/dashboard/app.py` (routes in the table at
      app.py:925-965 inside `create_app:191`; handler via the `render` helper,
      app.py:301-308; store selection `resolve_selection:310`);
      `src/cairn/dashboard/data.py` (new data function per T015's pinned shape);
      new template(s) beside `src/cairn/dashboard/templates/tasks.html`
      (badge/table style, tasks.html:18-45); and the D-002 renderer — a new pure
      module (e.g. `src/cairn/dashboard/markdown.py`), ~80 lines of stdlib (`html` +
      `re`), escaping everything first, whitelisting headings/paragraphs/lists/fenced
      code, mermaid fences as static `<pre class="language-mermaid">` (C-03: no new
      dependency). Heavy imports stay inside `create_app`/data functions (import
      guard); DB access, if any, via `get_read_only_db` (data.py:1069, mode=ro).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_app.py tests/test_dashboard_data.py -q` → green.

## Phase 8: Docs & changelog (FR-011)
<!-- Checkpoint (plan.md, ship gate): full suite green; docs mention all new
commands/tools; CHANGELOG `[Unreleased]` has the entry. Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest -q`
(whole suite) and `grep -n 'wiki' docs/cli-reference.md docs/mcp-tools.md docs/knowledge-and-memory.md | head`.
Then the repo's mandatory C-01 procedure (branch → pre-commit → PR → CI) and
post-merge `cairn update` + `record_memory`. -->

- [x] T017 Update the docs and CHANGELOG for the wiki generation feature (FR-011) (after T013) (after T014) (after T016)
      done 2026-08-31 — full suite 2713 passed at task close; TC-030 greps green; --force gap found -> D-014/T018
      Strictly after the lanes: `docs/mcp-tools.md` is contended with T014 (count
      heading) and docs must record the final surface (T013 CLI, T014 tool, T016
      dashboard). Touch: `docs/cli-reference.md` section
      `## Compass / wiki / tasks / dataflow` (:73) — generate with
      `--llm/--pages/--refine-catalog/--diagrams/--force`, `status`, `retry`;
      `docs/mcp-tools.md` — the `wiki_generate` entry plus the
      queue → claim/complete → critic → promotion workflow prose (count heading
      already moved to 28 by T014); `docs/knowledge-and-memory.md` section (:82) —
      the manifest/incremental/retry workflow; `CHANGELOG.md` `## [Unreleased]`
      (:14) `### Added` (:16) prose bullet (Keep-a-Changelog). Do not propagate the
      stale "49 commands" figure from the `cli/__init__.py` docstring (recount: 47
      decorator lines — survey).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest -q` (whole suite) → green; the grep above shows the new wiki sections.

- [x] T018 Add `--force` to `wiki generate --llm` — CLI parity with FR-005/TC-007 (D-014) (FR-005) (after T017)
      done 2026-08-31 — `pytest tests/test_wiki_cli.py tests/test_wiki_refine.py -q` -> 16 passed; TC-007 E2E chain proven on live store
      Appended mid-flight (D-014): T004's flag surface omitted `--force`; T009 wired
      only the pipeline parameter. Red-first in `tests/test_wiki_cli.py` (append):
      invoking `wiki generate --llm --force` fails today with click NoSuchOption;
      green when the flag exists, passes `force=True` through
      `run_wiki_generate(..., force=...)`, and re-queues an unchanged promoted page
      (skip logic bypassed). Also add `--force` to the generate row T017 wrote in
      `docs/cli-reference.md`.
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_cli.py tests/test_wiki_refine.py -q` → green.

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      done `<date>` — the test/command that proves it
- Dropped: `` `- [ ] ~~T018~~ dropped <date> (D-###)` `` — never delete the line;
      dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
      chained tasks note `(after T###)` and name the exact interface they
      consume from their upstream — symbols, signatures, file formats; serial
      runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
      only if the count lives here, in the status holder
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
      spec first
- Canonical verify prefix for every command above:
      `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <path> -q`
      (plain `uv run pytest` fails — pytest rides the `[test]` extra)

## Phase 9: Veto re-opens (D-017)

- [x] T020 Update the pre-rendered diagram assets to 28 tools (D-015 re-open) (FR-011)
      done 2026-08-31 — same-width `27 tools` -> `28 tools` text swap in the six
      self-contained sources (docs/diagrams/system-architecture.svg, docs/diagrams/system-architecture.html,
      docs/diagrams/system-architecture-dark.html, docs/diagrams/readme-architecture.svg,
      docs/diagrams/readme-architecture.html, docs/diagrams/readme-architecture-dark.html);
      grep over docs/ README.md src/
      tests/ shows zero remaining real-source mentions (egg-info is build debris).

- [x] T019 Re-key the wiki manifest to {repo}/{page_id} (D-012 re-open, schema cairn-wiki-manifest-2) (FR-005)
      done 2026-08-31 — 261 passed across 6 files; live store: 5/5 schema-1 rows migrated, on-disk schema-2, generate queued 0 new (D-016 intact); pin set extended per D-018
      One writer owns the format change end-to-end: `src/cairn/wiki/manifest.py`
      (marker bump; rows keyed f"{repo}/{page_id}"; schema-1 loads migrate
      opportunistically — repo from the row task's facts via get_task, fallback the
      promoted concept path wiki/pages/{repo}/{page_id}, unmigratable rows dropped
      with a warning — self-healing on next generate), `src/cairn/wiki/pipeline.py`
      (row reads/writes), `src/cairn/cli/wiki.py` (status/retry derive repo from the
      key; the ambiguous-repo refusal can simplify away), `src/cairn/dashboard/data.py`
      (manifest joins), and the pins: tests/test_wiki_manifest.py, tests/test_wiki_cli.py
      (fixture keys), tests/test_dashboard_data.py (fixture keys).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_manifest.py tests/test_wiki_promotion.py tests/test_wiki_cli.py tests/test_wiki_refine.py tests/test_dashboard_data.py tests/test_dashboard_app.py -q` -> green; live store: `cairn wiki status` still shows overview promoted (schema-1 manifest migrated), `cairn wiki generate --llm --pages 4` queues 0 new.
