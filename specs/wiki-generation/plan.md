# Plan: wiki-generation

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-31
**Baseline**: 0.16.0 @ 264647ae4cf286e7efed52afc87d98589b81258a · Branch `feat/wiki-generation` (already cut)
**Sources**: statuses below come only from [survey.md](survey.md); coupling claims verified this session via cairn graph tools + grep (noted inline).

## Milestones

Each milestone = a phase in task.md = one PR (repo cadence, specs/CONSTITUTION.md C-01). Every FR appears in exactly one milestone. Test-first (C-02): each milestone pairs failing-test-first tasks with implementation tasks; the task-breaker emits those pairs.

| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Page-plan core | A deterministic page plan (overview + top modules by incoming degree, `--pages` cap default 10, per-page id/title/description/module/seeds/input-hash) is computed from the graph, identical across runs; empty/unindexed graph exits 1 queueing nothing (US1 AC3) | FR-001 | — |
| 2 | Queue & promotion core | `cairn wiki generate --llm` queues one pending `wiki-page` task per planned page; an agent completing a task with resolvable Sources footer gets a promoted Wiki-Article concept (sources frontmatter + lineage); unresolvable refs spawn a revise task and promote nothing; pages are reachable via existing search/compass wiring | FR-002, FR-003, FR-004 (regression only — mechanism already DONE per survey item FR-004), FR-010 | Phase 1 |
| 3 | Manifest & incremental | Re-running generate queues only changed/new pages (`--force` overrides); manifest records plan entry, input hash, task id, state with atomic writes | FR-005 | Phases 1–2 |
| 4 | Status & retry CLI | `cairn wiki status` shows per-page states with aggregate counts; `cairn wiki retry` re-queues exactly failed/dropped pages, never touching promoted ones | FR-006 | Phase 3 |
| 5 | Refine-catalog path | `--refine-catalog` queues a `wiki-catalog` task; validated refined outline (bogus module entries revert to deterministic) becomes the page plan | FR-007 | Phase 4 |
| 6 | MCP tool | `wiki_generate` tool (repo/pages/refine-catalog/diagrams/force) returns page plan + queued task ids; tool count 27→28 across server assert + 3 pinned tests + docs heading | FR-008 | Phase 3 |
| 7 | Dashboard wiki view | Dashboard wiki tab lists pages with states and renders a selected page's markdown body + sources | FR-009 | Phase 3 |
| 8 | Docs & changelog | CLI reference, MCP tools reference, knowledge/memory docs updated; CHANGELOG `[Unreleased]` entry | FR-011 | Phases 4–7 |

## Dependencies

```
M1 ──► M2 ──► M3 ──┬──► M4 ──► M5 ─────────────┐
                   ├──► M6 ────────────────────┼──► M8
                   └──► M7 ────────────────────┘
```

Strictly-ordered edges, each justified by produced→consumed:

- **M1 → M2**: the queueing loop consumes the page-plan record and input-hash definition M1 produces. No queue loop can exist before a plan exists.
- **M2 → M3**: skip logic ("hash matches AND concept is promoted", spec FR-005) consumes M2's promotion branch — promoted-state is an input to the manifest state machine.
- **M3 → M4/M6/M7**: all three consume the manifest format and the promoted-article concept_id convention fixed in M1–M3 (producers before consumers).
- **M4 → M5**: both edit `src/cairn/cli/wiki.py` and the `src/cairn/wiki/` module (M5 additionally touches `src/cairn/llm/tasks.py:_output_spec`); same-file contention, so serial within the lane.
- **M6/M7 → M8**: docs record the final surface — final CLI command set (M4/M5), tool count 28 (M6), dashboard view (M7). `docs/mcp-tools.md` is touched by both M6 (heading count "The 27 tools by layer", survey FR-008 gap) and M8 (feature prose), so M8 strictly follows M6.

## Parallelization map

Parallel is the default; serial exceptions are listed with the file evidence that justifies them. Post-M3, three lanes run concurrently:

