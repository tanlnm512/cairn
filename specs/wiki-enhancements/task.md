# Tasks: wiki-enhancements

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: passed @ e002f9b (2026-08-31: check.py 0 fail after the
  spec/test guard fixes; full suite 2727 passed / 3 skipped on the clean tree;
  branch cut at the audited sha; constitution C-01 single end-of-plan PR shape
  pending gate confirmation, C-02 red-first pairs in every phase, C-03 zero new
  deps per tech-spec, C-04 rules embedded in task texts) — the orchestrator writes `passed @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 2     | 0    |
| 3     | 2     | 0    |
| 4     | 2     | 0    |
| 5     | 4     | 0    |
| 6     | 4     | 0    |
| 7     | 3     | 0    |
| **Σ** | 21    | 0    |

## Phase 1: Generation quality (FR-001, FR-005)
<!-- Checkpoint (plan.md): equal-degree plan puts the code module first and the test-majority module only with capacity left; one dead path → one critic error; a `wiki-page-enrich`-named task body carries the full Sources-footer spec. Verify: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_planner.py tests/test_compass_critic.py tests/test_wiki_promotion.py -q -->
- [ ] T001 [P] Write failing planner-demotion pins — `tests/test_wiki_planner.py` (FR-001)
      Red-first (C-02). In `TestPlanOrdering` add the class-tier case: a test-majority
      module with HIGHER cross-module incoming degree still plans after a code module
      (TC-002/TC-003 shape), and re-anchor
      `test_equal_degree_modules_tiebroken_by_module_name_asc` deliberately — the class
      tier now sits ABOVE name-ASC (D-014). `test_overview_page_is_planned_first` stays
      untouched (the overview is a synthetic entry outside `module_files`, exempt).
      Survey gap (FR-001 PARTIAL): no module is classified test-majority and no class
      split exists in the sort key at catalog.py:171.
      Verify red: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_planner.py -q` (survey baseline 15 passed — the new pins must fail)
- [ ] T002 Write the test-majority sort key — `src/cairn/wiki/catalog.py` (FR-001) (after T001)
      Consumes T001's failing pins in `TestPlanOrdering`. In `build_page_plan` add
      `_is_test_majority(paths) -> bool` — a path is a test file when any `/`-segment
      equals `test`, `tests`, `spec`, or `specs`; majority = strictly more test files
      than non-test — fed by `module_files` already built at catalog.py:162-166; change
      the rank at catalog.py:171 from `sorted(module_files, key=lambda m: (-degrees[m], m))`
      to key `(is_test_majority[m], -degrees[m], m)`. Demote-only, never exclusion;
      degree dominates within a class; name-ASC tiebreak preserved (D-014). Segment-check
      the same stored path strings `_module_of:29` already handles — don't re-derive
      normalization.
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_planner.py -q`
- [ ] T003 [P] Write failing critic-dedupe and spec-prefix pins — `tests/test_compass_critic.py`, `tests/test_wiki_promotion.py` (FR-005)
      Red-first (C-02). Two pins: (1) one completion citing the same dead path in body
      backticks AND the Sources footer yields exactly ONE error for it (TC-014), while
      rejection itself is not weakened (TC-029 guard) — update the double-error
      assertions in `TestResolveSources` (`test_unresolved_entry_is_reported_as_error:293`)
      and `TestCriticConceptIntegration:133` only where the single-error semantics
      legitimately change (plan assumption 4); (2) in `TestWikiOutputSpecRegistration:78`,
      a `wiki-page-enrich`-named task's rendered body carries the full wiki output spec
      (Sources-footer requirement), not the default `Process per the cairn skill.`
      string (TC-015). Survey gap (FR-005 PARTIAL): no dedupe anywhere on the error
      path and the spec lookup at tasks.py:630 is exact-match only.
      Verify red: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_compass_critic.py tests/test_wiki_promotion.py -q` (survey baseline 36 passed)
