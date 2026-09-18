# Plan: temporal-memory

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1     | Temporal foundation | Every memory record carries `valid_from` (default: creation time) and nullable `valid_until`; a store from the prior schema version migrates additively with no data loss; recall-latency baseline numbers are captured | FR-001 | — |
| 2     | Point-in-time recall | MCP `recall_memory` and `cairn memory search` accept an as-of date and return only memories valid at that date; default recall returns only currently-valid memories; before/after benchmark recorded in tech-spec.md | FR-002, FR-005 | Phase 1 |
| 3     | Auto-invalidation | A build pass that detects a renamed/removed referenced symbol sets that memory's `valid_until` to build time and records a successor-symbol link where the graph identifies a unique one; expired memories disappear from default recall | FR-003 | Phase 1 (write target); Phase 2 (recall-visibility demo) |
| 4     | Timeline & closeout | `cairn memory timeline <symbol>` prints the temporal history (validity intervals, successor links) of memories for that symbol; full regression green | FR-004 | Phase 1 (reads columns); Phase 2 (same-file sequencing, see map) |

## Dependencies
- Phase 1 blocks all others: `valid_from`/`valid_until` columns (and the FR-005
  index) are the contract every reader (recall filter, timeline) and writer
  (auto-invalidation) consumes.
- Phase 2 and Phase 3 are mutually independent in development (disjoint files,
  see map); Phase 3's end-to-end demo observes Phase 2's default filter.
- The FR-005 latency baseline must be captured before Phase 2's query changes
  land, or the before/after comparison is void.

## Parallelization map
<!-- The task-breaker turns this into [P] markers and (after T###) chains. -->

- Independent (all gated only on Phase 1):
  - **Recall as-of** ∥ **Auto-invalidation** — disjoint files:
    - Recall as-of touches `src/cairn/memory/promotion.py`
      (`search_memory`, promotion.py:242 — the shared chokepoint both
      surfaces call), `src/cairn/mcp_server/tools_memory.py`
      (`recall_memory`, tools_memory.py:21), `src/cairn/cli/memory.py`
      (`memory_search`, cli/memory.py:101).
    - Auto-invalidation touches `src/cairn/cli/validate.py`
      (`validate_paths`, validate.py:35), `src/cairn/compass/critic.py`
      (`validate_paths`, critic.py:202), and a new successor-link resolver
      derived from graph identity (location: unknown — verify; survey S5
      shows `src/cairn/graph/incremental.py` carries no rename signals, so
      this is new work, not an incremental-builder change).
    - Why parallel is safe: the recall area only *reads* `valid_until`;
      the invalidation area only *writes* it. No shared file; the sole
      shared artifact is the Phase 1 column contract.
- Strictly ordered (exceptions that justify serialization):
  - Phase 1 schema → recall as-of, auto-invalidation, timeline — the
    columns/index must exist before any reader or writer.
  - Baseline benchmark → recall as-of → after-benchmark — FR-005 numbers
    are only comparable if the baseline predates the query-path change.
  - Recall as-of → timeline (weak) — both edit `src/cairn/cli/memory.py`
    (recall adds an option to `memory_search` at :101; timeline adds a new
    subcommand). Different functions, same file: sequence to avoid edit
    collisions.
  - Auto-invalidation → Phase 3 checkpoint (integration only) — the demo
    "expired memory vanishes from default recall" consumes Phase 2's
    default filter; development itself is parallel with Phase 2.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: `grep -rn "valid_from\|valid_until" src/cairn/memory/ src/cairn/okf/` returns matches (inverts survey S1's empty result); migration test against a fixture store from the prior schema version passes with rows intact.
- **After Phase 2**: `grep -n "as_of\|as-of" src/cairn/mcp_server/tools_memory.py src/cairn/cli/memory.py` shows the filter on both surfaces; on a seeded store, default recall omits an expired memory and `--as-of` before its `valid_until` returns it; the before/after benchmark table is present in tech-spec.md.
- **After Phase 3**: fixture test — a renamed/removed referenced symbol triggers `valid_until` = build time plus a successor link where uniquely identifiable, and no link where ambiguous; `sed -n '31,66p' src/cairn/cli/validate.py` still delegates to `cairn.compass.critic.validate_paths` (extension, not parallel detector).
- **After Phase 4**: `cairn memory timeline <symbol>` on a seeded store lists memories with validity intervals and successor links; full test suite green.

## Risks & mitigations
- Risk: schema migration corrupts existing stores → mitigation: additive columns with defaults, riding the established additive-only `CREATE TABLE IF NOT EXISTS` pattern (src/cairn/graph/schema.py comments); migration tested against a prior-schema fixture store.
- Risk: successor links cannot be derived reliably → mitigation: links form only where the graph identifies a unique renamed successor; ambiguous cases record no link (spec assumption; survey S5 confirms the incremental builder carries no rename signals — plan assumption, resolve in tech spec).
- Risk: recall latency regression (FR-005) → mitigation: indexed validity columns land with Phase 1; baseline captured before Phase 2, after-numbers recorded in tech-spec.md. Measurement method: unknown — verify with the tech node.
- Risk: same-file contention in `src/cairn/cli/memory.py` (as-of option vs timeline subcommand) → mitigation: timeline sequenced after recall as-of.

## Delivery
Solo, PR-per-milestone (team context: none recorded — default applies).
Branch `feat/temporal-memory` (spec.md); one PR per phase with a
conventional title, each landing a demoable milestone; FR-005 benchmark
numbers recorded in tech-spec.md at the Phase 2 checkpoint.
