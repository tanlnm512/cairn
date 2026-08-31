# Plan: wiki-enhancements

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-31
**Baseline**: 0.16.2 @ e002f9b · Branch `feat/wiki-enhancements` (already cut; tip f24958c adds specs docs only — code ground identical to the survey baseline, re-verified 2026-08-31: `git diff --name-only e002f9b..f24958c` touches `specs/` only)
**AMENDED at the approval gate (2026-08-31)**: test-majority modules are EXCLUDED (was demote-only); enrich APPENDS sections (was replace); `--lang` (FR-009) deferred out of scope. Every milestone, dependency, lane, checkpoint and risk below reflects those three amendments.
**Sources**: coupling and gap claims come only from [survey.md](survey.md) citations, re-checked this session via cairn graph tools where flagged (`run_wiki_generate` callers: cli/wiki.py:68, tools_wiki.py:39 + 3 tests — confirms the survey's FR-003 wiring picture).

## Milestones

Each milestone = a phase in task.md. This round ships as ONE end-of-run PR
(user-approved shape), so phases are checkpoint gates, not PRs. Test-first
(C-02): the task-breaker pairs failing-test tasks with implementation tasks
per phase. Every FR appears in exactly one milestone.

| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Generation quality | A test-majority module is absent from the plan even when it outranks code modules by degree (exclusion); a dead path cited in both body backticks and the Sources footer produces ONE critic error; any `wiki-page*` kind renders with the full wiki output spec (Sources-footer instructions intact at any revise depth) | FR-001, FR-005 | — |
| 2 | Commit-sha provenance | Completing a wiki page task records the workspace HEAD sha in the promoted concept's extensions AND the manifest row; the sha rides task facts (resolved outside `complete_task`), which stays generic | FR-003 | Phase 1 (same-file order on `tasks.py`, see map) |
| 3 | Fresh/stale display | `cairn wiki status` and the dashboard list/detail views show fresh vs stale per page by comparing the recorded sha with current HEAD; `unknown` when either side is unavailable | FR-007 | Phase 2 (consumes the sha field) |
| 4 | Queue operations | `cairn task drop <id>` drops a pending/in-progress task (done refused; dropped is never claimable again, visible in listings); `cairn task list --kind-prefix wiki-page` lists every chain hop | FR-004 | Phase 1; runs concurrent with Phase 3 (disjoint files) |
| 5 | Human surface | Dashboard renders inline code spans as code elements and GFM pipe tables as tables with the escape-first contract intact; `cairn wiki export --dir out` writes every promoted page as frontmatter markdown named by page id, reports the count, refuses a non-empty dir without `--force` | FR-002, FR-006 | Phase 3 (frees the shared dashboard test file and `cli/wiki.py`) |
| 6 | Enrichment | `cairn wiki enrich [<page-id>] [--repo R] [--all]` queues a `wiki-page-enrich` task whose critic-passing completion APPENDS its new sections to the promoted body (prior content stays visible in the page itself; the task result records only the appended sections), riding the existing promotion branch and bounded revise cycle | FR-008 | Phases 1 (prefix rule), 3 and 5 (`cli/wiki.py` clear) |
| 7 | Onboarding, docs & ship gate | `cairn install-agents` emits an AGENTS.md wiki section (generate → claim → complete → ask_compass consumption); docs + CHANGELOG; full-suite gate; the single PR assembled per C-01 | FR-010 | Phases 1–6 |

## Dependencies

```
P1 ──► P2 ──► P3 ──┬──► P5 ──► P6 ──► P7
                   └──► P4 ──────────┘   (P4 ∥ P3)
```

Strictly-ordered edges, each justified:

- **P1 → P2**: both edit `src/cairn/llm/tasks.py` (FR-005's `_output_spec` rule + critic-merge dedupe; FR-003's promotion-extensions write) — one file, strictly ordered.
- **P2 → P3**: produced→consumed — the fresh/stale compare consumes the sha that FR-003 records; without it there is nothing to display.
- **P2 → P4**: FR-004's drop mutation and `list_tasks` prefix filter edit `tasks.py`; ordering after P2 keeps all `tasks.py` edits serial.
- **P3 → P5**: FR-007 and FR-002/FR-006 share `tests/test_dashboard_app.py` (badge tests vs renderer tests) and `cli/wiki.py` (status column vs export subcommand) — P5 starts when P3's edits to both are done.
- **P1 → P6 (behavioral)**: FR-008's enrich kind gets its output spec only through FR-005's startswith(`wiki-page`) rule — the dependency that pulled P1 first.
- **P3/P5 → P6**: FR-008 edits `cli/wiki.py` (enrich subcommand) and `src/cairn/wiki/pipeline.py` (enrich queue path + facts), the spine's hottest files; it runs last on the spine so no other phase interleaves.
- **All → P7**: docs record the final surface (subcommand set, flags, stanza text); the single PR assembles every phase.

## Parallelization map

Parallel is the default; serial exceptions carry the file evidence. Four
file-disjoint lanes exist; three hot files force the spine.

- **Independent (whole plan): planner lane (FR-001) ∥ agent-install lane (FR-010)** — file-disjoint from everything:
  - Planner lane: `src/cairn/wiki/catalog.py` (bucket/rank logic; the majority-test check computes over `module_files` already built at rank time per survey FR-001), `tests/test_wiki_planner.py` (TestPlanOrdering pins updated test-first).
  - Agent-install lane: `src/cairn/agent_install/_common.py` (`_INSTRUCTIONS_BODY` wiki section), `tests/test_agent_surface.py` (tool-count pins untouched — the stanza changes no tool count).
  - Neither touches `tasks.py`, `cli/wiki.py`, `pipeline.py`, or dashboard files. They may start immediately, in parallel with Phase 2.
- **Independent (from Phase 3): renderer lane (FR-002) ∥ export lane (FR-006)** — file-disjoint once P3 lands:
  - Renderer lane: `src/cairn/dashboard/markdown.py` (new inline-span pass + table block, escape-first), its new tests appended in `tests/test_dashboard_app.py`.
  - Export lane: `src/cairn/cli/wiki.py` (new `export` subcommand; iteration via `list_concepts`/manifest + `concept.py` round-trip, all existing — no edits outside `cli/wiki.py`), new `tests/test_wiki_export.py`.
- **Independent window (after P1): P3 (fresh/stale) ∥ P4 (task drop)** — disjoint: P3 touches `cli/wiki.py` status, `src/cairn/dashboard/data.py`, `templates/wiki.html`/`wiki_page.html`, `tests/test_wiki_cli.py` + dashboard tests; P4 touches `tasks.py` (drop + list filter), `src/cairn/cli/task.py`, new `tests/test_task_drop.py`. No shared file.
- **Strictly ordered (the serial spine): FR-005 → FR-003 → FR-007 → FR-004 → FR-006 → FR-008** — justified by three hot files no lane may share:
  - `src/cairn/llm/tasks.py`: FR-005 (`_output_spec` lookup, error merge), FR-003 (extensions write), FR-004 (drop mutation + `list_tasks` filter), FR-008 (rides the existing `startswith("wiki-page")` promotion branch — adds the enrich append seam beside the plain wiki-page write). One file, four FRs → strictly serial.
  - `src/cairn/cli/wiki.py`: FR-007 (status display), FR-006 (export), FR-008 (enrich) — strictly serial.
  - `src/cairn/wiki/pipeline.py`: FR-003 (facts + manifest row) and FR-008 (the new enrich queue path, a sibling of `_queue_pages`) — strictly serial; see assumption 2.
  - Shared test file `tests/test_wiki_promotion.py` (FR-005 dedupe pins, FR-003 extension pins) reinforces the spine order; new suites (`tests/test_wiki_enrich.py`, `tests/test_task_drop.py`, `tests/test_wiki_export.py`) keep the new FRs out of it.

Within-phase notes for the task-breaker:
- P1 is internally parallel: FR-001 (planner lane) ∥ FR-005 (critic/spec files: `tasks.py`, `src/cairn/compass/critic.py`, `src/cairn/refs.py`, `src/cairn/wiki/sources.py` + their suites).
- P6 carries FR-008 alone (the language option was deferred at the gate), but is internally serial: the enrich queue path (`pipeline.py`) lands before the append seam (`tasks.py`) and the CLI subcommand (`cli/wiki.py`) that drives it.

## Plan assumptions (for tech to resolve — flagged, not decided here)

1. **FR-003 sha resolution point**: `run_wiki_generate(conn, bundle, repo, …)` carries no workspace root and the graph DB stores `repos.path` workspace-relative (survey FR-003 gap). Assumed shape: the pipeline layer resolves HEAD via `utils/git.py:get_current_commit` (zero callers today) using `paths.resolve_store`/`resolve_workspace` + `scanner.resolve_repo_path`, derivable from the db/bundle path — callers stay untouched. If tech instead passes the sha in from `cli/wiki.py`/`tools_wiki.py`, those files join the P2 window (same spine either way). `complete_task` stays generic; a mirror sha param on the MCP `wiki_generate` tool would be a param addition only — tool count stays 28 (hard constraint; any new MCP tool re-enters via the orchestrator, never silently).
2. **FR-008 queueing shape** — **RESOLVED by tech (D-021), the other way**: not the assumed retry-style CLI-side facts assembly, but a NEW queueing function in `src/cairn/wiki/pipeline.py` (a sibling of `_queue_pages:52`, never routed through its skip condition) that requires a readable promoted concept and captures `facts["current_body"]`. The promotion branch and revise cycle are still reused unmodified (already keyed on the `wiki-page` prefix per survey FR-008). Build it in `pipeline.py`, not in `cli/wiki.py`.
3. ~~**FR-009 surface**: the spec names generate/enrich CLI; the MCP mirror is optional tech's-choice and file-disjoint from the spine tail only if `tools_wiki.py` is edited inside the P6 window.~~ **Deferred at the approval gate (2026-08-31) with FR-009 — no `--lang` surface lands this round.**
4. **New test files this round**: `tests/test_task_drop.py` (P4), `tests/test_wiki_export.py` (P5), `tests/test_wiki_enrich.py` (P6); existing pinned suites are regression gates — edited only where a pin's semantics legitimately change (TestPlanOrdering's ordering pin becomes an absence pin under D-014 exclusion; dedupe single-error assertions).

