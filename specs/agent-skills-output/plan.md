# Plan: agent-skills-output

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-16

**Team context**: none recorded — solo dev. "Parallel" means task-level
parallelism for the task-breaker's `[P]` markers, not multiple devs.

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1     | Skill generation core | `cairn skill generate <selector>` assembles compass + symbols + memories and writes an Anthropic-layout `SKILL.md` to `.agents/skills/cairn-<slug>/` or `--output`; loads in a skill-compatible client with no manual edits | FR-001, FR-005, FR-006 | — |
| 2     | Ranked, critic-verified symbols | generated skill contains only centrality-ranked symbols whose definitions exist in the graph; the critic rejects any planted invalid reference before write | FR-002, FR-004 | Phase 1 |
| 3     | Deterministic default + optional LLM polish | default run invokes no LLM and is byte-identical across runs; enabled polish flows through the task queue behind the critic | FR-003 | Phases 1–2 |

Milestone notes (evidence in survey.md):
- Phase 1 assembly is new code — S3 gap: "no assembly step (selector → ranked
  symbols → compass excerpt → memory → SKILL.md)". Symbol selection in Phase 1 is
  plain inclusion; ranking and verification belong to Phase 2.
- Phase 2 is the risk slot pulled early — S4 gap: "critic is not wired to any
  skill emitter (new output surface)", and critic internals are load-bearing
  (see Dependencies).
- Phase 3 has existing precedents for both halves: `generate_wiki_with_critic`
  (src/cairn/wiki/generator.py), `generate_compass_with_llm`
  (src/cairn/compass/generator.py), and deterministic-output-passes-own-critic
  (`tests/test_compass_generator.py::test_deterministic_template_passes_own_critic`).

## Dependencies

```
Phase 1 (core generation) ──► Phase 2 (ranking + critic gate) ──► Phase 3 (determinism + polish)
        └────────────────────────────► Phase 3 (polish needs a draft to edit)
```

- Phase 2 after Phase 1: FR-004's gate runs "before the skill is written" — the
  write step it intercepts is Phase 1's emitter.
- Phase 3 after Phase 2: FR-003's polish clause is "behind the deterministic
  critic" — the gate it runs behind is FR-004's.
- Phase 3 after Phase 1: polish operates on the generated draft body.

Graph-checked coupling between areas (this session's graph queries):
- `get_compass` (src/cairn/mcp_server/tools_compass.py) is a leaf with 0 callers —
  the assembly area consumes it without touching any existing consumer.
- src/cairn/compass/critic.py internals are identity-shared with memory scoring
  (`tests/test_scoring_fixes.py::TestCriticDedup` asserts `scoring._file_exists IS
  critic._file_exists`) — the Phase 2 gate consumes critic functions and never
  modifies them.
- FR-002's "precomputed transitive impact" input exists — `build_dataflow_index`,
  `build_transitive_closure`, `impact_from_closure` (src/cairn/graph/dataflow.py);
  cheaper in/out-degree rows also exist (S3). No new graph infrastructure needed.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->

Independent (default):

- Phase 1: CLI command surface ∥ selector resolution + assembly ∥ emitter + landing
  - CLI surface: new module under src/cairn/cli/ + one registration point
    (S: "src/cairn/cli/ has no skill module")
  - Assembly: new generator module (precedents: src/cairn/compass/generator.py,
    src/cairn/wiki/); reads `get_compass`, repo_map rows, `search_memory` — all
    read-only consumers
  - Emitter: separate new module; format reference src/cairn/agent_integration/skill/
    is read-only (S1); writes only to `.agents/skills/cairn-<slug>/` or `--output`
  - Disjointness is checkable: one touches CLI files, two are distinct new modules.
- Phase 2: centrality ranking ∥ critic gate
  - Ranking lives in the assembly module's selection functions; the gate is a new
    thin wrapper around src/cairn/compass/critic.py (consume-only).
  - Different files; they meet only at the write path.

Strictly ordered (exceptions — each justified by produce/consume):

- Generator function signature (selector → draft) → wiring of CLI + assembly +
  emitter — the wiring task consumes all three areas' outputs.
- Phase 1 emitter → Phase 2 critic gate — the gate wraps the write step.
- Phase 1 draft + Phase 2 gate → Phase 3 polish — polish edits the draft and its
  result is re-verified by the gate before write.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: the module skill was written —
  `test -f .agents/skills/cairn-zeta_hub/SKILL.md`; `head -n 6` shows `name:` and
  `description:` frontmatter; the `--output /tmp/sk` variant lands
  `test -f /tmp/sk/SKILL.md`; body has compass, symbol, memory sections; layout
  parity vs `find src/cairn/agent_integration/skill -type f | head` (S1 verify).
- **After Phase 2**: every symbol ref in the output resolves — `cairn def <symbol>`
  non-empty per ref; rejection-before-write — forced invalid ref exits non-zero
  with `test ! -f <target path>`; ranking order matches the dataflow index /
  repo_map rows on a fixture graph (unit test); critic identity tests
  (`tests/test_scoring_fixes.py::TestCriticDedup`) still pass.
- **After Phase 3**: `diff` of two default runs is empty and a test asserts no LLM
  client is constructed on the default path; polish run visible via
  `cairn task list --status pending`, and after `complete` the result passes the
  critic before write; `pre-commit run --all-files` green.

## Risks & mitigations
- Risk: Anthropic's skill format is vendor-specific and may change → mitigation:
  emitter is a thin final layer (spec's FR-005 ruling; enforced by the Phase 1
  file split — assembly stays format-agnostic).
- Risk: critic functions are load-bearing beyond skills (identity-coupled with
  memory scoring) → mitigation: consume-only gate, guarded by existing identity
  tests.
- Risk: unspecified contract details (CLI registration point in src/cairn/cli/,
  `cairn-<slug>` slug rule, polish opt-in flag) → mitigation: marked
  `unknown — verify`; tech phase pins each before Phase 1 tasks break down.

## Delivery
Solo, one PR per milestone (3 PRs) on `feat/agent-skills-output`; conventional
commit/PR titles per the workspace shipping workflow; code + docs land together
per PR, never per task (ADR-001). CI green on each PR before merge; post-merge
`cairn update` + `record_memory` per workspace rules.