- [ ] T004 Implement error dedupe + startswith spec fallback — `src/cairn/refs.py`, `src/cairn/compass/critic.py`, `src/cairn/llm/tasks.py`, `src/cairn/wiki/sources.py` (FR-005) (after T003)
      Consumes T003's failing pins. (1) `extract_file_refs` (refs.py:38-49) becomes
      order-preserving dedupe — BACKTICK_RE (:19) matches deduped by the resolved path
      string, first occurrence kept, iteration order unchanged. (2) Extract one shared
      unresolved-refs helper from the file-ref loop in `critic_concept`
      (critic.py:70-73) and use it in BOTH the critic loop and the footer merge at
      tasks.py:312 (`errors=critic_result.errors + source_errors` filters footer errors
      whose entry path the body already reported unresolved), so `resolve_sources`
      (sources.py:71) keys off the same existence check. (3) `_output_spec` (tasks.py:630):
      after the exact dict get, insert `task_kind.startswith("wiki-page")` → serve the
      `wiki-page` spec, else the default string (D-018); the four registered kinds
      (:603/:609/:615/:621) and the legacy bare `wiki` key at :602 keep byte-identical
      specs; the Mermaid appendix gate at tasks.py:631-632 stays.
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_compass_critic.py tests/test_wiki_promotion.py -q`

## Phase 2: Commit-sha provenance (FR-003)
<!-- Checkpoint (plan.md): a promoted page's extensions and manifest row carry the HEAD sha; a completion with no resolvable sha still promotes without one. Verify: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_promotion.py tests/test_wiki_manifest.py -q and grep -rn commit_sha src/ --include="*.py" now matches -->
- [ ] T005 Write failing sha-provenance pins — `tests/test_wiki_promotion.py`, `tests/test_wiki_manifest.py` (FR-003) (after T004)
      Red-first (C-02); appends to the file T003/T004 just re-pinned (P1 → P2 same-file
      order on the `tasks.py` spine). Pins: the wiki promotion branch writes extensions
      with a FIFTH key `commit_sha` alongside page_id/input_hash/task_id/refine_catalog
      (tasks.py:419-424 shape), copied from `facts["commit_sha"]` exactly like
      `input_hash` flows at tasks.py:421 (TC-007); a completion with NO resolvable sha
      still promotes without one (TC-008); the manifest row written at pipeline.py:89-94
      gains `commit_sha`. Survey gap (FR-003 PARTIAL): no sha in facts, no sha in
      manifest rows, no sha in promotion extensions — grep `commit_sha` over src/ is
      zero matches.
      Verify red: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_promotion.py tests/test_wiki_manifest.py -q` (survey baselines 20/33 passed)
- [ ] T006 Resolve HEAD in the pipeline; thread the sha through facts, row, extensions — `src/cairn/utils/git.py`, `src/cairn/wiki/pipeline.py`, `src/cairn/llm/tasks.py` (FR-003) (after T005)
      Consumes T005's pins; the shared interface is the name `commit_sha` used as the
      fact key == extensions key == manifest row field. Add `get_repo_head(repo_name,
      workspace=None)` beside `get_current_commit` (utils/git.py:29 — zero callers
      today, first caller is this feature) with LAZY imports inside the function (no
      utils→graph import cycle: incremental.py:16 imports FROM utils.git) composing
      `resolve_store().workspace` → `scanner.resolve_repo_path` (repos.path is
      WORKSPACE-RELATIVE per schema.py:15-22 — never `Path(row["path"])` directly) →
      `get_current_commit`; None on failure (10s timeout). In `run_wiki_generate` resolve
      once per repo and thread into `_queue_pages` facts + the manifest row
      (pipeline.py:77-94); the promotion branch in `complete_task` copies
      `facts.get("commit_sha")` as the fifth extensions key (D-016); `complete_task`
      stays generic; CLI/MCP callers (cli/wiki.py:68, tools_wiki.py:39) untouched.
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_promotion.py tests/test_wiki_manifest.py -q` and `grep -rn commit_sha src/ --include="*.py"` now matches

## Phase 3: Fresh/stale display (FR-007)
<!-- Checkpoint (plan.md): cairn wiki status shows fresh/stale/unknown; dashboard list + detail show the badge; unchanged sha → fresh, moved HEAD → stale, missing side → unknown. Verify: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_cli.py tests/test_dashboard_app.py tests/test_dashboard_data.py -q -->
- [ ] T007 Write failing staleness pins — `tests/test_wiki_cli.py`, `tests/test_dashboard_data.py`, `tests/test_dashboard_app.py` (FR-007) (after T006)
      Red-first (C-02). Consumes the recorded-sha interface T006 landed: concept
      `extensions["commit_sha"]` is the source of truth (manifest.py:14-16 — promoted
      is never trusted from a stored row), manifest row `commit_sha` is the fallback for
      not-yet-promoted pages. Pins: `cairn wiki status` gains a staleness column —
      fresh = recorded sha == current HEAD, stale = both present and differ, unknown =
      either unavailable (TC-019/TC-020); the `get_wiki_pages` dict (today EXACTLY
      page_id/title/state/promoted at data.py:675-681) and `get_wiki_page` (:712-718)
      gain a `staleness` field; badges appear beside the state badge in templates
      wiki.html:19 and wiki_page.html:7. Keep
      `test_importing_dashboard_never_loads_server_stack` green (C-04: lazy imports, no
      eager server stack in tests).
      Verify red: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_cli.py tests/test_dashboard_data.py tests/test_dashboard_app.py -q` (survey baselines 6/82/109 passed)
