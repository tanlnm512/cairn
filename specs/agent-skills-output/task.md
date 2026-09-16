# Tasks: agent-skills-output

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: passed @ 068eebb

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 3     | 0    |
| 3     | 2     | 0    |
| **Σ** | 9     | 0    |

## Phase 1: Skill generation core (FR-001, FR-005, FR-006)
<!-- Checkpoint (plan, After Phase 1): the module skill was written —
     `test -f .agents/skills/cairn-zeta_hub/SKILL.md` in the fixture workspace;
     `head -n 6` shows `name:` and `description:` frontmatter; the `--output`
     variant lands `test -f /tmp/sk/SKILL.md`; body has compass, symbol, memory
     sections; layout parity vs the static skill (survey S1, TC-003). -->
- [ ] T001 [P] Resolve skill selectors to candidate symbols — new `src/cairn/skillgen/__init__.py`, `src/cairn/skillgen/selector.py`, red-first `tests/test_skillgen_selector.py` (FR-001)
  - Exposes `resolve_selector(conn, selector) -> SelectorResolution` with fields `candidates: list[str]` (qualified symbol names) and `unmatched: list[str]` — the shared contract later tasks consume.
  - Selector forms (TC-005, TC-006): module name, directory prefix, explicit symbol list; resolved via existing graph queries, strictly read-only.
  - Unresolvable selector fails fast listing the unmatched candidates — no partial skill (TC-014).
  - Red-first per C-02; `tmp_path` workspaces; no eager `cairn.cli` imports in the test module (C-04).
  - Verify before implementing: `grep -rn "skillgen" src/cairn/` → no matches (survey: no assembly, no skill module).
  - Acceptance: `.venv/bin/pytest tests/ -k "skillgen and selector" -q`
- [ ] T002 (after T001) Assemble the skill draft from compass, symbols, and memory — `src/cairn/skillgen/assembly.py`, red-first `tests/test_skillgen_assembly.py` (FR-001)
  - Chained: consumes T001's `resolve_selector(conn, selector) -> SelectorResolution` and joins the same new package.
  - Exposes `assemble_draft(conn, resolution, top_k, top_n) -> SkillDraft` with fields `module: str`, `compass_body: str`, `symbols: list[str]`, `memories: list[str]`, `ranking_tier: str` — the contract T004 and T007 consume.
  - Phase 1 symbol selection is plain inclusion of resolved candidates in stable order, capped by `top_k` (default 20); ranking is Phase 2 (plan milestone note).
  - Compass body via the reader behind `get_compass` (`_get_compass`, `src/cairn/compass/router.py:245`); top-N memories (default 5) via `search_memory` (`src/cairn/memory/promotion.py:242`) called as-is — 14 precise callers, no signature changes (TC-004); default tiers only, superseded stay hidden.
  - Generated skill contains a compass excerpt even for a module with zero memories (TC-015).
  - Acceptance: `.venv/bin/pytest tests/ -k "skillgen and assembly" -q`
- [ ] T003 [P] Emit SKILL.md in the distributed layout and land it — `src/cairn/skillgen/emitter.py`, red-first `tests/test_skillgen_emit.py` (FR-005, FR-006)
  - Exposes `render_skill(name, description, sections) -> str` — sections are ordered `(heading, markdown)` pairs, the return value is the exact SKILL.md bytes — plus `landing_dir(workspace, slug) -> Path`; the contracts T004, T006, T007 consume.
  - Frontmatter emitted with the present `pyyaml>=6.0` (`pyproject.toml:45`) — zero new dependencies (D-003); `description` carries deterministic template-rendered load-trigger wording, never LLM-written (D-006); optional `references/` split for long sections.
  - Layout mirrors the static package `src/cairn/agent_integration/skill/SKILL.md` (frontmatter name/description + body; package shape per `src/cairn/agent_install/merge.py:97`) — generated frontmatter parses as the same name/description shape the static skill ships (TC-002, TC-003).
  - Default landing `<workspace>/.agents/skills/cairn-<slug>/`; `--output` overrides the directory (TC-001, TC-013); slug derives from the module stem — TC-001 pins the `cairn-` prefix via glob, not exact spelling.
  - Command output note states uninstall leaves `cairn-<slug>/` in place (D-004: the exact-name `cairn` guard at `src/cairn/agent_install/merge.py:393` is not modified); `src/cairn/agent_install/` is otherwise untouched (D-001).
  - Acceptance: `.venv/bin/pytest tests/ -k "skillgen and emit" -q`
