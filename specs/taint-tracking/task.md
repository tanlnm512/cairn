# Tasks: taint-tracking

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: pending — the orchestrator writes `passed @ <sha>` here

Status provenance: survey S1-S4 are DONE substrate items (dataflow index,
`transitive_edges` closure, `edges.resolution` labels, explore/blast surfaces —
each with a passing verify command in survey.md); they are what the tasks
below build on. The taint features themselves exist nowhere (survey
§Supporting evidence: `grep -rni "taint" src/cairn --include="*.py"` → no
source hits), so every task opens as todo; no task carries a done-note.

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 7 | 0 |
| 2 | 3 | 0 |
| **Σ** | 10 | 0 |

## Phase 1: Taint core (sources/sinks config + propagation + `cairn taint`) (FR-001, FR-002, FR-003, FR-005)
<!-- Checkpoint (plan): a fixture with a CWE-pattern flow yields the full path
     from the taint trace with a resolution label on every hop; the clean
     fixture yields zero findings default and fuzzy; a config override
     replaces a default source/sink. Verified by the T007 suite plus grepping
     the trace output for a resolution label per hop. -->
- [ ] T001 Define the taint registry contract and default source/sink tables — new src/cairn/graph/taint.py (FR-001, FR-005)
  - Contract both parallel tracks code against (plan §Dependencies names it the one shared artifact): `TaintRegistry` dataclass with fields `sources`/`sinks`, each dict[str, set[str]] mapping FR-001 category → exact call names; `DEFAULT_SOURCES`/`DEFAULT_SINKS` constants; `build_registry(source_overrides, sink_overrides) -> TaintRegistry` where an override category replaces the same-named default category and a new category extends the set (plan checkpoint: "config override replaces a default source/sink"); `source_names()`/`sink_names()` flattened exact-match sets.
  - Category tokens pinned here (test.md's TC commands consume them verbatim): sources `http`, `cli`, `env`, `file-read`, `api-response`; sinks `sql`, `shell`, `file-write`, `network-send`, `eval-deserialize`. Generic call names only, exact-name matching, no heuristics, no framework packs (D-002); stdlib only (D-006). No propagation in this commit.
  - Serial (no [P]): T002 and T003 both import this contract.
  - Anchors TC-011: the default tables must name every FR-001 category.
  - Verify before implementing: `grep -rni "taint" src/cairn --include="*.py"` → no source hits (survey §Supporting evidence).
- [ ] T002 [P] (after T001) Wire taint source/sink overrides into workspace config — src/cairn/graph/config.py (FR-001, FR-005)
  - Consumes T001's `build_registry`/`TaintRegistry`. Extend `load_config` (`src/cairn/graph/config.py:77`, session grep) with taint source/sink override tables parsed by the existing typed `_as_string_list` (:137) / `_as_string_dict` (:159) parsers (line numbers re-verified this session; plan §Assumptions flags them as session grep, not survey evidence).
  - Config replaces/extends whole categories through `build_registry`; defaults stay owned by the T001 module and are never edited in place.
  - Parallel with T003 — disjoint files (config.py vs the new taint module), plan's A ∥ B map.
  - Anchors TC-002: the custom-config fixture's declared `queue-msg`/`render` labels must reach the registry through this path.
  - Verify before implementing: `grep -n "taint" src/cairn/graph/config.py` → 0 matches.
- [ ] T003 [P] (after T001) Implement the propagation engine and path/seed queries — src/cairn/graph/taint.py (FR-002, FR-003)
  - Consumes T001's `source_names()`/`sink_names()`. Depth-capped BFS over `edges` — never the `transitive_edges` closure (it carries no resolution column, D-001) — default filter `resolution = 'exact'`, fuzzy opt-in adds `ambiguous`/`unresolved` (FR-003, S3); seeded at source call names, terminated at sink call names; default depth cap `CLOSURE_MAX_DEPTH = 4` (`src/cairn/graph/dataflow.py:28`, S2) with an explicit `max_depth` override. Document the cap as the precision boundary (plan §Risks).
  - Exports the CLI + Phase 2 contract: `TaintHop(file, symbol, resolution)`, `TaintPath(hops: list[TaintHop])`, `find_paths(conn, registry, from_pattern, to_pattern, fuzzy=False, max_depth=CLOSURE_MAX_DEPTH) -> list[TaintPath]` where a pattern is an exact call name or a T001 category token, and `intersect_seeds(conn, registry, seeds: set[str], fuzzy=False) -> list[TaintPath]`.
  - Parallel with T002 — disjoint files, plan's A ∥ B map.
  - Anchors TC-003/TC-004 (exact-only default, labeled fuzzy hop) and TC-001's per-hop labels.
  - Verify before implementing: `grep -n "resolution" src/cairn/graph/schema.py | head -3` (survey S3 verify).
- [ ] T004 (after T003) Add the cairn taint query command — new src/cairn/cli/taint.py registered via the package's `@main.command()` convention (src/cairn/cli/__init__.py:8) (FR-002, FR-003)
  - Consumes T003's exported API verbatim (`find_paths`, `TaintPath`, `TaintHop`) and T002's config keys (the registry loads through `load_config`). Flags `--from`, `--to`, `--fuzzy`, `--max-depth`, with `--fuzzy` mirroring cli/blast.py (S3); every hop renders `file:symbol [resolution-label]` (FR-002); help/output states the call-graph-level precision boundary and the depth cap (plan §Risks).
  - Unknown source/sink pattern or no path: report and exit non-zero — never silent success (tech-spec §Code guide pitfall); TC-006 pins crash-free output.
  - Serial: consumes T003's query output (plus T002/T006 artifacts named above); sole owner of this file.
  - Verify before implementing (US1 AC1): `cd specs/taint-tracking/fixtures/sql-flow && cairn taint --from http --to sql` prints the full three-hop path (fixture from T006).
- [ ] T005 (after T003) Ship the shared taint-warning formatter — src/cairn/graph/taint.py (FR-004)
  - Consumes T003's `TaintPath`. `format_taint_warning(paths: list[TaintPath]) -> str` returns the warning text naming the path (hops as `file:symbol [resolution-label]`); both Phase 2 surfaces render this text verbatim — one formatter, no per-surface renderer drift (plan §Risks).
  - The warning text never contains the `degraded: rung` substring, so it can never ride the degradation footnote channel (D-004).
  - Serial (no [P]): shares taint.py with T003 (chained above). Anchors TC-007/TC-008 warning wording.
  - Verify before implementing: `grep -rn "taint" src/cairn/graph/blast.py src/cairn/mcp_server/tools_graph.py` → 0 matches (survey S4 gap: neither surface renders any security annotation).
- [ ] T006 [P] Build the eight test.md fixture workspaces — new specs/taint-tracking/fixtures/ tree: sql-flow, ambiguous-hop, clean-workspace, custom-config, framework-only, cli-shell, category-matrix, change-intersect (FR-001, FR-002, FR-003, FR-005)
  - Pure data, no upstream task: each workspace is the self-contained mini workspace test.md §Fixtures defines — flows, shapes, and the config-declared `queue-msg`/`render` labels verbatim from that section.
  - Category tokens in fixture flows and configs follow T001's pinned list in this file — a documentation contract, no code dependency. custom-config and framework-only encode the FR-005 guards (declared labels; framework idioms no default call-name key matches).
  - change-intersect (sql-flow plus the off-path `format_label` helper) is built here though only TC-007/TC-008 consume it in Phase 2.
  - Parallel with T002/T003 — disjoint files.
  - Anchors the Given clause of every auto case, TC-001–TC-011.
- [ ] T007 (after T004) Add the Phase 1 fixture suite — new tests/test_taint_fixtures.py asserting the auto pass conditions of TC-001–TC-006 and TC-009–TC-011 (FR-001, FR-002, FR-003, FR-005)
  - Consumes T004's CLI, T006's fixtures, and T002's config path (TC-002). The suite indexes each fixture workspace before its assertions (the pass conditions run `cairn taint` from inside the fixture dir); assertions key on fixture symbol names, resolution labels, and exit status per test.md §Conventions.
  - TC-012's scale sweep stays manual per test.md — not automated here.
  - This suite is the Phase 1 checkpoint gate (plan §Checkpoints): clean fixture zero findings default and fuzzy; a resolution label per hop; a config override replaces a default source/sink.
  - Serial: consumes T004 + T006 outputs; sole owner of the test file.
  - Acceptance: `pytest tests/test_taint_fixtures.py -q` green = Phase 1 checkpoint verified.

## Phase 2: Security warnings in explore + blast (FR-004)
<!-- Checkpoint (plan): a diff intersecting a taint path makes both surfaces
     emit the warning naming the path; a clean diff emits none. Verified by
     the T010 surface assertions plus the survey's own S4 probes. -->
- [ ] T008 [P] (after T007) Surface the taint warning in explore — src/cairn/mcp_server/tools_graph.py explore (:432, S4) (FR-004)
  - Consumes T003's `intersect_seeds` and T005's `format_taint_warning`. Intersect the query's seeds with taint-path endpoints and append a `=== Taint paths ===` section rendering the formatter's text BEFORE the terminal degradation footnote — the footnote stays the last line and the warning never reuses the footnote channel (D-004).
  - Read only the seeds plus `.get()`-optional keys: the five-key stub result dict fed by the footnote tests must still render unchanged (D-005); no new MCP tool (D-003).
  - Parallel with T009 — disjoint files (plan's C ∥ D map); both wait for the Phase 1 checkpoint (T007 green) per the plan's strict A+B → C/D order.
  - Anchors TC-008 (mechanical assertions land in T010).
  - Verify before implementing: `pytest tests/test_mcp_degradation_footnote.py -q` green (footnote-once, footnote-last-line, healthy-path pins) — and green again after.
- [ ] T009 [P] (after T007) Add taint_paths to blast — src/cairn/graph/blast.py compute_blast (:471, S4) plus renderers (FR-004)
  - Consumes T003's `intersect_seeds` and T005's `format_taint_warning`. Intersect diff seeds with taint-path endpoints; add the additive `taint_paths` result key; `render_blast` renders it in text/mermaid/json via `result.get(...)`, so old-shape dicts (the hand-built mermaid fixture) render unchanged and a missing key means no section (D-005).
  - Never mutate `seeds`/`radius` in place — the exact-traffic pins on payload contents stay green (tech-spec §Test-tree sweep).
  - Parallel with T008 — disjoint files (plan's C ∥ D map); waits for the Phase 1 checkpoint (T007 green).
  - Anchors TC-007 (mechanical assertions land in T010).
  - Verify before implementing: `pytest tests/test_graft_parity_blast.py -q` green before and after.
- [ ] T010 (after T008) Add the Phase 2 surface assertions — new tests/test_taint_surfaces.py covering TC-007 and TC-008 (FR-004)
  - Consumes T008's explore section and T009's `taint_paths` rendering, on T006's change-intersect fixture: the on-path query/change output warns naming the path; the off-path `format_label` output stays silent — blast via CLI output (TC-007), explore via the tool function called directly the way the footnote pins do (TC-008's mechanical proxy; the human MCP-client pass remains test.md's manual case).
  - Survey probes still hold after both surfaces change: `grep -n "def explore" src/cairn/mcp_server/tools_graph.py` and `grep -n "def compute_blast" src/cairn/graph/blast.py` (plan Phase 2 checkpoint).
  - Serial: consumes T008 + T009 outputs; sole owner of the test file.
  - Acceptance: `pytest tests/test_taint_surfaces.py tests/test_mcp_degradation_footnote.py tests/test_graft_parity_blast.py -q` green = Phase 2 checkpoint verified.

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
- Post-task hygiene (workspace AGENTS.md): code tasks run `cairn update`
  and `record_memory` for learnings; `cairn doctor` when a performance or
  fallback path was touched