- [ ] T008 Implement fresh/stale on both surfaces — `src/cairn/cli/wiki.py`, `src/cairn/dashboard/data.py`, `src/cairn/dashboard/templates/wiki.html`, `src/cairn/dashboard/templates/wiki_page.html` (FR-007) (after T007)
      Consumes T007's pins and T006's `get_repo_head(repo_name, workspace=None)`. In
      `wiki_status` (cli/wiki.py:236) add the staleness column next to the `counts`
      aggregate (:247-262); in data.py add `staleness` to `get_wiki_pages`/`get_wiki_page`
      reaching HEAD WITHOUT threading new params (`resolve_store` is already imported at
      data.py:40 and returns `StorePaths(workspace=…)`); badge next to
      `<span class="badge badge-{{ p.state }}">` in both templates plus staleness text
      in the detail view. HEAD resolved ONCE per repo per render (10s subprocess timeout
      per call — never per page; D-020). Unknown is the explicit non-git/missing-sha
      answer — display degrades gracefully.
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_cli.py tests/test_dashboard_data.py tests/test_dashboard_app.py -q`

## Phase 4: Queue operations (FR-004)
<!-- Checkpoint (plan.md): dropped pending/in-progress task is listed dropped and refused by claim; done refused; --kind-prefix wiki-page lists every chain hop. Verify: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_tasks_safety.py tests/test_task_drop.py -q and grep -rn "kind-prefix" src/cairn/cli/task.py matches -->
- [ ] T009 Write failing drop/kind-prefix pins — `tests/test_task_drop.py` (new file) (FR-004) (after T006)
      Red-first (C-02); new suite per plan assumption 4. C-04: no eager `cairn.cli`
      import in the test module — follow the `cli_env` fixture + `CliRunner` pattern of
      tests/test_wiki_cli.py, and call `llm.tasks` functions directly for queue-level
      pins. Pins against the PLANNED interfaces (named here because the task entry is
      the contract): `drop_task(conn, task_id)` beside `claim_task:124`/`complete_task:210`
      drops a pending task (TC-009) and an in-progress task releasing its claim marker
      (TC-010), refuses done / not-found / already-dropped (TC-011/TC-012); a dropped
      task is refused by `claim_task` with NO guard edits (tasks.py:157-164 already
      refuses non-pending) and never claimable again;
      `list_tasks(conn, kind_prefix=…)` — an OPTIONAL kwarg, default None, zero call
      sites change — lists every `wiki-page` chain hop disjoint from `wiki-catalog`
      (TC-013); the `--status` help (cli/task.py:16) enumerates `dropped`.
      Verify red: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_tasks_safety.py tests/test_task_drop.py -q` (survey baseline 8 passed)