- [ ] T004 (after T002, after T003) Wire the `cairn skill generate` CLI — new `src/cairn/cli/skill.py`, registration in `src/cairn/cli/__init__.py`, red-first `tests/test_skillgen_cli.py` (FR-001)
  - Chained: consumes T001's `resolve_selector`, T002's `assemble_draft(conn, resolution, top_k, top_n) -> SkillDraft`, and T003's `render_skill` + `landing_dir` — the pipeline is resolve → assemble → render → write to the default landing or `--output`.
  - Command shape: `cairn skill generate SELECTOR [--output DIR] [--top-k K] [--top-n N]` with defaults top-k 20, top-n 5; one-module-per-command Click layout (peers `src/cairn/cli/wiki.py`, `src/cairn/cli/map.py`).
  - Import-light module — C-04 forbids eager `cairn.cli` imports in test modules.
  - Verify before implementing: `grep -n "skill" src/cairn/cli/__init__.py` → no skill command registered (survey supporting evidence).
  - Acceptance: `.venv/bin/pytest tests/ -k "skillgen and cli" -q`; end to end `.venv/bin/cairn skill generate compass --output /tmp/skillcheck` writes a loadable SKILL.md (tech-spec code-guide verify).

## Phase 2: Ranked, critic-verified symbols (FR-002, FR-004)
<!-- Checkpoint (plan, After Phase 2): every symbol ref in the output resolves —
     `cairn def <symbol>` non-empty per ref; rejection-before-write — a forced
     invalid ref exits non-zero with no file at the target path; ranking order
     matches the dataflow index / repo_map rows on a fixture graph (unit test);
     critic identity tests (`tests/test_scoring_fixes.py::TestCriticDedup`)
     still pass. -->
- [ ] T005 [P] (after T001) Rank candidates by structural centrality in two tiers — `src/cairn/skillgen/ranking.py`, red-first `tests/test_skillgen_ranking.py` (FR-002)
  - Chained on T001 for input shape; otherwise parallel with T006 (disjoint files, plan parallelization map).
  - Exposes `rank_candidates(conn, candidates) -> RankedSymbols` with fields `symbols: list[str]` (score desc, then qualified name asc — total order) and `tier: str` (`closure` or `degree`) — the contract T007 consumes.
  - Closure tier: aggregate transitive impact from the closure store built by `build_transitive_closure` (`src/cairn/graph/dataflow.py:229`) — read-only, never mutates closure or graph tables; availability gated the way `impact_analysis` gates via `closure_available` (`src/cairn/graph/dataflow.py:576`, usage pattern `src/cairn/graph/traversal.py:228`).
  - Degrade tier (D-002): direct in/out degree from `build_repo_map` (`src/cairn/graph/repo_map.py:160`) rows `_SymbolRow.incoming/outgoing` (`src/cairn/graph/repo_map.py:21-22`); a closure-absent workspace still generates, and the skill records which tier produced its ranking (TC-007, TC-008).
  - Existence filter: a candidate is included only if its definition resolves in the graph.
  - Acceptance: `.venv/bin/pytest tests/ -k "skillgen and ranking" -q`