- **Independent: Lane A (M4→M5, CLI/task-queue lane) ∥ Lane B (M6, MCP lane) ∥ Lane C (M7, dashboard lane)** — file-disjoint:
  - Lane A: `src/cairn/cli/wiki.py`, `src/cairn/wiki/` (status/retry/refine helpers), `src/cairn/llm/tasks.py` (M5's `_output_spec` entry only), new tests (e.g. `tests/test_wiki_cli.py`).
  - Lane B: new MCP tools file under `src/cairn/mcp_server/`, `src/cairn/mcp_server/server.py` (`_EXPECTED_TOOL_COUNT` at server.py:55), `tests/test_status_resource_health.py` (:281), `tests/test_server_robustness.py` (:192), `tests/test_agent_surface.py` (:11), `docs/mcp-tools.md` (count heading only). Disjoint from Lane A/C files; consumes only the generate pipeline's public function (exists after M3).
  - Lane C: `src/cairn/dashboard/app.py` (route table ~lines 925–965, handler pattern per `memory`/`tasks` handlers), `src/cairn/dashboard/data.py` (`get_task_queue`:613 / `get_recent_memories`:587 pattern), new template(s) in `src/cairn/dashboard/templates/`, `tests/test_dashboard_app.py`, `tests/test_dashboard_data.py`. No overlap with Lanes A/B.
- **Strictly ordered (the serial spine): M1 → M2 → M3** — one lane, two reasons:
  1. Produced→consumed: plan structure → queue loop → manifest skip (see Dependencies).
  2. Shared hot file inside M2: FR-002 (`_output_spec`, tasks.py:501) and FR-003 (promotion branch in `complete_task`, tasks.py:210) both edit `src/cairn/llm/tasks.py`. Impact check this session: `_output_spec` has 79 impacted symbols / 58 affected tests — additive dict entries are low-risk, but the file is contended, so M2's FR-002 and FR-003 work is one serial workstream, not parallel. `complete_task` has exactly one production caller (`cli/task.py:task_complete:103`) plus tests, so the new branch is contained.
- **Strictly ordered: lanes → M8** — same-file overlap on `docs/mcp-tools.md` (M6) and docs recording final state.

Within-lane notes for the task-breaker:
- FR-004 needs no implementation task — survey status DONE ("the revise cycle is kind-agnostic and already fires for ANY kind", tasks.py lines 368–403); pair only regression tests (revise spawn + bounded drop for the `wiki-page` kind) with M2.
- FR-010 is wiring verification, not new machinery: `bundle.search` already reaches `wiki/` concepts with no area filter (okf/bundle.py:182) and compass routing has a wiki layer (compass/router.py:108, `_search_wiki`:237) — survey FR-010. Its tasks are regression tests in M2 asserting promoted articles surface in both paths; the one substantive piece (populating `sources`) is FR-003's.

## Plan assumptions (for tech to resolve — flagged, not decided here)

1. **FR-007 sequencing rule**: the queue has no task-spawns-tasks-on-completion hook beyond the revise cycle (survey FR-007 gap). This plan assumes the thin shape — page tasks spawn on the *next* generate run after the `wiki-catalog` task's validated outline is persisted (two-step re-run), so M5 stays flag + kind + validator + fallback. If tech chooses a completion hook inside `complete_task`, M5 grows a queue-engine change and its tasks.py edits must be re-sequenced against the lane.
2. **Dashboard markdown rendering**: nothing in `src/cairn/dashboard/templates/` renders markdown today (survey FR-009, verified absence). Renderer choice (tiny vendored renderer vs new dependency) is tech's; if it needs a new dependency, M7 slips later but stays file-disjoint from Lanes A/B either way.
3. **MCP tool placement**: assumed to be a new `src/cairn/mcp_server/tools_wiki.py` (per-layer files are the existing pattern); any existing tools_*.py file is equally file-disjoint from the other lanes.
4. **`--llm` flag shape**: `generate` gains `--llm` (and `--pages`, `--force`, `--diagrams`, `--refine-catalog`) alongside the untouched deterministic path (spec Scope: deterministic behavior stays as-is) — flag surface detail is tech's.

## Checkpoints

Canonical verify form (survey): `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest <path> -q` — plain `uv run pytest` fails (pytest rides the `[test]` extra).

- **After Phase 1**: plan is deterministic (two runs → identical plan, same input hashes); empty-graph run exits 1 with nothing queued. Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_wiki_planner.py -q` (new file, created this phase) plus substrate regression `… pytest tests/test_tasks_safety.py tests/test_compass_critic.py -q` (survey verify FR-001: 24 passed at baseline).
- **After Phase 2**: queue a page task, complete with resolvable refs → Wiki-Article concept with `sources` frontmatter + page-id/input-hash lineage exists under `wiki/`; completion with an unresolvable ref → revise task spawned, no promoted concept. Verify: `… pytest tests/test_tasks_safety.py tests/test_compass_critic.py tests/test_wiki_promotion.py -q` (last file new) — must include the FR-004 regression class and the FR-010 search/compass assertions. Suite tripwires stay green: `… pytest tests/test_suite_hygiene.py -q`.
- **After Phase 3**: first run queues N pages + writes manifest; immediate re-run queues 0; touch a planned page's module inputs, re-run → exactly that page re-queued; `--force` re-queues all. Verify: `… pytest tests/test_wiki_manifest.py -q` (new) plus the Phase-2 set.
- **After Phase 4**: `cairn wiki status` prints per-page states + aggregates; `cairn wiki retry` re-queues only failed/dropped, attempt counters preserved, promoted untouched. Verify: `… pytest tests/test_wiki_cli.py -q` (new, CliRunner pattern per tests/test_knowledge_cli.py fixture style) and `grep -c 'wiki.command' src/cairn/cli/wiki.py` shows 4 registered subcommands (baseline: 2, survey FR-006 verify).
- **After Phase 5**: `--refine-catalog` queues a `wiki-catalog` task; a refined outline naming a nonexistent module reverts that entry to deterministic; page tasks spawn only from the validated plan. Verify: `… pytest tests/test_wiki_refine.py -q` (new) and `grep -rn 'wiki-catalog' src/ tests/` now matches (baseline: zero matches, survey FR-007 verify).
- **After Phase 6**: `wiki_generate` MCP tool returns plan + task ids; tool-count assertion updated. Verify: `… pytest tests/test_status_resource_health.py tests/test_server_robustness.py tests/test_agent_surface.py -q` (the three pinned tests from survey FR-008) and `grep -n '_EXPECTED_TOOL_COUNT' src/cairn/mcp_server/server.py` shows 28.
- **After Phase 7**: dashboard wiki route lists pages with states; detail view renders markdown + sources; import-guard still holds. Verify: `… pytest tests/test_dashboard_app.py tests/test_dashboard_data.py -q` — must include `test_importing_dashboard_never_loads_server_stack` (survey FR-009 verify).
- **After Phase 8 (ship gate)**: full suite green; docs mention all new commands/tools; CHANGELOG `[Unreleased]` has the entry. Verify: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest -q` (whole suite) and `grep -n 'wiki' docs/cli-reference.md docs/mcp-tools.md docs/knowledge-and-memory.md | head` shows the new sections (baseline: generate/search mentions only, survey FR-011 verify). Then the repo's mandatory branch→pre-commit→PR→CI procedure (C-01) and post-merge `cairn update` + `record_memory`.

## Risks & mitigations

- Risk: module ranking by incoming degree picks poor page boundaries → mitigated upstream (spec): `--pages` cap + refined-catalog path; M1 checkpoint asserts determinism, M5 adds the escape hatch.
- Risk: Sources-footer parsing rejects legitimate prose formatting → spec mitigation carries into M2 tasks: parser tolerates list/inline link forms; critic verdicts distinguish errors from warnings (critic.py:51 errors vs :57 warnings, survey FR-003).
- Risk: concurrent `generate` runs racing the manifest → M3 uses the atomic-write pattern (`paths.py:set_config_values:292` / `okf/concept.py:to_file:145` shape, survey FR-005) and reads the manifest before queue decisions.
- Risk: M2 touches the highest-contention file (`tasks.py`, `_output_spec` impact 79 symbols) → contained inside the single serial spine lane; no other lane edits it after M2 (M5's addition is one dict entry, verified additive pattern).
- Risk: FR-007 assumption 1 wrong (tech picks a completion hook) → M5 grows; scope change re-enters plan via the orchestrator, not silently.

## Delivery

One PR per milestone (solo dev, PR-per-feature cadence, C-01): branch `feat/wiki-generation` → pre-commit run --all-files → conventional commit (`feat(wiki): …` / `test(wiki): …`) → PR with the audit checklist → CI green → merge. Test-first pairs inside each PR (C-02): failing test committed with or before its implementation. Lanes A/B/C may interleave PRs once M3 merges; within a lane, PRs are strictly ordered. After final merge: `cairn update` + `record_memory` per AGENTS.md.