- [ ] T010 Implement drop + kind-prefix + the dropped display state — `src/cairn/llm/tasks.py`, `src/cairn/cli/task.py`, `src/cairn/cli/wiki.py` (FR-004) (after T008) (after T009)
      Consumes T009's pins (`drop_task(conn, task_id)`, `list_tasks(kind_prefix=…)`).
      New terminal task status `dropped` on the task concept (status enum comment
      tasks.py:47; "dropped" today is outcome-dict/telemetry only). `drop_task`:
      status-mutating for pending/in-progress only; done/not-found/already-dropped
      refused (exit 1); claim-marker removal mirroring complete_task's at
      tasks.py:279-283; TASK_LIFECYCLE event (emit precedent tasks.py:174/430/466/482).
      `cairn task drop` + `--kind-prefix` in cli/task.py (exactly four subcommands
      today, task.py:15-81); `--status` help gains `dropped`. In cli/wiki.py derive a
      `dropped` display state in `_page_state:218`/`_wiki_chains:193` that `wiki retry`
      (failed-only selection, cli/wiki.py:265-314) never selects — explicitly dropped
      pages are not resurrectable. Claim/complete guards untouched (D-017). Chained
      after T008 because FR-004's `_page_state`/`_wiki_chains` edit shares cli/wiki.py
      with FR-007's status column (tech-spec § 4 touch list) — all cli/wiki.py edits
      stay serial; P4 ∥ P3 otherwise (T009 vs T007 are file-disjoint).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_tasks_safety.py tests/test_task_drop.py tests/test_wiki_cli.py -q` and `grep -rn "kind-prefix" src/cairn/cli/task.py` matches (survey baseline: zero)

## Phase 5: Human surface (FR-002, FR-006)
<!-- Checkpoint (plan.md): code spans render as code elements and a GFM table as a table with the no-inline-HTML pins still green; export writes one frontmatter file per promoted page, prints the count, refuses a non-empty dir without --force. Verify: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_app.py tests/test_wiki_export.py -q and grep -n "def export" src/cairn/cli/wiki.py matches -->
- [ ] T011 Write failing renderer pins — `tests/test_dashboard_app.py` (FR-002) (after T008)
      Red-first (C-02); appended beside the two render pins
      (`test_render_markdown_whitelists_blocks_and_escapes_inline_html:3758`,
      `test_render_markdown_fenced_code_and_mermaid_fence:3778`) once P3 frees this
      shared file. Pins: an inline code span renders as a `<code>` element in
      paragraph, list-item and heading output; a GFM pipe table (header + delimiter
      row, colon alignment, optional leading/trailing pipes, `\|` escaped pipe,
      column-count mismatch → paragraph fallback) renders as a `<table>` with rows and
      cells (TC-004/TC-005); the escape-first pins stay byte-green — `&lt;script&gt;`
      and `&amp;` survive, fences keep precedence, the mermaid
      `<pre class="mermaid">` path is untouched (TC-006). Survey gap (FR-002 PARTIAL):
      no inline-span pass and no table block exist in the 91-line renderer.
      Verify red: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest "tests/test_dashboard_app.py::test_render_markdown_whitelists_blocks_and_escapes_inline_html" "tests/test_dashboard_app.py::test_render_markdown_fenced_code_and_mermaid_fence" -q` (survey baseline 2 passed)
- [ ] T012 Implement the inline-code pass + GFM table block — `src/cairn/dashboard/markdown.py` (FR-002) (after T011)
      Consumes T011's pins. Shared `_inline_code(escaped_text)` applied POST-escape
      (each line is already `html.escape(raw, quote=False)` at markdown.py:52; backticks
      survive that escape) in paragraph (`flush_paragraph:39`), list-item (`flush_list:44`)
      and heading emission, and table cells — zero re-escaping inside `<code>`. Table
      detector: header+delimiter-row pair (`^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$`)
      outside fences — fences keep absolute precedence (`_emit_fence:23`, mermaid path
      at :26 untouched); cells split on unescaped `|` (negative-lookbehind), `\|`
      unescaped after escaping, alignment from delimiter colons, column mismatch →
      paragraph; flush any open paragraph/list before a table block (D-015). Pure
      stdlib only — the import guard at tests/test_dashboard_app.py:41 forbids
      non-stdlib imports (C-03: no new runtime dependency).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_app.py -q`
- [ ] T013 Write failing export pins — `tests/test_wiki_export.py` (new file) (FR-006) (after T010)
      Red-first (C-02); new suite per plan assumption 4. C-04: lazy imports, no eager
      `cairn.cli` import — reuse the `cli_env` + `CliRunner` pattern. Pins against the
      PLANNED command `cairn wiki export --dir DIR [--force]`: one
      `DIR/{repo}/{page_id}.md` per PROMOTED page (page ids collide across repos —
      every repo plans an overview page), iterated from manifest rows keyed
      `{repo}/{page_id}` — not rglob; frontmatter preserved (sources emitted only
      `if self.sources`); success prints `Exported N page(s) to DIR`; a non-empty
      target dir is refused (exit 1) without `--force` (TC-016/TC-017); zero promoted
      pages is a valid count-0 success (TC-018).
      Verify red: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_export.py -q`