## Checkpoints

Canonical verify form (survey): `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <path> -q`.

- **After Phase 1**: a test-majority module is ABSENT from the plan even when it outranks every code module by degree, and the next code module takes its slot (D-014 exclusion — never demotion, no class tier); one dead path → one critic error; a `wiki-page-enrich`-named task body carries the full Sources-footer spec. Verify: `… pytest tests/test_wiki_planner.py tests/test_compass_critic.py tests/test_wiki_promotion.py -q` (survey baselines: 15/16/20 passed).
- **After Phase 2**: a promoted page's extensions and manifest row carry the HEAD sha; a completion with no resolvable sha still promotes without one. Verify: `… pytest tests/test_wiki_promotion.py tests/test_wiki_manifest.py -q` (20/33) and `grep -rn commit_sha src/ --include="*.py"` now matches (survey baseline: zero matches).
- **After Phase 3**: `cairn wiki status` shows fresh/stale/unknown; dashboard list + detail show the badge; unchanged sha → fresh, moved HEAD → stale, missing side → unknown. Verify: `… pytest tests/test_wiki_cli.py tests/test_dashboard_app.py tests/test_dashboard_data.py -q` (6/109/82) — must include `test_importing_dashboard_never_loads_server_stack`.
- **After Phase 4**: dropped pending/in-progress task is listed dropped and refused by claim; done refused; `--kind-prefix wiki-page` lists every chain hop. Verify: `… pytest tests/test_tasks_safety.py tests/test_task_drop.py -q` (8 + new file) and `grep -rn "kind-prefix" src/cairn/cli/task.py` matches (survey baseline: zero).
- **After Phase 5**: code spans render as code elements and a GFM table as a table with the no-inline-HTML pins still green; export writes one frontmatter file per promoted page, prints the count, refuses non-empty dir without `--force`. Verify: `… pytest tests/test_dashboard_app.py tests/test_wiki_export.py -q` and `grep -n "def export" src/cairn/cli/wiki.py` matches.
- **After Phase 6**: `cairn wiki enrich <page-id>` queues the enrich kind; a critic-passing completion APPENDS its new sections to the page body (prior content stays visible in the page; the task result records only the appended sections); a critic-failing cycle leaves the page byte-identical. Verify: `… pytest tests/test_wiki_enrich.py tests/test_wiki_promotion.py tests/test_wiki_cli.py -q`.
- **After Phase 7 (ship gate)**: fresh `install-agents` output contains the wiki section; full suite green; docs + CHANGELOG updated; MCP tool count unchanged. Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest -q` (whole suite), `… pytest tests/test_agent_surface.py tests/test_tool_annotations.py tests/test_status_resource_health.py -q`, and `grep -n '_EXPECTED_TOOL_COUNT = 28' src/cairn/mcp_server/server.py`. Then the C-01 procedure below.

## Risks & mitigations

- Risk: FR-001 exclusion re-anchors pinned planner semantics (TestPlanOrdering's degree-ordering pin becomes an absence pin) → test-first pin update inside the planner lane; the sort key itself stays `(-degree, name-ASC)` with no class tier, and an all-test-majority repo empties the candidate set — a path the planner lane must decide deliberately (D-014).
- Risk: FR-005 dedupe placement (extractor vs critic loop vs footer merge — four candidate sites per survey) changes error strings other pins assert → tech picks one site; the checkpoint requires the pinned suites green, and the single-error assertion is written before the change.
- Risk: FR-003 workspace-root resolution is genuinely unwired (relative repo paths, no root in DB, `get_current_commit` has zero callers) → assumption 1; if tech cannot resolve cleanly at the pipeline layer, the sha rides caller-side facts and P2 grows by the two caller files — same spine, re-brief via orchestrator, never silent.
- Risk: FR-007 dashboard HEAD resolution has the same reachability gap (wiki handlers discard the db element; store dir derivable, workspace root needs `resolve_workspace`) → spec already allows `unknown`; the display degrades gracefully rather than blocking the phase.
- Risk: four FRs edit `tasks.py` (FR-005, FR-003, FR-004, FR-008) and three edit `cli/wiki.py` (FR-007, FR-006, FR-008) — the highest-contention round yet → the spine is the mitigation: parallel lanes never touch spine files, and the task-breaker serializes `[P]` markers accordingly.
- Risk: enrich churns pages → rides the existing critic gate + bounded revise cycle and requires an already-promoted page (spec mitigation; no new machinery).

## Delivery

Single end-of-plan PR (user-approved shape for this run): all phases land as
conventional commits on `feat/wiki-enhancements` (already cut) — one commit
per task pair where practical (`feat(wiki): …` / `test(wiki): …` / `fix(wiki): …`),
`pre-commit run --all-files` before every commit (never `--no-verify`), and
one PR at Phase 7 carrying the filled audit checklist → CI green → merge →
post-merge `cairn update` + `record_memory`. Phases are working-tree
checkpoints, not merges: do not start a phase before its dependency's
checkpoint passes. No new runtime dependencies anywhere (C-03): renderer,
export, drop, and sha resolution are all stdlib over surveyed machinery.
