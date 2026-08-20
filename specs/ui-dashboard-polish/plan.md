# Plan: <name>

**Spec**: [spec.md](spec.md) | **Created**: YYYY-MM-DD

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1     | <name>    | <what's true when it lands> | FR-### | — |
| 2     | <name>    | <...>               | FR-### | Phase 1 |

## Dependencies
<Short graph or prose: what blocks what; what can run in parallel.>

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
- Independent: <area A> ∥ <area B> — <why: disjoint files/Concerns>
- Strictly ordered: <area C> → <area D> — <why: C produces what D consumes>

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: <observable condition + verify command>
- **After Phase 2**: <...>

## Risks & mitigations
- Risk: <...> → mitigation: <...>

## Delivery
<Branch/PR/commit cadence. Default: one commit per task, code + docs together.>