- [ ] T006 [P] (after T003) Gate every generated body through the deterministic critic before write — `src/cairn/skillgen/gate.py`, red-first `tests/test_skillgen_gate.py` (FR-004)
  - Chained on T003 for the rendered-bytes input shape; otherwise parallel with T005 (disjoint files, plan parallelization map).
  - Exposes `verify_draft(conn, rendered) -> GateResult` with outcomes `ok` or `rejected(failing_refs)` — the contract T007 and T009 consume.
  - Wraps `validate_paths` (`src/cairn/compass/critic.py:202`) consume-only — critic.py internals are never modified; they are identity-coupled with memory scoring (`tests/test_scoring_fixes.py::TestCriticDedup` stays green).
  - Synchronous and in-process, not a queue stage (D-005); unconditional for deterministic and polished bodies; a hand-planted bogus backtick symbol rejects the skill — nothing written, non-zero exit, failing refs listed (TC-011, TC-012).
  - The gate runs on the exact bytes destined for disk: validate after `render_skill`, never validate a draft and then re-render.
  - Acceptance: `.venv/bin/pytest tests/ -k "skillgen and gate" -q` and `.venv/bin/pytest tests/test_scoring_fixes.py -q`
- [ ] T007 (after T005, after T006) Integrate ranking and the critic gate into the generate pipeline — `src/cairn/skillgen/assembly.py`, `src/cairn/skillgen/emitter.py` write path, `src/cairn/cli/skill.py`, red-first `tests/test_skillgen_pipeline.py` (FR-002, FR-004)
  - Chained: consumes T005's `rank_candidates(conn, candidates) -> RankedSymbols` and T006's `verify_draft(conn, rendered) -> GateResult`, and edits the Phase 1 files those wrap.
  - Symbol selection switches from plain inclusion to ranked top-K — a very large module yields a bounded skill, not the whole module (TC-008).
  - The gate wraps the write step: `verify_draft` runs between `render_skill` and the file write; rejection exits non-zero and leaves no file on disk (TC-012).
  - Ranking order matches the dataflow index / repo_map rows on a fixture graph (plan checkpoint unit test).
  - Acceptance: `.venv/bin/pytest tests/ -k skillgen -q`

## Phase 3: Deterministic default + optional LLM polish (FR-003)
<!-- Checkpoint (plan, After Phase 3): `diff` of two default runs is empty and a
     test asserts no LLM client is constructed on the default path; a polish run
     is visible via `cairn task list --status pending`, and after `complete` the
     result passes the critic before write; `pre-commit run --all-files` green. -->
- [ ] T008 (after T007) Prove the deterministic default — byte-identical reruns, no LLM — `src/cairn/skillgen/` pipeline ordering, red-first `tests/test_skillgen_determinism.py` (FR-003)
  - Chained: pins total ordering across T005's ranking tie-breaks and the T007 assembly output, so it follows the integration it constrains.
  - Same selector twice yields byte-identical SKILL.md (tech-spec verify anchor): total tie-break (score desc, qualified name asc), stable memory order from `search_memory` rank order, no timestamps or set-iteration order in the draft or frontmatter (D-006 template wording).
  - A test asserts no LLM client is constructed on the default path — default generation never touches the LLM path (TC-009).
  - Acceptance: `.venv/bin/pytest tests/ -k "skillgen and determinism" -q`; two-run `diff` empty on the TC-001 fixture workspace.
- [ ] T009 (after T008) Add `--polish` through the LLM task queue behind the critic — `src/cairn/cli/skill.py`, polish stage in `src/cairn/skillgen/`, red-first `tests/test_skillgen_polish.py` (FR-003)
  - Chained: shares the `src/cairn/cli/skill.py` flag surface and the pipeline write path with T008.
  - `--polish` never invokes an LLM in-process: it enqueues one polish task through the existing queue API — `create_task` / `read_result` (`src/cairn/llm/tasks.py`), the mechanism `run_wiki_generate` uses (`src/cairn/wiki/pipeline.py:210`; precedent `generate_wiki_with_critic`, `src/cairn/wiki/generator.py:41`); the queued task is visible via `cairn task list --status pending` (TC-010).
  - The polished result re-enters T006's `verify_draft(conn, rendered) -> GateResult` and only then replaces the deterministic body — the gate stays unconditional (D-005).
  - Acceptance: `.venv/bin/pytest tests/ -k skillgen -q`

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