- [ ] T014 Implement `cairn wiki export` — `src/cairn/cli/wiki.py` (FR-006) (after T013)
      Consumes T013's pins; no edits outside cli/wiki.py (the plan's export lane). New
      subcommand in the wiki group (registers generate/search/status/retry today; grep
      `export` over the wiki surface: zero matches). Iterate manifest rows via
      `_load_manifest_or_exit:167` + `_split_page_key:179`, promoted derivation via
      `_is_promoted:185`; each file is a `from_file:92` → `to_file:145` round-trip of
      the stored concept (atomic tmp + os.replace — write through it, don't hand-roll;
      validates the concept parses; extensions incl. `commit_sha` ride into the export).
      `--knowledge` default and `click.echo(..., err=True); sys.exit(1)` error exits
      copied from the file's own idioms (cli/wiki.py:22, :174-176, :308-310). Chained
      after T010: FR-006 follows FR-004 on the cli/wiki.py serial spine.
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_cli.py tests/test_wiki_export.py -q` and `grep -n "def export" src/cairn/cli/wiki.py` matches

## Phase 6: Enrichment + language (FR-008, FR-009)
<!-- Checkpoint (plan.md): cairn wiki enrich <page-id> queues the enrich kind; a critic-passing completion replaces the body with the prior body readable from the task result; --lang zh renders a task body instructing Chinese (default en). Verify: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_enrich.py tests/test_wiki_promotion.py tests/test_wiki_cli.py -q -->
- [ ] T015 Write failing enrich pins — `tests/test_wiki_enrich.py` (new file) (FR-008) (after T014)
      Red-first (C-02); new suite per plan assumption 4. Pins:
      `cairn wiki enrich [<page-id>] [--repo R] [--all]` — exactly one selector required, else exit 1 —
      queues kind `wiki-page-enrich` with resource=page_id ONLY when the promoted
      concept is readable; unpromoted/unknown page-id → exit 1, nothing queued
      (TC-022); facts carry `current_body` (the page's body), fresh seeds/input_hash/
      repo from the manifest row, a fresh `commit_sha`, optional `lang` (TC-021); the
      row's task_id/state update WITHOUT overwriting its `commit_sha`; a critic-passing
      completion replaces the promoted body via the unmodified
      startswith(`wiki-page`) branch and the prior body stays readable from the
      Task-Result sibling's `extensions.facts` (D-021); critic failure spawns
      `wiki-page-enrich-revise` and max-cycle drop is inherited (TC-023); `--all` and
      `--repo` scoping (TC-024); an in-flight enrich keeps duplicate generate blocked
      via `_live_task_pages` (pipeline.py:39-48).
      Verify red: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_enrich.py -q`
- [ ] T016 Implement the enrich queue path + CLI — `src/cairn/wiki/pipeline.py`, `src/cairn/cli/wiki.py` (FR-008) (after T015)
      Consumes T015's pins. New queue function beside `_queue_pages:52` (NEVER through
      its hash-match skip at :70-75 — enrich is an explicit override): requires the
      promoted concept at `wiki/pages/{repo}/{page_id}`, captures
      `facts["current_body"]` from `concept.body`, copies seeds/input_hash/repo from
      the manifest row (row shape pipeline.py:77-94; retry's facts-assembly precedent
      cli/wiki.py:292-301), resolves `commit_sha` fresh at enrich time, updates the
      row's task_id/state. Promotion needs ZERO keying changes — startswith sites at
      tasks.py:291 (section_vocab `("## Sources",)` at :294), tasks.py:385, and
      pipeline.py:47 already route the kind; revise mapping (tasks.py:448-451) yields
      `wiki-page-enrich-revise`, served the full spec by FR-005's prefix rule.
      `cairn wiki enrich` subcommand in cli/wiki.py. Verify revise-spawn fact
      propagation (tasks.py:445-463) so `current_body`/`lang` reach the revise hop —
      if facts don't propagate today, that is a deliberate small extension of the
      spawn, not a new mechanism (unknown — verify at implementation). No MCP tool:
      `_EXPECTED_TOOL_COUNT = 28` (server.py:56) stays.
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_enrich.py tests/test_wiki_promotion.py tests/test_wiki_cli.py -q`
- [ ] T017 Write failing --lang pins — `tests/test_wiki_enrich.py`, `tests/test_wiki_promotion.py` (FR-009) (after T016)
      Red-first (C-02); extends T015's suite and the `TestMermaidGating:101` precedent
      file. Pins: `--lang zh` on generate and on enrich sets `facts["lang"]` to `zh`
      and the rendered task body instructs writing in Chinese; `--lang en` explicitly
      instructs English; the flag omitted → no `lang` key → today's bodies
      byte-identical (TC-025); an invalid value (`--lang fr`) is refused by
      `click.Choice(["en","zh"])` before anything queues (TC-026). Survey gap (FR-009
      TODO): grep `--lang` over src/ + tests/ is zero matches today.
      Verify red: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_enrich.py "tests/test_wiki_promotion.py::TestMermaidGating" -q`
