# Tasks: <name>

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| **Σ** | 4     | 0    |

## Phase 1: <milestone name> (FR-###)
<!-- Checkpoint: <exit condition from plan.md> -->
- [ ] T001 <verb phrase — files touched> (FR-###)
- [ ] T002 [P] <...> (FR-###)
- [ ] T003 <...> (FR-###)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      done <date> — <test/command that proves it>
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)`; serial runs need a reason, parallel
  runs need none
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
