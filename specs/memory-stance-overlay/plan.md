# Plan: memory-stance-overlay

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-05

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1     | Stance substrate | A memory recorded via `cairn memory record --stance` or the MCP `record_memory` tool persists an explicit prior stance plus a refs-verification baseline in the memory file's frontmatter | FR-001 | — |
| 2     | Reflect engine | `cairn memory reflect` recomputes stances on a fixture store: a superseded memory is contested with its peer's id, a memory whose refs rotted is tentative, a fully verified one is preferred; a second run changes nothing | FR-002, FR-003, FR-005, FR-006, NFR-003, NFR-004 | Phase 1 |
| 3     | Surfacing + docs | A contested memory renders its stance inline with the peer reference in recall results, explore's tribal-memory section, and the CLI search/list/digest lines; docs updated | FR-004 | Phase 1 (runs parallel to Phase 2) |

## Dependencies
Stance substrate (Phase 1) defines the frontmatter keys every later phase reads
and writes, so it lands first. After Phase 1 the graph splits: the reflect
engine (Phase 2) and the render paths (Phase 3) touch disjoint files except
`src/cairn/cli/memory.py`, which carries both the new `reflect` verb (Phase 2)
and the `_memory_line` stance segment (Phase 3) — those two tasks chain, and
Phase 3's other render tasks stay parallel with all of Phase 2.

## Parallelization map
- Independent (after Phase 1): reflect engine — `src/cairn/memory/` stance
  rules module, `src/cairn/memory/promotion.py` read-only supersession
  consumption, `src/cairn/cli/memory.py` reflect verb ∥ recall/explore
  rendering — `src/cairn/mcp_server/tools_memory.py`,
  `src/cairn/mcp_server/tools_graph.py`. Disjoint files; neither consumes the
  other's output (both read only the Phase 1 frontmatter keys).
- Strictly ordered: Phase 1 capture plumbing → Phases 2 and 3 — Phase 1
  produces the `memory_stance` / `memory_stance_peer` / `memory_refs_baseline`
  frontmatter keys that both consumers read and write.
- Strictly ordered (shared file): the `cairn memory reflect` CLI verb and the
  `_memory_line` stance segment both edit `src/cairn/cli/memory.py` — the
  `_memory_line` task chains after the reflect verb task.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: a memory recorded with an explicit stance carries the
  stance and baseline keys in its file frontmatter.
  Verify: `cairn memory record pattern "stance-seed" --body "cites \`tier_for_score\`" --stance preferred --db /tmp/s.db --knowledge /tmp/k` then `grep -r "memory_stance" /tmp/k/memory/`.
- **After Phase 2**: reflect runs on the fixture store and a second run is a
  no-op (idempotent, FR-006). Verify: run `cairn memory reflect --db /tmp/s.db --knowledge /tmp/k` twice and diff the stance keys, plus `pytest tests/ -q -k stance`.
- **After Phase 3**: a contested memory shows its stance and peer inline.
  Verify: `pytest tests/test_explore_memory.py tests/test_memory_stale_flag.py -q` and recall a contested fixture memory (renders `stance=contested` with the peer id).

## Risks & mitigations
- Risk: co-cited complementary memories get flagged as contradicting peers →
  mitigation: the conservative disagreement rule (tech-spec D-002): a
  supersession edge plus a shared verified symbol ref is required; co-citation
  alone never contests (survey S5/S6).
- Risk: reflect colliding with concurrent agents writing memories →
  mitigation: per-file atomic writes via the bundle's existing write path under
  its lock (tech-spec D-003; survey S1).
- Risk: stance rendering breaking pinned recall/explore output assertions →
  mitigation: stance appends inline to existing lines; no new lines
  (tech-spec Impact analysis test sweep).

## Delivery
Solo, one PR per phase on `feat/memory-stance-overlay` (3 PRs), conventional
commits, code + docs together per PR per C-01. Each phase's checkpoint is the
PR's demo evidence.