- [ ] T018 Implement --lang en|zh — `src/cairn/cli/wiki.py`, `src/cairn/wiki/pipeline.py`, `src/cairn/llm/tasks.py` (FR-009) (after T017)
      Consumes T017's pins. `click.Choice(["en","zh"])` on generate and enrich (current
      generate flag set cli/wiki.py:19-39); `facts["lang"]` set by BOTH queue paths —
      the generate path's `_queue_pages` facts (pipeline.py:77-86, where
      `facts["diagrams"]` is set at :85-86 as the shape) and T016's enrich path;
      `_output_spec` appends the language instruction INSIDE the existing
      startswith(`wiki-page`) gate whenever `facts["lang"]` is present — the diagrams
      appendix at tasks.py:631-632 is the pinned precedent (D-022); omitted key →
      byte-identical bodies. MCP untouched; tool count stays 28. P6 is internally
      serial: FR-008 before FR-009 (both edit cli/wiki.py + tasks.py).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_enrich.py tests/test_wiki_promotion.py tests/test_wiki_cli.py -q`

## Phase 7: Onboarding, docs & ship gate (FR-010)
<!-- Checkpoint (plan.md): fresh install-agents output contains the wiki section; full suite green; docs + CHANGELOG updated; MCP tool count unchanged. Verify: CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest -q, plus tests/test_agent_surface.py tests/test_tool_annotations.py tests/test_status_resource_health.py -q, and grep -n '_EXPECTED_TOOL_COUNT = 28' src/cairn/mcp_server/server.py -->
- [ ] T019 [P] Write failing wiki-section pins — `tests/test_agent_surface.py` (FR-010)
      Red-first (C-02). The agent-install lane is file-disjoint from everything (plan:
      it may start immediately, in parallel with Phase 2) — new tests beside
      `test_tool_count_string_matches_server:392`/`test_skill_tool_index_lists_all_registered_tools:446`
      pin a `## Wiki` section in `_agents_instructions()` output covering generate →
      task claim → task complete → ask_compass consumption (TC-027). The tool-count
      pins (`len(registered) == 28` at :457) must stay green — the stanza changes no
      tool count (TC-030 guard). Survey gap (FR-010 PARTIAL): no wiki workflow section
      exists in `_INSTRUCTIONS_BODY` (only incidental LLM Task Queue + Knowledge Files
      mentions at _common.py:366 and ~:384).
      Verify red: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_agent_surface.py -q` (survey baseline 7 passed)
- [ ] T020 Add the `## Wiki` section to the instructions body — `src/cairn/agent_install/_common.py`; regenerate this workspace's own `AGENTS.md` (FR-010) (after T019)
      Consumes T019's section pins. One new `## Wiki` section inside the shared
      `_INSTRUCTIONS_BODY:284` (ends ~:406), after the LLM Task Queue section
      (:366-368) and before the Knowledge Files listing (~:384) — the same body feeds
      `_claude_instructions:409` (CLAUDE.md) and `_agents_instructions:419` (AGENTS.md),
      a superset of AC1. Keep the body a well-formed module-level `"""…"""` literal —
      the AST fallback `_render_agents_instructions_from_source:198` re-renders it from
      source. NEVER touch the tool-count blurb in the AGENTS.md header (_common.py:427)
      nor the `28 tools across 4 layers` line (D-023). Regenerate this workspace's own
      AGENTS.md in the same task (the on-disk agreement check inside
      `test_tool_count_string_matches_server` demands it).
      Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_agent_surface.py tests/test_tool_annotations.py tests/test_status_resource_health.py -q` and `grep -n '_EXPECTED_TOOL_COUNT = 28' src/cairn/mcp_server/server.py`
- [ ] T021 Docs, CHANGELOG and the full-suite ship gate — `docs/`, `CHANGELOG.md` (FR-010) (after T018) (after T020)
      The FR-010-carried docs pass recording the final surface FR-001..FR-009 delivered
      (spec In-scope: docs + CHANGELOG): wiki subcommands generate/search/status/retry/
      export/enrich, `cairn task drop`, `--kind-prefix`, `--lang`, fresh/stale display;
      CHANGELOG entry. Then the Phase-7 gate: whole suite green
      (`CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest -q`), the three
      enforcement suites of T020's verify green, tool count 28. After green, the single
      end-of-run PR assembles per C-01 (branch → pre-commit run --all-files →
      conventional commits → PR with the audit checklist) — commits/PR are the
      orchestrator's, not the implementer's.

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      done `<date>` — `<test/command that proves it>`
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
